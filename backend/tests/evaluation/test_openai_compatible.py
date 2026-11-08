"""Offline transport tests: no real cloud or local model requests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.contracts.models import EvaluationInput, EvaluationOutput
from app.evaluation.demo import load_synthetic_answers, load_synthetic_input
from app.evaluation.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleEvaluationProvider,
)
from app.evaluation.provider import ProviderExecutionError

TEST_KEY = "offline-test-key"


def live_input(provider: str = "openai") -> EvaluationInput:
    data = load_synthetic_input().model_dump(mode="json")
    data["settings"] = {
        "mode": "live", "provider": provider, "model": "chosen-model", "timeout_seconds": 3
    }
    return EvaluationInput.model_validate(data)


def model_result() -> dict[str, Any]:
    return {"answers": {
        item.criterion_id: {
            "answer": item.answer.model_dump(mode="json"),
            "explanation": item.explanation,
            "evidence": [{
                "start_message_id": evidence.start_message_id,
                "end_message_id": evidence.end_message_id,
            } for evidence in item.evidence],
        } for item in load_synthetic_answers()
    }}


def completion(
    result: dict[str, Any] | None = None, *, text: str | None = None
) -> dict[str, Any]:
    return {
        "choices": [{
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": text if text is not None else json.dumps(
                    model_result() if result is None else result, ensure_ascii=False
                ),
                "refusal": None,
            },
        }],
        "usage": {
            "prompt_tokens": 40, "completion_tokens": 12,
            "completion_tokens_details": {"reasoning_tokens": 5},
        },
    }


def run_provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    config: OpenAICompatibleConfig | None = None,
    evaluation_input: EvaluationInput | None = None,
    on_attempt: Callable[[], None] | None = None,
) -> EvaluationOutput:
    async def run() -> EvaluationOutput:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True, trust_env=False
        ) as client:
            provider = OpenAICompatibleEvaluationProvider(
                config or OpenAICompatibleConfig(api_key=TEST_KEY),
                client=client, on_attempt=on_attempt,
            )
            return await provider.evaluate(evaluation_input or live_input())
    return asyncio.run(run())


def test_openai_request_uses_dynamic_rubric_and_accounts_reasoning_once() -> None:
    observed: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.openai.com/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer " + TEST_KEY
        observed.append(json.loads(request.content))
        return httpx.Response(200, json=completion())

    output = run_provider(handler)
    body = observed[0]
    assert body["model"] == "chosen-model"
    assert body["max_completion_tokens"] == 8192
    assert body["store"] is False
    assert "tools" not in body and "temperature" not in body
    schema = body["response_format"]["json_schema"]
    assert body["response_format"]["type"] == "json_schema" and schema["strict"] is True
    assert schema["schema"]["properties"]["answers"]["required"] == [
        criterion.id for criterion in live_input().checklist.criteria
    ]
    assert "0.7" in body["messages"][0]["content"]
    user_payload = json.loads(body["messages"][1]["content"])
    assert "conversation_id" not in user_payload["untrusted_dialogue"]
    assert "audio_url" not in user_payload["untrusted_dialogue"]
    assert len(output.answers) == 8
    assert output.metadata.mode == "live"
    call = output.metadata.calls[0]
    assert call.input_tokens == 40 and call.output_tokens == 12
    assert call.cost_usd is None and call.latency_ms >= 0


def test_new_criterion_is_generated_without_eight_id_hardcoding() -> None:
    raw = live_input().model_dump(mode="json")
    extra = dict(raw["checklist"]["criteria"][0])
    extra.update(id="CUSTOM", title="Новый критерий")
    raw["checklist"]["criteria"].append(extra)
    updated = EvaluationInput.model_validate(raw)
    result = model_result()
    result["answers"]["CUSTOM"] = dict(result["answers"]["C01"])

    def handler(request: httpx.Request) -> httpx.Response:
        schema = json.loads(request.content)["response_format"]["json_schema"]["schema"]
        assert "CUSTOM" in schema["properties"]["answers"]["required"]
        return httpx.Response(200, json=completion(result))

    output = run_provider(handler, evaluation_input=updated)
    assert {answer.criterion_id for answer in output.answers} == {
        criterion.id for criterion in updated.checklist.criteria
    }


@pytest.mark.parametrize("model", ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "other-schema-model"])
def test_groq_keeps_identity_strict_schema_and_own_request_parameters(model: str) -> None:
    case = live_input("groq")
    case = case.model_copy(update={"settings": case.settings.model_copy(update={"model": model})})
    observed = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer " + TEST_KEY
        body = json.loads(request.content)
        observed.append(body)
        assert body["model"] == model
        assert body["response_format"]["json_schema"]["strict"] is True
        assert body["max_completion_tokens"] == 4096
        assert body["n"] == 1 and body["stream"] is False
        assert not {"store", "max_tokens", "reasoning_format", "tools"} & body.keys()
        if model.startswith("openai/gpt-oss-"):
            assert body["reasoning_effort"] == "medium"
            assert body["include_reasoning"] is False
        else:
            assert "reasoning_effort" not in body and "include_reasoning" not in body
        return httpx.Response(200, json=completion())

    output = run_provider(handler, evaluation_input=case, config=OpenAICompatibleConfig(
        provider="groq", api_key=TEST_KEY, max_output_tokens=4096,
    ))
    assert len(observed) == 1 and len(output.answers) == 8
    assert output.metadata.provider == "groq" and output.metadata.model == model
    assert output.metadata.pipeline_version == "groq-chat-v1"
    assert output.metadata.calls[0].provider == "groq"
    assert output.metadata.calls[0].output_tokens == 12  # Includes reasoning once.
    assert output.metadata.calls[0].cost_usd is None
    assert output.answers[1].answer.type == "na"
    assert all(e.conversation_version_id == case.conversation.id for a in output.answers for e in a.evidence)


@pytest.mark.parametrize("override", [
    {"api_key": None}, {"base_url": "https://example.org/v1"}, {"response_format": "json_object"},
])
def test_groq_rejects_missing_key_custom_endpoint_or_non_strict_mode(override: dict) -> None:
    values = {"provider": "groq", "api_key": TEST_KEY, **override}
    with pytest.raises(ValueError):
        OpenAICompatibleConfig(**values)


@pytest.mark.parametrize("status", [302, 429])
def test_groq_failure_has_one_recorded_attempt_without_redirect_or_fallback(status: int) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, headers={"Location": "https://example.org/receive"},
                              json={"error": "private upstream detail"})

    with pytest.raises(ProviderExecutionError) as caught:
        run_provider(handler, evaluation_input=live_input("groq"), config=OpenAICompatibleConfig(
            provider="groq", api_key=TEST_KEY, max_attempts=1,
        ))
    assert len(requests) == 1
    assert caught.value.status_code == status
    assert len(caught.value.calls) == 1 and caught.value.calls[0].provider == "groq"
    assert "private upstream detail" not in str(caught.value)


@pytest.mark.parametrize("base_url", [
    "http://localhost:11434/v1", "http://127.0.0.1:8001/v1/", "http://[::1]:8080/v1",
])
def test_local_json_mode_is_explicit_and_validated(base_url: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == base_url.rstrip("/") + "/chat/completions"
        assert "Authorization" not in request.headers
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert body["max_tokens"] == 8192
        assert "JSON object satisfying this schema" in body["messages"][0]["content"]
        assert "C04" in body["messages"][0]["content"]
        return httpx.Response(200, json=completion())

    output = run_provider(
        handler,
        config=OpenAICompatibleConfig(
            provider="local", base_url=base_url, response_format="json_object"
        ),
        evaluation_input=live_input("local"),
    )
    assert output.metadata.provider == "local"


@pytest.mark.parametrize("base_url", [
    "https://example.com/v1", "http://127.0.0.2/v1", "http://localhost.evil.test/v1",
    "http://user:synthetic@localhost/v1", "http://localhost/v1?option=synthetic",
    "http://localhost/v1#secret", "http://localhost/v1?","ftp://localhost/v1",
    "http://localhost/other", "http://localhost:0/v1", "http://localhost:99999/v1",
    "http://localhost/v1\n", "http://[::1%25eth0]/v1",
])
def test_non_loopback_or_credential_urls_are_rejected_without_echo(base_url: str) -> None:
    with pytest.raises(ValueError) as caught:
        OpenAICompatibleConfig(provider="local", base_url=base_url)
    assert base_url not in str(caught.value)
    assert "secret" not in str(caught.value)


def test_openai_cannot_redirect_credentials_to_custom_endpoint() -> None:
    with pytest.raises(ValueError):
        OpenAICompatibleConfig(api_key=TEST_KEY, base_url="https://example.com/v1")
    with pytest.raises(ValueError):
        OpenAICompatibleConfig(api_key=TEST_KEY, response_format="json_object")
    assert TEST_KEY not in repr(OpenAICompatibleConfig(api_key=TEST_KEY))


def test_no_automatic_key_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", TEST_KEY)
    with pytest.raises(ValueError, match="explicit"):
        OpenAICompatibleConfig()


@pytest.mark.parametrize("attempts", [0, 4, True, 1.5])
def test_max_attempts_is_bounded(attempts: Any) -> None:
    with pytest.raises(ValueError):
        OpenAICompatibleConfig(api_key=TEST_KEY, max_attempts=attempts)


def test_redirect_is_never_followed_even_if_client_enables_it() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://other.invalid/collect"})

    with pytest.raises(ProviderExecutionError) as caught:
        run_provider(handler)
    assert len(requests) == 1
    assert caught.value.status_code == 302 and len(caught.value.calls) == 1


def test_transient_retry_calls_gate_and_keeps_each_attempt() -> None:
    sent = 0
    gates = 0

    def gate() -> None:
        nonlocal gates
        gates += 1

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        return httpx.Response(429, text="private error") if sent == 1 else httpx.Response(
            200, json=completion()
        )

    output = run_provider(
        handler, config=OpenAICompatibleConfig(
            api_key=TEST_KEY, max_attempts=2, retry_delay_seconds=0
        ), on_attempt=gate,
    )
    assert gates == sent == 2
    assert [call.attempt for call in output.metadata.calls] == [1, 2]
    assert output.metadata.calls[0].input_tokens is None
    assert output.metadata.calls[1].output_tokens == 12


def test_gate_rejection_preserves_prior_calls_and_stops_retry() -> None:
    sent = 0
    gates = 0

    def gate() -> None:
        nonlocal gates
        gates += 1
        if gates > 1:
            raise RuntimeError("private gate details")

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        return httpx.Response(503, text="private upstream details")

    with pytest.raises(ProviderExecutionError) as caught:
        run_provider(
            handler, config=OpenAICompatibleConfig(
                api_key=TEST_KEY, max_attempts=3, retry_delay_seconds=0
            ), on_attempt=gate,
        )
    assert caught.value.code == "ATTEMPT_BLOCKED"
    assert gates == 2 and sent == 1 and len(caught.value.calls) == 1
    assert "private" not in str(caught.value)


def test_auth_error_is_not_retried_and_response_body_is_not_exposed() -> None:
    sent = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        return httpx.Response(401, json={"error": {"message": "private transcript " + TEST_KEY}})

    with pytest.raises(ProviderExecutionError) as caught:
        run_provider(handler, config=OpenAICompatibleConfig(api_key=TEST_KEY, max_attempts=3))
    assert sent == 1 and caught.value.status_code == 401
    assert len(caught.value.calls) == 1
    assert "private" not in str(caught.value) and TEST_KEY not in str(caught.value)


@pytest.mark.parametrize("mutation,expected_code", [
    ("refusal", "MODEL_REFUSAL"),
    ("length", "TRUNCATED_RESPONSE"),
    ("content_filter", "INCOMPLETE_RESPONSE"),
    ("tool_calls", "UNEXPECTED_TOOL_CALL"),
])
def test_unusable_completions_fail_without_returning_fake_answers(
    mutation: str, expected_code: str
) -> None:
    body = completion()
    message = body["choices"][0]["message"]
    if mutation == "refusal":
        message["refusal"] = "private refusal text"
    elif mutation == "tool_calls":
        message["tool_calls"] = [{"function": {"name": "unsafe"}}]
    else:
        body["choices"][0]["finish_reason"] = mutation
    with pytest.raises(ProviderExecutionError) as caught:
        run_provider(lambda request: httpx.Response(200, json=body))
    assert caught.value.code == expected_code
    assert caught.value.calls[0].output_tokens == 12
    assert "private" not in str(caught.value)


@pytest.mark.parametrize("mutation", ["missing", "invalid_scale", "invalid_na", "bad_evidence", "extra"])
def test_structural_failures_are_not_successful_evaluations(mutation: str) -> None:
    result = model_result()
    if mutation == "missing":
        del result["answers"]["C04"]
    elif mutation == "invalid_scale":
        result["answers"]["C04"]["answer"]["value"] = 17
    elif mutation == "invalid_na":
        result["answers"]["C01"]["answer"] = {"type": "na", "reason": "not valid"}
    elif mutation == "bad_evidence":
        result["answers"]["C04"]["evidence"][0]["start_message_id"] = "nonexistent"
    else:
        result["answers"]["C04"]["evidence"][0]["conversation_version_id"] = "invented"
    with pytest.raises(ProviderExecutionError) as caught:
        run_provider(lambda request: httpx.Response(200, json=completion(result)))
    assert caught.value.code == "INVALID_RESPONSE"
    assert len(caught.value.calls) == 1


def test_invalid_json_is_safe_and_usage_unknown() -> None:
    with pytest.raises(ProviderExecutionError) as caught:
        run_provider(lambda request: httpx.Response(200, text="private incomplete JSON"))
    assert caught.value.code == "INVALID_RESPONSE"
    assert caught.value.calls[0].input_tokens is None
    assert "private" not in str(caught.value)


def test_structured_response_retry_records_usage_of_invalid_attempt() -> None:
    sent = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        body = completion(text="not JSON") if sent == 1 else completion()
        return httpx.Response(200, json=body)

    result = run_provider(handler, config=OpenAICompatibleConfig(
        api_key=TEST_KEY, max_attempts=2, retry_delay_seconds=0
    ))
    assert len(result.metadata.calls) == 2
    assert sum(call.output_tokens or 0 for call in result.metadata.calls) == 24


def test_timeout_is_sanitized_and_accounted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private upstream content", request=request)

    with pytest.raises(ProviderExecutionError) as caught:
        run_provider(handler)
    assert caught.value.code == "PROVIDER_TIMEOUT"
    assert len(caught.value.calls) == 1 and caught.value.calls[0].cost_usd is None
    assert "private" not in str(caught.value)


def test_input_limit_prevents_http_and_attempt_gate() -> None:
    def forbidden(*args: Any) -> Any:
        pytest.fail("No request or gate should run for oversized input")

    with pytest.raises(ProviderExecutionError) as caught:
        run_provider(forbidden, config=OpenAICompatibleConfig(
            api_key=TEST_KEY, max_input_bytes=1
        ), on_attempt=forbidden)
    assert caught.value.code == "INPUT_TOO_LARGE" and caught.value.calls == ()


def test_response_limit_is_enforced_before_parsing() -> None:
    with pytest.raises(ProviderExecutionError) as caught:
        run_provider(
            lambda request: httpx.Response(200, content=b"x" * 101),
            config=OpenAICompatibleConfig(api_key=TEST_KEY, max_response_bytes=100),
        )
    assert caught.value.code == "RESPONSE_TOO_LARGE" and len(caught.value.calls) == 1


def test_wrong_mode_prevents_http() -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        pytest.fail("Mock input must never reach the live transport")

    with pytest.raises(ProviderExecutionError) as caught:
        run_provider(forbidden, evaluation_input=load_synthetic_input())
    assert caught.value.code == "INVALID_INPUT" and caught.value.calls == ()


def test_unknown_usage_is_not_invented_from_reasoning_details() -> None:
    body = completion()
    body["usage"] = {"prompt_tokens": True, "completion_tokens_details": {"reasoning_tokens": 7}}
    result = run_provider(lambda request: httpx.Response(200, json=body))
    assert result.metadata.calls[0].input_tokens is None
    assert result.metadata.calls[0].output_tokens is None
    assert result.metadata.calls[0].cost_usd is None
