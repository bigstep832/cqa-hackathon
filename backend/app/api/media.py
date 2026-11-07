"""Fetch a stored audio resource only from a configured trusted media origin."""

import os
import re

from fastapi import APIRouter, Header
from fastapi.responses import Response

from app.contracts.models import ErrorResponse
from app.services import source_http
from app.storage.conversations import audio_source

router = APIRouter()
_MAX_AUDIO = 64 * 1024 * 1024
_RANGE = re.compile(r"bytes=(?:[0-9]+-[0-9]*|-[0-9]+)")
_CONTENT_RANGE = re.compile(r"bytes [0-9]+-[0-9]+/(?:[0-9]+|\*)")
_AUDIO_TYPES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/aac",
    "audio/ogg",
    "audio/wav",
    "audio/x-wav",
    "audio/webm",
    "audio/flac",
    "audio/x-m4a",
    "audio/3gpp",
    "audio/amr",
}


def _media_origin(url: str, *, configured: bool = False) -> tuple[str | None, int]:
    target = source_http.https_target(url)
    if configured and (target.path not in ("", "/") or target.query):
        raise ValueError("Expected an HTTPS origin without query or path")
    return target.hostname, target.port or 443


@router.get(
    "/api/v1/conversation-versions/{version_id}/audio",
    response_class=Response,
    tags=["media"],
    responses={
        **{
            status: {
                "description": label,
                "content": {
                    mime: {"schema": {"type": "string", "format": "binary"}}
                    for mime in sorted(_AUDIO_TYPES)
                },
            }
            for status, label in ((200, "Complete audio file"), (206, "Partial audio content"))
        },
        **{
            status: {"description": label, "model": ErrorResponse}
            for status, label in (
                (403, "Media origin not allowed"),
                (404, "Version or audio not found"),
                (416, "Invalid or unavailable range"),
                (502, "Invalid upstream media"),
                (503, "Source busy or media not configured"),
            )
        },
    },
)
def get_audio(version_id: str, range_header: str | None = Header(None, alias="Range")) -> Response:
    url = audio_source(version_id)
    try:
        allowed = {
            _media_origin(origin.strip(), configured=True)
            for origin in os.environ.get("CQA_AUDIO_ORIGINS", "").split(",")
            if origin.strip()
        }
        permitted = _media_origin(url) in allowed
    except ValueError:
        permitted = False
    if not permitted:
        raise source_http.failure(403, "MEDIA_ORIGIN_NOT_ALLOWED", "Источник аудио не разрешён.")
    if range_header is not None and (len(range_header) > 80 or not _RANGE.fullmatch(range_header)):
        raise source_http.failure(416, "INVALID_RANGE", "Некорректный диапазон аудио.")
    request_headers = {"Range": range_header} if range_header is not None else {}
    status, headers, data = source_http.fetch(url, headers=request_headers, max_bytes=_MAX_AUDIO)
    if status == 416:
        raise source_http.failure(416, "INVALID_RANGE", "Диапазон недоступен.")
    mime = headers.get("content-type", "").split(";")[0].strip().lower()
    if status not in (200, 206) or mime not in _AUDIO_TYPES:
        raise source_http.failure(502, "INVALID_MEDIA", "Источник не вернул поддерживаемое аудио.")
    public_headers = {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Accept-Ranges": "bytes",
    }
    if status == 206:
        span = headers.get("content-range", "")
        if not _CONTENT_RANGE.fullmatch(span):
            raise source_http.failure(502, "INVALID_MEDIA", "Неверный диапазон аудио.")
        public_headers["Content-Range"] = span
    return Response(data, status_code=status, media_type=mime, headers=public_headers)
