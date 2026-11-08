import asyncio
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from evals.live import LivePlan, RequestLedger, execute, main, private_output, validate_cases
from pydantic import ValidationError

from app.contracts.models import EvaluationInput, EvaluationSettings
from app.evaluation.compact import unpack_dialogue_payload
from app.evaluation.demo import load_synthetic_answers, load_synthetic_input
from app.evaluation.structured import build_system_instruction, build_user_payload


def plan_data() -> dict:
    return {
        "provider": "gemini", "model": "offline-test-model", "data_transfer_approved": True,
        "dataset_group_ids": ["SYNTHETIC"], "max_cases": 2, "max_requests": 2,
        "max_attempts": 1, "max_output_tokens": 1000, "budget_usd": "3",
        "pricing": {
            "input_token_limit": 1_000_000, "output_token_limit": 2000,
            "input_usd_per_million": "1", "output_usd_per_million": "2",
            "source_url": "https://example.org/synthetic-test-pricing",
            "verified_on": date.today().isoformat(), "valid_until": date.today().isoformat(),
        },
    }


def live_input() -> EvaluationInput:
    return load_synthetic_input().model_copy(update={"settings": EvaluationSettings(
        mode="live", provider="gemini", model="offline-test-model",
    )})


def valid_response() -> dict:
    answers = {
        item.criterion_id: {
            "answer": item.answer.model_dump(mode="json"), "explanation": item.explanation,
            "evidence": [e.model_dump(mode="json", exclude={"conversation_version_id"}) for e in item.evidence],
        }
        for item in load_synthetic_answers()
    }
    return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{
        "text": json.dumps({"answers": answers}, ensure_ascii=False),
    }]}}], "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 200, "totalTokenCount": 300}}


def test_budget_covers_full_input_limit_and_every_retry() -> None:
    data = plan_data()
    plan = LivePlan.model_validate(data)
    assert plan.reservation_per_request_usd == Decimal("1.002")
    data["budget_usd"] = "2.003"
    with pytest.raises(ValidationError, match="Budget"):
        LivePlan.model_validate(data)


def test_data_authorization_and_model_are_required() -> None:
    data = plan_data()
    data["data_transfer_approved"] = False
    with pytest.raises(ValidationError):
        LivePlan.model_validate(data)
    plan = LivePlan.model_validate(plan_data())
    with pytest.raises(ValueError, match="settings"):
        validate_cases([load_synthetic_input()], plan)
    case = live_input()
    altered = case.model_copy(update={"conversation": case.conversation.model_copy(
        update={"dataset_group_id": "UNAPPROVED"},
    )})
    with pytest.raises(ValueError, match="scope"):
        validate_cases([altered], plan)


def test_snapshot_conflict_and_duplicate_batch_fail_before_calls() -> None:
    case = live_input()
    plan = LivePlan.model_validate(plan_data())
    with pytest.raises(ValueError, match="Duplicate"):
        validate_cases([case, case], plan)
    invalid = case.model_copy(update={"conversation": case.conversation.model_copy(
        update={"content_hash": "0" * 64},
    )})
    with pytest.raises(ValueError, match="hash"):
        validate_cases([invalid], plan)


def test_output_must_be_new_and_private(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="artifacts/private"):
        private_output(tmp_path / "public", tmp_path)
    target = tmp_path / "artifacts" / "private" / "new-run"
    assert private_output(target, tmp_path) == target.resolve()
    target.mkdir(parents=True)
    with pytest.raises(ValueError, match="exists"):
        private_output(target, tmp_path)


def test_reservation_is_persisted_before_request_and_never_refunded(tmp_path: Path) -> None:
    plan = LivePlan.model_validate(plan_data())
    ledger = RequestLedger(plan, tmp_path)
    ledger.before_attempt()
    assert json.loads((tmp_path / "ledger.json").read_text())["attempts_reserved"] == 1
    ledger.before_attempt()
    with pytest.raises(RuntimeError, match="limit"):
        ledger.before_attempt()
    stored = json.loads((tmp_path / "ledger.json").read_text())
    assert stored["reserved_usd"] == "2.004"
    assert stored["actual_cost_usd"] is None


@pytest.mark.parametrize("profile", [None, "cqa-rubric-v1", "cqa-rubric-v4-candidate"])
def test_runner_scores_valid_output_and_saves_original_result(tmp_path: Path, profile) -> None:
    output = tmp_path / "valid"
    calls = []
    data = plan_data()
    if profile is not None:
        data["instruction_profile"] = profile
    expected_profile = profile or "cqa-rubric-v1"

    def transport(request: httpx.Request) -> httpx.Response:
        assert json.loads((output / "ledger.json").read_text())["attempts_reserved"] == 1
        sent = json.loads(request.content)["systemInstruction"]["parts"][0]["text"]
        assert sent == build_system_instruction(live_input(), profile=expected_profile)
        calls.append(request)
        return httpx.Response(200, json=valid_response())

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            predictions = await execute(
                [live_input()], LivePlan.model_validate(data), output,
                api_key="synthetic-test-credential", client=client,
            )
        result = predictions[0]
        assert result.status == "succeeded"
        assert result.original_score is not None
        assert result.original_score.total == Decimal("85.38")
        assert result.effective_score == result.original_score
        assert result.output is not None and result.output.metadata.mode == "live"
        assert result.output.metadata.instruction_version == expected_profile

    asyncio.run(run())
    assert len(calls) == 1
    assert (output / "attempts.json").exists()
    assert "synthetic-test-credential" not in (output / "plan.json").read_text()
    assert json.loads((output / "plan.json").read_text())["instruction_profile"] == expected_profile


@pytest.mark.parametrize("profile", ["unknown", "", None, 1])
def test_unknown_instruction_profile_rejected_before_execution(profile) -> None:
    with pytest.raises(ValidationError):
        LivePlan.model_validate({**plan_data(), "instruction_profile": profile})


def test_global_call_cap_includes_retry_and_keeps_failed_without_score(tmp_path: Path) -> None:
    data = plan_data()
    data.update(max_requests=1, max_attempts=2)
    plan = LivePlan.model_validate(data)
    calls = []

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, json={"error": {"message": "synthetic-private-upstream-detail"}})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            predictions = await execute(
                [live_input()], plan, tmp_path / "failed",
                api_key="synthetic-test-credential", client=client,
            )
        assert predictions[0].status == "failed"
        assert predictions[0].original_score is None
        assert predictions[0].output is None
        assert "synthetic-private-upstream-detail" not in predictions[0].model_dump_json()

    asyncio.run(run())
    assert len(calls) == 1
    recorded = json.loads((tmp_path / "failed" / "attempts.json").read_text())
    assert len(recorded[0]["calls"]) == 1


@pytest.mark.parametrize("encoding", ["canonical", "compact-v1"])
def test_groq_free_plan_executes_through_common_scoring_and_ledger(tmp_path: Path, encoding: str) -> None:
    data = plan_data()
    data.update(provider="groq", model="openai/gpt-oss-120b", budget_usd="0", max_requests=1)
    data.update(request_encoding=encoding, instruction_profile="cqa-rubric-v4-candidate")
    data["pricing"].update(input_usd_per_million="0", output_usd_per_million="0")
    plan = LivePlan.model_validate(data)
    case = live_input().model_copy(update={"settings": EvaluationSettings(
        mode="live", provider=plan.provider, model=plan.model,
    )})
    output = tmp_path / "groq"
    requests = []

    def transport(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.groq.com"
        assert json.loads((output / "ledger.json").read_text())["attempts_reserved"] == 1
        requests.append(request)
        if encoding == "compact-v1":
            body = json.loads(request.content)
            assert unpack_dialogue_payload(json.loads(body["messages"][1]["content"])) == json.loads(build_user_payload(case))
        content = valid_response()["candidates"][0]["content"]["parts"][0]["text"]
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {"content": content},
        }]})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            results = await execute([case], plan, output, api_key="offline-key", client=client)
        assert results[0].status == "succeeded"
        assert results[0].original_score.total == Decimal("85.38")
        assert results[0].output.metadata.provider == "groq"
        assert results[0].output.metadata.instruction_version == "cqa-rubric-v4-candidate"
        assert results[0].output.metadata.pipeline_version.endswith("+cqa-compact-v1") == (encoding == "compact-v1")

    asyncio.run(run())
    assert len(requests) == 1
    assert json.loads((output / "ledger.json").read_text())["reserved_usd"] == "0"


@pytest.mark.parametrize("encoding", ["unknown", "", None, 1, "compact-v1"])
def test_invalid_or_unsupported_encoding_rejected_before_execution(encoding) -> None:
    with pytest.raises(ValidationError):
        LivePlan.model_validate({**plan_data(), "request_encoding": encoding})  # Gemini has no compact codec.


def test_local_runner_supports_server_without_api_key(tmp_path: Path) -> None:
    data = plan_data()
    data.update(provider="local", local_base_url="http://127.0.0.1:11434/v1",
                local_response_format="json_object")
    case = live_input().model_copy(update={"settings": EvaluationSettings(
        mode="live", provider="local", model="offline-test-model",
    )})

    def transport(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        assert "authorization" not in request.headers
        content = valid_response()["candidates"][0]["content"]["parts"][0]["text"]
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {"content": content},
        }]})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            predictions = await execute([case], LivePlan.model_validate(data), tmp_path / "local",
                                        api_key="", client=client)
        assert predictions[0].status == "succeeded"
        assert predictions[0].output is not None
        assert predictions[0].output.metadata.provider == "local"

    asyncio.run(run())


def test_cli_default_only_validates_without_network_or_key(tmp_path: Path, monkeypatch, capsys) -> None:
    inputs = tmp_path / "inputs.json"
    plan = tmp_path / "plan.json"
    inputs.write_text(json.dumps([live_input().model_dump(mode="json")]), encoding="utf-8")
    plan.write_text(json.dumps(plan_data()), encoding="utf-8")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setattr("evals.live.private_output", lambda path: path)
    monkeypatch.setattr("sys.argv", ["live", "--inputs", str(inputs), "--plan", str(plan), "--output", str(tmp_path / "out")])
    assert main() == 0
    assert "validate_only_no_network" in capsys.readouterr().out
    assert not (tmp_path / "out").exists()
