"""Общие неизменяемые контракты. Decimal сериализуется строкой без потери точности."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    AnyHttpUrl,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
    model_validator,
)

NonEmpty: TypeAlias = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
NonNegativeDecimal: TypeAlias = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
Quality: TypeAlias = Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]
FiniteNumber: TypeAlias = Annotated[float, Field(strict=True, allow_inf_nan=False)]
OptionValue: TypeAlias = StrictBool | StrictInt | FiniteNumber | StrictStr
Language: TypeAlias = Literal["RU", "KZ", "EN"]
Channel: TypeAlias = Literal["agent chat", "voice call"]
Speaker: TypeAlias = Literal["client", "operator", "bot", "system"]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


def value_key(value: bool | int | float | str) -> tuple[str, str]:
    """Не смешивать True с 1; 7 и 7.0 означают одну числовую ступень."""
    if isinstance(value, bool):
        return ("boolean", str(value))
    if isinstance(value, (int, float)):
        return ("scale", str(Decimal(str(value)).normalize()))
    return ("choice", value)


class SectionDefinition(FrozenModel):
    id: NonEmpty
    title: NonEmpty


class AnswerOption(FrozenModel):
    value: OptionValue
    label: NonEmpty
    description: NonEmpty
    quality: Quality | None = None
    flagged: StrictBool = False


class CriterionDefinition(FrozenModel):
    id: NonEmpty
    section_id: NonEmpty
    title: NonEmpty
    description: NonEmpty
    kind: Literal["scored", "flag"]
    answer_type: Literal["boolean", "scale", "choice"]
    weight: NonNegativeDecimal | None
    allow_na: StrictBool = False
    na_condition: NonEmpty | None = None
    options: Annotated[tuple[AnswerOption, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_configuration(self) -> CriterionDefinition:
        keys = [value_key(option.value) for option in self.options]
        if len(keys) != len(set(keys)):
            raise ValueError("Option values must be unique")
        if any(key[0] != self.answer_type for key in keys):
            raise ValueError("Option value type must match answer_type")
        if self.answer_type == "boolean" and {key[1] for key in keys} != {"True", "False"}:
            raise ValueError("Boolean criteria require exactly true and false")
        if self.answer_type == "scale" and max(Decimal(key[1]) for key in keys) <= 0:
            raise ValueError("Scale must have a positive maximum")
        if self.allow_na != (self.na_condition is not None):
            raise ValueError("N/A must have an explicit condition exactly when enabled")
        if self.kind == "scored":
            if self.weight is None or any(option.quality is None for option in self.options):
                raise ValueError("Scored criteria require weight and every option's quality")
            if any(option.flagged for option in self.options):
                raise ValueError("Scored options cannot also be violation flags")
        elif self.weight is not None or any(option.quality is not None for option in self.options):
            raise ValueError("Flags must have neither weight nor quality")
        return self


class ChecklistDefinition(FrozenModel):
    name: NonEmpty
    sections: Annotated[tuple[SectionDefinition, ...], Field(min_length=1)]
    criteria: Annotated[tuple[CriterionDefinition, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_ids(self) -> ChecklistDefinition:
        section_ids = [section.id for section in self.sections]
        criterion_ids = [criterion.id for criterion in self.criteria]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("Section IDs must be unique")
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("Criterion IDs must be unique")
        if any(criterion.section_id not in section_ids for criterion in self.criteria):
            raise ValueError("Every criterion must reference an existing section")
        return self


class ChecklistVersion(ChecklistDefinition):
    id: NonEmpty
    checklist_id: NonEmpty
    version: Annotated[int, Field(strict=True, ge=1)]
    created_at: AwareDatetime


class ChecklistCreateRequest(ChecklistDefinition):
    pass


class ChecklistVersionCreateRequest(ChecklistDefinition):
    pass


class ChecklistSummary(FrozenModel):
    checklist_id: NonEmpty
    latest_version_id: NonEmpty
    name: NonEmpty
    version: Annotated[int, Field(strict=True, ge=1)]


class ChecklistList(FrozenModel):
    items: tuple[ChecklistSummary, ...]


class Message(FrozenModel):
    message_id: NonEmpty
    speaker: Speaker
    text: Annotated[str, Field(min_length=1)]
    timestamp_raw: Annotated[str, Field(min_length=1)]
    order: Annotated[int, Field(strict=True, ge=0)]
    audio_offset_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None


class ConversationVersion(FrozenModel):
    id: NonEmpty
    conversation_id: NonEmpty
    source: Literal["synthetic", "organizer", "manual"]
    dataset_group_id: NonEmpty
    language: Language
    channel: Channel
    content_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    imported_at: AwareDatetime
    messages: Annotated[tuple[Message, ...], Field(min_length=1)]
    audio_url: AnyHttpUrl | None = None
    synthetic: StrictBool = False

    @model_validator(mode="after")
    def validate_messages(self) -> ConversationVersion:
        ids = [message.message_id for message in self.messages]
        if len(ids) != len(set(ids)):
            raise ValueError("Message IDs must be unique within a conversation version")
        if [message.order for message in self.messages] != list(range(len(self.messages))):
            raise ValueError("Message order must preserve the contiguous original array order")
        if self.synthetic != (self.source == "synthetic"):
            raise ValueError("Synthetic data must be explicitly marked with synthetic source")
        return self


class ConversationSummary(FrozenModel):
    conversation_id: NonEmpty
    latest_version_id: NonEmpty
    language: Language
    channel: Channel
    message_count: Annotated[int, Field(strict=True, ge=0)]
    has_audio: StrictBool


class ConversationList(FrozenModel):
    items: tuple[ConversationSummary, ...]
    total: Annotated[int, Field(strict=True, ge=0)]
    offset: Annotated[int, Field(strict=True, ge=0)] = 0
    limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 25


class SourceMessage(FrozenModel):
    timestamp: Annotated[str, Field(min_length=1)]
    speaker: Speaker
    text: Annotated[str, Field(min_length=1)]


class SourceConversation(FrozenModel):
    conversation_id: NonEmpty
    channel: Channel
    language: Language
    transcript: Annotated[tuple[SourceMessage, ...], Field(min_length=1)]
    audio_url: AnyHttpUrl | None = None


class DatasetImportRequest(FrozenModel):
    group_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,80}$")]


class ConversationImportRequest(FrozenModel):
    dataset_group_id: NonEmpty
    records: Annotated[tuple[SourceConversation, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_record_ids(self) -> ConversationImportRequest:
        ids = [record.conversation_id for record in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate conversation IDs in one import are ambiguous")
        return self


class ImportResponse(FrozenModel):
    dataset_group_id: NonEmpty
    imported_count: Annotated[int, Field(strict=True, ge=0)]
    unchanged_count: Annotated[int, Field(strict=True, ge=0)]
    conversation_version_ids: tuple[str, ...]


class BooleanAnswer(FrozenModel):
    type: Literal["boolean"] = "boolean"
    value: StrictBool


class ScaleAnswer(FrozenModel):
    type: Literal["scale"] = "scale"
    value: StrictInt | FiniteNumber


class ChoiceAnswer(FrozenModel):
    type: Literal["choice"] = "choice"
    value: NonEmpty


class NotApplicableAnswer(FrozenModel):
    type: Literal["na"] = "na"
    reason: NonEmpty


Answer: TypeAlias = Annotated[
    BooleanAnswer | ScaleAnswer | ChoiceAnswer | NotApplicableAnswer,
    Field(discriminator="type"),
]


class EvidenceRef(FrozenModel):
    conversation_version_id: NonEmpty
    start_message_id: NonEmpty
    end_message_id: NonEmpty


class CriterionAnswer(FrozenModel):
    criterion_id: NonEmpty
    answer: Answer
    explanation: NonEmpty
    evidence: Annotated[tuple[EvidenceRef, ...], Field(min_length=1)]


class ProviderCall(FrozenModel):
    provider: NonEmpty
    model: NonEmpty
    purpose: Literal["evaluate", "verify", "repair"]
    attempt: Annotated[int, Field(strict=True, ge=1)]
    latency_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    input_tokens: Annotated[int, Field(strict=True, ge=0)] | None = None
    output_tokens: Annotated[int, Field(strict=True, ge=0)] | None = None
    cost_usd: NonNegativeDecimal | None = None


class EvaluationMetadata(FrozenModel):
    mode: Literal["mock", "live"]
    provider: NonEmpty
    model: NonEmpty
    instruction_version: NonEmpty
    pipeline_version: NonEmpty
    calls: tuple[ProviderCall, ...]


class EvaluationSettings(FrozenModel):
    mode: Literal["mock", "live"]
    provider: NonEmpty
    model: NonEmpty
    timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=300)] = 60


class EvaluationInput(FrozenModel):
    conversation: ConversationVersion
    checklist: ChecklistVersion
    settings: EvaluationSettings


class EvaluationOutput(FrozenModel):
    answers: Annotated[tuple[CriterionAnswer, ...], Field(min_length=1)]
    metadata: EvaluationMetadata


class CriterionScore(FrozenModel):
    criterion_id: NonEmpty
    applicable: StrictBool
    weight: NonNegativeDecimal | None
    quality: Quality | None
    earned_points: NonNegativeDecimal | None
    normalized_points: NonNegativeDecimal | None
    flagged: StrictBool


class SectionScore(FrozenModel):
    section_id: NonEmpty
    title: NonEmpty
    earned_points: NonNegativeDecimal
    applicable_weight: NonNegativeDecimal
    normalized_points: NonNegativeDecimal | None
    normalized_maximum: NonNegativeDecimal | None


class ScoreResult(FrozenModel):
    total: Annotated[Decimal, Field(ge=0, le=100, allow_inf_nan=False)] | None
    applicable_weight: NonNegativeDecimal
    earned_points: NonNegativeDecimal
    reason: NonEmpty | None
    criteria: tuple[CriterionScore, ...]
    sections: tuple[SectionScore, ...]
    flags: tuple[str, ...]


class ErrorField(FrozenModel):
    field: NonEmpty
    message: NonEmpty


class ErrorDetail(FrozenModel):
    code: NonEmpty
    message: NonEmpty
    details: tuple[ErrorField, ...] = ()


class ErrorResponse(FrozenModel):
    error: ErrorDetail


class HealthResponse(FrozenModel):
    status: Literal["ok"] = "ok"
    stage: Literal["bootstrap"] = "bootstrap"
    version: str = "0.1.0"


class EvaluationCreateRequest(FrozenModel):
    conversation_version_id: NonEmpty
    checklist_version_id: NonEmpty
    mode: Literal["mock", "live"]
    provider: NonEmpty | None = None
    model: NonEmpty | None = None


class Evaluation(FrozenModel):
    id: NonEmpty
    conversation_version_id: NonEmpty
    checklist_version_id: NonEmpty
    status: Literal["queued", "running", "succeeded", "failed"]
    created_at: AwareDatetime
    output: EvaluationOutput | None = None
    original_score: ScoreResult | None = None
    effective_score: ScoreResult | None = None
    failure: ErrorDetail | None = None

    @model_validator(mode="after")
    def validate_state(self) -> Evaluation:
        if self.status == "succeeded":
            if self.output is None or self.original_score is None or self.effective_score is None:
                raise ValueError("Succeeded evaluations require valid output and both scores")
            if self.failure is not None:
                raise ValueError("Succeeded evaluations cannot contain a failure")
        else:
            if any(item is not None for item in (self.output, self.original_score, self.effective_score)):
                raise ValueError("Incomplete evaluations cannot expose final results")
            if (self.status == "failed") != (self.failure is not None):
                raise ValueError("Only failed evaluations require a failure reason")
        return self


class AppealCreateRequest(FrozenModel):
    criterion_id: NonEmpty
    comment: NonEmpty


class DecisionCreateRequest(FrozenModel):
    action: Literal["confirm", "override"]
    reason: NonEmpty
    override_answer: Answer | None = None
    idempotency_key: NonEmpty

    @model_validator(mode="after")
    def validate_action(self) -> DecisionCreateRequest:
        if (self.action == "override") != (self.override_answer is not None):
            raise ValueError("Only override requires a replacement answer")
        return self


class ReviewDecision(DecisionCreateRequest):
    id: NonEmpty
    appeal_id: NonEmpty
    created_at: AwareDatetime
    actor_label: NonEmpty = "Демонстрационный проверяющий"


class Appeal(FrozenModel):
    id: NonEmpty
    evaluation_id: NonEmpty
    criterion_id: NonEmpty
    comment: NonEmpty
    status: Literal["open", "resolved"]
    created_at: AwareDatetime
    decision: ReviewDecision | None = None

    @model_validator(mode="after")
    def validate_resolution(self) -> Appeal:
        if (self.status == "resolved") != (self.decision is not None):
            raise ValueError("Resolved appeal must have a decision")
        if self.decision is not None and self.decision.appeal_id != self.id:
            raise ValueError("Decision must belong to this appeal")
        return self


class AppealList(FrozenModel):
    items: tuple[Appeal, ...]


class DecisionResponse(FrozenModel):
    decision: ReviewDecision
    evaluation: Evaluation


class DemoPayload(FrozenModel):
    mode: Literal["mock"] = "mock"
    notice: Literal["Тестовые данные — не AI-оценка"] = "Тестовые данные — не AI-оценка"
    conversation: ConversationVersion
    checklist: ChecklistVersion
    evaluation: Evaluation

    @model_validator(mode="after")
    def validate_demo(self) -> DemoPayload:
        if not self.conversation.synthetic or self.conversation.source != "synthetic":
            raise ValueError("Demo may contain only explicitly synthetic conversations")
        if self.evaluation.conversation_version_id != self.conversation.id:
            raise ValueError("Demo must show the evaluated conversation snapshot")
        if self.evaluation.checklist_version_id != self.checklist.id:
            raise ValueError("Demo must show the evaluated checklist snapshot")
        if self.evaluation.output is None or self.evaluation.output.metadata.mode != "mock":
            raise ValueError("Demo must be explicitly marked as mock")
        return self
