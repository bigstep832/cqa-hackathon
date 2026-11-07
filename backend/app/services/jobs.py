"""Bounded local workers. Queue/claims/results in SQLite are durable across app restarts."""

import asyncio
import contextlib
import sqlite3
import sys
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from pydantic import TypeAdapter
from sqlalchemy import Connection, text
from sqlalchemy.exc import OperationalError

from app.contracts.models import (
    ErrorDetail,
    Evaluation,
    EvaluationCreateRequest,
    EvaluationInput,
    EvaluationSettings,
    ProviderCall,
)
from app.domain.scoring import score
from app.evaluation.factory import create_provider
from app.evaluation.provider import ProviderExecutionError
from app.evaluation.validation import validate_output
from app.services.live_workflow import (
    approved_snapshot,
    configured_plan,
    live_provider,
    resolve_settings,
)
from app.storage.conversations import get_version
from app.storage.database import get_engine, write_transaction
from app.storage.workflow import (
    error,
    evaluation_snapshot,
    get_checklist_version,
    initialize_workflow,
    update_evaluation,
)

CALLS = TypeAdapter(tuple[ProviderCall, ...])


def existing_request(
    db: Connection,
    idempotency_key: str | None,
    original: EvaluationCreateRequest,
) -> Evaluation | None:
    if idempotency_key is None:
        return None
    row = db.execute(
        text("""SELECT original_request,snapshot FROM evaluations
        WHERE idempotency_key=:key"""),
        {"key": idempotency_key},
    ).one_or_none()
    if row is None:
        return None
    if EvaluationCreateRequest.model_validate_json(row.original_request) != original:
        raise error(409, "IDEMPOTENCY_CONFLICT", "Ключ уже использован с другим запросом.")
    return Evaluation.model_validate_json(row.snapshot)


def create_evaluation(
    request: EvaluationCreateRequest, idempotency_key: str | None = None
) -> Evaluation:
    original = request
    if idempotency_key is not None:
        if not idempotency_key.strip() or len(idempotency_key) > 200:
            raise error(422, "VALIDATION_ERROR", "Некорректный ключ повтора.")
        with get_engine().connect() as db:
            existing = existing_request(db, idempotency_key, original)
            if existing is not None:
                return existing
    conversation = get_version(request.conversation_version_id)
    get_checklist_version(request.checklist_version_id)
    if request.mode == "mock" and (
        not conversation.synthetic
        or request.provider not in (None, "fixture")
        or request.model not in (None, "synthetic-v1")
    ):
        raise error(422, "MOCK_NOT_SUPPORTED", "Mock доступен только для встроенного примера.")
    plan_id = None
    plan = None
    if request.mode == "live":
        try:
            plan_id, plan = configured_plan()
            settings = resolve_settings(request, plan)
        except (ValueError, OSError):
            raise error(
                409, "LIVE_NOT_AUTHORIZED", "Живой запуск не согласован для этой конфигурации."
            ) from None
        request = request.model_copy(
            update={"provider": settings.provider, "model": settings.model}
        )
    evaluation = Evaluation(
        id=str(uuid4()),
        conversation_version_id=conversation.id,
        checklist_version_id=request.checklist_version_id,
        created_at=datetime.now(UTC),
        status="queued",
    )
    with write_transaction() as db:
        existing = existing_request(db, idempotency_key, original)
        if existing is not None:
            return existing
        if plan is not None and not approved_snapshot(db, conversation, plan):
            raise error(
                409, "LIVE_NOT_AUTHORIZED", "Эта версия диалога не входит в согласованный набор."
            )
        db.execute(
            text("""INSERT INTO evaluations(id,status,request,snapshot,plan_id,idempotency_key,original_request)
            VALUES(:id,'queued',:request,:snapshot,:plan_id,:key,:original)"""),
            {
                "id": evaluation.id,
                "request": request.model_dump_json(),
                "plan_id": plan_id,
                "key": idempotency_key,
                "original": original.model_dump_json(),
                "snapshot": evaluation.model_dump_json(),
            },
        )
    return evaluation


def claim_job() -> tuple[Evaluation, EvaluationCreateRequest, str | None] | None:
    with write_transaction() as db:
        row = db.execute(
            text("""SELECT snapshot, request, plan_id FROM evaluations
            WHERE status='queued' ORDER BY rowid LIMIT 1""")
        ).one_or_none()
        if row is None:
            return None
        evaluation = Evaluation.model_validate_json(row.snapshot).model_copy(
            update={"status": "running"}
        )
        update_evaluation(db, evaluation)
        return evaluation, EvaluationCreateRequest.model_validate_json(row.request), row.plan_id


def fail(evaluation: Evaluation, code: str, message: str) -> Evaluation:
    return evaluation.model_copy(
        update={"status": "failed", "failure": ErrorDetail(code=code, message=message)}
    )


def recover_interrupted_jobs() -> None:
    with write_transaction() as db:
        rows: Sequence[str] = (
            db.execute(text("SELECT snapshot FROM evaluations WHERE status='running'"))
            .scalars()
            .all()
        )
        for row in rows:
            interrupted = Evaluation.model_validate_json(row)
            update_evaluation(
                db,
                fail(
                    interrupted,
                    "WORKER_INTERRUPTED",
                    "Оценка прервана остановкой процесса. Можно создать новую оценку.",
                ),
            )


async def run_claimed(
    evaluation: Evaluation, request: EvaluationCreateRequest, plan_id: str | None
) -> None:
    calls: tuple[ProviderCall, ...] = ()
    try:
        conversation = get_version(evaluation.conversation_version_id)
        checklist = get_checklist_version(evaluation.checklist_version_id)
        if request.mode == "mock":
            settings = EvaluationSettings(mode="mock", provider="fixture", model="synthetic-v1")
            case = EvaluationInput(
                conversation=conversation, checklist=checklist, settings=settings
            )
            provider = create_provider()
        else:
            current_id, plan = configured_plan()
            if plan_id != current_id:
                raise ValueError("Authorized plan changed")
            settings = resolve_settings(request, plan)
            case = EvaluationInput(
                conversation=conversation, checklist=checklist, settings=settings
            )
            provider = live_provider(case, current_id, plan)
        async with asyncio.timeout(settings.timeout_seconds):
            output = await provider.evaluate(case)
        validate_output(case, output)
        result = score(checklist, output.answers)
        calls = output.metadata.calls
        completed = evaluation.model_copy(
            update={
                "status": "succeeded",
                "output": output,
                "original_score": result,
                "effective_score": result,
            }
        )
    except TimeoutError:
        completed = fail(evaluation, "PROVIDER_TIMEOUT", "Истекло время ожидания оценщика.")
    except ProviderExecutionError as exc:
        calls = exc.calls
        completed = fail(evaluation, exc.code, "Оценщик завершился с ошибкой.")
    except Exception:
        # Provider/validation errors can embed private prompt text, API responses or credentials.
        completed = fail(evaluation, "EVALUATION_FAILED", "Не удалось получить и проверить оценку.")
    await retry_busy(save_completed, completed, calls)


def save_completed(completed: Evaluation, calls: tuple[ProviderCall, ...]) -> None:
    with write_transaction() as db:
        if evaluation_snapshot(db, completed.id).status == "running":
            update_evaluation(db, completed)
            db.execute(
                text("UPDATE evaluations SET calls=:calls WHERE id=:id"),
                {"calls": CALLS.dump_json(calls).decode(), "id": completed.id},
            )


async def retry_busy[**P, T](operation: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    # Wait outside the event loop and retry only lock contention, never a model call.
    while True:
        task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Finish a started claim/commit before shutdown recovery releases the DB lock.
            with contextlib.suppress(OperationalError):
                await task
            raise
        except OperationalError as exc:
            code = getattr(exc.orig, "sqlite_errorcode", 0)
            if code & 0xFF not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise
            await asyncio.sleep(0.1)


async def work() -> None:
    while True:
        claimed = await retry_busy(claim_job)
        if claimed:
            await run_claimed(*claimed)
        else:
            await asyncio.sleep(0.15)


@contextmanager
def worker_lock() -> Iterator[None]:
    """One owner of recovery/workers per on-disk DB, including across uvicorn processes."""
    database = get_engine().url.database
    if not database or database == ":memory:":
        yield
        return
    lock = Path(database).resolve()
    with exclusive_file_lock(lock.with_name(lock.name + ".worker.lock")):
        yield


@contextmanager
def exclusive_file_lock(lock: Path) -> Iterator[None]:
    """Use a dedicated persistent lock file; its OS lock releases on process death."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+b") as handle:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


@asynccontextmanager
async def workflow_lifespan(app: FastAPI) -> AsyncIterator[None]:
    with worker_lock():
        initialize_workflow()
        recover_interrupted_jobs()
        async with asyncio.TaskGroup() as group:
            workers = [group.create_task(work()) for _ in range(2)]
            try:
                yield
            finally:
                for worker in workers:
                    worker.cancel()
                for worker in workers:
                    with contextlib.suppress(asyncio.CancelledError):
                        await worker
                recover_interrupted_jobs()
