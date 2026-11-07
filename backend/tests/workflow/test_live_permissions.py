"""Offline tests of the API job gate using no live endpoint and no credentials."""

import asyncio
import json
from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.errors import ApiError
from app.contracts.models import ConversationImportRequest, EvaluationCreateRequest
from app.evaluation.demo import load_synthetic_input
from app.services import jobs, live_workflow
from app.storage.conversations import import_conversations
from app.storage.database import get_engine
from app.storage.workflow import get_calls, get_evaluation, get_plan_id, initialize_workflow


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + (tmp_path / "jobs.sqlite3").as_posix())
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("LIVE_EVAL_ENABLED", raising=False)
    initialize_workflow()
    yield
    get_engine().dispose()


def request(mode):
    case = load_synthetic_input()
    return EvaluationCreateRequest(
        mode=mode,
        conversation_version_id=case.conversation.id,
        checklist_version_id=case.checklist.id,
    )


def install_plan(tmp_path, monkeypatch):
    today = date.today().isoformat()
    plan = {
        "model": "synthetic-transport",
        "provider": "local",
        "local_base_url": "http://127.0.0.1:1/v1",
        "max_cases": 2,
        "max_requests": 1,
        "max_output_tokens": 30,
        "budget_usd": "0",
        "dataset_group_ids": ["SYNTHETIC"],
        "data_transfer_approved": True,
        "pricing": {
            "input_token_limit": 100,
            "output_token_limit": 30,
            "input_usd_per_million": "0",
            "output_usd_per_million": "0",
            "source_url": "https://example.org/test",
            "verified_on": today,
            "valid_until": today,
        },
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setenv("CQA_LIVE_PLAN", str(path))
    monkeypatch.setenv("LIVE_EVAL_ENABLED", "true")
    return path


def test_reserve_before_call_and_do_not_refund_after_failures(database, tmp_path, monkeypatch):
    install_plan(tmp_path, monkeypatch)
    monkeypatch.setenv("MODEL_API_KEY", "fixture-cloud-credential")
    transported = []

    def factory(config, *, on_attempt):
        assert config.api_key is None

        class FailedTransport:
            async def evaluate(self, case):
                on_attempt()
                transported.append(case)
                raise ValueError("private transport message")

        return FailedTransport()

    monkeypatch.setattr(live_workflow, "create_provider", factory)
    evaluations = []
    for _ in range(3):
        queued = jobs.create_evaluation(request("live"))
        asyncio.run(jobs.run_claimed(*jobs.claim_job()))
        evaluations.append(get_evaluation(queued.id))
    assert all(item.status == "failed" and item.output is None for item in evaluations)
    assert len(transported) == 1 and transported[0].settings.provider == "local"
    assert all("private transport message" not in item.model_dump_json() for item in evaluations)
    assert all(get_calls(item.id) == () for item in evaluations)
    with get_engine().connect() as db:
        assert tuple(db.execute(text("SELECT cases,attempts FROM live_limits")).one()) == (2, 1)


def test_changed_plan_cannot_run_queued_job(database, tmp_path, monkeypatch):
    path = install_plan(tmp_path, monkeypatch)
    queued = jobs.create_evaluation(request("live"), "case")
    assert get_plan_id(queued.id) == live_workflow.configured_plan()[0]
    previous = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**previous, "model": "replacement"}), encoding="utf-8")
    asyncio.run(jobs.run_claimed(*jobs.claim_job()))
    assert get_evaluation(queued.id).status == "failed"
    assert jobs.create_evaluation(request("live"), "case").id == queued.id
    with get_engine().connect() as db:
        assert not db.execute(text("SELECT * FROM live_limits")).all()


def import_origin(source, message):
    payload = ConversationImportRequest.model_validate(
        {
            "dataset_group_id": "SYNTHETIC",
            "records": [
                {
                    "conversation_id": "organizer-fixture",
                    "channel": "agent chat",
                    "language": "RU",
                    "transcript": [{"timestamp": "00:01", "speaker": "operator", "text": message}],
                }
            ],
        }
    )
    return import_conversations(payload, source=source).conversation_version_ids[0]


def test_manual_spoof_and_superseded_manifest_cannot_reach_model(database, tmp_path, monkeypatch):
    install_plan(tmp_path, monkeypatch)
    manual = import_origin("manual", "Текст вне разрешённой передачи")
    with pytest.raises(ApiError) as denied:
        jobs.create_evaluation(
            request("live").model_copy(update={"conversation_version_id": manual})
        )
    assert denied.value.error.code == "LIVE_NOT_AUTHORIZED"
    original = import_origin("organizer", "Начальная версия")
    req = request("live").model_copy(update={"conversation_version_id": original})
    queued = jobs.create_evaluation(req)
    current = import_origin("organizer", "Актуальная версия")
    assert current != original
    with pytest.raises(ApiError) as denied:
        jobs.create_evaluation(req)
    assert denied.value.error.code == "LIVE_NOT_AUTHORIZED"
    attempts = []
    monkeypatch.setattr(live_workflow, "create_provider", lambda *a, **kw: attempts.append(1))
    asyncio.run(jobs.run_claimed(*jobs.claim_job()))
    assert get_evaluation(queued.id).status == "failed" and not attempts
    with get_engine().connect() as db:
        assert not db.execute(text("SELECT * FROM live_limits")).all()


def test_replacement_between_reservation_and_transport_blocks_attempt(
    database, tmp_path, monkeypatch
):
    install_plan(tmp_path, monkeypatch)
    current = import_origin("organizer", "Исходный текст")
    req = request("live").model_copy(update={"conversation_version_id": current})
    queued = jobs.create_evaluation(req)
    transported = []

    def factory(config, *, on_attempt):
        class Transport:
            async def evaluate(self, case):
                import_origin("organizer", "Версия заменилась перед отправкой")
                on_attempt()
                transported.append(case)

        return Transport()

    monkeypatch.setattr(live_workflow, "create_provider", factory)
    asyncio.run(jobs.run_claimed(*jobs.claim_job()))
    assert get_evaluation(queued.id).status == "failed" and not transported
    with get_engine().connect() as db:
        assert tuple(db.execute(text("SELECT cases,attempts FROM live_limits")).one()) == (1, 0)


def test_lifespan_recovers_then_executes_queue(database):
    import time

    app = FastAPI(lifespan=jobs.workflow_lifespan)
    first = jobs.create_evaluation(request("mock"))
    second = jobs.create_evaluation(request("mock"))
    jobs.claim_job()
    with TestClient(app):
        assert get_evaluation(first.id).failure.code == "WORKER_INTERRUPTED"
        end = time.monotonic() + 3
        while get_evaluation(second.id).status != "succeeded" and time.monotonic() < end:
            time.sleep(0.02)
        assert get_evaluation(second.id).status == "succeeded"
        with pytest.raises(OSError):
            with jobs.worker_lock():
                pytest.fail("Second process owner acquired a live DB lock")
