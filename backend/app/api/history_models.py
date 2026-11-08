"""Compact, paginated history for a conversation card."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from app.contracts.models import FrozenModel, NonEmpty


class VersionHistoryItem(FrozenModel):
    id: NonEmpty
    imported_at: AwareDatetime


class EvaluationHistoryItem(FrozenModel):
    id: NonEmpty
    conversation_version_id: NonEmpty
    checklist_version_id: NonEmpty
    mode: Literal["mock", "live"]
    status: Literal["queued", "running", "succeeded", "failed"]
    created_at: AwareDatetime


class HistoryPage(FrozenModel):
    total: Annotated[int, Field(strict=True, ge=0)]
    offset: Annotated[int, Field(strict=True, ge=0)] = 0
    limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 25


class VersionHistoryList(HistoryPage):
    items: tuple[VersionHistoryItem, ...]


class EvaluationHistoryList(HistoryPage):
    items: tuple[EvaluationHistoryItem, ...]
