"""Только синтетические проверки механики разметки; не реальные эталоны качества."""

import json
from pathlib import Path

import pytest
from evals.compare import ReferenceRecord
from evals.prepare import (
    AnnotationDraft,
    DraftDocument,
    ScenarioAssignment,
    complete_reference,
    finalize_references,
    main,
    prepare_drafts,
    private_path,
    split_groups,
    write_private_json,
)
from pydantic import ValidationError

from app.contracts.models import EvaluationInput
from app.domain.scoring import DomainValidationError
from app.domain.snapshots import conversation_content_hash
from app.evaluation.demo import load_synthetic_answers, load_synthetic_input


def sample_case(name: str, language: str = "RU") -> EvaluationInput:
    raw = load_synthetic_input().model_dump(mode="json")
    raw["conversation"].update(id=f"version-{name}", conversation_id=name, language=language)
    raw["conversation"]["messages"][0]["text"] += f" Синтетический вариант {name}."
    case = EvaluationInput.model_validate(raw)
    raw["conversation"]["content_hash"] = conversation_content_hash(case.conversation)
    return EvaluationInput.model_validate(raw)


def prepare_sample() -> DraftDocument:
    cases = [sample_case("ru"), sample_case("kz", "KZ"), sample_case("other", "EN")]
    groups = [ScenarioAssignment(conversation_version_id=case.conversation.id,
                                 scenario_group="translated" if index < 2 else "other")
              for index, case in enumerate(cases)]
    return prepare_drafts(cases, groups, seed=17, holdout_groups=1)


def manually_completed(draft: AnnotationDraft) -> AnnotationDraft:
    # This fills only synthetic TEST labels. Production prepare never loads these fixture answers.
    raw = draft.model_dump(mode="json")
    raw["answers"] = [item.model_dump(mode="json") for item in load_synthetic_answers()]
    for answer in raw["answers"]:
        for evidence in answer["evidence"]:
            evidence["conversation_version_id"] = draft.evaluation_input.conversation.id
    raw["review"] = {
        "method": "human_review", "annotated_by": "Synthetic test annotator",
        "completed_at": "2026-11-07T10:00:00+05:00", "language_understood": True,
    }
    return AnnotationDraft.model_validate(raw)


def test_drafts_preserve_snapshots_and_do_not_invent_any_labels() -> None:
    document = prepare_sample()
    assert document.origin == "team_manual" and document.status == "draft"
    for record in document.records:
        assert record.answers
        assert all(item.answer is None and item.explanation is None and item.evidence == ()
                   for item in record.answers)
        assert record.review.annotated_by is None
        assert record.review.language_understood is False
        assert [row.criterion_id for row in record.answers] == [
            item.id for item in record.evaluation_input.checklist.criteria
        ]
        assert record.evaluation_input.conversation.messages[0].text
        assert record.evaluation_input.checklist.criteria[3].options
    with pytest.raises(ValueError, match="Manual annotator"):
        finalize_references(document, "tune")


def test_split_is_order_independent_and_keeps_translations_together() -> None:
    document = prepare_sample()
    inputs = [record.evaluation_input for record in reversed(document.records)]
    groups = [ScenarioAssignment(conversation_version_id=record.evaluation_input.conversation.id,
                                 scenario_group=record.scenario_group)
              for record in reversed(document.records)]
    assert prepare_drafts(inputs, groups, seed=17, holdout_groups=1) == document
    translations = [record.split for record in document.records if record.scenario_group == "translated"]
    assert len(translations) == 2 and len(set(translations)) == 1
    assert {record.split for record in document.records} == {"tune", "holdout"}
    groups_list = [str(index) for index in range(20)]
    assert split_groups(groups_list, 1, 5) != split_groups(groups_list, 2, 5)


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "empty", "one_group"])
def test_mapping_is_explicit_complete_nonempty_and_unique(mutation: str) -> None:
    cases = [sample_case("a"), sample_case("b")]
    raw = [{"conversation_version_id": case.conversation.id, "scenario_group": str(index)}
           for index, case in enumerate(cases)]
    if mutation == "missing":
        raw.pop()
    elif mutation == "extra":
        raw.append({"conversation_version_id": "unknown", "scenario_group": "new"})
    elif mutation == "duplicate":
        raw.append(raw[0].copy())
    elif mutation == "empty":
        raw[0]["scenario_group"] = "   "
    else:
        raw[1]["scenario_group"] = raw[0]["scenario_group"]
    with pytest.raises(ValueError):
        assignments = [ScenarioAssignment.model_validate(item) for item in raw]
        prepare_drafts(cases, assignments, seed=1, holdout_groups=1)


def test_duplicate_inputs_and_exact_content_leakage_are_rejected() -> None:
    cases = [sample_case("a"), sample_case("b")]
    assignments = [ScenarioAssignment(conversation_version_id=case.conversation.id,
                                      scenario_group=str(index)) for index, case in enumerate(cases)]
    with pytest.raises(ValueError, match="Duplicate conversation/checklist"):
        prepare_drafts([*cases, cases[0]], assignments, seed=1, holdout_groups=1)
    raw = cases[0].model_dump(mode="json")
    raw["conversation"].update(id=cases[1].conversation.id, conversation_id="b")
    with pytest.raises(ValueError, match="Exact duplicate"):
        prepare_drafts([cases[0], EvaluationInput.model_validate(raw)], assignments,
                       seed=1, holdout_groups=1)


def test_changed_versions_of_one_logical_dialogue_cannot_leak_across_splits() -> None:
    cases = [sample_case("old-version"), sample_case("new-version"), sample_case("independent")]
    for index in (0, 1):
        raw = cases[index].model_dump(mode="json")
        raw["conversation"]["conversation_id"] = "one-logical-dialogue"
        cases[index] = EvaluationInput.model_validate(raw)
    assignments = [ScenarioAssignment(conversation_version_id=case.conversation.id,
                                      scenario_group=f"scenario-{index}")
                   for index, case in enumerate(cases)]
    with pytest.raises(ValueError, match="logical conversation"):
        prepare_drafts(cases, assignments, seed=17, holdout_groups=1)
    assignments[1] = ScenarioAssignment(conversation_version_id=cases[1].conversation.id,
                                        scenario_group=assignments[0].scenario_group)
    document = prepare_drafts(cases, assignments, seed=17, holdout_groups=1)
    versions = [record for record in document.records
                if record.evaluation_input.conversation.conversation_id == "one-logical-dialogue"]
    assert len(versions) == 2 and len({record.split for record in versions}) == 1


def test_logical_identity_includes_dataset_scope() -> None:
    cases = [sample_case("a"), sample_case("b")]
    for index, case in enumerate(cases):
        raw = case.model_dump(mode="json")
        raw["conversation"].update(conversation_id="reused-external-id", dataset_group_id=f"dataset-{index}")
        cases[index] = EvaluationInput.model_validate(raw)
    assignments = [ScenarioAssignment(conversation_version_id=case.conversation.id,
                                      scenario_group=f"scenario-{index}")
                   for index, case in enumerate(cases)]
    document = prepare_drafts(cases, assignments, seed=17, holdout_groups=1)
    assert {record.split for record in document.records} == {"tune", "holdout"}


@pytest.mark.parametrize("mutation", ["snapshot", "criteria", "split", "origin"])
def test_edited_draft_cannot_silently_change_fixed_inputs(mutation: str) -> None:
    raw = prepare_sample().model_dump(mode="json")
    record = raw["records"][0]
    if mutation == "snapshot":
        record["evaluation_input"]["checklist"]["criteria"][0]["weight"] = "11"
    elif mutation == "criteria":
        record["answers"].pop()
    elif mutation == "split":
        record["split"] = "holdout" if record["split"] == "tune" else "tune"
    else:
        raw["origin"] = "organizer"
    with pytest.raises(ValidationError):
        DraftDocument.model_validate(raw)


def test_completed_reference_uses_scorer_and_is_provider_independent() -> None:
    draft = manually_completed(prepare_sample().records[0])
    reference = complete_reference(draft)
    assert str(reference.total) == "85.38" and reference.origin == "team_manual"
    raw = draft.model_dump(mode="json")
    raw["evaluation_input"]["settings"].update(mode="live", provider="future-provider", model="future-model")
    assert complete_reference(AnnotationDraft.model_validate(raw)) == reference


def test_finalize_exports_only_completed_selected_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import evals.prepare as preparation

    monkeypatch.setattr(preparation, "REPOSITORY_ROOT", tmp_path)
    document = prepare_sample()
    raw = document.model_dump(mode="json")
    raw["records"] = [
        manually_completed(record).model_dump(mode="json") if record.split == "tune"
        else record.model_dump(mode="json") for record in document.records
    ]
    completed = DraftDocument.model_validate(raw)
    expected = sum(record.split == "tune" for record in completed.records)
    assert len(finalize_references(completed, "tune")) == expected
    with pytest.raises(ValueError, match="Manual annotator"):
        finalize_references(completed, "holdout")
    draft_path = tmp_path / "data/private/filled-draft.json"
    export_path = tmp_path / "artifacts/private/tune-references.json"
    write_private_json(draft_path, raw, tmp_path)
    main(["finalize", "--draft", str(draft_path), "--split", "tune", "--output", str(export_path)])
    references = [ReferenceRecord.model_validate(item)
                  for item in json.loads(export_path.read_text(encoding="utf-8"))]
    assert len(references) == expected
    assert all(item.origin == "team_manual" and item.split == "tune" for item in references)
    assert all(str(item.total) == "85.38" for item in references)


@pytest.mark.parametrize("mutation", ["blank", "scale", "na", "missing_evidence", "foreign", "reversed"])
def test_incomplete_or_invalid_manual_answers_cannot_become_references(mutation: str) -> None:
    raw = manually_completed(prepare_sample().records[0]).model_dump(mode="json")
    if mutation == "blank":
        raw["answers"][0]["answer"] = None
    elif mutation == "scale":
        raw["answers"][3]["answer"]["value"] = 17
    elif mutation == "na":
        raw["answers"][0]["answer"] = {"type": "na", "reason": "Not permitted"}
    elif mutation == "missing_evidence":
        raw["answers"][0]["evidence"] = []
    elif mutation == "foreign":
        raw["answers"][0]["evidence"][0]["conversation_version_id"] = "other"
    else:
        raw["answers"][0]["evidence"][0].update(start_message_id="m8", end_message_id="m1")
    with pytest.raises((ValueError, DomainValidationError)):
        complete_reference(AnnotationDraft.model_validate(raw))


def test_kz_requires_explicit_human_language_understanding() -> None:
    kz = next(record for record in prepare_sample().records
              if record.evaluation_input.conversation.language == "KZ")
    raw = manually_completed(kz).model_dump(mode="json")
    raw["review"]["language_understood"] = False
    with pytest.raises(ValueError, match="language understanding"):
        complete_reference(AnnotationDraft.model_validate(raw))


def test_ambiguity_requires_recorded_resolution_and_distinct_reviewer() -> None:
    raw = manually_completed(prepare_sample().records[0]).model_dump(mode="json")
    raw["answers"][3]["ambiguity_note"] = "Правильность ответа требует регламента."
    with pytest.raises(ValueError, match="Ambiguous"):
        complete_reference(AnnotationDraft.model_validate(raw))
    raw["answers"][3]["resolution_note"] = "Второй человек проверил учебное правило."
    raw["review"].update(reviewed_by=raw["review"]["annotated_by"], reviewed_at="2026-11-07T11:00:00+05:00")
    with pytest.raises(ValueError, match="second reviewer"):
        complete_reference(AnnotationDraft.model_validate(raw))
    raw["review"]["reviewed_by"] = "Synthetic second reviewer"
    assert complete_reference(AnnotationDraft.model_validate(raw)).origin == "team_manual"


def test_private_paths_and_exclusive_creation_protect_source_files(tmp_path: Path) -> None:
    target = tmp_path / "data/private/draft.json"
    write_private_json(target, {"origin": "team_manual"}, tmp_path)
    with pytest.raises(FileExistsError):
        write_private_json(target, {}, tmp_path)
    assert json.loads(target.read_text(encoding="utf-8"))["origin"] == "team_manual"
    for wrong in (tmp_path / "references.json", tmp_path / "data/private/../../public.json"):
        with pytest.raises(ValueError, match="private"):
            private_path(wrong, tmp_path)


def test_cli_creates_blank_draft_and_rejects_unsafe_output_without_content_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import evals.prepare as preparation

    monkeypatch.setattr(preparation, "REPOSITORY_ROOT", tmp_path)
    cases = [sample_case("a"), sample_case("b")]
    input_path = tmp_path / "data/private/inputs.json"
    groups_path = tmp_path / "data/private/groups.json"
    output_path = tmp_path / "data/private/draft.json"
    write_private_json(input_path, [case.model_dump(mode="json") for case in cases], tmp_path)
    write_private_json(groups_path, [{"conversation_version_id": case.conversation.id,
                                     "scenario_group": str(index)} for index, case in enumerate(cases)], tmp_path)
    args = ["prepare", "--inputs", str(input_path), "--groups", str(groups_path),
            "--seed", "42", "--holdout-groups", "1", "--output", str(output_path)]
    main(args)
    draft = DraftDocument.model_validate_json(output_path.read_text(encoding="utf-8"))
    assert len(draft.records) == 2 and draft.records[0].answers[0].answer is None
    with pytest.raises(SystemExit) as error:
        main(args[:-1] + [str(tmp_path / "public.json")])
    assert error.value.code == 2 and not (tmp_path / "public.json").exists()
    input_path.write_text('[{"PRIVATE_TRANSCRIPT_DO_NOT_PRINT": "client details"}]', encoding="utf-8")
    with pytest.raises(SystemExit):
        main(args)
    assert "PRIVATE_TRANSCRIPT_DO_NOT_PRINT" not in capsys.readouterr().err
