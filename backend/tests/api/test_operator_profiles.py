"""Synthetic operator assignments and stored jobs; never requires a provider or customer data."""

import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.contracts.models import ChecklistVersion, EvaluationCreateRequest, Message
from app.domain.scoring import score
from app.evaluation.demo import build_demo_payload, load_synthetic_input
from app.main import app
from app.services.operator_profiles import first_reply
from app.storage.database import write_transaction


@pytest.fixture
def api():
    with TestClient(app, base_url="http://localhost") as client:
        yield client


def ingest(api, identifier, group, channel, turns, operator=None, **facts):
    record = {
        "conversation_id": identifier,
        "language": "RU",
        "channel": channel,
        "transcript": [
            {"speaker": who, "timestamp": when, "text": "Учебная реплика."} for who, when in turns
        ],
    }
    if operator is not None:
        record["operator"] = {"operator_id": operator, **facts}
    response = api.post(
        "/api/v1/conversations/import",
        json={
            "dataset_group_id": group,
            "records": [record],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["conversation_version_ids"][0]


def store_evaluation(version_id, mode="live", checklist=None):
    # A synthetic stand-in for previously completed jobs, scoped to this isolated test DB.
    demo = asyncio.run(build_demo_payload())
    template = demo.evaluation
    checklist = checklist or demo.checklist
    selected_ids = {c.id for c in checklist.criteria}
    job_id = str(uuid4())
    metadata = template.output.metadata.model_copy(update={"mode": mode})
    answers = tuple(
        answer.model_copy(
            update={
                "evidence": (
                    answer.evidence[0].model_copy(
                        update={
                            "conversation_version_id": version_id,
                            "start_message_id": "m1",
                            "end_message_id": "m1",
                        }
                    ),
                )
            }
        )
        for answer in template.output.answers
        if answer.criterion_id in selected_ids
    )
    output = template.output.model_copy(update={"metadata": metadata, "answers": answers})
    original = score(checklist, answers)
    job = template.model_copy(
        update={
            "id": job_id,
            "conversation_version_id": version_id,
            "output": output,
            "checklist_version_id": checklist.id,
            "original_score": original,
            "effective_score": original,
        }
    )
    request = EvaluationCreateRequest(
        conversation_version_id=version_id, checklist_version_id=job.checklist_version_id, mode=mode
    )
    with write_transaction() as db:
        db.execute(
            text("""INSERT INTO evaluations(id, status, request, original_request, snapshot)
            VALUES(:id, 'succeeded', :request, :request, :snapshot)"""),
            {"id": job_id, "request": request.model_dump_json(), "snapshot": job.model_dump_json()},
        )
    return job_id


def override(api, job_id, criterion_id, value):
    appeal = api.post(
        f"/api/v1/evaluations/{job_id}/appeals",
        json={"criterion_id": criterion_id, "comment": "Учебная проверка."},
    )
    assert appeal.status_code == 201, appeal.text
    result = api.post(
        f"/api/v1/appeals/{appeal.json()['id']}/decision",
        json={
            "action": "override",
            "reason": "Проверено на примере.",
            "idempotency_key": str(uuid4()),
            "override_answer": {"type": "boolean", "value": value},
        },
    )
    assert result.status_code == 200, result.text
    return result.json()["evaluation"]


def test_empty_directory_and_missing_operator(api):
    assert api.get("/api/v1/operators").json() == {"items": [], "unassigned_count": 0}
    assert api.get("/api/v1/operators/unassigned/profile").status_code == 404


def test_complete_transcript_spans_are_separate_from_measured_call_durations(api):
    # An exact zero and a six-second international timestamp span both count.
    ingest(
        api,
        "zero",
        "A",
        "voice call",
        [("client", "00:01"), ("operator", "00:01")],
        "agent-a",
        duration_seconds=40,
    )
    ingest(
        api,
        "international",
        "A",
        "voice call",
        [
            ("system", "2026-11-07T10:00:00+05:00"),
            ("client", "2026-11-07T05:00:01Z"),
            ("operator", "2026-11-07T05:00:03Z"),
            ("bot", "2026-11-07T05:00:06Z"),
        ],
        "agent-a",
    )
    for identifier, stamps in (
        ("single", ["00:00"]),
        ("missing", ["00:00", "unknown", "00:10"]),
        ("reverse", ["00:00", "00:11", "00:10"]),
        ("mixed", ["00:00", "2026-11-07T05:00:06Z"]),
    ):
        ingest(
            api, identifier, "A", "voice call", [("client", stamp) for stamp in stamps], "agent-a"
        )
    ingest(api, "chat", "A", "agent chat", [("client", "00:00"), ("operator", "00:30")], "agent-a")
    profile = api.get("/api/v1/operators/agent-a/profile").json()
    assert (profile["timed_call_spans"], profile["average_call_span_seconds"]) == (2, 3)
    assert (profile["measured_calls"], profile["average_call_seconds"]) == (1, 40)
    assert sum(c["duration_seconds"] is not None for c in profile["conversations"]) == 1


def test_flag_is_counted_even_with_every_scored_answer_na(api):
    sample = load_synthetic_input().checklist
    definition = sample.model_dump(
        mode="json", exclude={"id", "checklist_id", "version", "created_at"}
    )
    definition["criteria"] = [c for c in definition["criteria"] if c["id"] in ("C02", "C08")]
    created = api.post("/api/v1/checklists", json=definition)
    assert created.status_code == 201, created.text
    checklist = ChecklistVersion.model_validate(created.json())
    version_id = ingest(api, "null-total", "A", "agent chat", [("client", "00:01")], "agent-a")
    job_id = store_evaluation(version_id, checklist=checklist)
    override(api, job_id, "C08", True)
    profile = api.get("/api/v1/operators/agent-a/profile").json()
    assert profile["flagged_conversations"] == profile["unanswered_conversations"] == 1
    assert profile["scored_conversations"] == 0 and profile["average_effective_score"] is None
    assert profile["criteria"][0]["assessed_count"] == 0
    assert profile["conversations"][0]["evaluation_id"] == job_id


def test_profile_latest_families_attribution_and_effective_live_scores(api):
    same = "shared-id"
    primary = ingest(
        api,
        same,
        "A",
        "voice call",
        [("operator", "00:00"), ("client", "00:02"), ("bot", "00:04"), ("operator", "00:08")],
        "agent-a",
        outcome="successful",
        duration_seconds=12,
    )
    chat = ingest(
        api, same, "B", "agent chat", [("operator", "00:00"), ("client", "00:03")], "agent-a"
    )
    previous = ingest(api, "changed", "A", "voice call", [("client", "00:00")], "agent-a")
    store_evaluation(previous)
    changed = ingest(
        api, "changed", "A", "voice call", [("client", "00:08"), ("operator", "00:03")], "agent-a"
    )
    ingest(
        api,
        "lost",
        "A",
        "voice call",
        [("operator", "00:00")],
        "agent-a",
        outcome="unsuccessful",
        duration_seconds=0,
    )
    ingest(api, same, "C", "voice call", [("client", "00:00")], "agent-b")
    ingest(api, "unassigned", "A", "agent chat", [("bot", "00:00")])
    store_evaluation(primary)
    selected = store_evaluation(primary)
    override(api, selected, "C01", False)
    decision = override(api, selected, "C08", True)
    store_evaluation(primary, mode="mock")  # Even the most recent mock cannot replace live.
    store_evaluation(chat, mode="mock")
    assert api.get("/api/v1/operators").json() == {
        "items": [
            {"operator_id": "agent-a", "conversation_count": 4},
            {"operator_id": "agent-b", "conversation_count": 1},
        ],
        "unassigned_count": 1,
    }
    response = api.get("/api/v1/operators/agent-a/profile")
    assert response.status_code == 200, response.text
    profile = response.json()
    assert (profile["conversation_count"], profile["call_count"], profile["chat_count"]) == (
        4,
        3,
        1,
    )
    assert (
        profile["successful_calls"],
        profile["unsuccessful_calls"],
        profile["unknown_calls"],
    ) == (1, 1, 1)
    assert (
        profile["measured_calls"],
        profile["total_call_seconds"],
        profile["average_call_seconds"],
    ) == (2, 12, 6)
    assert (profile["operator_messages"], profile["client_messages"]) == (5, 3)
    assert (profile["first_reply_count"], profile["average_first_reply_seconds"]) == (1, 6)
    assert profile["unanswered_conversations"] == 1
    assert (profile["scored_conversations"], profile["flagged_conversations"]) == (1, 1)
    effective = float(decision["effective_score"]["total"])
    assert effective != float(decision["original_score"]["total"])
    assert profile["average_effective_score"] == effective
    details = {c["version_id"]: c for c in profile["conversations"]}
    assert details[primary]["evaluation_id"] == selected
    assert details[primary]["effective_score"] == effective
    assert details[changed]["evaluation_id"] is None and previous not in details
    assert details[chat]["evaluation_id"] is None
    criteria = {row["criterion_id"]: row for row in profile["criteria"]}
    assert (criteria["C01"]["assessed_count"], criteria["C01"]["average_quality"]) == (1, 0)
    assert (criteria["C02"]["assessed_count"], criteria["C02"]["average_quality"]) == (0, None)
    assert criteria["C06"]["average_quality"] == 70
    assert "C08" not in criteria
    unknown = api.get("/api/v1/operators/agent-b/profile").json()
    assert unknown["timed_call_spans"] == 0 and unknown["average_call_span_seconds"] is None
    assert unknown["average_call_seconds"] is None and unknown["total_call_seconds"] == 0
    assert unknown["average_effective_score"] is None and unknown["criteria"] == []


@pytest.mark.parametrize(
    ("clock", "expected"),
    [
        (["00:58", "00:59", "00:01:01"], (False, 3)),
        (["2026-11-07T10:00:00+05:00", "2026-11-07T05:00:02Z"], (False, 2)),
        (["2026-11-07T10:00:00", "2026-11-07T10:00:00"], (False, 0)),
        (["2026-11-07T10:00:00", "2026-11-07T10:00:01Z"], (False, None)),
        (["00:01", "2026-11-07T10:00:01Z"], (False, None)),
        (["unknown", "00:05"], (False, None)),
        (["00:10", "00:07", "00:12"], (False, None)),
        (["00:00", "00:61:01"], (False, None)),
    ],
)
def test_comparable_first_reply_clocks(clock, expected):
    messages = tuple(
        Message(
            message_id=f"m{i}",
            timestamp_raw=value,
            order=i,
            text="Пример",
            speaker="client" if i == 0 else "operator" if i == len(clock) - 1 else "system",
        )
        for i, value in enumerate(clock)
    )
    assert first_reply(messages) == expected
