"""Build the public CQA 1.0 document exclusively from pinned jobs and snapshots."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, NoReturn

from pydantic import StrictStr

from app.contracts.models import (
    ChecklistVersion,
    ConversationVersion,
    Evaluation,
    EvaluationCreateRequest,
    EvaluationInput,
    EvaluationSettings,
    FrozenModel,
    NonEmpty,
    NotApplicableAnswer,
    ProviderCall,
)
from app.domain.scoring import score
from app.domain.snapshots import checklist_content_hash, conversation_content_hash
from app.evaluation.validation import validate_output

ROOT = Path(__file__).resolve().parents[3]
TEAM_NAME = "DT20"
OFFICIAL_ID = "checklist-default-v1"
OFFICIAL_HASH = "f172eb1ab664d3e4d11f1a55a6b34b51e6ca3fd7af0c402443f065b10b8c9437"
CRITERIA = dict(zip(
    ("C01", "C02", "C03", "C04", "C05", "C06", "C07", "C08"),
    ("contact", "discovery", "identification", "consultation", "empathy", "dialogue",
     "closure", "critical"), strict=True,
))
PRIVATE_TEXT = re.compile(
    r"https?://|www\.|\bBearer\s+|(?:api[_-]?key|token|signature|sig)\s*[:=]|"
    r"\b(?:sk-|AIza|gh[pousr]_)[A-Za-z0-9_-]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY|"
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\w)\+?\d[\s().-]*(?:\d[\s().-]*){8,}\d(?!\w)|"
    r"\b[A-Z]{2}\d{2}[A-Z0-9]{12,30}\b|\b[A-Za-z]:[\\/]|/(?:home|Users|tmp|var)/",
    re.IGNORECASE,
)
PRIVATE_ID = re.compile(r"https?://|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|"
                        r"\b(?:sk-|AIza|gh[pousr]_)[A-Za-z0-9_-]{12,}|"
                        r"(?:api[_-]?key|token|signature|sig)\s*[:=]", re.IGNORECASE)


class SubmissionError(ValueError):
    """A diagnostic safe to print without leaking source records."""


class ReleaseSelection(FrozenModel):
    dataset: Literal["dev", "prod"]
    group_id: NonEmpty
    team_name: StrictStr
    jobs: dict[NonEmpty, NonEmpty | None]


@dataclass(frozen=True)
class SelectedJob:
    evaluation: Evaluation
    request: EvaluationCreateRequest
    calls: tuple[ProviderCall, ...]
    plan_id: str | None = None


def _public_text(value: str) -> str:
    if not value.strip() or PRIVATE_TEXT.search(value) or any(ord(c) < 32 for c in value):
        raise SubmissionError("Potentially sensitive public text. Review private input.")
    return value


def _error_row(conversation_id: str, reason: str) -> dict[str, Any]:
    return {"conversation_id": conversation_id, "status": "error", "criteria": [],
            "score": None, "error": reason}


def _failure_text(job: Evaluation) -> str:
    assert job.failure is not None
    # Provider messages may include credentials, transcripts or response fragments.
    reasons = {
        "TIMEOUT": "Истекло время ожидания ответа.",
        "PROVIDER_TIMEOUT": "Истекло время ожидания ответа.",
        "MODEL_REFUSAL": "Модель отказалась оценить диалог.",
        "INVALID_RESPONSE": "Ответ модели не прошёл проверку.",
        "EMPTY_RESPONSE": "Модель вернула пустой ответ.",
        "INCOMPLETE_RESPONSE": "Модель вернула неполный ответ.",
        "TRUNCATED_RESPONSE": "Модель вернула неполный ответ.",
        "PROVIDER_HTTP_ERROR": "Сервис модели вернул ошибку HTTP.",
        "HTTP_ERROR": "Сервис модели вернул ошибку HTTP.",
        "PROVIDER_CONNECTION_ERROR": "Соединение с сервисом модели прервалось.",
        "NETWORK_ERROR": "Соединение с сервисом модели прервалось.",
        "CALL_LIMIT": "Исчерпан лимит вызовов модели.",
        "ATTEMPT_BLOCKED": "Попытка вызова не была разрешена.",
        "INPUT_TOO_LARGE": "Диалог превышает допустимый размер.",
        "RESPONSE_TOO_LARGE": "Ответ модели превышает допустимый размер.",
        "WORKER_INTERRUPTED": "Выполнение оценки прервано перезапуском.",
    }
    return reasons.get(job.failure.code, "Задание оценки завершилось ошибкой.")


def _result(
    conversation: ConversationVersion, checklist: ChecklistVersion, selected: SelectedJob,
    *, pending_at_cutoff: bool, rehearsal: bool,
) -> dict[str, Any]:
    job, req = selected.evaluation, selected.request
    if (job.conversation_version_id != conversation.id or req.conversation_version_id != conversation.id
            or job.checklist_version_id != checklist.id or req.checklist_version_id != checklist.id):
        raise SubmissionError("Job and snapshots do not match.")
    expected_mode: Literal["live", "mock"] = "mock" if rehearsal else "live"
    if req.mode != expected_mode:
        raise SubmissionError("Job mode is incompatible with the selected dataset.")
    if not rehearsal and not selected.plan_id:
        raise SubmissionError("No approved live plan was recorded for this job.")
    if pending_at_cutoff:
        return _error_row(conversation.conversation_id, "Оценка не завершилась к сроку.")
    if job.status == "failed":
        return _error_row(conversation.conversation_id, _failure_text(job))
    if job.status != "succeeded":
        raise SubmissionError("A selected job is still pending.")
    assert job.output is not None
    metadata = job.output.metadata
    if (not req.provider or not req.model or metadata.calls != selected.calls
            or (not rehearsal and not selected.calls)):
        raise SubmissionError("Missing or inconsistent job execution provenance.")
    settings = EvaluationSettings(mode=req.mode, provider=req.provider, model=req.model)
    validate_output(EvaluationInput(conversation=conversation, checklist=checklist, settings=settings),
                    job.output)
    original = score(checklist, job.output.answers)
    if original != job.original_score or original.total is None:
        raise SubmissionError("Persisted original score differs from the canonical scorer.")
    positions = {message.message_id: message.order + 1 for message in conversation.messages}
    transcripts = [" ".join(m.text.casefold().split()) for m in conversation.messages]
    substantial_text = [s for s in transcripts if len(s) >= 60]
    complete_transcript = " ".join(transcripts)
    answers = {answer.criterion_id: answer for answer in job.output.answers}
    criteria = []
    for internal, public in CRITERIA.items():
        answer = answers[internal]
        value = None if isinstance(answer.answer, NotApplicableAnswer) else answer.answer.value
        if type(value) is float:  # validated against the official integer-valued options
            value = int(value)
        evidence = {turn for ref in answer.evidence
                    for turn in range(positions[ref.start_message_id], positions[ref.end_message_id] + 1)}
        rationale = _public_text(answer.explanation)
        normalized = " ".join(rationale.casefold().split())
        if (any(part in normalized for part in substantial_text)
                or len(complete_transcript) >= 30 and complete_transcript in normalized):
            raise SubmissionError("Rationale reproduces private transcript text.")
        criteria.append({"criterion_id": public, "value": value,
                         "rationale": rationale, "evidence_turns": sorted(evidence)})
    return {"conversation_id": conversation.conversation_id, "status": "ok", "criteria": criteria,
            "score": {"final_score": float(original.total), "critical_flag": "C08" in original.flags}}


def build_submission(
    selection: ReleaseSelection, conversations: Sequence[ConversationVersion],
    checklist: ChecklistVersion, jobs: dict[str, SelectedJob], *,
    cutoff: dict[str, tuple[ProviderCall, ...]] | None = None, rehearsal: bool = False,
) -> dict[str, Any]:
    if not rehearsal and selection.team_name != TEAM_NAME:
        raise SubmissionError(f"The official team name must be exactly {TEAM_NAME}.")
    if checklist.id != OFFICIAL_ID or checklist_content_hash(checklist) != OFFICIAL_HASH:
        raise SubmissionError("Expected the pinned official checklist ID and content hash.")
    expected_channels = {"agent chat": 10} if selection.dataset == "prod" else {
        "agent chat": 60, "voice call": 5}
    ids = {c.conversation_id for c in conversations}
    versions = {c.id for c in conversations}
    if (len(ids) != len(conversations) or len(versions) != len(conversations)
            or Counter(c.channel for c in conversations) != expected_channels
            or set(selection.jobs) != versions):
        raise SubmissionError("The selected job map or channel counts do not match the organizer manifest.")
    requested_jobs = [i for i in selection.jobs.values() if i is not None]
    if len(set(requested_jobs)) != len(requested_jobs) or set(requested_jobs) != jobs.keys():
        raise SubmissionError("Duplicate, missing or unexpected job.")
    if cutoff is not None and cutoff.keys() - jobs.keys():
        raise SubmissionError("Cutoff belongs to different jobs.")
    models: set[tuple[str, str]] = set()
    rows = []
    for c in conversations:
        if PRIVATE_ID.search(c.conversation_id) or any(ord(char) < 32 for char in c.conversation_id):
            raise SubmissionError("Source ID may expose sensitive information. Review the organizer manifest.")
        if (c.dataset_group_id != selection.group_id or conversation_content_hash(c) != c.content_hash
                or c.source != ("synthetic" if rehearsal else "organizer") or c.synthetic != rehearsal):
            raise SubmissionError("Source, group or content hash is incompatible with this release.")
        job_id = selection.jobs[c.id]
        if job_id is None:
            if cutoff is None:
                raise SubmissionError("An organizer conversation has no selected job.")
            rows.append(_error_row(c.conversation_id, "Оценка не была запущена к сроку."))
            continue
        job = jobs[job_id]
        if job.evaluation.id != job_id:
            raise SubmissionError("Selected job ID does not match the stored job.")
        pending_at_cutoff = cutoff is not None and job_id in cutoff
        calls = cutoff[job_id] if cutoff is not None and pending_at_cutoff else job.calls
        for call in calls:
            if call.provider not in {"openai", "gemini", "local"}:
                raise SubmissionError("Unknown provider in execution history.")
            if call.purpose == "evaluate" and (call.provider, call.model) != (
                job.request.provider, job.request.model
            ):
                raise SubmissionError("The recorded evaluation call differs from the requested model.")
            models.add((call.provider, _public_text(call.model)))
        rows.append(_result(c, checklist, job, pending_at_cutoff=pending_at_cutoff, rehearsal=rehearsal))
    if rehearsal:
        mode, name = "local", "fixture/synthetic-v1 (offline rehearsal)"
    else:
        if not models:
            raise SubmissionError("No actual model calls were recorded.")
        localities = {"local" if p == "local" else "external_api" for p, _ in models}
        mode = next(iter(localities)) if len(localities) == 1 else "hybrid"
        name = ", ".join(f"{p}/{m}" for p, m in sorted(models))
    return {"schema_version": "1.0", "team_name": _public_text(selection.team_name),
            "model": {"mode": mode, "name": name}, "results": rows}


def _reject_constant(_: str) -> NoReturn:
    raise SubmissionError("Non-finite JSON number.")


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in pairs:
        if key in values:
            raise SubmissionError("Duplicate JSON object key.")
        values[key] = value
    return values


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant,
                      object_pairs_hook=_unique_keys, parse_float=Decimal)


def _same_value(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return (isinstance(actual, dict) and actual.keys() == expected.keys()
                and all(_same_value(actual[k], v) for k, v in expected.items()))
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        if expected and isinstance(expected[0], dict):
            key = "criterion_id" if "criterion_id" in expected[0] else "conversation_id"
            if any(not isinstance(i, dict) or not isinstance(i.get(key), str) for i in actual):
                return False
            actual = sorted(actual, key=lambda item: item[key])
            expected = sorted(expected, key=lambda item: item[key])
        return all(_same_value(a, e) for a, e in zip(actual, expected, strict=True))
    if type(expected) is float:
        return type(actual) in (int, Decimal) and Decimal(str(expected)) == actual
    return type(actual) is type(expected) and actual == expected


def validate_file(path: Path, expected: dict[str, Any]) -> None:
    if not _same_value(read_json(path), expected):
        raise SubmissionError("Public JSON differs from the exact types, IDs, original results or execution history.")


def write_verified(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2,
                                        allow_nan=False) + "\n", encoding="utf-8")
        validate_file(temporary, document)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
