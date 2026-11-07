"""Explicit operator metadata stays outside immutable model input and official exports."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.contracts.models import ConversationImportRequest
from app.main import app
from app.storage.conversations import import_conversations


def record(channel: str = "voice call", **extra: Any) -> dict[str, Any]:
    return {
        "conversation_id": "synthetic/same",
        "language": "RU",
        "channel": channel,
        "transcript": [{"timestamp": "00:02", "speaker": "operator", "text": "Пример"}],
        **extra,
    }


def ingest(client: TestClient, row: dict[str, Any], group: str = "TEST") -> str:
    response = client.post(
        "/api/v1/conversations/import", json={"dataset_group_id": group, "records": [row]}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["conversation_version_ids"][0])


def route(version_id: str) -> str:
    return f"/api/v1/conversation-versions/{version_id}/operator"


def test_import_and_manual_corrections_are_scoped_to_one_family() -> None:
    client = TestClient(app, base_url="http://localhost")
    facts = {"operator_id": "Тест-01", "outcome": "successful", "duration_seconds": 0}
    first = ingest(client, record(operator=facts))
    assert client.get(route(first)).json() == facts
    other = ingest(client, record(), "OTHER")
    organizer = import_conversations(
        ConversationImportRequest.model_validate(
            {"dataset_group_id": "TEST", "records": [record()]},
        ),
        source="organizer",
    ).conversation_version_ids[0]
    for separate in (other, organizer):
        assert client.get(route(separate)).json()["operator_id"] is None
    corrected = {**facts, "outcome": "unsuccessful", "duration_seconds": 45}
    assert ingest(client, record(operator=corrected)) == first
    assert client.get(route(first)).json() == corrected
    assert "Тест-01" not in client.get(f"/api/v1/conversation-versions/{first}").text
    updated = record()
    updated["transcript"][0]["text"] = "Обновлённый пример"
    second = ingest(client, updated)
    assert second != first
    assert client.get(route(second)).json() == corrected
    assert client.put(route(first), json=facts).status_code == 409
    assert client.put(route(second), json={"operator_id": "Б-02"}).status_code == 200
    assert client.get(route(second)).json() == {
        "operator_id": "Б-02",
        "outcome": None,
        "duration_seconds": None,
    }
    assert ingest(client, {**updated, "operator": None}) == second
    assert client.get(route(second)).json()["operator_id"] is None


def test_call_metadata_cannot_contaminate_chat_or_later_voice_version() -> None:
    client = TestClient(app, base_url="http://localhost")
    facts = {"operator_id": "agent.1", "outcome": "successful", "duration_seconds": 20}
    ingest(client, record(operator=facts))
    chat = ingest(client, record("agent chat"))
    assert client.get(route(chat)).json() == dict.fromkeys(facts) | {"operator_id": "agent.1"}
    assert client.put(route(chat), json=facts).status_code == 422
    response = client.post(
        "/api/v1/conversations/import",
        json={
            "dataset_group_id": "TEST",
            "records": [record("agent chat", operator=facts)],
        },
    )
    assert response.status_code == 422
    voice = ingest(client, record())
    assert client.get(route(voice)).json()["duration_seconds"] is None


@pytest.mark.parametrize(
    "invalid",
    [
        {"operator_id": "../person"},
        {"operator_id": "a/b"},
        {"operator_id": " "},
        {"duration_seconds": -1},
        {"duration_seconds": True},
        {"outcome": "estimated"},
    ],
)
def test_invalid_fields_are_rejected(invalid: dict[str, Any]) -> None:
    client = TestClient(app, base_url="http://localhost")
    version = ingest(client, record())
    assert client.put(route(version), json=invalid).status_code == 422


def test_assignment_uses_same_browser_and_json_boundary_as_import() -> None:
    client = TestClient(app, base_url="http://localhost")
    version = ingest(client, record())
    url = route(version)
    assert (
        client.put(
            url, json={"operator_id": "x"}, headers={"Origin": "https://unrelated.invalid"}
        ).status_code
        == 403
    )
    assert (
        client.put(
            url,
            content='{"operator_id":"x","operator_id":"y"}',
            headers={"Content-Type": "application/json"},
        ).status_code
        == 422
    )
    preflight = client.options(
        url, headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "PUT"}
    )
    assert preflight.status_code == 200
    assert client.get(url).json()["operator_id"] is None
