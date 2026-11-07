import json

import pytest
from pydantic import TypeAdapter, ValidationError

from app.contracts.models import (
    Answer,
    ChecklistVersion,
    ConversationImportRequest,
    ConversationVersion,
    CriterionAnswer,
    DecisionCreateRequest,
    SourceMessage,
)
from app.evaluation.demo import load_synthetic_answers, load_synthetic_input


@pytest.mark.parametrize("field,value", [
    ("weight", "NaN"), ("weight", "Infinity"), ("weight", "-1"),
    ("section_id", "missing"), ("na_condition", "Unexpected condition"),
])
def test_invalid_criterion_configuration(field: str, value: str) -> None:
    raw = load_synthetic_input().checklist.model_dump(mode="json")
    raw["criteria"][0][field] = value
    with pytest.raises(ValidationError):
        ChecklistVersion.model_validate(raw)


@pytest.mark.parametrize("quality", ["NaN", "Infinity", "-0.1", "1.1", None])
def test_invalid_quality(quality: str | None) -> None:
    raw = load_synthetic_input().checklist.model_dump(mode="json")
    raw["criteria"][0]["options"][0]["quality"] = quality
    with pytest.raises(ValidationError):
        ChecklistVersion.model_validate(raw)


@pytest.mark.parametrize("value", ["", "   ", "\t\n", " done", "done ", "\u00a0done", "done\u202f"])
def test_choice_options_reject_values_that_answers_would_change_or_reject(value: str) -> None:
    raw = load_synthetic_input().checklist.model_dump(mode="json")
    raw["criteria"][0].update(answer_type="choice", options=[{
        "value": value, "label": "Вариант", "description": "Синтетический вариант",
        "quality": "1", "flagged": False,
    }])
    with pytest.raises(ValidationError, match="Choice option values"):
        ChecklistVersion.model_validate(raw)
    assert raw["criteria"][0]["options"][0]["value"] == value


def test_duplicate_ids_values_and_wrong_types_rejected() -> None:
    original = load_synthetic_input().checklist.model_dump(mode="json")
    for variant in range(5):
        raw = json.loads(json.dumps(original))
        if variant == 0:
            raw["criteria"].append(raw["criteria"][0])
        elif variant == 1:
            raw["sections"].append(raw["sections"][0])
        elif variant == 2:
            raw["criteria"][0]["options"].append(raw["criteria"][0]["options"][0])
        elif variant == 3:
            raw["criteria"][0]["options"][0]["value"] = 1
        else:
            raw["criteria"][3]["options"].append({**raw["criteria"][3]["options"][0], "value": 0.0})
        with pytest.raises(ValidationError):
            ChecklistVersion.model_validate(raw)


def test_zero_maximum_scale_and_weighted_flag_rejected() -> None:
    raw = load_synthetic_input().checklist.model_dump(mode="json")
    raw["criteria"][3]["options"] = [raw["criteria"][3]["options"][0]]
    with pytest.raises(ValidationError):
        ChecklistVersion.model_validate(raw)
    raw = load_synthetic_input().checklist.model_dump(mode="json")
    raw["criteria"][-1]["weight"] = "20"
    with pytest.raises(ValidationError):
        ChecklistVersion.model_validate(raw)


@pytest.mark.parametrize("raw", [
    {"type": "boolean", "value": 1},
    {"type": "boolean", "value": "true"},
    {"type": "scale", "value": True},
    {"type": "scale", "value": "7"},
    {"type": "scale", "value": float("nan")},
    {"type": "scale", "value": float("inf")},
    {"type": "na", "reason": " "},
    {"type": "unknown", "value": 1},
])
def test_strict_answer_types(raw: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Answer).validate_python(raw)


def test_evidence_and_explanation_cannot_be_empty() -> None:
    original = load_synthetic_answers()[0].model_dump(mode="json")
    for changes in ({"evidence": []}, {"explanation": " "}):
        with pytest.raises(ValidationError):
            CriterionAnswer.model_validate({**original, **changes})


def test_original_unicode_timestamps_and_order_are_preserved() -> None:
    for language, text in [("RU", "Здравствуйте"), ("KZ", "Сәлеметсіз бе, өтінішіңізді нақтылай аласыз ба?"), ("EN", "Hello, how can I help?")]:
        message = SourceMessage(timestamp="2026-11-03T20:49:13.190067+05:00", speaker="operator", text=text)
        assert SourceMessage.model_validate_json(message.model_dump_json()).text == text
        assert message.timestamp.endswith("+05:00")
        raw = load_synthetic_input().conversation.model_dump(mode="json")
        raw["language"] = language
        assert ConversationVersion.model_validate(raw).language == language
    raw = load_synthetic_input().conversation.model_dump(mode="json")
    raw["messages"][0]["order"] = 1
    with pytest.raises(ValidationError):
        ConversationVersion.model_validate(raw)


def test_import_is_not_fixed_to_65_records_and_rejects_duplicates() -> None:
    record = {"conversation_id": "new-id", "language": "KZ", "channel": "agent chat",
              "transcript": [{"timestamp": "00:00", "speaker": "client", "text": "Сәлем!"}]}
    imported = ConversationImportRequest.model_validate({"dataset_group_id": "NEW", "records": [record]})
    assert len(imported.records) == 1
    with pytest.raises(ValidationError):
        ConversationImportRequest.model_validate({"dataset_group_id": "NEW", "records": [record, record]})


def test_decision_requires_reason_and_override_only_for_override() -> None:
    for raw in (
        {"action": "override", "reason": "Исправление", "idempotency_key": "key"},
        {"action": "confirm", "reason": "Да", "idempotency_key": "key", "override_answer": {"type": "boolean", "value": False}},
        {"action": "confirm", "reason": " ", "idempotency_key": "key"},
    ):
        with pytest.raises(ValidationError):
            DecisionCreateRequest.model_validate(raw)


def test_audio_reference_accepts_http_urls_only() -> None:
    raw = load_synthetic_input().conversation.model_dump(mode="json")
    raw["audio_url"] = "javascript:alert(1)"
    with pytest.raises(ValidationError):
        ConversationVersion.model_validate(raw)
