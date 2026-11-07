"""Bind live jobs to the server owner's explicit plan and reserve its limits in SQLite."""

import hashlib
import os
from datetime import date
from pathlib import Path

from evals.live import LivePlan, validate_cases
from sqlalchemy import text

from app.contracts.models import EvaluationCreateRequest, EvaluationInput, EvaluationSettings
from app.evaluation.factory import ProviderConfig, create_provider
from app.evaluation.provider import EvaluationProvider
from app.storage.database import write_transaction


def configured_plan() -> tuple[str, LivePlan]:
    if os.environ.get("LIVE_EVAL_ENABLED") != "true" or not os.environ.get("CQA_LIVE_PLAN"):
        raise ValueError("Live not enabled")
    plan = LivePlan.model_validate_json(Path(os.environ["CQA_LIVE_PLAN"]).read_bytes())
    if not plan.pricing.verified_on <= date.today() <= plan.pricing.valid_until:
        raise ValueError("Live pricing expired")
    if plan.provider != "local" and not os.environ.get("MODEL_API_KEY", "").strip():
        raise ValueError("Live credentials missing")
    digest = hashlib.sha256(plan.model_dump_json().encode()).hexdigest()
    return digest, plan


def resolve_settings(request: EvaluationCreateRequest, plan: LivePlan) -> EvaluationSettings:
    if request.provider not in (None, plan.provider) or request.model not in (None, plan.model):
        raise ValueError("Live model not approved")
    return EvaluationSettings(mode="live", provider=plan.provider, model=plan.model)


def live_provider(case: EvaluationInput, plan_id: str, plan: LivePlan) -> EvaluationProvider:
    validate_cases([case], plan)
    with write_transaction() as db:
        db.execute(
            text("INSERT OR IGNORE INTO live_limits(plan_id,cases,attempts) VALUES(:id,0,0)"),
            {"id": plan_id},
        )
        reserved = db.execute(
            text("""UPDATE live_limits SET cases=cases+1
            WHERE plan_id=:id AND cases<:max"""),
            {"id": plan_id, "max": plan.max_cases},
        )
        if not reserved.rowcount:
            raise ValueError("Live case limit reached")

    def before_attempt() -> None:
        # No refund after ambiguous timeout or process failure; the validated price ceiling
        # multiplied by the persisted attempt cap is bounded by the approved total budget.
        with write_transaction() as db:
            reserved = db.execute(
                text("""UPDATE live_limits SET attempts=attempts+1
                WHERE plan_id=:id AND attempts<:max"""),
                {"id": plan_id, "max": plan.max_requests},
            )
            if not reserved.rowcount:
                raise RuntimeError("Live request limit reached")

    return create_provider(
        ProviderConfig(
            provider=plan.provider,
            api_key=None if plan.provider == "local" else os.environ.get("MODEL_API_KEY"),
            local_base_url=plan.local_base_url,
            local_response_format=plan.local_response_format,
            max_attempts=plan.max_attempts,
            max_output_tokens=plan.max_output_tokens,
        ),
        on_attempt=before_attempt,
    )
