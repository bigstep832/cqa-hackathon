"""Offline source, history, and media regression tests using only invented records."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.contracts.models import ConversationVersion
from app.domain.snapshots import conversation_content_hash
from app.main import app
from app.services import source_http
from app.storage import conversations
from app.storage.database import write_transaction

PREFIX = "/api/v1"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path.as_posix()}/history.sqlite3")
    monkeypatch.delenv("ORGANIZER_API_URL", raising=False)
    monkeypatch.delenv("CQA_AUDIO_ORIGINS", raising=False)
    return TestClient(app)


def record(identifier="example-1", text="Образец", **kwargs):
    return {
        "conversation_id": identifier,
        "language": "RU",
        "channel": "agent chat",
        "transcript": [
            {
                "speaker": "client",
                "text": "Учебный пример",
                "timestamp": "2026-11-07T12:01:05+05:00",
            },
            {"speaker": "operator", "text": text, "timestamp": "00:03"},
        ],
        **kwargs,
    }


def submit(client, *records, group_id="TEST"):
    return client.post(
        f"{PREFIX}/conversations/import",
        json={"dataset_group_id": group_id, "records": list(records)},
    )


def test_history_and_filters_survive_restart(client):
    first = submit(client, record(), record("example-2", language="KZ")).json()
    second = submit(client, record()).json()
    assert second["conversation_version_ids"] == first["conversation_version_ids"][:1]
    assert second["unchanged_count"] == 1
    old_id = first["conversation_version_ids"][0]
    new = submit(client, record(text="Изменено", language="EN")).json()["conversation_version_ids"][
        0
    ]
    assert old_id != new
    from app.storage.database import get_engine

    get_engine().dispose()
    old = client.get(f"{PREFIX}/conversation-versions/{old_id}").json()
    assert old["source"] == "manual" and not old["synthetic"]
    assert old["messages"][0]["timestamp_raw"] == "2026-11-07T12:01:05+05:00"
    assert all(message["audio_offset_seconds"] is None for message in old["messages"])
    assert [(msg["order"], msg["message_id"]) for msg in old["messages"]] == [(0, "m1"), (1, "m2")]
    assert old["messages"][1]["text"] == "Образец"
    assert client.get(f"{PREFIX}/conversations/example-1").json()["id"] == new
    assert client.get(f"{PREFIX}/conversations?language=RU").json()["total"] == 0
    page = client.get(f"{PREFIX}/conversations?offset=1&limit=1").json()
    assert (page["total"], page["items"][0]["conversation_id"]) == (2, "example-2")
    assert client.get(f"{PREFIX}/conversation-versions/absent").status_code == 404


def test_atomic_validation_and_failed_sql_write(client, monkeypatch):
    assert submit(client, record(), record("bad", transcript=[])).status_code == 422
    assert submit(client, record(), record()).status_code == 422
    original = conversations._save

    def fail_second(connection, snapshot):
        if snapshot.conversation_id == "fail":
            raise RuntimeError("injected synthetic failure")
        return original(connection, snapshot)

    monkeypatch.setattr(conversations, "_save", fail_second)
    with pytest.raises(RuntimeError, match="injected synthetic failure"):
        submit(client, record(), record("fail"))
    assert client.get(f"{PREFIX}/conversations").json()["total"] == 0


def test_concurrent_imports_create_one_version(client):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: submit(client, record()).json(), range(8)))
    assert sum(item["imported_count"] for item in results) == 1
    assert len({item["conversation_version_ids"][0] for item in results}) == 1


def test_database_error_hides_private_parameters(client):
    conversations.initialize_conversations()
    with write_transaction() as connection:
        connection.exec_driver_sql("""
            CREATE TRIGGER reject_example BEFORE INSERT ON conversation_versions
            BEGIN SELECT RAISE(ABORT,'synthetic failure'); END
        """)
    signed_url = "https://media.invalid/test?private_marker=invented"
    with pytest.raises(IntegrityError) as caught:
        submit(client, record("private-synthetic-id", audio_url=signed_url))
    assert "synthetic failure" in str(caught.value)
    assert signed_url not in str(caught.value)
    assert "private-synthetic-id" not in str(caught.value)


def test_organizer_manifest_is_exact_and_independent(client, monkeypatch):
    source = f"{PREFIX}/datasets/import"
    assert client.post(source, json={"group_id": "TEST"}).status_code == 503
    monkeypatch.setenv("ORGANIZER_API_URL", "https://source.invalid/import?synthetic=a%2Bb")
    records = [record("one"), record("two")]

    def fetch(url, **kwargs):
        assert url == "https://source.invalid/import?synthetic=a%2Bb&group_id=TEST"
        return (
            200,
            {},
            json.dumps({"isSuccess": True, "error": None, "data": {"records": records}}).encode(),
        )

    monkeypatch.setattr(source_http, "fetch", fetch)
    imported = client.post(source, json={"group_id": "TEST"})
    assert imported.status_code == 200
    assert "source.invalid" not in imported.text
    assert [v.conversation_id for v in conversations.group_versions("TEST")] == ["one", "two"]
    assert submit(client, record("one", text="Другой текст")).status_code == 200
    assert conversations.group_versions("TEST")[0].source == "organizer"
    records[:] = [record("three"), record("bad", language="bogus")]
    assert client.post(source, json={"group_id": "TEST"}).status_code == 502
    assert [v.conversation_id for v in conversations.group_versions("TEST")] == ["one", "two"]
    assert client.get(f"{PREFIX}/conversations/three").status_code == 404
    records[:] = [record("two")]
    assert client.post(source, json={"group_id": "TEST"}).json()["unchanged_count"] == 1
    assert [v.conversation_id for v in conversations.group_versions("TEST")] == ["two"]


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (307, b""),
        (200, b'{"isSuccess":false,"error":"synthetic-secret"}'),
        (200, b"null"),
        (200, b"{broken"),
    ],
)
def test_bad_source_never_echoes_url_or_response(client, monkeypatch, status, body):
    monkeypatch.setenv(
        "ORGANIZER_API_URL", "https://source.invalid/import?example=synthetic-secret"
    )
    monkeypatch.setattr(source_http, "fetch", lambda *args, **kwargs: (status, {}, body))
    response = client.post(f"{PREFIX}/datasets/import", json={"group_id": "TEST"})
    assert response.status_code == 502
    assert "synthetic-secret" not in response.text
    assert client.get(f"{PREFIX}/conversations").json()["total"] == 0


def test_audio_is_opaque_allowlisted_and_range_checked(client, monkeypatch):
    version_id = submit(
        client, record(audio_url="https://media.invalid/test?example=synthetic-secret")
    ).json()["conversation_version_ids"][0]
    snapshot = client.get(f"{PREFIX}/conversation-versions/{version_id}")
    assert "media.invalid" not in snapshot.text and "synthetic-secret" not in snapshot.text
    endpoint = f"{PREFIX}/conversation-versions/{version_id}/audio"
    assert snapshot.json()["audio_url"].endswith(endpoint)
    public = ConversationVersion.model_validate(snapshot.json())
    assert public.content_hash == conversation_content_hash(public)
    monkeypatch.setenv("CQA_PUBLIC_API_BASE_URL", "https://new-location.invalid")
    from app.storage.database import get_engine

    get_engine().dispose()
    assert client.get(f"{PREFIX}/conversation-versions/{version_id}").json() == snapshot.json()
    assert client.get(endpoint).status_code == 403
    monkeypatch.setenv("CQA_AUDIO_ORIGINS", "https://media.invalid")
    assert client.get(endpoint, headers={"Range": "bytes=0-1,5-6"}).status_code == 416
    monkeypatch.setattr(
        source_http,
        "fetch",
        lambda url, **kwargs: (
            206,
            {
                "content-type": "audio/mpeg",
                "content-range": "bytes 0-1/8",
                "set-cookie": "synthetic-secret",
            },
            b"ab",
        ),
    )
    response = client.get(endpoint, headers={"Range": "bytes=0-1"})
    assert (response.status_code, response.content) == (206, b"ab")
    assert response.headers["content-range"] == "bytes 0-1/8"
    assert "set-cookie" not in response.headers
    monkeypatch.setattr(
        source_http,
        "fetch",
        lambda url, **kwargs: (200, {"content-type": "text/html"}, b"<script>"),
    )
    assert client.get(endpoint).status_code == 502
