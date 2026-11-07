import json

from fastapi.testclient import TestClient

from app.api import boundary
from app.main import app


def test_request_limit_including_chunked_json(monkeypatch) -> None:
    monkeypatch.setattr(boundary, "MAX_BODY_BYTES", 32)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        for body in (b"x" * 33, iter([b"x" * 12, b"y" * 21])):
            response = client.post("/api/v1/conversations/import", content=body)
            assert response.status_code == 413
            assert response.json()["error"]["code"] == "BODY_TOO_LARGE"


def test_foreign_sites_cannot_write_or_rebind_local_api() -> None:
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/v1/datasets/import",
            json={"group_id": "DEV"},
            headers={"Origin": "https://outside.example"},
        )
        assert response.status_code == 403
        assert (
            client.get("/api/v1/conversations", headers={"Host": "outside.example"}).status_code
            == 400
        )
        assert client.get("/api/v1/conversations", headers={"Host": "localhost"}).status_code == 200


def test_malformed_json_is_rejected_before_any_write() -> None:
    record = {"conversation_id": "example", "language": "RU", "channel": "agent chat",
              "transcript": [{"timestamp": "00:01", "speaker": "operator", "text": "marker"}]}
    body = json.dumps({"dataset_group_id": "QA", "records": [record]})
    ambiguous = body.replace('"text": "marker"', '"text": "private-input", "text": "marker"')
    with TestClient(app, base_url="http://127.0.0.1") as client:
        for invalid in (ambiguous.encode(), b"\xff", b"[" * 2000 + b"]" * 2000):
            response = client.post("/api/v1/conversations/import", content=invalid,
                                   headers={"Content-Type": "application/json"})
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "VALIDATION_ERROR"
            assert "private-input" not in response.text
        assert client.get("/api/v1/conversations").json()["total"] == 0
