"""Checks of implemented bootstrap behavior, not claims about future workflows."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.contracts.models import DemoPayload, ErrorResponse
from app.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as instance:
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


def test_every_agreed_workflow_returns_typed_501(client: TestClient) -> None:
    demo = client.get("/api/v1/demo").json()
    checklist = {key: demo["checklist"][key] for key in ("name", "sections", "criteria")}
    operations = [
        ("POST", "/api/v1/datasets/import", {"group_id": "DEV"}),
        (
            "POST", "/api/v1/conversations/import",
            {"dataset_group_id": "SYNTHETIC", "records": [{
                "conversation_id": "synthetic-contract-test", "language": "RU",
                "channel": "agent chat", "audio_url": None,
                "transcript": [{"timestamp": "00:00", "speaker": "client", "text": "Тест"}],
            }]},
        ),
        ("GET", "/api/v1/conversations", None),
        ("GET", "/api/v1/conversations/synthetic", None),
        ("GET", "/api/v1/conversation-versions/synthetic-v1", None),
        ("POST", "/api/v1/checklists", checklist),
        ("GET", "/api/v1/checklists", None),
        ("POST", "/api/v1/checklists/default/versions", checklist),
        ("GET", "/api/v1/checklist-versions/default-v1", None),
        ("POST", "/api/v1/evaluations", {
            "conversation_version_id": "synthetic-v1", "checklist_version_id": "default-v1",
            "mode": "live", "provider": "unconfigured", "model": "unconfigured",
        }),
        ("GET", "/api/v1/evaluations/e1", None),
        ("POST", "/api/v1/evaluations/e1/appeals", {
            "criterion_id": "C01", "comment": "Синтетический пример апелляции",
        }),
        ("GET", "/api/v1/evaluations/e1/appeals", None),
        ("POST", "/api/v1/appeals/a1/decision", {
            "action": "confirm", "reason": "Синтетический пример решения",
            "idempotency_key": "synthetic-decision-1",
        }),
    ]
    for method, path, body in operations:
        response = client.request(method, path, json=body)
        assert response.status_code == 501, (method, path, response.text)
        error = ErrorResponse.model_validate(response.json())
        assert error.error.code == "NOT_IMPLEMENTED"
        assert "C-" in error.error.message


def test_validation_error_has_contract_without_echoing_input(client: TestClient) -> None:
    secret_marker = "DO_NOT_ECHO_PRIVATE_TEST_INPUT"
    response = client.post(
        "/api/v1/datasets/import", json={"group_id": "DEV", "signed_url": secret_marker}
    )
    assert response.status_code == 422
    error = ErrorResponse.model_validate(response.json())
    assert error.error.code == "VALIDATION_ERROR"
    assert error.error.details[0].field == "body.signed_url"
    assert secret_marker not in response.text


def test_openapi_describes_success_requests_and_consistent_errors(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    operations = [
        operation
        for path in schema["paths"].values()
        for method, operation in path.items()
        if method in {"get", "post"}
    ]
    assert len(operations) == 16
    for operation in operations:
        responses = operation["responses"]
        success = [value for code, value in responses.items() if code.startswith("2")]
        assert len(success) == 1
        assert "$ref" in success[0]["content"]["application/json"]["schema"]
        for code in ("404", "409", "422", "501", "502"):
            assert responses[code]["content"]["application/json"]["schema"]["$ref"].endswith(
                "/ErrorResponse"
            )
    for path, spec in schema["paths"].items():
        if "post" in spec:
            assert spec["post"]["requestBody"]["required"], path


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "language=XX"])
def test_pagination_and_language_are_validated_before_stub(
    client: TestClient, query: str
) -> None:
    assert client.get(f"/api/v1/conversations?{query}").status_code == 422
