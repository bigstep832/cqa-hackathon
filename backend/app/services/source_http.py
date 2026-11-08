"""Bounded HTTPS reads. Never log signed request URLs or follow redirects."""

import http.client
import time
from threading import BoundedSemaphore
from urllib.parse import SplitResult, urlsplit

from app.api.errors import ApiError
from app.contracts.models import ErrorDetail

_slots = BoundedSemaphore(3)


def failure(status: int, code: str, message: str) -> ApiError:
    return ApiError(status, ErrorDetail(code=code, message=message))


def https_target(value: str) -> SplitResult:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("HTTPS origin required")
    _ = parsed.port  # Reject invalid ports before attempting a connection.
    return parsed


def fetch(
    url: str, *, max_bytes: int, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, str], bytes]:
    if not _slots.acquire(blocking=False):
        raise failure(503, "SOURCE_BUSY", "Источник занят. Повторите запрос позже.")
    try:
        target = https_target(url)
        deadline = time.monotonic() + 30
        connection = http.client.HTTPSConnection(target.hostname or "", target.port, timeout=8)
        try:
            path = target.path or "/"
            if target.query:
                path += "?" + target.query
            connection.request("GET", path, headers=headers or {})
            response = connection.getresponse()
            if response.status not in (200, 206):
                return response.status, {}, b""
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise ValueError("Unexpected encoding")
            declared = response.getheader("Content-Length")
            if declared is not None and not 0 <= int(declared) <= max_bytes:
                raise ValueError("Payload limit exceeded")
            data = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError
                block = response.read1(min(max_bytes - len(data) + 1, 64 * 1024))
                if not block:
                    break
                data.extend(block)
                if len(data) > max_bytes:
                    raise ValueError("Payload limit exceeded")
            if declared is not None and len(data) != int(declared):
                raise ValueError("Truncated response")
            return (
                response.status,
                {key.lower(): value for key, value in response.getheaders()},
                bytes(data),
            )
        finally:
            connection.close()
    except (http.client.HTTPException, OSError, ValueError):
        raise failure(502, "UPSTREAM_ERROR", "Не удалось прочитать данные источника.") from None
    finally:
        _slots.release()
