import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import text

from app.api.errors import ApiError
from app.contracts.models import (
    AppealCreateRequest,
    ChecklistCreateRequest,
    ChecklistVersionCreateRequest,
    DecisionCreateRequest,
    EvaluationCreateRequest,
)
from app.evaluation.demo import load_synthetic_input
from app.services import jobs
from app.services.appeals import create_appeal, decide_appeal, list_appeals
from app.storage.database import get_engine
from app.storage.workflow import (
    get_checklist_version,
    get_evaluation,
    initialize_workflow,
    list_checklists,
    save_checklist,
)


@pytest.fixture(autouse=True)
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + (tmp_path / "workflow.sqlite3").as_posix())
    monkeypatch.delenv("LIVE_EVAL_ENABLED", raising=False)
    initialize_workflow()
    yield
    get_engine().dispose()


def sample_request(**fields) -> EvaluationCreateRequest:
    example = load_synthetic_input()
    return EvaluationCreateRequest.model_validate(
        {
            "mode": "mock",
            "conversation_version_id": example.conversation.id,
            "checklist_version_id": example.checklist.id,
            **fields,
        }
    )


def run_one():
    claimed = jobs.claim_job()
    assert claimed is not None
    asyncio.run(jobs.run_claimed(*claimed))
    return get_evaluation(claimed[0].id)


def test_versioning_and_concurrent_creates():
    default = load_synthetic_input().checklist
    definition = default.model_dump(exclude={"id", "checklist_id", "version", "created_at"})
    first = save_checklist(ChecklistCreateRequest(**definition))
    definition["name"] = "Изменённый"
    update = ChecklistVersionCreateRequest(**definition)
    with ThreadPoolExecutor(max_workers=3) as pool:
        versions = list(pool.map(lambda _: save_checklist(update, first.checklist_id), range(3)))
    assert {v.version for v in versions} == {2}
    assert get_checklist_version(first.id) == first
    assert [
        (c.version, c.name) for c in list_checklists().items if c.checklist_id == first.checklist_id
    ] == [(2, update.name)]
    definition["criteria"] = (*default.criteria, default.criteria[0])
    with pytest.raises(ValueError):
        ChecklistVersionCreateRequest(**definition)


def test_recover_interrupted_resume_queued_and_idempotency():
    first = jobs.create_evaluation(sample_request(), "client-1")
    other = jobs.create_evaluation(sample_request(), "client-2")
    assert jobs.claim_job()[0].id == first.id
    jobs.recover_interrupted_jobs()
    assert jobs.create_evaluation(sample_request(), "client-1").failure.code == "WORKER_INTERRUPTED"
    with pytest.raises(ApiError) as conflict:
        jobs.create_evaluation(sample_request(mode="live"), "client-1")
    assert conflict.value.status_code == 409
    completed = run_one()
    assert completed.id == other.id
    assert completed.status == "succeeded" and completed.output.metadata.mode == "mock"
    assert completed.original_score == completed.effective_score
    assert (
        completed.output.answers[0].evidence[0].conversation_version_id
        == completed.conversation_version_id
    )


def test_live_fails_closed_and_unknown_versions():
    with pytest.raises(ApiError) as disabled:
        jobs.create_evaluation(sample_request(mode="live"))
    assert disabled.value.error.code == "LIVE_NOT_AUTHORIZED"
    with pytest.raises(ApiError) as unknown:
        jobs.create_evaluation(sample_request(checklist_version_id="missing"))
    assert unknown.value.status_code == 404


def test_timeout_and_invalid_provider(monkeypatch):
    class Broken:
        async def evaluate(self, case):
            raise TimeoutError("sensitive upstream details")

    monkeypatch.setattr(jobs, "create_provider", lambda: Broken())
    jobs.create_evaluation(sample_request())
    failure = run_one()
    assert failure.failure.code == "PROVIDER_TIMEOUT" and failure.original_score is None
    assert "sensitive" not in failure.model_dump_json()

    class Invalid:
        async def evaluate(self, case):
            raise ValueError("sensitive transcript")

    monkeypatch.setattr(jobs, "create_provider", lambda: Invalid())
    jobs.create_evaluation(sample_request())
    failure = run_one()
    assert failure.failure.code == "EVALUATION_FAILED" and failure.output is None
    assert "sensitive" not in failure.model_dump_json()


def test_appeal_atomic_replay_and_immutable_original():
    jobs.create_evaluation(sample_request())
    original = run_one()
    item = AppealCreateRequest(criterion_id="C01", comment="Повторная проверка приветствия")
    appeal = create_appeal(original.id, item)
    assert create_appeal(original.id, item).id == appeal.id
    with pytest.raises(ApiError) as conflict:
        create_appeal(original.id, item.model_copy(update={"comment": "другой текст"}))
    assert conflict.value.status_code == 409
    invalid = DecisionCreateRequest.model_validate(
        {
            "action": "override",
            "reason": "Пересмотрен ответ",
            "override_answer": {"type": "na", "reason": "здесь недопустимо"},
            "idempotency_key": "review-1",
        }
    )
    with pytest.raises(ApiError) as error:
        decide_appeal(appeal.id, invalid)
    assert error.value.status_code == 422
    assert list_appeals(original.id).items[0].status == "open"
    assert get_evaluation(original.id) == original
    original_bool = next(a.answer.value for a in original.output.answers if a.criterion_id == "C01")
    valid = DecisionCreateRequest.model_validate(
        {
            **invalid.model_dump(),
            "override_answer": {"type": "boolean", "value": not original_bool},
        }
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        decisions = list(pool.map(lambda _: decide_appeal(appeal.id, valid), range(2)))
    assert decisions[0] == decisions[1]
    result = decisions[0].evaluation
    assert result.output == original.output and result.original_score == original.original_score
    assert result.effective_score != original.effective_score
    with pytest.raises(ApiError) as conflict:
        decide_appeal(appeal.id, valid.model_copy(update={"reason": "Изменено решение"}))
    assert conflict.value.status_code == 409
    second = create_appeal(original.id, AppealCreateRequest(criterion_id="C08", comment="Флаг"))
    confirmed = decide_appeal(
        second.id,
        DecisionCreateRequest(
            action="confirm",
            reason="Исходный ответ верен",
            idempotency_key="review-2",
        ),
    )
    assert confirmed.evaluation.effective_score == result.effective_score
    assert decide_appeal(appeal.id, valid) == decisions[0]
    with get_engine().connect() as db:
        assert db.execute(text("SELECT COUNT(*) FROM appeal_decisions")).scalar_one() == 2
