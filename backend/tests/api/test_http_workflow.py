"""Exercise the HTTP handlers, SQLite, worker and review together across two lifespans."""

from time import monotonic, sleep

from fastapi.testclient import TestClient

from app.main import app


def test_job_review_and_restart_over_http() -> None:
    with TestClient(app, base_url="http://127.0.0.1") as client:
        demo = client.get("/api/v1/demo").json()
        body = {
            "mode": "mock",
            "conversation_version_id": demo["conversation"]["id"],
            "checklist_version_id": demo["checklist"]["id"],
        }
        queued = client.post("/api/v1/evaluations", json=body, headers={"Idempotency-Key": "one"})
        assert queued.status_code == 202
        url = "/api/v1/evaluations/" + queued.json()["id"]
        deadline = monotonic() + 5
        while True:
            original = client.get(url).json()
            if original["status"] not in ("queued", "running") or monotonic() > deadline:
                break
            sleep(0.03)
        assert original["status"] == "succeeded"
        assert original["original_score"]["total"] == "85.38"
        repeated = client.post("/api/v1/evaluations", json=body, headers={"Idempotency-Key": "one"})
        assert repeated.json()["id"] == original["id"]
        appeal = client.post(
            url + "/appeals", json={"criterion_id": "C01", "comment": "Проверка приветствия"}
        )
        assert appeal.status_code == 201
        decision_url = "/api/v1/appeals/" + appeal.json()["id"] + "/decision"
        review = {
            "action": "override",
            "reason": "Тестовая поправка",
            "override_answer": {"type": "boolean", "value": False},
            "idempotency_key": "review",
        }
        decision = client.post(decision_url, json=review)
        assert decision.status_code == 200
        assert client.post(decision_url, json=review).json() == decision.json()
    with TestClient(app, base_url="http://127.0.0.1") as restarted:
        final = restarted.get(url).json()
        assert final["output"] == original["output"]
        assert final["original_score"] == original["original_score"]
        assert final["effective_score"] != original["original_score"]
        assert restarted.get(url + "/appeals").json()["items"][0]["status"] == "resolved"
        snapshot = restarted.get("/api/v1/conversation-versions/" + body["conversation_version_id"])
        assert snapshot.json() == demo["conversation"]
