from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contracts.models import ChecklistVersion, CriterionAnswer
from app.domain.scoring import DomainValidationError, score
from app.evaluation.demo import load_synthetic_answers, load_synthetic_input


def answers_with(**changes: object) -> tuple[CriterionAnswer, ...]:
    result = []
    for answer in load_synthetic_answers():
        data = answer.model_dump(mode="json")
        if answer.criterion_id in changes:
            data["answer"] = changes[answer.criterion_id]
        result.append(CriterionAnswer.model_validate(data))
    return tuple(result)


def maximum_answers() -> tuple[CriterionAnswer, ...]:
    return answers_with(
        C02={"type": "boolean", "value": True},
        C03={"type": "boolean", "value": True},
        C04={"type": "scale", "value": 20},
        C06={"type": "scale", "value": 10},
    )


def test_all_maximum_is_100() -> None:
    result = score(load_synthetic_input().checklist, maximum_answers())
    assert result.total == Decimal("100.00")
    assert result.applicable_weight == Decimal("100")


def test_only_c06_seven_is_95_50() -> None:
    answers = tuple(
        CriterionAnswer.model_validate({**answer.model_dump(mode="json"),
                                       "answer": {"type": "scale", "value": 7}})
        if answer.criterion_id == "C06" else answer
        for answer in maximum_answers()
    )
    result = score(load_synthetic_input().checklist, answers)
    assert result.total == Decimal("95.50")
    assert next(item for item in result.criteria if item.criterion_id == "C06").earned_points == Decimal("10.5")


def test_na_normalization_regression() -> None:
    result = score(load_synthetic_input().checklist, load_synthetic_answers())
    assert result.applicable_weight == Decimal("65")
    assert result.earned_points == Decimal("55.5")
    assert result.total == Decimal("85.38")
    excluded = [item for item in result.criteria if item.criterion_id in {"C02", "C03"}]
    assert all(not item.applicable and item.earned_points is None for item in excluded)


def test_flag_does_not_change_score() -> None:
    result = score(load_synthetic_input().checklist, answers_with(C08={"type": "boolean", "value": True}))
    assert result.total == Decimal("85.38")
    assert result.flags == ("C08",)
    assert result.criteria[-1].weight is None
    assert result.criteria[-1].earned_points is None


@pytest.mark.parametrize("criterion_id,answer", [
    ("C04", {"type": "scale", "value": 17}),
    ("C06", {"type": "scale", "value": 8}),
    ("C01", {"type": "na", "reason": "Не применимо"}),
    ("C08", {"type": "na", "reason": "Не применимо"}),
    ("C06", {"type": "boolean", "value": True}),
    ("C01", {"type": "choice", "value": "true"}),
])
def test_invalid_values_are_rejected(criterion_id: str, answer: dict[str, object]) -> None:
    with pytest.raises(DomainValidationError):
        score(load_synthetic_input().checklist, answers_with(**{criterion_id: answer}))


def test_missing_duplicate_and_foreign_criteria_are_rejected() -> None:
    checklist = load_synthetic_input().checklist
    answers = load_synthetic_answers()
    wrong = CriterionAnswer.model_validate({**answers[0].model_dump(mode="json"), "criterion_id": "foreign"})
    for invalid in (answers[:-1], answers + (answers[0],), (wrong,) + answers[1:]):
        with pytest.raises(DomainValidationError):
            score(checklist, invalid)


def test_zero_weight_has_no_numeric_total() -> None:
    raw = load_synthetic_input().checklist.model_dump(mode="json")
    for criterion in raw["criteria"]:
        if criterion["kind"] == "scored":
            criterion["weight"] = "0"
    result = score(ChecklistVersion.model_validate(raw), maximum_answers())
    assert result.total is None
    assert result.reason
    assert result.applicable_weight == 0
    assert all(section.normalized_points is None for section in result.sections)


def test_sections_use_global_denominator() -> None:
    result = score(load_synthetic_input().checklist, load_synthetic_answers())
    assert sum(section.earned_points for section in result.sections) == result.earned_points
    assert sum(section.applicable_weight for section in result.sections) == result.applicable_weight
    opening = result.sections[0]
    assert opening.applicable_weight == Decimal("10")
    assert opening.normalized_maximum is not None
    assert abs(opening.normalized_maximum - Decimal(1000) / Decimal(65)) < Decimal("1e-25")
    assert opening.normalized_points == opening.normalized_maximum
    assert opening.normalized_points != Decimal("100")


def test_new_choice_and_type_change_are_configuration_only() -> None:
    snapshot = load_synthetic_input().checklist
    raw = snapshot.model_dump(mode="json")
    raw["id"] = "edited-checklist-v2"
    raw["version"] = 2
    raw["criteria"][0]["answer_type"] = "choice"
    raw["criteria"][0]["options"] = [
        {"value": "done", "label": "Выполнено", "description": "Выполнены условия", "quality": "1"},
        {"value": "missing", "label": "Пропущено", "description": "Условия не выполнены", "quality": "0"},
    ]
    extra = {
        "id": "CUSTOM", "section_id": "opening", "title": "Новый критерий",
        "description": "Учебный настраиваемый критерий", "kind": "scored",
        "answer_type": "choice", "weight": "5", "allow_na": False,
        "options": [{"value": "partial", "label": "Частично", "description": "Половина", "quality": "0.5"}],
    }
    raw["criteria"].append(extra)
    checklist = ChecklistVersion.model_validate(raw)
    answers = answers_with(C01={"type": "choice", "value": "done"})
    additional = CriterionAnswer.model_validate({
        **answers[0].model_dump(mode="json"), "criterion_id": "CUSTOM",
        "answer": {"type": "choice", "value": "partial"},
    })
    result = score(checklist, answers + (additional,))
    assert result.applicable_weight == Decimal("70")
    assert result.earned_points == Decimal("58")
    assert result.total == Decimal("82.86")
    assert snapshot.criteria[0].answer_type == "boolean"
    assert len(snapshot.criteria) == 8


def test_half_up_is_explicit() -> None:
    raw = load_synthetic_input().checklist.model_dump(mode="json")
    raw["criteria"] = [raw["criteria"][0]]
    raw["criteria"][0]["weight"] = "1"
    raw["criteria"][0]["options"][0]["quality"] = "0.12345"
    result = score(ChecklistVersion.model_validate(raw), (load_synthetic_answers()[0],))
    assert result.total == Decimal("12.35")


def test_snapshots_are_deeply_immutable() -> None:
    snapshot = load_synthetic_input()
    with pytest.raises(ValidationError):
        snapshot.conversation.messages[0].text = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        snapshot.checklist.criteria[0].weight = Decimal("99")  # type: ignore[misc]
    assert isinstance(snapshot.checklist.criteria, tuple)
    assert isinstance(snapshot.checklist.criteria[0].options, tuple)


@pytest.mark.parametrize("quality,expected", [
    ("0.12344" + "9" * 55, "12.34"),
    ("0.12345", "12.35"),
    ("0.12345" + "0" * 55 + "1", "12.35"),
])
def test_high_precision_quality_is_not_rounded_before_total(quality: str, expected: str) -> None:
    raw = load_synthetic_input().checklist.model_dump(mode="json")
    raw["criteria"] = [raw["criteria"][0]]
    raw["criteria"][0]["weight"] = "1"
    raw["criteria"][0]["options"][0]["quality"] = quality
    result = score(ChecklistVersion.model_validate(raw), (load_synthetic_answers()[0],))
    assert result.total == Decimal(expected)
    assert result.earned_points == Decimal(quality)
    assert result.criteria[0].earned_points == Decimal(quality)
    assert result.sections[0].earned_points == Decimal(quality)


def test_weight_sum_preserves_more_than_50_decimal_places() -> None:
    raw = load_synthetic_input().checklist.model_dump(mode="json")
    raw["criteria"] = raw["criteria"][:2]
    raw["criteria"][0]["weight"] = "1"
    raw["criteria"][1]["weight"] = "0." + "0" * 59 + "1"
    answers = answers_with(C02={"type": "boolean", "value": True})[:2]
    result = score(ChecklistVersion.model_validate(raw), answers)
    expected = Decimal("1." + "0" * 59 + "1")
    assert result.applicable_weight == expected
    assert result.earned_points == expected
    assert result.sections[0].applicable_weight == expected
    assert result.total == Decimal("100.00")


def test_large_adjacent_scale_values_select_distinct_options() -> None:
    large = 10 ** 30
    raw = load_synthetic_input().checklist.model_dump(mode="json")
    raw["criteria"] = [raw["criteria"][0]]
    raw["criteria"][0]["answer_type"] = "scale"
    raw["criteria"][0]["options"] = [
        {"value": large, "label": "Первый", "description": "Ноль", "quality": "0"},
        {"value": large + 1, "label": "Второй", "description": "Максимум", "quality": "1"},
    ]
    checklist = ChecklistVersion.model_validate(raw)
    first = score(checklist, answers_with(C01={"type": "scale", "value": large})[:1])
    second = score(checklist, answers_with(C01={"type": "scale", "value": large + 1})[:1])
    assert first.total == Decimal("0.00")
    assert second.total == Decimal("100.00")
