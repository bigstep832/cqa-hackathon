"""Bind live jobs to the server owner's explicit plan and reserve its limits in SQLite."""

import asyncio
import hashlib
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
from evals.live import LivePlan, validate_cases
from sqlalchemy import Connection, text

from app.contracts.models import (
    ConversationVersion,
    EvaluationCreateRequest,
    EvaluationInput,
    EvaluationSettings,
    ProviderCall,
)
from app.evaluation.demo import load_synthetic_input
from app.evaluation.factory import ProviderConfig, create_provider
from app.evaluation.provider import EvaluationProvider
from app.storage.conversations import organizer_version_is_current
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


def approved_snapshot(db: Connection, conversation: ConversationVersion, plan: LivePlan) -> bool:
    if conversation.dataset_group_id not in plan.dataset_group_ids:
        return False
    if conversation.source == "synthetic":
        return conversation == load_synthetic_input().conversation
    return conversation.source == "organizer" and organizer_version_is_current(
        db, conversation.dataset_group_id, conversation.id
    )


def outbound_transport() -> httpx.AsyncBaseTransport:
    return httpx.AsyncHTTPTransport(trust_env=False, retries=0)


class DispatchTransport(httpx.AsyncBaseTransport):
    """Persist transport handoff separately from adapter permission and completed usage."""

    def __init__(
        self,
        evaluation_id: str,
        case: EvaluationInput,
        plan_id: str,
        plan: LivePlan,
    ) -> None:
        self.evaluation_id = evaluation_id
        self.case = case
        self.plan_id = plan_id
        self.plan = plan
        self.attempt_deadline = 0.0
        self.reserved = 0
        self.dispatched = 0
        self.transport = outbound_transport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.reserved != self.dispatched + 1:
            raise ValueError("No reservation for this transport dispatch")
        with write_transaction() as db:
            if (
                configured_plan()[0] != self.plan_id
                or not approved_snapshot(db, self.case.conversation, self.plan)
                or asyncio.get_running_loop().time() >= self.attempt_deadline
            ):
                raise ValueError("Attempt is no longer authorized")
            db.execute(
                text("""INSERT INTO evaluation_dispatches
                (evaluation_id,attempt,provider,model,started_at)
                VALUES(:id,:attempt,:provider,:model,:at)"""),
                {
                    "id": self.evaluation_id,
                    "attempt": self.reserved,
                    "provider": self.plan.provider,
                    "model": self.plan.model,
                    "at": datetime.now(UTC).isoformat(),
                },
            )
        self.dispatched = self.reserved
        return await self.transport.handle_async_request(request)

    async def aclose(self) -> None:
        await self.transport.aclose()


class AttemptClient(httpx.AsyncClient):
    """Bound the entire HTTP exchange so the adapter can record a timeout and retry."""

    def __init__(self, transport: DispatchTransport, deadline: float) -> None:
        super().__init__(transport=transport, trust_env=False, follow_redirects=False)
        self.dispatch = transport
        self.job_deadline = deadline

    @asynccontextmanager
    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[httpx.Response]:
        plan = self.dispatch.plan
        index = self.dispatch.dispatched
        # The existing adapters wait 0.25s between attempts; Gemini doubles its backoff.
        backoff = sum(
            0.25 * (2**i if plan.provider == "gemini" else 1)
            for i in range(index, plan.max_attempts - 1)
        )
        remaining = self.job_deadline - asyncio.get_running_loop().time() - backoff - 0.05
        if remaining <= 0 or index >= plan.max_attempts:
            raise ValueError("No time left for an authorized HTTP attempt")
        self.dispatch.attempt_deadline = asyncio.get_running_loop().time() + remaining / (
            plan.max_attempts - index
        )
        try:
            async with asyncio.timeout_at(self.dispatch.attempt_deadline):
                async with super().stream(*args, **kwargs) as response:
                    yield response
        except TimeoutError:
            raise httpx.ReadTimeout("HTTP attempt deadline exceeded") from None


@asynccontextmanager
async def live_provider(
    evaluation_id: str, case: EvaluationInput, plan_id: str, plan: LivePlan, deadline: float
) -> AsyncIterator[EvaluationProvider]:
    validate_cases([case], plan)
    with write_transaction() as db:
        if not approved_snapshot(db, case.conversation, plan):
            raise ValueError("Snapshot outside current approved manifest")
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

    transport = DispatchTransport(evaluation_id, case, plan_id, plan)

    def before_attempt() -> None:
        # No refund after ambiguous timeout or process failure; the validated price ceiling
        # multiplied by the persisted attempt cap is bounded by the approved total budget.
        with write_transaction() as db:
            if configured_plan()[0] != plan_id or not approved_snapshot(
                db, case.conversation, plan
            ):
                raise ValueError("Live plan or organizer manifest changed")
            reserved = db.execute(
                text("""UPDATE live_limits SET attempts=attempts+1
                WHERE plan_id=:id AND attempts<:max"""),
                {"id": plan_id, "max": plan.max_requests},
            )
            if not reserved.rowcount:
                raise RuntimeError("Live request limit reached")

        transport.reserved += 1

    def record_call(call: ProviderCall) -> None:
        with write_transaction() as db:
            db.execute(
                text("""UPDATE evaluation_dispatches SET completed_call=:call
                WHERE evaluation_id=:id AND attempt=:attempt AND provider=:provider AND model=:model
                AND completed_call IS NULL"""),
                {
                    "call": call.model_dump_json(),
                    "id": evaluation_id,
                    "attempt": call.attempt,
                    "provider": call.provider,
                    "model": call.model,
                },
            )

    config = ProviderConfig(
        provider=plan.provider,
        api_key=None if plan.provider == "local" else os.environ.get("MODEL_API_KEY"),
        local_base_url=plan.local_base_url,
        local_response_format=plan.local_response_format,
        max_attempts=plan.max_attempts,
        max_output_tokens=plan.max_output_tokens,
    )
    async with AttemptClient(transport, deadline) as client:
        yield create_provider(config, client=client, on_attempt=before_attempt, on_call=record_call)
