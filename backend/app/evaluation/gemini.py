"""Gemini generateContent REST adapter. Tests inject httpx.MockTransport.

Verified documentation (2026-11-07):
https://ai.google.dev/gemini-api/docs/generate-content/structured-output
https://ai.google.dev/gemini-api/docs/generate-content/thinking
https://ai.google.dev/api/generate-content
https://ai.google.dev/gemini-api/docs/troubleshooting

No automatic environment key lookup, model selection, continuation or semantic
repair. Byte limits are not token estimates. Cost stays unknown (None).
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.contracts.models import EvaluationInput, EvaluationMetadata, EvaluationOutput, ProviderCall
from app.evaluation.provider import ProviderExecutionError
from app.evaluation.structured import (
    InstructionProfile,
    StructuredOutputError,
    build_response_schema,
    build_system_instruction,
    build_user_payload,
    instruction_version,
    parse_model_response,
    validate_evaluation_input,
)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
MODEL_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


@dataclass(frozen=True)
class GeminiConfig:
    api_key: str = field(repr=False)
    max_attempts: int = 1
    max_output_tokens: int = 8192
    max_input_bytes: int = 200_000
    max_response_bytes: int = 2_000_000
    retry_delay_seconds: float = 0.25
    instruction_profile: InstructionProfile = "cqa-rubric-v1"

    def __post_init__(self) -> None:
        instruction_version(self.instruction_profile)
        if (
            not isinstance(self.api_key, str) or not self.api_key or not self.api_key.isascii()
            or any(character.isspace() or ord(character) < 32 for character in self.api_key)
        ):
            raise ValueError("API key must be a non-empty ASCII token")
        limits = (
            (self.max_attempts, 3), (self.max_output_tokens, 1_000_000),
            (self.max_input_bytes, 10_000_000), (self.max_response_bytes, 10_000_000),
        )
        if any(type(value) is not int or not 1 <= value <= maximum for value, maximum in limits):
            raise ValueError("Provider limits must be positive integers within configured bounds")
        if (
            isinstance(self.retry_delay_seconds, bool)
            or not isinstance(self.retry_delay_seconds, (int, float))
            or not math.isfinite(self.retry_delay_seconds)
            or not 0 <= self.retry_delay_seconds <= 5
        ):
            raise ValueError("Retry delay must be finite and between 0 and 5 seconds")


class GeminiProviderError(ProviderExecutionError):
    pass


class _ResponseTooLarge(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def build_gemini_request(
    evaluation_input: EvaluationInput, max_output_tokens: int,
    *, instruction_profile: InstructionProfile = "cqa-rubric-v1",
) -> dict[str, Any]:
    """Request без секретов; API key передаётся отдельно только в HTTP header."""
    return {
        "systemInstruction": {"parts": [{"text": build_system_instruction(
            evaluation_input, profile=instruction_profile,
        )}]},
        "contents": [{"role": "user", "parts": [{"text": build_user_payload(evaluation_input)}]}],
        "generationConfig": {
            # The default is one candidate. Newer Gemini models reject this
            # parameter even when explicitly set to 1 (Google model parameters).
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
            "responseJsonSchema": build_response_schema(evaluation_input.checklist),
        },
    }


def _nonnegative_integer(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _usage_tokens(response: Any) -> tuple[int | None, int | None]:
    usage = response.get("usageMetadata") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return None, None
    prompt = _nonnegative_integer(usage.get("promptTokenCount"))
    total = _nonnegative_integer(usage.get("totalTokenCount"))
    candidates = _nonnegative_integer(usage.get("candidatesTokenCount"))
    thoughts = _nonnegative_integer(usage.get("thoughtsTokenCount"))
    output: int | None = None
    # Total = prompt + tool-use prompt + thoughts + candidates. This adapter
    # enables no tools; subtraction includes thinking exactly once.
    if total is not None and prompt is not None and total >= prompt:
        output = total - prompt
        if candidates is not None and output < candidates:
            output = None
        if candidates is not None and thoughts is not None and output != candidates + thoughts:
            output = None
    elif candidates is not None and thoughts is not None:
        output = candidates + thoughts
    # Missing thought usage cannot safely be assumed zero for an unknown model.
    return prompt, output


def _extract_text(response: Any) -> str:
    if not isinstance(response, dict):
        raise StructuredOutputError("INVALID_RESPONSE", "Provider returned invalid response JSON")
    feedback = response.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        raise StructuredOutputError("MODEL_REFUSAL", "Provider blocked the evaluation")
    candidates = response.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1 or not isinstance(candidates[0], dict):
        raise StructuredOutputError("EMPTY_RESPONSE", "Provider did not return one completed answer")
    candidate = candidates[0]
    finish_reason = candidate.get("finishReason")
    if not isinstance(finish_reason, str):
        raise StructuredOutputError("INVALID_RESPONSE", "Provider returned an invalid finish reason")
    if finish_reason in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}:
        raise StructuredOutputError("MODEL_REFUSAL", "Provider refused the evaluation")
    if finish_reason != "STOP":
        raise StructuredOutputError("INCOMPLETE_RESPONSE", "Provider response did not finish normally")
    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        raise StructuredOutputError("EMPTY_RESPONSE", "Provider returned no answer text")
    texts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            raise StructuredOutputError("INVALID_RESPONSE", "Provider returned invalid content parts")
        if part.get("thought") is True:
            continue
        if "thought" in part and type(part["thought"]) is not bool:
            raise StructuredOutputError("INVALID_RESPONSE", "Provider returned invalid content parts")
        if not isinstance(part.get("text"), str):
            raise StructuredOutputError("INVALID_RESPONSE", "Provider returned a non-text answer")
        texts.append(part["text"])
    text = "".join(texts)
    if not text.strip():
        raise StructuredOutputError("EMPTY_RESPONSE", "Provider returned no answer text")
    return text


class GeminiEvaluationProvider:
    def __init__(
        self,
        config: GeminiConfig,
        *,
        client: httpx.AsyncClient | None = None,
        on_attempt: Callable[[], None] | None = None,
        on_call: Callable[[ProviderCall], None] | None = None,
    ) -> None:
        self.config = config
        self._client = client
        self._on_attempt = on_attempt
        self._on_call = on_call

    async def evaluate(self, evaluation_input: EvaluationInput) -> EvaluationOutput:
        try:
            evaluation_input = validate_evaluation_input(evaluation_input, "gemini")
        except StructuredOutputError as error:
            raise GeminiProviderError(error.code, str(error)) from None
        if not MODEL_ID_PATTERN.fullmatch(evaluation_input.settings.model):
            raise GeminiProviderError("INVALID_INPUT", "Gemini requires a bare model ID")
        payload = json.dumps(
            build_gemini_request(evaluation_input, self.config.max_output_tokens,
                                 instruction_profile=self.config.instruction_profile),
            ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        if len(payload) > self.config.max_input_bytes:
            raise GeminiProviderError("INPUT_TOO_LARGE", "Evaluation request exceeds the byte limit")
        if self._client is not None:
            return await self._evaluate_with_client(self._client, evaluation_input, payload)
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
            return await self._evaluate_with_client(client, evaluation_input, payload)

    async def _request(
        self, client: httpx.AsyncClient, evaluation_input: EvaluationInput, payload: bytes
    ) -> tuple[int, Any]:
        endpoint = f"{GEMINI_BASE_URL}/{evaluation_input.settings.model}:generateContent"
        timeout = evaluation_input.settings.timeout_seconds
        async with asyncio.timeout(timeout):
            async with client.stream(
                "POST", endpoint, content=payload,
                headers={"x-goog-api-key": self.config.api_key, "Content-Type": "application/json"},
                timeout=httpx.Timeout(timeout), follow_redirects=False,
            ) as response:
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > self.config.max_response_bytes:
                        raise _ResponseTooLarge(response.status_code)
                    content.extend(chunk)
                try:
                    outer = json.loads(content)
                except (ValueError, UnicodeError, RecursionError):
                    outer = None
                return response.status_code, outer

    async def _evaluate_with_client(
        self, client: httpx.AsyncClient, evaluation_input: EvaluationInput, payload: bytes
    ) -> EvaluationOutput:
        calls: list[ProviderCall] = []
        for attempt in range(1, self.config.max_attempts + 1):
            if self._on_attempt is not None:
                try:
                    self._on_attempt()
                except Exception:
                    raise GeminiProviderError(
                        "CALL_LIMIT", "Request gate did not authorize another attempt", calls=tuple(calls)
                    ) from None
            started = time.perf_counter()
            status_code: int | None = None
            outer: Any = None
            error_code: str | None = None
            retryable = False
            try:
                status_code, outer = await self._request(client, evaluation_input, payload)
            except _ResponseTooLarge as error:
                status_code = error.status_code
                error_code = "RESPONSE_TOO_LARGE"
            except (TimeoutError, httpx.TimeoutException):
                error_code, retryable = "TIMEOUT", True
            except httpx.DecodingError:
                error_code = "INVALID_RESPONSE"
            except httpx.TransportError:
                error_code, retryable = "NETWORK_ERROR", True
            input_tokens, output_tokens = _usage_tokens(outer)
            calls.append(ProviderCall(
                provider="gemini", model=evaluation_input.settings.model,
                purpose="evaluate", attempt=attempt,
                latency_ms=max(0.0, (time.perf_counter() - started) * 1000),
                input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=None,
            ))
            if self._on_call is not None:
                self._on_call(calls[-1])
            if error_code is None and (status_code is None or not 200 <= status_code < 300):
                error_code = "HTTP_ERROR"
                retryable = status_code in {408, 429} or (status_code is not None and status_code >= 500)
            if error_code is not None:
                if retryable and attempt < self.config.max_attempts:
                    await asyncio.sleep(min(5.0, self.config.retry_delay_seconds * 2 ** (attempt - 1)))
                    continue
                raise GeminiProviderError(
                    error_code, "Gemini request failed; no evaluation was produced",
                    calls=tuple(calls), status_code=status_code,
                ) from None
            try:
                text = _extract_text(outer)
                metadata = EvaluationMetadata(
                    mode="live", provider="gemini", model=evaluation_input.settings.model,
                    instruction_version=instruction_version(self.config.instruction_profile),
                    pipeline_version="gemini-rest-v2", calls=tuple(calls),
                )
                return parse_model_response(text, evaluation_input, metadata)
            except StructuredOutputError as error:
                raise GeminiProviderError(
                    error.code, str(error), calls=tuple(calls), status_code=status_code,
                ) from None
        raise AssertionError("Validated attempt limit must permit at least one attempt")
