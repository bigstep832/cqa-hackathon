"""Выбор транспорта без изменения EvaluationProvider или потребителей его результата."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import httpx

from app.contracts.models import ProviderCall
from app.evaluation.compact import RequestEncoding
from app.evaluation.provider import EvaluationProvider
from app.evaluation.structured import InstructionProfile, instruction_version


@dataclass(frozen=True, kw_only=True)
class ProviderConfig:
    provider: Literal["fixture", "gemini", "openai", "groq", "local"] = "fixture"
    api_key: str | None = field(default=None, repr=False)
    local_base_url: str | None = None
    local_response_format: Literal["json_schema", "json_object"] = "json_schema"
    max_attempts: int = 1
    max_output_tokens: int = 8192
    max_input_bytes: int = 200_000
    max_response_bytes: int = 2_000_000
    instruction_profile: InstructionProfile = "cqa-rubric-v1"
    request_encoding: RequestEncoding = "canonical"


def create_provider(
    config: ProviderConfig | None = None,
    *,
    client: httpx.AsyncClient | None = None,
    on_attempt: Callable[[], None] | None = None,
    on_call: Callable[[ProviderCall], None] | None = None,
) -> EvaluationProvider:
    """Никаких env/default облаков, автоматического fallback и сетевого discovery.

    По умолчанию доступна только явно синтетическая fixture. Клиент принадлежит
    вызывающему коду; без него транспорт управляет собственным HTTP клиентом.
    Разрешение live/бюджет проверяет вызывающий workflow или отдельный eval runner.
    """
    config = config or ProviderConfig()
    instruction_version(config.instruction_profile)
    if config.request_encoding not in ("canonical", "compact-v1"):
        raise ValueError("Unknown request encoding")
    if config.request_encoding == "compact-v1" and config.provider not in {"openai", "groq"}:
        raise ValueError("Compact encoding is supported only for OpenAI and Groq")
    if config.provider != "local" and config.local_base_url is not None:
        raise ValueError("Custom endpoint is allowed only for local models")
    if config.provider == "fixture":
        if config.instruction_profile != "cqa-rubric-v1":
            raise ValueError("A prompt candidate requires a live provider, not a fixture")
        from app.evaluation.mock import MockEvaluationProvider

        return MockEvaluationProvider()
    if config.provider == "gemini":
        from app.evaluation.gemini import GeminiConfig, GeminiEvaluationProvider

        return GeminiEvaluationProvider(
            GeminiConfig(
                api_key=config.api_key or "", max_attempts=config.max_attempts,
                max_output_tokens=config.max_output_tokens,
                max_input_bytes=config.max_input_bytes,
                max_response_bytes=config.max_response_bytes,
                instruction_profile=config.instruction_profile,
            ),
            client=client, on_attempt=on_attempt, on_call=on_call,
        )
    if config.provider in {"openai", "groq", "local"}:
        from app.evaluation.openai_compatible import (
            OpenAICompatibleConfig,
            OpenAICompatibleEvaluationProvider,
        )

        return OpenAICompatibleEvaluationProvider(
            OpenAICompatibleConfig(
                provider=config.provider, api_key=config.api_key or None,
                base_url=config.local_base_url,
                response_format=config.local_response_format if config.provider == "local" else "json_schema",
                max_attempts=config.max_attempts, max_output_tokens=config.max_output_tokens,
                max_input_bytes=config.max_input_bytes,
                max_response_bytes=config.max_response_bytes,
                instruction_profile=config.instruction_profile,
                request_encoding=config.request_encoding,
            ),
            client=client, on_attempt=on_attempt, on_call=on_call,
        )
    raise ValueError("Unknown evaluation provider")
