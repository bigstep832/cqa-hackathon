"""Offline tests of the API job gate using no live endpoint and no credentials."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from threading import Event
from time import monotonic, sleep

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.errors import ApiError
from app.contracts.models import (
    ConversationImportRequest,
    EvaluationCreateRequest,
    EvaluationMetadata,
    EvaluationOutput,
    ProviderCall,
)
from app.evaluation.demo import load_synthetic_answers, load_synthetic_input
from app.evaluation.factory import create_provider
from app.main import app
from app.services import jobs, live_workflow
from app.storage.conversations import import_conversations
from app.storage.database import get_engine, write_transaction
from app.storage.workflow import (
    get_calls,
    get_dispatches,
    get_evaluation,
    get_plan_id,
    initialize_workflow,
)


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

    def factory(config, *, client, on_attempt, on_call):
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
    assert all(get_calls(item.id) == () and get_dispatches(item.id) == () for item in evaluations)
    with get_engine().connect() as db:
        assert tuple(db.execute(text("SELECT cases,attempts FROM live_limits")).one()) == (2, 1)


def test_transport_timeouts_keep_real_attempt_history(database, tmp_path, monkeypatch):
    path = install_plan(tmp_path, monkeypatch)
    plan = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**plan, "max_attempts": 2, "max_requests": 2}), encoding="utf-8")
    resolve = jobs.resolve_settings
    monkeypatch.setattr(
        jobs,
        "resolve_settings",
        lambda *args: resolve(*args).model_copy(update={"timeout_seconds": 1}),
    )
    requests = []

    async def expire(request):
        requests.append(request.url.path)
        await asyncio.sleep(10)
        pytest.fail("The adapter should have timed out")

    async def run():
        monkeypatch.setattr(
            live_workflow, "outbound_transport", lambda: httpx.MockTransport(expire)
        )
        item = jobs.create_evaluation(request("live"))
        await jobs.run_claimed(*jobs.claim_job())
        assert [v.attempt for v in get_dispatches(item.id)] == [1, 2]
        return get_evaluation(item.id), get_calls(item.id)

    result, calls = asyncio.run(run())
    assert result.status == "failed" and result.failure.code == "PROVIDER_TIMEOUT"
    assert len(requests) == 2
    assert [call.attempt for call in calls] == [1, 2]
    assert all(call.model == plan["model"] and call.latency_ms > 0 for call in calls)


def test_postprocessing_failure_preserves_reported_usage(database, tmp_path, monkeypatch):
    install_plan(tmp_path, monkeypatch)
    reported = ProviderCall(
        provider="local",
        model="synthetic-transport",
        purpose="evaluate",
        attempt=1,
        latency_ms=12,
        input_tokens=8,
        output_tokens=4,
    )

    monkeypatch.setattr(
        live_workflow,
        "outbound_transport",
        lambda: httpx.MockTransport(lambda _: httpx.Response(200)),
    )

    def factory(config, *, client, on_attempt, on_call):
        class IncompleteOutput:
            async def evaluate(self, case):
                on_attempt()
                async with client.stream("POST", "http://127.0.0.1:1/v1/chat/completions"):
                    pass
                on_call(reported)
                return EvaluationOutput(
                    answers=load_synthetic_answers()[:1],
                    metadata=EvaluationMetadata(
                        mode="live",
                        provider=reported.provider,
                        model=reported.model,
                        instruction_version="test",
                        pipeline_version="test",
                        calls=(reported,),
                    ),
                )

        return IncompleteOutput()

    monkeypatch.setattr(live_workflow, "create_provider", factory)
    item = jobs.create_evaluation(request("live"))
    asyncio.run(jobs.run_claimed(*jobs.claim_job()))
    assert get_evaluation(item.id).status == "failed"
    assert get_evaluation(item.id).output is None
    assert get_calls(item.id) == (reported,)


def test_sqlite_reservation_wait_does_not_block_http_loop(database, tmp_path, monkeypatch):
    install_plan(tmp_path, monkeypatch)
    # The import writer acquires a real SQLite write lock between the worker's claim
    # and its case reservation, then another HTTP request must succeed during that wait.
    at_reservation, writer_active, release, writer_finished = (Event() for _ in range(4))
    reserve = jobs.live_provider

    def reserve_during_import(*args):
        at_reservation.set()
        assert writer_active.wait(3)
        return reserve(*args)

    def concurrent_import():
        assert at_reservation.wait(3)
        with write_transaction():
            writer_active.set()
            release.wait(3)
        writer_finished.set()

    class Offline:
        async def evaluate(self, case):
            raise ValueError("Synthetic unavailable transport")

    monkeypatch.setattr(live_workflow, "create_provider", lambda *a, **kw: Offline())
    monkeypatch.setattr(jobs, "live_provider", reserve_during_import)
    jobs.create_evaluation(request("live"))
    app = FastAPI(lifespan=jobs.workflow_lifespan)

    @app.get("/status")
    async def status():
        return {"writer_active": writer_active.is_set() and not writer_finished.is_set()}

    with ThreadPoolExecutor(max_workers=1) as pool:
        writer = pool.submit(concurrent_import)
        with TestClient(app) as client:
            try:
                assert writer_active.wait(3)
                assert client.get("/status").json() == {"writer_active": True}
            finally:
                release.set()
        writer.result()


def test_shutdown_joins_active_worker_before_recovery(database, monkeypatch):
    started, cancelled = Event(), Event()

    class Pending:
        async def evaluate(self, case):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    monkeypatch.setattr(jobs, "create_provider", Pending)
    queued = jobs.create_evaluation(request("mock"))
    with TestClient(FastAPI(lifespan=jobs.workflow_lifespan)):
        assert started.wait(3)
        assert get_evaluation(queued.id).status == "running"
    assert cancelled.is_set()
    assert get_evaluation(queued.id).failure.code == "WORKER_INTERRUPTED"


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


def test_history_from_reimported_card_is_scoped_and_paginated(database, tmp_path, monkeypatch):
    install_plan(tmp_path, monkeypatch)
    client = TestClient(app, base_url="http://127.0.0.1")

    def save(group, source, message):
        packet = ConversationImportRequest.model_validate({
            "dataset_group_id": group,
            "records": [{"conversation_id": "case/RU-17", "channel": "agent chat", "language": "RU",
                         "transcript": [{"timestamp": "00:01", "speaker": "operator", "text": message}]}],
        })
        return import_conversations(packet, source=source).conversation_version_ids[0]

    v1 = save("SYNTHETIC", "organizer", "Первая реплика")
    body = request("live").model_copy(update={"conversation_version_id": v1})
    first, second = (jobs.create_evaluation(body) for _ in range(2))
    v2 = save("SYNTHETIC", "organizer", "Новая реплика")
    other_group = save("OTHER", "organizer", "Другой набор")
    other_source = save("SYNTHETIC", "manual", "Другой источник")
    cards = client.get("/api/v1/conversations").json()["items"]
    assert {card["latest_version_id"] for card in cards} == {v2, other_group, other_source}
    path = f"/api/v1/conversation-versions/{v2}/history"
    assert [v["id"] for v in client.get(f"{path}/versions").json()["items"]] == [v2, v1]
    page = client.get(f"{path}/evaluations", params={"offset": 1, "limit": 1}).json()
    assert (page["offset"], page["limit"], page["total"]) == (1, 1, 2)
    item, = page["items"]
    assert item == {"id": first.id, "conversation_version_id": v1,
                    "checklist_version_id": body.checklist_version_id, "mode": "live",
                    "status": "queued", "created_at": first.model_dump(mode="json")["created_at"]}
    newest = client.get(f"{path}/evaluations").json()["items"]
    assert [job["id"] for job in newest] == [second.id, first.id]
    original = client.get(f"/api/v1/evaluations/{item['id']}").json()
    prior = client.get(f"/api/v1/conversation-versions/{original['conversation_version_id']}").json()
    assert prior["messages"][0]["text"] == "Первая реплика"
    for unrelated in (other_source, other_group):
        sibling = f"/api/v1/conversation-versions/{unrelated}/history"
        assert client.get(f"{sibling}/evaluations").json()["total"] == 0
        assert [v["id"] for v in client.get(f"{sibling}/versions").json()["items"]] == [unrelated]
    for collection in ("versions", "evaluations"):
        assert client.get(f"/api/v1/conversation-versions/missing/history/{collection}").status_code == 404
        assert client.get(f"{path}/{collection}", params={"limit": 101}).status_code == 422


def test_history_includes_evaluations_of_builtin_demo(database):
    client = TestClient(app, base_url="http://127.0.0.1")
    queued = jobs.create_evaluation(request("mock"))
    history = f"/api/v1/conversation-versions/{queued.conversation_version_id}/history"
    assert client.get(f"{history}/versions").json()["items"][0]["id"] == queued.conversation_version_id
    job, = client.get(f"{history}/evaluations").json()["items"]
    assert (job["id"], job["mode"]) == (queued.id, "mock")


def test_replacement_between_reservation_and_transport_blocks_attempt(
    database, tmp_path, monkeypatch
):
    install_plan(tmp_path, monkeypatch)
    current = import_origin("organizer", "Исходный текст")
    req = request("live").model_copy(update={"conversation_version_id": current})
    queued = jobs.create_evaluation(req)
    transported = []

    def factory(config, *, client, on_attempt, on_call):
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


def test_checklist_edits_reach_worker_and_preserve_prior_evaluation(
    database, tmp_path, monkeypatch
):
    from app.main import app

    plan_path = install_plan(tmp_path, monkeypatch)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan_path.write_text(json.dumps({**plan, "max_requests": 2}), encoding="utf-8")
    captured = []
    schemas = []
    fixture = load_synthetic_input()
    evidence = load_synthetic_answers()[0].evidence[0]

    def synthetic_completion(request):
        schema = json.loads(request.content)["response_format"]["json_schema"]["schema"]
        expected_ids = schema["properties"]["answers"]["required"]
        schemas.append(expected_ids)
        answers = {
            item.criterion_id: {
                "answer": item.answer.model_dump(mode="json"),
                "explanation": item.explanation,
                "evidence": [
                    e.model_dump(mode="json", exclude={"conversation_version_id"})
                    for e in item.evidence
                ],
            }
            for item in load_synthetic_answers()
        }
        if "C09" in expected_ids:
            answers["C01"]["answer"] = {"type": "scale", "value": 2}
            answers["C09"] = {
                "answer": {"type": "boolean", "value": True},
                "explanation": "Синтетический девятый критерий.",
                "evidence": [evidence.model_dump(mode="json", exclude={"conversation_version_id"})],
            }
        message = {"role": "assistant", "content": json.dumps({"answers": answers})}
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": message}]}
        )

    monkeypatch.setattr(
        live_workflow, "outbound_transport", lambda: httpx.MockTransport(synthetic_completion)
    )

    def capture_provider(config, *, client, on_attempt, on_call):
        class ObservedAdapter:
            async def evaluate(self, case):
                captured.append(case)
                return await create_provider(
                    config, client=client, on_attempt=on_attempt, on_call=on_call
                ).evaluate(case)

        return ObservedAdapter()

    monkeypatch.setattr(live_workflow, "create_provider", capture_provider)

    def evaluate(client, version_id):
        started = client.post(
            "/api/v1/evaluations",
            json={
                "mode": "live",
                "conversation_version_id": fixture.conversation.id,
                "checklist_version_id": version_id,
            },
        )
        assert started.status_code == 202
        url = "/api/v1/evaluations/" + started.json()["id"]
        deadline = monotonic() + 5
        while True:
            response = client.get(url)
            if response.json()["status"] not in {"queued", "running"} or monotonic() >= deadline:
                break
            sleep(0.02)
        assert response.json()["status"] == "succeeded", response.text
        return url, response

    original_definition = fixture.checklist.model_dump(
        mode="json", exclude={"id", "checklist_id", "version", "created_at"}
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        first = client.post("/api/v1/checklists", json=original_definition)
        assert first.status_code == 201
        original = first.json()
        old_url, old_response = evaluate(client, original["id"])
        old_snapshot = client.get("/api/v1/checklist-versions/" + original["id"]).content
        first_criterion = original_definition["criteria"][0]
        revised_criterion = {
            **first_criterion,
            "answer_type": "scale",
            "weight": "36",
            "allow_na": True,
            "na_condition": "Сценарий не требует приветствия.",
            "options": [
                {
                    "value": value,
                    "label": str(value),
                    "description": "Синтетическая ступень.",
                    "quality": quality,
                }
                for value, quality in ((0, "0"), (2, "0.5"), (4, "1"))
            ],
        }
        ninth = {**first_criterion, "id": "C09", "title": "Новый критерий", "weight": "3"}
        updated = {
            **original_definition,
            "criteria": [revised_criterion, *original_definition["criteria"][1:], ninth],
        }
        revisions_url = "/api/v1/checklists/" + original["checklist_id"] + "/versions"
        second = client.post(revisions_url, json=updated)
        assert second.status_code == 201 and second.json()["version"] == 2
        # The submitted version is already historical by the time its job starts.
        latest = client.post(revisions_url, json={**updated, "name": "Следующая редакция"})
        assert latest.status_code == 201 and latest.json()["version"] == 3
        _, current = evaluate(client, second.json()["id"])
        assert [case.checklist.id for case in captured] == [original["id"], second.json()["id"]]
        assert captured[1].checklist.model_dump(mode="json") == second.json()
        assert schemas == [
            [item["id"] for item in definition["criteria"]]
            for definition in (original_definition, updated)
        ]
        output = current.json()["output"]["answers"]
        assert {item["criterion_id"] for item in output} == set(schemas[1])
        assert next(item["answer"] for item in output if item["criterion_id"] == "C01") == {
            "type": "scale",
            "value": 2,
        }
        c01 = next(
            c for c in current.json()["original_score"]["criteria"] if c["criterion_id"] == "C01"
        )
        assert c01["earned_points"] == "18" and c01["weight"] == "36"
        assert client.get(old_url).content == old_response.content
        assert client.get("/api/v1/checklist-versions/" + original["id"]).content == old_snapshot


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
