"""Loopback-only workspace for human annotation; no model, audio or external requests."""

from __future__ import annotations

import argparse
import os
import secrets
import sys
import threading
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import Field, StrictBool, StrictInt, TypeAdapter, ValidationError

from app.contracts.models import EvaluationInput, FrozenModel, NonEmpty
from app.domain.scoring import resolve_option
from app.domain.snapshots import conversation_content_hash, stable_hash
from evals.prepare import (
    AnnotationDraft,
    DraftAnswer,
    DraftDocument,
    ManualReview,
    ScenarioAssignment,
    Split,
    complete_reference,
    finalize_references,
    prepare_drafts,
    private_path,
    write_private_json,
)

ASSETS = Path(__file__).with_name("review_ui")
Revision = Annotated[int, Field(strict=True, ge=0)]


class Selection(FrozenModel):
    conversation_version_id: NonEmpty
    selected: StrictBool = False
    scenario_group: NonEmpty | None = None


class SelectionUpdate(FrozenModel):
    revision: Revision
    selections: tuple[Selection, ...]
    seed: StrictInt = 42
    holdout_groups: Annotated[int, Field(strict=True, ge=1)] = 1
    grouped_by: NonEmpty | None = None


class AnnotationUpdate(FrozenModel):
    revision: Revision
    conversation_version_id: NonEmpty
    answers: tuple[DraftAnswer, ...]
    annotated_by: NonEmpty | None = None
    language_understood: StrictBool = False
    reviewed_by: NonEmpty | None = None
    review_confirmed: StrictBool = False
    finish: StrictBool = False


class ExportRequest(FrozenModel):
    revision: Revision
    split: Split


class ReviewState(SelectionUpdate):
    schema_version: Literal["local-human-review-v1"] = "local-human-review-v1"
    inputs_hash: NonEmpty
    draft: DraftDocument | None = None


def incomplete(record: AnnotationDraft) -> bool:
    try:
        complete_reference(record)
        return False
    except ValueError:
        return True


class ReviewWorkspace:
    def __init__(self, inputs: Path, directory: Path) -> None:
        self.directory = private_path(directory / "state.json").parent
        self.directory.mkdir(parents=True, exist_ok=True)
        self.mutex = threading.RLock()
        self._lock = (self.directory / "workspace.lock").open("a+b")
        try:
            self._lock.seek(0, os.SEEK_END)
            if self._lock.tell() == 0:
                self._lock.write(b"0")
                self._lock.flush()
            self._lock.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.inputs = TypeAdapter(list[EvaluationInput]).validate_json(
                private_path(inputs).read_text(encoding="utf-8")
            )
            self.index = {item.conversation.id: item for item in self.inputs}
            if not self.inputs or len(self.index) != len(self.inputs):
                raise ValueError("Входы должны содержать уникальные версии диалогов.")
            # Validate hashes/snapshots before exposing any transcript or accepting labels.
            for item in self.inputs:
                if item.conversation.content_hash != conversation_content_hash(item.conversation):
                    raise ValueError("Хэш диалога не соответствует сохранённому тексту.")
            fingerprint = stable_hash([item.model_dump(mode="json") for item in self.inputs])
            saved = sorted(self.directory.glob("revision-*.json"))
            if saved:
                self.state = ReviewState.model_validate_json(saved[-1].read_text(encoding="utf-8"))
                if self.state.inputs_hash != fingerprint:
                    raise ValueError("Входные snapshots изменились. Используйте новую рабочую папку.")
                self._validate_state(self.state)
            else:
                self.state = ReviewState(
                    revision=0, inputs_hash=fingerprint,
                    selections=tuple(Selection(conversation_version_id=key) for key in self.index),
                )
                self._persist(self.state)
        except BaseException:
            self._lock.close()
            raise

    def close(self) -> None:
        self._lock.close()  # OS releases the advisory lock, including after process termination.

    def _persist(self, state: ReviewState) -> None:
        self._validate_state(state)
        target = self.directory / f"revision-{state.revision:08d}.json"
        temporary = self.directory / f"pending-{uuid4().hex}.json"
        write_private_json(temporary, state.model_dump(mode="json"))
        # The OS workspace lock permits one writer; rename publishes a complete revision.
        if target.exists():
            raise ValueError("Такая ревизия уже сохранена.")
        temporary.rename(target)
        self.state = state

    def _validate_state(self, state: ReviewState) -> None:
        mapping = {row.conversation_version_id: row for row in state.selections}
        if len(mapping) != len(state.selections) or set(mapping) != set(self.index):
            raise ValueError("Сохранённая выборка не совпадает с входами.")
        if state.draft is not None:
            if (state.draft.seed != state.seed or state.draft.holdout_groups != state.holdout_groups
                    or not state.grouped_by
                    or {r.evaluation_input.conversation.id for r in state.draft.records}
                    != {key for key, row in mapping.items() if row.selected}):
                raise ValueError("Сохранённая фиксация выборки повреждена.")
            for record in state.draft.records:
                key = record.evaluation_input.conversation.id
                if (record.evaluation_input != self.index[key]
                        or record.scenario_group != mapping[key].scenario_group):
                    raise ValueError("Сохранённый snapshot или группа изменены.")

    def _expect(self, revision: int) -> None:
        if revision != self.state.revision:
            raise HTTPException(409, "Есть изменения из другой вкладки. Обновите страницу.")

    def _partition(self, update: SelectionUpdate) -> DraftDocument:
        chosen = [row for row in update.selections if row.selected]
        if not update.grouped_by or any(row.scenario_group is None for row in chosen):
            raise HTTPException(422, "Укажите проверяющего группировки и группу каждого выбранного диалога.")
        if len(chosen) < 2:
            raise HTTPException(422, "Выберите минимум два диалога из разных групп.")
        try:
            return prepare_drafts(
                [self.index[row.conversation_version_id] for row in chosen],
                [ScenarioAssignment(conversation_version_id=row.conversation_version_id,
                                    scenario_group=row.scenario_group or "") for row in chosen],
                seed=update.seed, holdout_groups=update.holdout_groups,
            )
        except (ValueError, KeyError):
            raise HTTPException(422, "Проверьте группы: минимум одна для настройки и одна для проверки; переводы и версии должны оставаться вместе.") from None

    def _selection_allowed(self, update: SelectionUpdate) -> None:
        self._expect(update.revision)
        if self.state.draft is not None:
            raise HTTPException(409, "Состав и группы уже зафиксированы.")
        if (len(update.selections) != len(self.index)
                or {row.conversation_version_id for row in update.selections} != set(self.index)):
            raise HTTPException(422, "Список должен содержать каждую версию ровно один раз.")

    def preview(self, update: SelectionUpdate) -> dict[str, object]:
        with self.mutex:
            self._selection_allowed(update)
            draft = self._partition(update)
            return {split: {
                "count": sum(r.split == split for r in draft.records),
                "languages": dict(Counter(r.evaluation_input.conversation.language
                                          for r in draft.records if r.split == split)),
                "channels": dict(Counter(r.evaluation_input.conversation.channel
                                         for r in draft.records if r.split == split)),
            } for split in ("tune", "holdout")}

    def selection(self, update: SelectionUpdate, *, freeze: bool = False) -> None:
        with self.mutex:
            self._selection_allowed(update)
            raw = self.state.model_dump(mode="json") | update.model_dump(mode="json")
            raw["revision"] += 1
            if freeze:
                raw["draft"] = self._partition(update).model_dump(mode="json")
            self._persist(ReviewState.model_validate(raw))

    def annotation(self, update: AnnotationUpdate) -> None:
        with self.mutex:
            self._expect(update.revision)
            document = self.state.draft
            if document is None:
                raise HTTPException(409, "Сначала зафиксируйте выборку и группы.")
            record = next((r for r in document.records
                           if r.evaluation_input.conversation.id == update.conversation_version_id), None)
            if record is None:
                raise HTTPException(404, "Диалог не входит в зафиксированную выборку.")
            now = datetime.now(UTC)
            review = ManualReview(
                annotated_by=update.annotated_by, language_understood=update.language_understood,
                completed_at=now if update.finish else None,
                reviewed_by=update.reviewed_by if update.finish and update.review_confirmed else None,
                reviewed_at=now if update.finish and update.review_confirmed and update.reviewed_by else None,
            )
            try:
                candidate = AnnotationDraft.model_validate(record.model_dump(mode="json") | {
                    "answers": [row.model_dump(mode="json") for row in update.answers],
                    "review": review.model_dump(mode="json"),
                })
                positions = {m.message_id: m.order for m in candidate.evaluation_input.conversation.messages}
                for criterion, answer in zip(candidate.evaluation_input.checklist.criteria,
                                              candidate.answers, strict=True):
                    if answer.answer is not None:
                        resolve_option(criterion, answer.answer)
                    for evidence in answer.evidence:
                        if (evidence.conversation_version_id != update.conversation_version_id
                                or evidence.start_message_id not in positions
                                or evidence.end_message_id not in positions
                                or positions[evidence.start_message_id] > positions[evidence.end_message_id]):
                            raise ValueError("Invalid evidence")
                if update.finish:
                    complete_reference(candidate)
            except ValueError:
                raise HTTPException(422, "Проверьте ответы, пояснения и диапазоны реплик. Для завершения нужны имя и понимание языка; для спорного ответа — решение и второй проверяющий.") from None
            records = [candidate if r is record else r for r in document.records]
            draft = DraftDocument.model_validate(document.model_dump(mode="json") | {
                "records": [r.model_dump(mode="json") for r in records],
            })
            self._persist(ReviewState.model_validate(self.state.model_dump(mode="json") | {
                "revision": self.state.revision + 1, "draft": draft.model_dump(mode="json"),
            }))

    def export(self, request: ExportRequest) -> dict[str, str]:
        with self.mutex:
            self._expect(request.revision)
            document = self.state.draft
            if document is None:
                raise HTTPException(409, "Сначала зафиксируйте выборку.")
            try:
                references = finalize_references(document, request.split)
            except ValueError:
                raise HTTPException(422, "В этой части остались незавершённые или спорные ответы.") from None
            if not references:
                raise HTTPException(422, "В выбранной части нет диалогов.")
            folder = self.directory / "exports" / f"r{self.state.revision}-{request.split}-{uuid4().hex[:8]}"
            # No holdout content in a tune export. Full canonical draft remains in its revision.
            write_private_json(folder / "references.json", [r.model_dump(mode="json") for r in references])
            write_private_json(folder / "annotations.json", [
                r.model_dump(mode="json") for r in document.records if r.split == request.split
            ])
            write_private_json(folder / "inputs.json", [
                r.evaluation_input.model_dump(mode="json") for r in document.records if r.split == request.split
            ])
            return {"folder": str(folder), "split": request.split, "count": str(len(references))}

    def summary(self) -> dict[str, object]:
        with self.mutex:
            annotations = {r.evaluation_input.conversation.id: r for r in self.state.draft.records} if self.state.draft else {}
            rows = []
            for selection in self.state.selections:
                case = self.index[selection.conversation_version_id]
                record = annotations.get(case.conversation.id)
                rows.append(selection.model_dump(mode="json") | {
                    "conversation_id": case.conversation.conversation_id,
                    "language": case.conversation.language, "channel": case.conversation.channel,
                    "message_count": len(case.conversation.messages),
                    "split": record.split if record else None,
                    "complete": not incomplete(record) if record else False,
                })
            return {"revision": self.state.revision, "frozen": self.state.draft is not None,
                    "seed": self.state.seed, "holdout_groups": self.state.holdout_groups,
                    "grouped_by": self.state.grouped_by, "rows": rows}

    def record(self, version_id: str) -> dict[str, object]:
        with self.mutex:
            case = self.index.get(version_id)
            if case is None:
                raise HTTPException(404, "Версия диалога не найдена.")
            record = next((r for r in self.state.draft.records
                           if r.evaluation_input.conversation.id == version_id), None) if self.state.draft else None
            return {"conversation": case.conversation.model_dump(mode="json", exclude={"audio_url"}),
                    "checklist": case.checklist.model_dump(mode="json"),
                    "annotation": ({"answers": [a.model_dump(mode="json") for a in record.answers],
                                    "review": record.review.model_dump(mode="json"), "split": record.split}
                                   if record else None)}


def create_app(workspace: ReviewWorkspace, port: int = 8787) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    token = secrets.token_urlsafe(32)
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{host}" for host in hosts}

    @app.middleware("http")
    async def boundary(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.headers.get("host") not in hosts:
            return JSONResponse({"detail": "Недопустимый адрес."}, status_code=403)
        if request.headers.get("origin") not in (None, *origins):
            return JSONResponse({"detail": "Недопустимый источник."}, status_code=403)
        if request.method == "POST":
            if not secrets.compare_digest(request.headers.get("x-review-token", ""), token):
                return JSONResponse({"detail": "Обновите страницу перед сохранением."}, status_code=403)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 1_048_576:
                    return JSONResponse({"detail": "Запрос слишком большой."}, status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'",
        })
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(_request: Request, _error: RequestValidationError) -> JSONResponse:
        return JSONResponse({"detail": "Некорректная форма запроса; данные не сохранены."}, status_code=422)

    @app.exception_handler(OSError)
    async def disk_error(_request: Request, _error: OSError) -> JSONResponse:
        return JSONResponse({"detail": "Не удалось сохранить файл. Изменения формы остаются в браузере."}, status_code=503)

    @app.get("/")
    def index() -> HTMLResponse:
        return HTMLResponse((ASSETS / "index.html").read_text(encoding="utf-8").replace("REVIEW_TOKEN", token))

    @app.get("/assets/{name}")
    def asset(name: str) -> FileResponse:
        if name not in ("review.js", "review.css"):
            raise HTTPException(404)
        return FileResponse(ASSETS / name)

    @app.get("/api/state")
    def state() -> dict[str, object]:
        return workspace.summary()

    @app.get("/api/record/{version_id}")
    def record(version_id: str) -> dict[str, object]:
        return workspace.record(version_id)

    @app.post("/api/selection")
    def selection(update: SelectionUpdate) -> dict[str, object]:
        workspace.selection(update)
        return workspace.summary()

    @app.post("/api/freeze")
    def freeze(update: SelectionUpdate) -> dict[str, object]:
        workspace.selection(update, freeze=True)
        return workspace.summary()

    @app.post("/api/preview")
    def preview(update: SelectionUpdate) -> dict[str, object]:
        return workspace.preview(update)

    @app.post("/api/annotation")
    def annotation(update: AnnotationUpdate) -> dict[str, object]:
        workspace.annotation(update)
        return workspace.summary()

    @app.post("/api/export")
    def export(request: ExportRequest) -> dict[str, str]:
        return workspace.export(request)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8787)
    arguments = parser.parse_args()
    if not 1024 <= arguments.port <= 65535:
        parser.error("Выберите порт от 1024 до 65535.")
    try:
        workspace = ReviewWorkspace(arguments.inputs, arguments.workspace)
    except (ValueError, OSError, ValidationError):
        parser.error("Не удалось открыть приватные входы или рабочую папку. Проверьте файлы и отсутствие второго процесса.")
    print(f"Разметка DT20: http://127.0.0.1:{arguments.port} — только этот компьютер, без AI-запросов.")
    try:
        uvicorn.run(create_app(workspace, arguments.port), host="127.0.0.1", port=arguments.port,
                    access_log=False, log_level="warning")
    finally:
        workspace.close()


if __name__ == "__main__":
    main()
