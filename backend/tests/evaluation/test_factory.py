import asyncio

import httpx
import pytest

from app.contracts.models import EvaluationSettings
from app.evaluation.demo import load_synthetic_input
from app.evaluation.factory import ProviderConfig, create_provider
from app.evaluation.mock import MockEvaluationProvider
from app.evaluation.provider import MockInputNotSupported


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


@pytest.mark.parametrize("name", ["gemini", "openai", "local"])
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
