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
