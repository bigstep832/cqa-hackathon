"""Отдельный live runner: проверка плана без сети по умолчанию, явный --execute."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, ValidationError, model_validator

from app.contracts.models import (
    ErrorDetail,
    Evaluation,
    EvaluationInput,
    FrozenModel,
    NonEmpty,
)
from app.domain.scoring import score
from app.domain.snapshots import checklist_content_hash, conversation_content_hash

PositiveInt = Annotated[int, Field(strict=True, ge=1)]
Money = Annotated[Decimal, Field(ge=0, allow_inf_nan=False, max_digits=18, decimal_places=9)]


class PricingCeiling(FrozenModel):
    """Проверенные вручную верхние тарифы и лимиты ИМЕННО выбранной модели.

    Резервируется весь input context, а не эвристический подсчёт токенов текста.
    Цены должны покрывать максимальный тарифный tier, включая thinking output.
    Это условная оценка при верных тарифах, не лимит биллинга провайдера.
    """

    input_token_limit: Annotated[int, Field(strict=True, ge=1, le=10_000_000)]
    output_token_limit: Annotated[int, Field(strict=True, ge=1, le=10_000_000)]
    input_usd_per_million: Money
    output_usd_per_million: Money
    source_url: Annotated[str, Field(pattern=r"^https://[^\s]+$")]
    verified_on: date
    valid_until: date

    @model_validator(mode="after")
    def valid_dates(self) -> PricingCeiling:
        if self.valid_until < self.verified_on:
            raise ValueError("Pricing validity ends before verification")
        return self


class LivePlan(FrozenModel):
    provider: Literal["gemini", "openai", "groq", "local"]
    model: NonEmpty
    local_base_url: str | None = None
    local_response_format: Literal["json_schema", "json_object"] = "json_schema"
    data_transfer_approved: Literal[True]
    dataset_group_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    max_cases: Annotated[int, Field(strict=True, ge=1, le=10_000)]
    max_requests: Annotated[int, Field(strict=True, ge=1, le=30_000)]
    max_attempts: Annotated[int, Field(strict=True, ge=1, le=3)] = 1
    max_output_tokens: Annotated[int, Field(strict=True, ge=1, le=100_000)] = 8192
    budget_usd: Money
    pricing: PricingCeiling

    @property
    def reservation_per_request_usd(self) -> Decimal:
        return (
            self.pricing.input_token_limit * self.pricing.input_usd_per_million
            + self.max_output_tokens * self.pricing.output_usd_per_million
        ) / 1_000_000

    @model_validator(mode="after")
    def check_ceiling(self) -> LivePlan:
        if self.provider != "local" and self.local_base_url is not None:
            raise ValueError("Custom endpoint is allowed only for local models")
        if self.provider == "local" and self.local_base_url is None:
            raise ValueError("Local model requires an explicit loopback endpoint")
        if self.max_output_tokens > self.pricing.output_token_limit:
            raise ValueError("Output cap exceeds the verified model limit")
        if self.reservation_per_request_usd * self.max_requests > self.budget_usd:
            raise ValueError("Budget cannot cover every authorized request at the pricing ceiling")
        return self


def validate_cases(cases: list[EvaluationInput], plan: LivePlan) -> list[EvaluationInput]:
    """До первого запроса проверяем весь выбранный batch и его snapshots."""
    selected = cases[:plan.max_cases]
    if not selected:
        raise ValueError("Input batch is empty")
    seen: set[tuple[str, str]] = set()
    snapshot_hashes: dict[tuple[str, str], str] = {}
    for case in selected:
        key = (case.conversation.id, case.checklist.id)
        if key in seen:
            raise ValueError("Duplicate conversation/checklist pair")
        seen.add(key)
        if (
            case.settings.mode != "live"
            or case.settings.provider != plan.provider
            or case.settings.model != plan.model
        ):
            raise ValueError("Input settings must match the authorized live model")
        if case.conversation.dataset_group_id not in plan.dataset_group_ids:
            raise ValueError("Input dataset is outside the approved scope")
        if case.conversation.content_hash != conversation_content_hash(case.conversation):
            raise ValueError("Conversation snapshot hash mismatch")
        for kind, snapshot_id, digest in (
            ("conversation", case.conversation.id, case.conversation.content_hash),
            ("checklist", case.checklist.id, checklist_content_hash(case.checklist)),
        ):
            identity = (kind, snapshot_id)
            if identity in snapshot_hashes and snapshot_hashes[identity] != digest:
                raise ValueError("Snapshot ID reused for different content")
            snapshot_hashes[identity] = digest
    return selected


def private_output(path: Path, root: Path | None = None) -> Path:
    """Не позволяет сохранить реальные результаты в публичную часть репозитория."""
    repository = root or Path(__file__).resolve().parents[1]
    # Do not resolve the allowed root: a private-directory symlink must not escape the repo.
    allowed = repository.resolve() / "artifacts" / "private"
    target = path.resolve()
    if target == allowed or not target.is_relative_to(allowed):
        raise ValueError("Use a new run directory below artifacts/private")
    if target.exists():
        raise ValueError("Run directory already exists; overwriting or resuming is not supported")
    return target


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class RequestLedger:
    """Резерв записывается ДО отправки, повторы и неизвестные расходы не возвращаются."""

    def __init__(self, plan: LivePlan, output: Path) -> None:
        self.plan = plan
        self.output = output
        self.attempts_reserved = 0

    def before_attempt(self) -> None:
        if self.attempts_reserved >= self.plan.max_requests:
            raise RuntimeError("Request limit exhausted")
        self.attempts_reserved += 1
        self.save()

    def save(self) -> None:
        write_json(self.output / "ledger.json", {
            "attempts_reserved": self.attempts_reserved,
            "max_requests": self.plan.max_requests,
            "reserved_usd": str(self.plan.reservation_per_request_usd * self.attempts_reserved),
            "budget_usd": str(self.plan.budget_usd),
            "actual_cost_usd": None,
            "note": "Reservations use manually verified price ceilings; actual provider billing is unknown. No refunds for retries, failures or timeouts.",
        })


async def execute(
    selected: list[EvaluationInput], plan: LivePlan, output: Path, *, api_key: str,
    client: object = None,
) -> list[Evaluation]:
    # Импорт не создаёт клиента и не выполняет сеть; client используется только offline tests.
    import httpx

    from app.evaluation.factory import ProviderConfig, create_provider
    from app.evaluation.provider import ProviderExecutionError

    if client is not None and not isinstance(client, httpx.AsyncClient):
        raise TypeError("client must be an httpx.AsyncClient")
    selected = validate_cases(selected, plan)
    if plan.provider != "local" and not api_key.strip():
        raise ValueError("MODEL_API_KEY is required")
    if not plan.pricing.verified_on <= date.today() <= plan.pricing.valid_until:
        raise ValueError("Pricing verification is not valid today")
    if output.exists():
        raise ValueError("Run directory already exists")
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "plan.json", plan.model_dump(mode="json"))
    write_json(output / "inputs.json", [case.model_dump(mode="json") for case in selected])
    ledger = RequestLedger(plan, output)
    ledger.save()
    provider = create_provider(
        ProviderConfig(provider=plan.provider, api_key=api_key,
                       local_base_url=plan.local_base_url,
                       local_response_format=plan.local_response_format,
                       max_attempts=plan.max_attempts,
                       max_output_tokens=plan.max_output_tokens),
        client=client, on_attempt=ledger.before_attempt,
    )
    predictions: list[Evaluation] = []
    attempts: list[dict[str, object]] = []
    for case in selected:
        evaluation_id = str(uuid4())
        created_at = datetime.now(UTC)
        try:
            result = await provider.evaluate(case)
        except ProviderExecutionError as error:
            evaluation = Evaluation(
                id=evaluation_id, conversation_version_id=case.conversation.id,
                checklist_version_id=case.checklist.id, created_at=created_at,
                status="failed", failure=ErrorDetail(code=error.code, message=str(error)),
            )
            calls = error.calls
        else:
            result_score = score(case.checklist, result.answers)
            evaluation = Evaluation(
                id=evaluation_id, conversation_version_id=case.conversation.id,
                checklist_version_id=case.checklist.id, created_at=created_at,
                status="succeeded", output=result,
                original_score=result_score, effective_score=result_score,
            )
            calls = result.metadata.calls
        predictions.append(evaluation)
        attempts.append({"evaluation_id": evaluation_id,
                         "calls": [call.model_dump(mode="json") for call in calls]})
        write_json(output / "predictions.json", [item.model_dump(mode="json") for item in predictions])
        write_json(output / "attempts.json", attempts)
    return predictions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True, help="JSON array of EvaluationInput")
    parser.add_argument("--plan", type=Path, required=True, help="Approved local LivePlan JSON")
    parser.add_argument("--output", type=Path, required=True, help="New artifacts/private/run-name directory")
    parser.add_argument("--execute", action="store_true", help="Actually send data; otherwise validate without network")
    args = parser.parse_args()
    try:
        plan = LivePlan.model_validate_json(args.plan.read_text(encoding="utf-8-sig"))
        raw = args.inputs.read_bytes()
        document = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(document, list):
            raise ValueError("Inputs must be a JSON array")
        cases = [EvaluationInput.model_validate(item) for item in document]
        selected = validate_cases(cases, plan)
        output = private_output(args.output)
        if not plan.pricing.verified_on <= date.today() <= plan.pricing.valid_until:
            raise ValueError("Pricing verification is not valid today")
        summary = {
            "mode": "execute" if args.execute else "validate_only_no_network",
            "provider": plan.provider, "model": plan.model,
            "selected_cases": len(selected), "omitted_cases": len(cases) - len(selected),
            "max_requests": plan.max_requests,
            "worst_case_reservation_usd": str(plan.reservation_per_request_usd * plan.max_requests),
            "input_file_sha256": hashlib.sha256(raw).hexdigest(),
        }
        if not args.execute:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        if os.environ.get("LIVE_EVAL_ENABLED") != "true":
            raise ValueError("LIVE_EVAL_ENABLED=true is required for --execute")
        predictions = asyncio.run(execute(selected, plan, output, api_key=os.environ.get("MODEL_API_KEY", "")))
        write_json(output / "manifest.json", summary)
        failed = sum(item.status == "failed" for item in predictions)
        print(json.dumps({**summary, "succeeded": len(predictions) - failed, "failed": failed}, indent=2))
        return 1 if failed else 0
    except ValidationError:
        # Pydantic's default text contains input values, possibly private transcripts.
        print("Invalid plan/input schema; inspect the local files. No validation input is logged.")
        return 2
    except (OSError, ValueError) as error:
        # JSON parser errors and file contents must not escape into console logs.
        if isinstance(error, (json.JSONDecodeError, UnicodeError)):
            print("Invalid JSON encoding or structure in a local input file.")
        else:
            print(f"Run not completed: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
