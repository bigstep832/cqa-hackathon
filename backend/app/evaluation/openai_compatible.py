"""Explicit OpenAI, Groq, or loopback-compatible provider.

No key discovery, network call on construction, redirects, tools, or mock fallback.
The configured model/server must support the selected response format. Local JSON
mode is explicit and always receives the same schema plus strict server validation.
API reference: https://developers.openai.com/api/docs/guides/structured-outputs
Groq: https://console.groq.com/docs/structured-outputs
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from app.contracts.models import EvaluationInput, EvaluationMetadata, EvaluationOutput, ProviderCall
from app.evaluation.provider import ProviderExecutionError
from app.evaluation.structured import (
    INSTRUCTION_VERSION,
    StructuredOutputError,
    build_response_schema,
    build_system_instruction,
    build_user_payload,
    parse_model_response,
    validate_evaluation_input,
)

OPENAI_BASE_URL = "https://api.openai.com/v1"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
PIPELINE_VERSION = "openai-compatible-v1"
GROQ_PIPELINE_VERSION = "groq-chat-v1"
_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


@dataclass(frozen=True, kw_only=True)
class OpenAICompatibleConfig:
    provider: Literal["openai", "groq", "local"] = "openai"
    api_key: str | None = field(default=None, repr=False)
    base_url: str | None = None
    response_format: Literal["json_schema", "json_object"] = "json_schema"
    max_attempts: int = 1
    max_output_tokens: int = 8192
    max_input_bytes: int = 200_000
    max_response_bytes: int = 2_000_000
    retry_delay_seconds: float = 0.25

    def __post_init__(self) -> None:
        if self.provider not in {"openai", "groq", "local"}:
            raise ValueError("Unsupported provider")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 3:
            raise ValueError("max_attempts must be an integer from 1 to 3")
        for value in (self.max_output_tokens, self.max_input_bytes, self.max_response_bytes):
            if type(value) is not int or value < 1:
                raise ValueError("Token and byte limits must be positive integers")
        if (
            isinstance(self.retry_delay_seconds, bool)
            or not isinstance(self.retry_delay_seconds, (int, float))
            or not math.isfinite(self.retry_delay_seconds)
            or not 0 <= self.retry_delay_seconds <= 60
        ):
            raise ValueError("retry_delay_seconds must be finite and between 0 and 60")
        if self.response_format not in {"json_schema", "json_object"}:
            raise ValueError("Unsupported response format")
        if self.api_key is not None and (
            not isinstance(self.api_key, str)
            or not self.api_key.strip()
            or not self.api_key.isascii()
            or any(character.isspace() for character in self.api_key)
        ):
            raise ValueError("API key must be a nonempty ASCII token without whitespace")
        if self.provider in {"openai", "groq"}:
            label = "OpenAI" if self.provider == "openai" else "Groq"
            if self.api_key is None:
                raise ValueError(f"An explicit {label} API key is required")
            if self.base_url is not None:
                raise ValueError(f"{label} uses a fixed endpoint; base_url is local-only")
            if self.response_format != "json_schema":
                raise ValueError(f"{label} requires a model supporting strict JSON Schema")
        else:
            _local_base_url(self.base_url)


def _local_base_url(value: str | None) -> str:
    if not isinstance(value, str) or not value or any(character.isspace() for character in value):
        raise ValueError("An explicit loopback /v1 base_url is required")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            and parsed.username is None
            and parsed.password is None
            and "?" not in value
            and "#" not in value
            and parsed.path.rstrip("/") == "/v1"
            and (port is None or 1 <= port <= 65535)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Local endpoint must be an HTTP(S) loopback /v1 URL without credentials")
    return value.rstrip("/")


class _AttemptFailure(Exception):
    def __init__(
        self, code: str, message: str, *, retryable: bool = False, status_code: int | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


def _token_count(usage: object, key: str) -> int | None:
    if not isinstance(usage, dict):
        return None
    value = usage.get(key)
    return value if type(value) is int and value >= 0 else None


def _response_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise _AttemptFailure("INVALID_RESPONSE", "Expected exactly one completion", retryable=True)
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise _AttemptFailure("INVALID_RESPONSE", "Missing completion message", retryable=True)
    if message.get("refusal"):
        raise _AttemptFailure("MODEL_REFUSAL", "Model refused the evaluation")
    if choice.get("finish_reason") == "length":
        raise _AttemptFailure("TRUNCATED_RESPONSE", "Model output exceeded the token limit")
    if choice.get("finish_reason") != "stop":
        raise _AttemptFailure("INCOMPLETE_RESPONSE", "Model did not complete a valid evaluation")
    if message.get("tool_calls") or message.get("function_call"):
        raise _AttemptFailure("UNEXPECTED_TOOL_CALL", "Evaluation must not invoke tools")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise _AttemptFailure("INVALID_RESPONSE", "Missing JSON text", retryable=True)
    return content


class OpenAICompatibleEvaluationProvider:
    def __init__(
        self,
        config: OpenAICompatibleConfig,
        *,
        client: httpx.AsyncClient | None = None,
        on_attempt: Callable[[], None] | None = None,
        on_call: Callable[[ProviderCall], None] | None = None,
    ) -> None:
        self.config = config
        self._client = client
        self._on_attempt = on_attempt
        self._on_call = on_call
        if config.provider == "openai":
            base_url = OPENAI_BASE_URL
        elif config.provider == "groq":
            base_url = GROQ_BASE_URL
        else:
            base_url = _local_base_url(config.base_url)
        self._endpoint = base_url + "/chat/completions"

    async def evaluate(self, evaluation_input: EvaluationInput) -> EvaluationOutput:
        try:
            validated = validate_evaluation_input(evaluation_input, self.config.provider)
            schema = build_response_schema(validated.checklist)
            instruction = build_system_instruction(validated)
            if self.config.response_format == "json_object":
                instruction += "\nReturn a JSON object satisfying this schema:\n" + json.dumps(
                    schema, ensure_ascii=False, separators=(",", ":"), allow_nan=False
                )
            response_format: dict[str, Any] = {"type": self.config.response_format}
            if self.config.response_format == "json_schema":
                response_format["json_schema"] = {
                    "name": "cqa_evaluation", "strict": True, "schema": schema
                }
            payload = {
                "model": validated.settings.model,
                "messages": [
                    {"role": "system", "content": instruction},
                    {"role": "user", "content": build_user_payload(validated)},
                ],
                "response_format": response_format,
                "stream": False,
                "n": 1,
            }
            # The cloud parameter includes hidden reasoning tokens. Local endpoints
            # use the common legacy compatible parameter; no silent parameter retry.
            token_parameter = (
                "max_tokens" if self.config.provider == "local" else "max_completion_tokens"
            )
            payload[token_parameter] = self.config.max_output_tokens
            if self.config.provider == "openai":
                payload["store"] = False
            elif self.config.provider == "groq" and validated.settings.model in {
                "openai/gpt-oss-120b", "openai/gpt-oss-20b",
            }:
                # GPT-OSS uses these parameters, not reasoning_format. Keep hidden
                # reasoning out of the response; explanations remain in the schema.
                payload["reasoning_effort"] = "medium"
                payload["include_reasoning"] = False
            encoded = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        except StructuredOutputError as error:
            raise ProviderExecutionError(error.code, str(error)) from None
        except (ValueError, TypeError, OverflowError):
            raise ProviderExecutionError("INVALID_INPUT", "Evaluation request failed validation") from None
        if len(encoded) > self.config.max_input_bytes:
            raise ProviderExecutionError("INPUT_TOO_LARGE", "Evaluation request exceeds the byte limit")
        if self._client is not None:
            return await self._evaluate(validated, encoded, self._client)
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
            return await self._evaluate(validated, encoded, client)

    async def _evaluate(
        self, evaluation_input: EvaluationInput, encoded: bytes, client: httpx.AsyncClient
    ) -> EvaluationOutput:
        calls: list[ProviderCall] = []
        for attempt in range(1, self.config.max_attempts + 1):
            if self._on_attempt is not None:
                try:
                    self._on_attempt()
                except Exception:
                    raise ProviderExecutionError(
                        "ATTEMPT_BLOCKED", "Request permission or budget gate rejected the attempt",
                        calls=tuple(calls),
                    ) from None
            started = perf_counter()
            response: dict[str, Any] | None = None
            failure: _AttemptFailure | None = None
            try:
                response = await self._request(client, encoded, evaluation_input.settings.timeout_seconds)
            except _AttemptFailure as error:
                failure = error
            except (httpx.TimeoutException, TimeoutError):
                failure = _AttemptFailure("PROVIDER_TIMEOUT", "Provider request timed out", retryable=True)
            except httpx.HTTPError:
                failure = _AttemptFailure("PROVIDER_CONNECTION_ERROR", "Provider connection failed", retryable=True)
            usage = response.get("usage") if response is not None else None
            calls.append(ProviderCall(
                provider=self.config.provider,
                model=evaluation_input.settings.model,
                purpose="evaluate",
                attempt=attempt,
                latency_ms=max(0.0, (perf_counter() - started) * 1000),
                input_tokens=_token_count(usage, "prompt_tokens"),
                # completion_tokens already includes reasoning; never add details again.
                output_tokens=_token_count(usage, "completion_tokens"),
                cost_usd=None,
            ))
            if self._on_call is not None:
                self._on_call(calls[-1])
            if failure is None:
                assert response is not None
                try:
                    text = _response_text(response)
                    metadata = EvaluationMetadata(
                        mode="live", provider=self.config.provider, model=evaluation_input.settings.model,
                        instruction_version=INSTRUCTION_VERSION,
                        pipeline_version=(GROQ_PIPELINE_VERSION if self.config.provider == "groq"
                                          else PIPELINE_VERSION),
                        calls=tuple(calls),
                    )
                    return parse_model_response(text, evaluation_input, metadata)
                except _AttemptFailure as error:
                    failure = error
                except StructuredOutputError as error:
                    failure = _AttemptFailure(error.code, str(error), retryable=True)
            assert failure is not None
            if not failure.retryable or attempt == self.config.max_attempts:
                raise ProviderExecutionError(
                    failure.code, str(failure), calls=tuple(calls), status_code=failure.status_code
                ) from None
            await asyncio.sleep(self.config.retry_delay_seconds)
        raise AssertionError("Validated max_attempts must yield a result or an error")

    async def _request(
        self, client: httpx.AsyncClient, encoded: bytes, timeout_seconds: int
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.config.api_key is not None:
            headers["Authorization"] = "Bearer " + self.config.api_key
        async with asyncio.timeout(timeout_seconds):
            async with client.stream(
                "POST", self._endpoint, content=encoded, headers=headers,
                timeout=timeout_seconds, follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise _AttemptFailure(
                        "PROVIDER_HTTP_ERROR", "Provider returned an unsuccessful HTTP status",
                        retryable=response.status_code in _RETRYABLE_STATUS,
                        status_code=response.status_code,
                    )
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self.config.max_response_bytes:
                        raise _AttemptFailure("RESPONSE_TOO_LARGE", "Provider response exceeds the byte limit")
                    chunks.append(chunk)
        try:
            result = json.loads(b"".join(chunks))
        except (ValueError, UnicodeError, RecursionError):
            raise _AttemptFailure("INVALID_RESPONSE", "Provider returned invalid JSON", retryable=True) from None
        if not isinstance(result, dict):
            raise _AttemptFailure("INVALID_RESPONSE", "Provider response must be an object", retryable=True)
        return result
