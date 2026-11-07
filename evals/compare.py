"""Офлайн-диагностика исходных AI-оценок; не официальная метрика жюри."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.contracts.models import Answer, Channel, Evaluation, FrozenModel, Language, NonEmpty


class ReferenceAnswer(FrozenModel):
    criterion_id: NonEmpty
    answer: Answer


class ReferenceRecord(FrozenModel):
    conversation_version_id: NonEmpty
    checklist_version_id: NonEmpty
    language: Language
    channel: Channel
    origin: Literal["team_manual", "organizer"]
    split: Literal["tune", "holdout"]
    scenario_group: NonEmpty
    answers: tuple[ReferenceAnswer, ...]
    total: Decimal | None = Field(ge=0, le=100, allow_inf_nan=False)


def compare(references: list[ReferenceRecord], predictions: list[Evaluation]) -> dict[str, object]:
    reference_index = {(item.conversation_version_id, item.checklist_version_id): item for item in references}
    prediction_index = {(item.conversation_version_id, item.checklist_version_id): item for item in predictions}
    if len(reference_index) != len(references) or len(prediction_index) != len(predictions):
        raise ValueError("Select exactly one prediction/reference per pair of snapshots")
    if set(prediction_index) != set(reference_index):
        raise ValueError("Prediction/reference snapshot sets must match exactly")
    criterion_counts: dict[str, Counter[str]] = {}
    absolute_errors: list[Decimal] = []
    succeeded = failed = pending = na_total = na_correct = 0
    for key, reference in reference_index.items():
        prediction = prediction_index[key]
        if prediction.status == "failed":
            failed += 1
            continue
        if prediction.status != "succeeded":
            pending += 1
            continue
        assert prediction.output is not None and prediction.original_score is not None
        expected = {item.criterion_id: item.answer for item in reference.answers}
        actual = {item.criterion_id: item.answer for item in prediction.output.answers}
        if len(expected) != len(reference.answers) or len(actual) != len(prediction.output.answers):
            raise ValueError("Duplicate criterion IDs")
        if set(expected) != set(actual):
            raise ValueError("Criterion sets differ within matching checklist snapshots")
        succeeded += 1
        for criterion_id, answer in expected.items():
            counter = criterion_counts.setdefault(criterion_id, Counter())
            counter["count"] += 1
            # Текст причины N/A и объяснения не является классификационным ответом.
            correct = answer.type == actual[criterion_id].type and (
                answer.type == "na" or answer == actual[criterion_id]
            )
            counter["matches"] += int(correct)
            if answer.type == "na":
                na_total += 1
                na_correct += int(actual[criterion_id].type == "na")
        if reference.total is not None and prediction.original_score.total is not None:
            absolute_errors.append(abs(reference.total - prediction.original_score.total))
    correct_count = sum(item["matches"] for item in criterion_counts.values())
    answer_count = sum(item["count"] for item in criterion_counts.values())
    return {
        "metric_status": "internal_diagnostic_not_jury_formula",
        "reference_count": len(references),
        "succeeded": succeeded,
        "failed": failed,
        "pending": pending,
        "compared_answers": answer_count,
        "matched_answers": correct_count,
        "criterion_accuracy": str(Decimal(correct_count) / answer_count) if answer_count else None,
        "score_mae": str(sum(absolute_errors) / len(absolute_errors)) if absolute_errors else None,
        "score_pairs": len(absolute_errors),
        "reference_na_count": na_total,
        "matched_na_count": na_correct,
        "per_criterion": {key: dict(value) for key, value in sorted(criterion_counts.items())},
        "limitations": "Не проверяет смысл доказательств; ошибки/ожидание отражены отдельно; использован original_score до апелляций.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    arguments = parser.parse_args()
    references = [ReferenceRecord.model_validate(item) for item in json.loads(arguments.references.read_text(encoding="utf-8"))]
    predictions = [Evaluation.model_validate(item) for item in json.loads(arguments.predictions.read_text(encoding="utf-8"))]
    print(json.dumps(compare(references, predictions), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
