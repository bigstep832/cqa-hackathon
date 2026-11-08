"""Limit JSON before decoding and reject browser mutations from unrelated sites."""

import json
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi.routing import APIRoute
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.services.json_input import decode_json

LOCAL_ORIGINS = {
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
}
MAX_BODY_BYTES = 16 * 1024 * 1024


class StrictJSONRequest(Request):
    async def json(self) -> Any:
        try:
            return decode_json(await self.body())
        except (ValueError, RecursionError):
            # FastAPI maps JSONDecodeError to our typed validation response.
            raise json.JSONDecodeError("Invalid JSON", "", 0) from None


class StrictJSONRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handle = super().get_route_handler()

        async def handle_request(request: Request) -> Response:
            if request.method in {"POST", "PUT"}:
                request = StrictJSONRequest(request.scope, request.receive)
            return await handle(request)

        return handle_request


class RequestBoundary:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def private_response(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "private, no-store"
                headers["X-Content-Type-Options"] = "nosniff"
            await send(message)

        if scope["method"] not in {"POST", "PUT"}:
            await self.app(scope, receive, private_response)
            return

        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if origin is not None and origin not in LOCAL_ORIGINS:
            await self.reject(scope, receive, send, 403, "ORIGIN_DENIED", "Источник запрещён.")
            return

        too_large = "Запрос превышает допустимый размер."
        declared = headers.get("content-length")
        if declared and (
            not declared.isascii()
            or not declared.isdecimal()
            or len(declared) > 10
            or int(declared) > MAX_BODY_BYTES
        ):
            await self.reject(scope, receive, send, 413, "BODY_TOO_LARGE", too_large)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                await self.reject(scope, receive, send, 413, "BODY_TOO_LARGE", too_large)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        consumed = False

        async def replay() -> Message:
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, private_response)

    @staticmethod
    async def reject(
        scope: Scope, receive: Receive, send: Send, status: int, code: str, message: str
    ) -> None:
        response = JSONResponse(
            {"error": {"code": code, "message": message, "details": []}}, status_code=status
        )
        await response(scope, receive, send)
