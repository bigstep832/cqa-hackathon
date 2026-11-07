import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest
from evals import prepare
from evals.compare import ReferenceAnswer, ReferenceRecord, answer_label, compare, main

from app.contracts.models import BooleanAnswer, ChoiceAnswer, Evaluation, ScaleAnswer
from app.domain.scoring import score
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


def changed_pair(
    index: int, *, expected: bool, actual: bool, language: str = "RU", channel: str = "agent chat"
) -> tuple[ReferenceRecord, Evaluation]:
    reference, evaluation = sample()
    ref = reference.model_dump(mode="json")
    raw = evaluation.model_dump(mode="json")
    version = f"synthetic-review-{index}"
    ref.update(conversation_version_id=version, language=language, channel=channel)
    raw.update(conversation_version_id=version, id=f"synthetic-evaluation-{index}")
    for answer in ref["answers"]:
        if answer["criterion_id"] == "C08":
            answer["answer"]["value"] = expected
    for answer in raw["output"]["answers"]:
        if answer["criterion_id"] == "C08":
            answer["answer"]["value"] = actual
        for evidence in answer["evidence"]:
            evidence["conversation_version_id"] = version
    prediction = Evaluation.model_validate(raw)
    assert prediction.output is not None
    scored = score(asyncio.run(build_demo_payload()).checklist, prediction.output.answers)
    prediction = prediction.model_copy(update={"original_score": scored, "effective_score": scored})
    return ReferenceRecord.model_validate(ref), prediction


def test_critical_flag_precision_recall_and_language_channel_breakdown() -> None:
    pairs = [
        changed_pair(1, expected=True, actual=True),
        changed_pair(2, expected=True, actual=False),
        changed_pair(3, expected=False, actual=True, language="KZ"),
        changed_pair(4, expected=False, actual=False, language="EN", channel="voice call"),
    ]
    report = compare([r for r, _ in pairs], [p for _, p in reversed(pairs)])
    flag = report["per_criterion"]["C08"]
    assert flag["count"] == 4 and flag["matches"] == 2
    assert flag["boolean_true"] == {
        "true_positive": 1, "false_positive": 1, "false_negative": 1, "true_negative": 1,
        "precision": "0.5", "recall": "0.5", "f1": "0.5",
    }
    assert report["exact_dialogue_matches"] == 2
    assert report["dialogue_accuracy"] == "0.5"
    assert report["by_language"]["RU"]["reference_count"] == 2
    assert report["by_language"]["KZ"]["per_criterion"]["C08"]["matches"] == 0
    assert report["by_channel"]["voice call"]["reference_count"] == 1
    assert report["score_mae"] == "0.00"  # C08 changes no numeric points.
    assert {item["conversation_version_id"] for item in report["disagreements"]} == {
        "synthetic-review-2", "synthetic-review-3",
    }


def test_false_na_and_missed_na_are_both_counted() -> None:
    reference, evaluation = sample()
    raw = evaluation.model_dump(mode="json")
    raw["output"]["answers"][6]["answer"] = {"type": "na", "reason": "synthetic false NA"}
    raw["output"]["answers"][1]["answer"] = {"type": "boolean", "value": False}
    prediction = Evaluation.model_validate(raw)
    assert prediction.output is not None
    scored = score(asyncio.run(build_demo_payload()).checklist, prediction.output.answers)
    prediction = prediction.model_copy(update={"original_score": scored, "effective_score": scored})
    report = compare([reference], [prediction])
    assert report["na_detection"] == {
        "true_positive": 1, "false_positive": 1, "false_negative": 1, "true_negative": 5,
        "precision": "0.5", "recall": "0.5", "f1": "0.5",
    }
    assert report["matched_na_count"] == 1
    assert report["reference_na_count"] == 2
    assert report["per_criterion"]["C07"]["boolean_true"] is None


def test_raw_scale_confusion_is_not_a_weighted_contribution() -> None:
    reference, evaluation = sample()
    raw = evaluation.model_dump(mode="json")
    raw["output"]["answers"][5]["answer"]["value"] = 4
    prediction = Evaluation.model_validate(raw)
    assert prediction.output is not None
    scored = score(asyncio.run(build_demo_payload()).checklist, prediction.output.answers)
    prediction = prediction.model_copy(update={"original_score": scored, "effective_score": scored})
    report = compare([reference], [prediction])
    assert report["per_criterion"]["C06"]["confusion"] == [
        {"expected": "scale:7", "predicted": "scale:4", "count": 1},
    ]
    assert Decimal(report["score_mae"]) == Decimal("6.92")


def test_failure_and_pending_denominators_remain_visible_in_each_group() -> None:
    pairs = [changed_pair(i, expected=False, actual=False) for i in range(3)]
    failed = pairs[1][1].model_dump(mode="json")
    failed.update(status="failed", output=None, original_score=None, effective_score=None,
                  failure={"code": "PROVIDER_ERROR", "message": "synthetic failure"})
    pending = pairs[2][1].model_dump(mode="json")
    pending.update(status="queued", output=None, original_score=None, effective_score=None)
    report = compare([r for r, _ in pairs], [pairs[0][1], Evaluation.model_validate(failed),
                                            Evaluation.model_validate(pending)])
    assert report["criterion_accuracy"] == "1"
    assert report["compared_answers"] == 8
    assert report["succeeded"] == report["failed"] == report["pending"] == 1
    assert Decimal(report["failure_rate"]) == Decimal(1) / 3
    assert report["by_language"]["RU"]["pending"] == 1


def test_no_positive_flags_does_not_claim_perfect_recall() -> None:
    reference, evaluation = sample()
    report = compare([reference], [evaluation])
    metrics = report["per_criterion"]["C08"]["boolean_true"]
    assert metrics["true_negative"] == 1
    assert metrics["precision"] is metrics["recall"] is metrics["f1"] is None
    assert compare([], [])["criterion_accuracy"] is None


def test_null_score_mismatch_is_visible_and_not_converted_to_zero() -> None:
    reference, evaluation = sample()
    reference = reference.model_copy(update={"total": None})
    report = compare([reference], [evaluation])
    assert report["score_null_mismatches"] == 1
    assert report["score_mae"] is None and report["score_pairs"] == 0


def test_typed_labels_do_not_collapse_booleans_choices_or_large_numbers() -> None:
    assert answer_label(BooleanAnswer(value=True)) != answer_label(ChoiceAnswer(value="true"))
    assert answer_label(ScaleAnswer(value=7.0)) == answer_label(ScaleAnswer(value=7))
    large = 10**35
    assert answer_label(ScaleAnswer(value=large)) != answer_label(ScaleAnswer(value=large + 1))


def test_mixed_tune_holdout_or_prediction_identity_is_rejected() -> None:
    first_ref, first_prediction = changed_pair(1, expected=False, actual=False)
    second_ref, second_prediction = changed_pair(2, expected=False, actual=False)
    with pytest.raises(ValueError, match="tune and holdout"):
        compare([first_ref, second_ref.model_copy(update={"split": "holdout"})],
                [first_prediction, second_prediction])
    raw = second_prediction.model_dump(mode="json")
    raw["output"]["metadata"]["model"] = "another-synthetic-model"
    with pytest.raises(ValueError, match="provider/model"):
        compare([first_ref, second_ref], [first_prediction, Evaluation.model_validate(raw)])


def test_reference_duplicates_rejected_even_if_prediction_failed() -> None:
    reference, evaluation = sample()
    duplicate = reference.model_copy(update={"answers": reference.answers + reference.answers[:1]})
    raw = evaluation.model_dump(mode="json")
    raw.update(status="failed", output=None, original_score=None, effective_score=None,
               failure={"code": "PROVIDER_ERROR", "message": "synthetic failure"})
    with pytest.raises(ValueError, match="unique"):
        compare([duplicate], [Evaluation.model_validate(raw)])


def test_cli_private_report_and_no_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(prepare, "REPOSITORY_ROOT", tmp_path)
    reference, evaluation = sample()
    refs = tmp_path / "data/private/references.json"
    predictions = tmp_path / "artifacts/private/predictions.json"
    output = tmp_path / "artifacts/private/report.json"
    prepare.write_private_json(refs, [reference.model_dump(mode="json")])
    prepare.write_private_json(predictions, [evaluation.model_dump(mode="json")])
    arguments = ["--references", str(refs), "--predictions", str(predictions), "--output", str(output)]
    main(arguments)
    saved = output.read_bytes()
    assert json.loads(saved)["successful_prediction_identity"]["mode"] == "mock"
    assert reference.conversation_version_id not in capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2 and output.read_bytes() == saved
    arguments[-1] = str(tmp_path / "public.json")
    with pytest.raises(SystemExit):
        main(arguments)
    assert not (tmp_path / "public.json").exists()


def test_cli_invalid_private_input_does_not_print_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(prepare, "REPOSITORY_ROOT", tmp_path)
    refs = tmp_path / "data/private/references.json"
    prepare.write_private_json(refs, [{"private_marker": "DO_NOT_PRINT_SYNTHETIC_SECRET"}])
    with pytest.raises(SystemExit) as error:
        main(["--references", str(refs), "--predictions", str(refs)])
    output = capsys.readouterr()
    assert error.value.code == 2
    assert "DO_NOT_PRINT_SYNTHETIC_SECRET" not in output.out + output.err
