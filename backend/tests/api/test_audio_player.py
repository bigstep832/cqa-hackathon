"""Invented HTML-player resources; no organizer or model requests."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import source_http

PLAYER = "https://player.example.test/recording?example=synthetic-player-secret"
MEDIA = "https://media.example.test/recording?example=synthetic-media-secret&part=1"
HTML = ("<html><audio controls><source src=\"" + MEDIA.replace("&", "&amp;")
        + "\" type=\"audio/mpeg\"></audio></html>").encode()


@pytest.fixture
def audio_client(monkeypatch):
    monkeypatch.setenv("CQA_AUDIO_ORIGINS", "https://player.example.test,https://media.example.test")
    client = TestClient(app, base_url="http://127.0.0.1")
    response = client.post("/api/v1/conversations/import", json={
        "dataset_group_id": "SYNTHETIC_AUDIO_PLAYER",
        "records": [{"conversation_id": "audio-player-example", "language": "RU",
                     "channel": "voice call", "audio_url": PLAYER,
                     "transcript": [{"speaker": "client", "text": "Синтетический пример",
                                     "timestamp": "00:01"}]}],
    })
    assert response.status_code == 200
    version = response.json()["conversation_version_ids"][0]
    return client, f"/api/v1/conversation-versions/{version}/audio"


@pytest.mark.parametrize("requested", [None, "bytes=2-3"])
def test_allowlisted_html_player_returns_audio_and_preserves_range(audio_client, monkeypatch, requested):
    client, endpoint = audio_client
    calls = []

    def fetch(url, *, max_bytes, headers):
        calls.append((url, headers))
        assert max_bytes == 64 * 1024 * 1024
        if url == PLAYER:
            return 200, {"content-type": "text/html; charset=utf-8"}, HTML
        assert url == MEDIA
        return ((206, {"content-type": "audio/mpeg", "content-range": "bytes 2-3/8",
                       "set-cookie": "synthetic-media-secret"}, b"cd") if requested else
                (200, {"content-type": "audio/mpeg"}, b"abcdefgh"))

    monkeypatch.setattr(source_http, "fetch", fetch)
    headers = {"Range": requested} if requested else {}
    response = client.get(endpoint, headers=headers)
    assert response.status_code == (206 if requested else 200)
    assert response.content == (b"cd" if requested else b"abcdefgh")
    assert calls == [(PLAYER, headers), (MEDIA, headers)]
    assert "set-cookie" not in response.headers
    assert "synthetic-" not in response.text
    assert response.headers["cache-control"] == "private, no-store"
    if requested:
        assert response.headers["content-range"] == "bytes 2-3/8"


@pytest.mark.parametrize("target", [
    "https://unapproved.example.test/a", "http://media.example.test/a", "data:audio/mpeg,x",
    "https://user:password@media.example.test/a", "https://media.example.test:444/a",
    "//media.example.test/a", "/relative.mp3", "https://media.example.test/a#fragment",
])
def test_player_cannot_expand_media_allowlist(audio_client, monkeypatch, target):
    client, endpoint = audio_client
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        return 200, {"content-type": "text/html"}, f'<audio src="{target}"></audio>'.encode()

    monkeypatch.setattr(source_http, "fetch", fetch)
    response = client.get(endpoint)
    assert response.status_code == 403
    assert calls == [PLAYER]
    assert target not in response.text and "synthetic-player-secret" not in response.text


@pytest.mark.parametrize("page", [
    b"<script>alert('x')</script>", b'<source src="https://media.example.test/a">',
    b'<audio src="https://media.example.test/a" src="https://media.example.test/b"></audio>',
    b'<audio src="https://media.example.test/a"></audio><audio src="https://media.example.test/b"></audio>',
    b'<audio><source src="https://media.example.test/a"><source src="https://media.example.test/b"></audio>',
    b'<audio src="https://media.example.test/a"></audio>' + b'x' * (256 * 1024),
    b'<audio src="https://media.example.test/a"></audio>\xff',
])
def test_invalid_ambiguous_or_oversized_player_is_not_executed(audio_client, monkeypatch, page):
    client, endpoint = audio_client
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        return 200, {"content-type": "text/html"}, page

    monkeypatch.setattr(source_http, "fetch", fetch)
    response = client.get(endpoint)
    assert response.status_code == 502
    assert calls == [PLAYER]


@pytest.mark.parametrize("status,mime,content_range", [
    (200, "text/html", ""), (302, "audio/mpeg", ""),
    (206, "audio/mpeg", "bytes 0-1/8"), (416, "audio/mpeg", ""),
])
def test_resolved_target_must_be_audio_and_match_requested_range(
    audio_client, monkeypatch, status, mime, content_range
):
    client, endpoint = audio_client
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        if url == PLAYER:
            return 200, {"content-type": "text/html"}, HTML
        return status, {"content-type": mime, "content-range": content_range}, b"ab"

    monkeypatch.setattr(source_http, "fetch", fetch)
    response = client.get(endpoint, headers={"Range": "bytes=2-3"})
    assert response.status_code == (416 if status == 416 else 502)
    assert calls == [PLAYER, MEDIA]
