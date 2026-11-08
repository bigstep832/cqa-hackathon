"""Read models for explicit conversation attribution and operator statistics."""

from typing import Literal

from app.contracts.models import Channel, FrozenModel, Language

Outcome = Literal["successful", "unsuccessful"]


class OperatorDirectoryItem(FrozenModel):
    operator_id: str
    conversation_count: int


class OperatorDirectory(FrozenModel):
    items: tuple[OperatorDirectoryItem, ...]
    unassigned_count: int


class OperatorConversation(FrozenModel):
    version_id: str
    conversation_id: str
    channel: Channel
    language: Language
    outcome: Outcome | None
    duration_seconds: int | None
    evaluation_id: str | None
    effective_score: float | None


class OperatorCriterion(FrozenModel):
    checklist_version_id: str
    criterion_id: str
    title: str
    assessed_count: int
    average_quality: float | None


class OperatorProfile(FrozenModel):
    operator_id: str
    conversation_count: int
    call_count: int
    chat_count: int
    successful_calls: int
    unsuccessful_calls: int
    unknown_calls: int
    measured_calls: int
    total_call_seconds: int
    average_call_seconds: float | None
    timed_call_spans: int
    average_call_span_seconds: float | None
    operator_messages: int
    client_messages: int
    first_reply_count: int
    unanswered_conversations: int
    average_first_reply_seconds: float | None
    scored_conversations: int
    flagged_conversations: int
    average_effective_score: float | None
    criteria: tuple[OperatorCriterion, ...]
    conversations: tuple[OperatorConversation, ...]
