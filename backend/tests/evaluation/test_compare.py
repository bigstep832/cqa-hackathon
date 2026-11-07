import asyncio
from decimal import Decimal

import pytest
from evals.compare import ReferenceAnswer, ReferenceRecord, compare

from app.contracts.models import Evaluation
from app.evaluation.demo import build_demo_payload


def sample() -> tuple[ReferenceRecord, Evaluation]:
    payload = asyncio.run(build_demo_payload())
    evaluation = payload.evaluation
    assert evaluation.output is not None and evaluation.original_score is not None
    reference = ReferenceRecord(
        conversation_version_id=payload.conversation.id,
        checklist_version_id=payload.checklist.id,
        language=payload.conversation.language,
        channel=payload.conversation.channel,
        origin="team_manual", split="tune", scenario_group="synthetic-example",
        answers=tuple(ReferenceAnswer(criterion_id=item.criterion_id, answer=item.answer)
                      for item in evaluation.output.answers),
        total=evaluation.original_score.total,
    )
    return reference, evaluation


def test_compare_uses_original_score_before_appeals() -> None:
    reference, evaluation = sample()
    raw = evaluation.model_dump(mode="json")
    raw["effective_score"]["total"] = "100.00"
    result = compare([reference], [Evaluation.model_validate(raw)])
    assert result["score_mae"] == "0.00"
    assert result["criterion_accuracy"] == "1"
    assert result["compared_answers"] == 8


def test_failures_are_reported_and_not_silently_scored_zero() -> None:
    reference, evaluation = sample()
    raw = evaluation.model_dump(mode="json")
    raw.update(status="failed", output=None, original_score=None, effective_score=None,
               failure={"code": "PROVIDER_ERROR", "message": "Provider unavailable"})
    result = compare([reference], [Evaluation.model_validate(raw)])
    assert result["failed"] == 1
    assert result["succeeded"] == 0
    assert result["score_mae"] is None
    assert result["criterion_accuracy"] is None


def test_reference_pairs_and_duplicate_predictions_are_checked() -> None:
    reference, evaluation = sample()
    with pytest.raises(ValueError):
        compare([reference], [])
    with pytest.raises(ValueError):
        compare([reference], [evaluation, evaluation])


def test_na_reason_wording_is_not_classification_error() -> None:
    reference, evaluation = sample()
    raw = evaluation.model_dump(mode="json")
    raw["output"]["answers"][1]["answer"]["reason"] = "Иная корректная формулировка причины"
    result = compare([reference], [Evaluation.model_validate(raw)])
    assert Decimal(str(result["criterion_accuracy"])) == Decimal(1)
    assert result["reference_na_count"] == result["matched_na_count"] == 2
