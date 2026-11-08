import asyncio
import json

import httpx
import pytest

from app.contracts.models import EvaluationSettings
from app.evaluation.compact import (
    RequestEncoding,
    build_compact_system_instruction,
    unpack_dialogue_payload,
)
from app.evaluation.demo import load_synthetic_answers, load_synthetic_input
from app.evaluation.factory import ProviderConfig, create_provider
from app.evaluation.mock import MockEvaluationProvider
from app.evaluation.provider import MockInputNotSupported
from app.evaluation.structured import (
    CANDIDATE_INSTRUCTION,
    build_system_instruction,
    build_user_payload,
)


def test_default_factory_is_offline_synthetic_mock() -> None:
    calls = []

    def forbidden_request(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("Mock must not use any HTTP transport")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden_request)) as client:
            provider = create_provider(client=client)
            assert isinstance(provider, MockEvaluationProvider)
            result = await provider.evaluate(load_synthetic_input())
            assert result.metadata.mode == "mock"
            assert result.metadata.calls == ()

    asyncio.run(scenario())
    assert calls == []


def test_mock_cannot_run_as_live_or_analyze_organizer_data() -> None:
    original = load_synthetic_input()
    provider = create_provider()
    live = original.model_copy(update={"settings": EvaluationSettings(
        mode="live", provider="gemini", model="not-a-real-model",
    )})
    with pytest.raises(MockInputNotSupported):
        asyncio.run(provider.evaluate(live))
    organizer = original.model_copy(update={"conversation": original.conversation.model_copy(
        update={"source": "organizer", "synthetic": False, "dataset_group_id": "DEV"},
    )})
    with pytest.raises(MockInputNotSupported):
        asyncio.run(provider.evaluate(organizer))


@pytest.mark.parametrize("name", ["gemini", "openai", "groq", "local"])
def test_transport_selection_does_not_call_network(name: str) -> None:
    config = ProviderConfig(
        provider=name,  # type: ignore[arg-type]
        api_key="synthetic-test-credential", local_base_url="http://127.0.0.1:11434/v1" if name == "local" else None,
    )
    provider = create_provider(config)
    assert not isinstance(provider, MockEvaluationProvider)
    assert callable(provider.evaluate)


def test_unknown_provider_is_not_silently_mocked() -> None:
    with pytest.raises(ValueError, match="Unknown"):
        create_provider(ProviderConfig(provider="typo"))  # type: ignore[arg-type]


def test_config_repr_never_includes_api_key() -> None:
    assert "synthetic-test-credential" not in repr(ProviderConfig(api_key="synthetic-test-credential"))


@pytest.mark.parametrize(("name", "encoding"), [
    ("gemini", "canonical"), ("openai", "canonical"), ("groq", "canonical"), ("local", "canonical"),
    ("openai", "compact-v1"), ("groq", "compact-v1"),
])
def test_candidate_is_explicit_and_records_the_instruction_actually_sent(
    name: str, encoding: RequestEncoding,
) -> None:
    source = load_synthetic_input()
    case = source.model_copy(update={"settings": EvaluationSettings(
        mode="live", provider=name, model="synthetic-model",
    )})
    reply = {"answers": {
        answer.criterion_id: {
            "answer": answer.answer.model_dump(mode="json"),
            "explanation": answer.explanation,
            "evidence": [e.model_dump(mode="json", exclude={"conversation_version_id"})
                         for e in answer.evidence],
        } for answer in load_synthetic_answers()
    }}
    sent: list[dict] = []

    def transport(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        text = (body["systemInstruction"]["parts"][0]["text"] if name == "gemini"
                else body["messages"][0]["content"])
        if encoding == "compact-v1":
            assert text == build_compact_system_instruction(case, instruction=CANDIDATE_INSTRUCTION)
            assert unpack_dialogue_payload(json.loads(body["messages"][1]["content"])) == json.loads(
                build_user_payload(case),
            )
            assert "$defs" in body["response_format"]["json_schema"]["schema"]
            assert body["response_format"]["json_schema"]["strict"] is True
        else:
            assert text == build_system_instruction(case, profile="cqa-rubric-v4-candidate")
        if name == "gemini":
            return httpx.Response(200, json={"candidates": [{
                "finishReason": "STOP", "content": {"parts": [{"text": json.dumps(reply)}]},
            }]})
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {"content": json.dumps(reply)},
        }]})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            provider = create_provider(ProviderConfig(
                provider=name,  # type: ignore[arg-type]
                api_key="synthetic-test-credential",
                local_base_url="http://127.0.0.1:11434/v1" if name == "local" else None,
                instruction_profile="cqa-rubric-v4-candidate", max_attempts=1,
                request_encoding=encoding,
            ), client=client)
            output = await provider.evaluate(case)
        assert output.metadata.instruction_version == "cqa-rubric-v4-candidate"
        assert output.metadata.provider == name
        assert output.metadata.pipeline_version.endswith("+cqa-compact-v1") == (encoding == "compact-v1")
        # Changing the instruction must not modify the supplied synthetic model answers.
        assert output.answers == load_synthetic_answers()
        assert len(output.metadata.calls) == 1

    asyncio.run(scenario())
    assert len(sent) == 1


@pytest.mark.parametrize("profile", ["typo", "cqa-rubric-v4-candidate"])
def test_factory_does_not_report_fixture_as_candidate_eval(profile: str) -> None:
    with pytest.raises(ValueError):
        create_provider(ProviderConfig(instruction_profile=profile))  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["fixture", "local", "gemini"])
def test_compact_encoding_cannot_silently_run_as_canonical(name: str) -> None:
    with pytest.raises(ValueError, match="Compact encoding"):
        create_provider(ProviderConfig(provider=name, request_encoding="compact-v1"))  # type: ignore[arg-type]


def test_unknown_encoding_is_rejected_before_transport() -> None:
    with pytest.raises(ValueError, match="Unknown request encoding"):
        create_provider(ProviderConfig(provider="groq", request_encoding="typo"))  # type: ignore[arg-type]
