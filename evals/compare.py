"""Офлайн-диагностика исходных AI-оценок; не официальная метрика жюри."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, TypeAdapter, ValidationError

from app.contracts.models import Answer, Channel, Evaluation, FrozenModel, Language, NonEmpty


class ReferenceAnswer(FrozenModel):
    criterion_id: NonEmpty
    answer: Answer


class ReferenceRecord(FrozenModel):
    conversation_version_id: NonEmpty
    checklist_version_id: NonEmpty
    language: Language
    channel: Channel
    origin: Literal["team_manual", "team_human_confirmed_ai_draft", "organizer"]
    split: Literal["tune", "holdout"]
    scenario_group: NonEmpty
    answers: tuple[ReferenceAnswer, ...]
    total: Decimal | None = Field(ge=0, le=100, allow_inf_nan=False)


def ratio(numerator: int | Decimal, denominator: int) -> str | None:
    return str(Decimal(numerator) / denominator) if denominator else None


def answer_label(answer: Answer) -> str:
    """Typed labels keep choice 'true' distinct from boolean True; ignore N/A wording."""
    if answer.type == "na":
        return "na"
    if answer.type == "boolean":
        return "boolean:true" if answer.value else "boolean:false"
    if answer.type == "scale":
        value = Decimal(str(answer.value))
        label = format(value, "f") if value else "0"
        return "scale:" + (label.rstrip("0").rstrip(".") if "." in label else label)
    return "choice:" + json.dumps(answer.value, ensure_ascii=False)


def binary_metrics(matrix: Counter[tuple[str, str]], positive: str) -> dict[str, object]:
    tp = sum(count for (expected, actual), count in matrix.items()
             if expected == positive and actual == positive)
    fp = sum(count for (expected, actual), count in matrix.items()
             if expected != positive and actual == positive)
    fn = sum(count for (expected, actual), count in matrix.items()
             if expected == positive and actual != positive)
    tn = sum(matrix.values()) - tp - fp - fn
    return {
        "true_positive": tp, "false_positive": fp, "false_negative": fn, "true_negative": tn,
        "precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn),
        "f1": ratio(2 * tp, 2 * tp + fp + fn),
    }


def summarize(pairs: Sequence[tuple[ReferenceRecord, Evaluation]]) -> dict[str, object]:
    matrices: dict[str, Counter[tuple[str, str]]] = {}
    absolute_errors: list[Decimal] = []
    succeeded = failed = pending = exact_dialogues = null_mismatches = both_null = 0
    for reference, prediction in pairs:
        if prediction.status == "failed":
            failed += 1
            continue
        if prediction.status != "succeeded":
            pending += 1
            continue
        assert prediction.output is not None and prediction.original_score is not None
        expected = {item.criterion_id: item.answer for item in reference.answers}
        actual = {item.criterion_id: item.answer for item in prediction.output.answers}
        succeeded += 1
        dialogue_matches = True
        for criterion_id, answer in expected.items():
            labels = (answer_label(answer), answer_label(actual[criterion_id]))
            matrices.setdefault(criterion_id, Counter())[labels] += 1
            dialogue_matches &= labels[0] == labels[1]
        exact_dialogues += int(dialogue_matches)
        if reference.total is not None and prediction.original_score.total is not None:
            absolute_errors.append(abs(reference.total - prediction.original_score.total))
        elif reference.total is None and prediction.original_score.total is None:
            both_null += 1
        else:
            null_mismatches += 1
    all_answers: Counter[tuple[str, str]] = Counter()
    per_criterion: dict[str, object] = {}
    for criterion_id, matrix in sorted(matrices.items()):
        all_answers.update(matrix)
        count = sum(matrix.values())
        matches = sum(n for (expected, actual), n in matrix.items() if expected == actual)
        labels_seen = {label for pair in matrix for label in pair}
        per_criterion[criterion_id] = {
            "count": count, "matches": matches, "accuracy": ratio(matches, count),
            "confusion": [
                {"expected": expected, "predicted": actual, "count": n}
                for (expected, actual), n in sorted(matrix.items())
            ],
            "na_detection": binary_metrics(matrix, "na"),
            "boolean_true": (
                binary_metrics(matrix, "boolean:true")
                if labels_seen <= {"boolean:true", "boolean:false"} else None
            ),
        }
    correct_count = sum(n for (expected, actual), n in all_answers.items() if expected == actual)
    answer_count = sum(all_answers.values())
    na_total = sum(n for (expected, _), n in all_answers.items() if expected == "na")
    return {
        "reference_count": len(pairs),
        "succeeded": succeeded,
        "failed": failed,
        "pending": pending,
        "success_rate": ratio(succeeded, len(pairs)),
        "failure_rate": ratio(failed, len(pairs)),
        "pending_rate": ratio(pending, len(pairs)),
        "compared_answers": answer_count,
        "matched_answers": correct_count,
        "criterion_accuracy": ratio(correct_count, answer_count),
        "exact_dialogue_matches": exact_dialogues,
        "dialogue_accuracy": ratio(exact_dialogues, succeeded),
        "score_mae": ratio(sum(absolute_errors, Decimal(0)), len(absolute_errors)),
        "score_pairs": len(absolute_errors),
        "score_null_mismatches": null_mismatches,
        "score_both_null": both_null,
        "reference_na_count": na_total,
        "matched_na_count": all_answers[("na", "na")],
        "na_detection": binary_metrics(all_answers, "na"),
        "per_criterion": per_criterion,
    }


def compare(references: list[ReferenceRecord], predictions: list[Evaluation]) -> dict[str, object]:
    reference_index = {(item.conversation_version_id, item.checklist_version_id): item for item in references}
    prediction_index = {(item.conversation_version_id, item.checklist_version_id): item for item in predictions}
    if len(reference_index) != len(references) or len(prediction_index) != len(predictions):
        raise ValueError("Select exactly one prediction/reference per pair of snapshots")
    if set(prediction_index) != set(reference_index):
        raise ValueError("Prediction/reference snapshot sets must match exactly")
    splits = {reference.split for reference in references}
    if len(splits) > 1:
        raise ValueError("Compare tune and holdout separately; mixed splits hide independence")
    identities: set[tuple[str, str, str, str, str]] = set()
    disagreements: list[dict[str, str]] = []
    pairs = [(reference, prediction_index[key]) for key, reference in sorted(reference_index.items())]
    for reference, prediction in pairs:
        expected = {item.criterion_id: item.answer for item in reference.answers}
        if not expected or len(expected) != len(reference.answers):
            raise ValueError("Reference criteria must be nonempty and unique")
        if prediction.status != "succeeded":
            continue
        assert prediction.output is not None
        metadata = prediction.output.metadata
        identities.add((metadata.mode, metadata.provider, metadata.model,
                        metadata.instruction_version, metadata.pipeline_version))
        actual = {item.criterion_id: item.answer for item in prediction.output.answers}
        if len(actual) != len(prediction.output.answers):
            raise ValueError("Duplicate criterion IDs")
        if set(expected) != set(actual):
            raise ValueError("Criterion sets differ within matching checklist snapshots")
        for criterion_id, answer in sorted(expected.items()):
            if answer_label(answer) != answer_label(actual[criterion_id]):
                disagreements.append({
                    "conversation_version_id": reference.conversation_version_id,
                    "checklist_version_id": reference.checklist_version_id,
                    "evaluation_id": prediction.id, "criterion_id": criterion_id,
                    "expected": answer_label(answer), "predicted": answer_label(actual[criterion_id]),
                })
    if len(identities) > 1:
        raise ValueError("Compare each mode/provider/model/instruction/pipeline separately")
    report: dict[str, object] = {
        "metric_status": "internal_diagnostic_not_jury_formula",
        "report_version": "2.0",
        "split": next(iter(splits), None),
        "successful_prediction_identity": (
            dict(zip(("mode", "provider", "model", "instruction_version", "pipeline_version"),
                     next(iter(identities)), strict=True)) if identities else None
        ),
        "reference_origins": sorted({reference.origin for reference in references}),
        **summarize(pairs),
        "disagreements": disagreements,
        "limitations": (
            "Accuracy/MAE относятся к succeeded; failed/pending имеют отдельные знаменатели. "
            "Использован original_score. Смысл evidence, правильность ручных эталонов, "
            "usage/cost всех попыток и идентичность модели у failed здесь не проверяются. "
            "Mock/synthetic не доказывают точность AI; формула жюри неизвестна."
        ),
    }
    for field, label in (("language", "by_language"), ("channel", "by_channel"),
                         ("checklist_version_id", "by_checklist")):
        groups: dict[str, list[tuple[ReferenceRecord, Evaluation]]] = {}
        for pair in pairs:
            groups.setdefault(str(getattr(pair[0], field)), []).append(pair)
        report[label] = {key: summarize(group) for key, group in sorted(groups.items())}
    return report


def main(argv: Sequence[str] | None = None) -> None:
    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    # Lazy import: prepare uses ReferenceRecord, and owns the shared private-path policy.
    from evals.prepare import private_path, write_private_json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="Сохранить новый приватный JSON вместо stdout")
    arguments = parser.parse_args(argv)
    try:
        references = TypeAdapter(list[ReferenceRecord]).validate_json(
            private_path(arguments.references).read_text(encoding="utf-8")
        )
        predictions = TypeAdapter(list[Evaluation]).validate_json(
            private_path(arguments.predictions).read_text(encoding="utf-8")
        )
        report = compare(references, predictions)
        if arguments.output:
            write_private_json(arguments.output, report)
            print("Приватный диагностический отчёт сохранён. Это не метрика жюри.")
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
    except ValidationError:
        parser.error("Некорректный приватный JSON или DTO. Содержимое данных не выводится.")
    except FileExistsError:
        parser.error("Выходной файл уже существует; выберите новое имя. Перезапись запрещена.")
    except OSError:
        parser.error("Не удалось прочитать или создать локальный приватный файл.")
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
