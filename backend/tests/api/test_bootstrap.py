"""Checks of implemented bootstrap behavior, not claims about future workflows."""

from collections.abc import Iterator
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app.contracts.models import DemoPayload, ErrorResponse
from app.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app, base_url="http://127.0.0.1") as instance:
        yield instance


def test_health_is_explicitly_bootstrap(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "stage": "bootstrap", "version": "0.1.0"}


def test_demo_is_valid_synthetic_and_links_to_its_own_snapshot(client: TestClient) -> None:
    response = client.get("/api/v1/demo")
    assert response.status_code == 200
    demo = DemoPayload.model_validate(response.json())
    assert demo.conversation.synthetic
    assert demo.evaluation.output is not None
    assert demo.evaluation.output.metadata.mode == "mock"
    assert demo.evaluation.original_score is not None
    assert str(demo.evaluation.original_score.total) == "85.38"
    ids = {message.message_id for message in demo.conversation.messages}
    for answer in demo.evaluation.output.answers:
        for evidence in answer.evidence:
            assert evidence.conversation_version_id == demo.conversation.id
            assert evidence.start_message_id in ids
            assert evidence.end_message_id in ids


def test_unconfigured_source_and_unknown_snapshots_return_typed_errors(client: TestClient) -> None:
    for method, path, body, status in (
        ("POST", "/api/v1/datasets/import", {"group_id": "DEV"}, 503),
        ("GET", "/api/v1/conversations/unknown", None, 404),
        ("GET", "/api/v1/conversation-versions/unknown", None, 404),
        ("GET", "/api/v1/checklist-versions/unknown", None, 404),
        ("GET", "/api/v1/evaluations/unknown", None, 404),
        ("GET", "/api/v1/evaluations/unknown/appeals", None, 404),
    ):
        response = client.request(method, path, json=body)
        assert response.status_code == status
        ErrorResponse.model_validate(response.json())


def test_validation_error_has_contract_without_echoing_input(client: TestClient) -> None:
    secret_marker = "DO_NOT_ECHO_PRIVATE_TEST_INPUT"
    response = client.post(
        "/api/v1/datasets/import", json={"group_id": "DEV", secret_marker: secret_marker}
    )
    assert response.status_code == 422
    error = ErrorResponse.model_validate(response.json())
    assert error.error.code == "VALIDATION_ERROR"
    assert error.error.details[0].field == "body"
    assert secret_marker not in response.text


def test_openapi_describes_success_requests_and_consistent_errors(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    operations = [
        operation
        for path in schema["paths"].values()
        for method, operation in path.items()
        if method in {"get", "post"} and "media" not in operation.get("tags", [])
    ]
    assert len(operations) == 16
    for operation in operations:
        responses = operation["responses"]
        success = [value for code, value in responses.items() if code.startswith("2")]
        assert len(success) == 1
        assert "$ref" in success[0]["content"]["application/json"]["schema"]
        for code in ("404", "409", "422", "413", "502", "503"):
            assert responses[code]["content"]["application/json"]["schema"]["$ref"].endswith(
                "/ErrorResponse"
            )
    for path, spec in schema["paths"].items():
        if "post" in spec:
            assert spec["post"]["requestBody"]["required"], path


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "language=XX"])
def test_pagination_and_language_are_validated(
    client: TestClient, query: str
) -> None:
    assert client.get(f"/api/v1/conversations?{query}").status_code == 422


def test_imported_external_ids_round_trip_and_out_of_range_pages_are_empty(client: TestClient) -> None:
    external_ids = ["case/RU-17", "c" * 201]
    records = [
        {"conversation_id": identifier, "channel": "agent chat", "language": "RU",
         "transcript": [{"timestamp": "00:01", "speaker": "operator", "text": "Пример"}]}
        for identifier in external_ids
    ]
    response = client.post("/api/v1/conversations/import",
                           json={"dataset_group_id": "TEST", "records": records})
    assert response.status_code == 200
    for identifier, version in zip(external_ids, response.json()["conversation_version_ids"], strict=True):
        detail = client.get("/api/v1/conversations/" + quote(identifier, safe=""))
        assert detail.status_code == 200
        assert (detail.json()["conversation_id"], detail.json()["id"]) == (identifier, version)
    for path in ("/api/v1/conversations", "/api/v1/conversations/"):
        page = client.get(path, params={"offset": 2**63, "limit": 5})
        assert page.status_code == 200
        assert page.json() == {"items": [], "total": 2, "offset": 2**63, "limit": 5}
