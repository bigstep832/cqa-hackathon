"""Офлайн-заготовки ручной разметки команды и разбиение по явным группам сценариев."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, StrictBool, TypeAdapter, ValidationError, model_validator

from app.contracts.models import (
    Answer,
    CriterionAnswer,
    EvaluationInput,
    EvidenceRef,
    FrozenModel,
    NonEmpty,
)
from app.domain.scoring import score
from app.domain.snapshots import conversation_content_hash, stable_hash
from evals.compare import ReferenceAnswer, ReferenceRecord

Split = Literal["tune", "holdout"]
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class ScenarioAssignment(FrozenModel):
    conversation_version_id: NonEmpty
    scenario_group: NonEmpty


class DraftAnswer(FrozenModel):
    criterion_id: NonEmpty
    answer: Answer | None = None
    explanation: NonEmpty | None = None
    evidence: tuple[EvidenceRef, ...] = ()
    ambiguity_note: NonEmpty | None = None
    resolution_note: NonEmpty | None = None


class ManualReview(FrozenModel):
    method: Literal["human_review"] = "human_review"
    annotated_by: NonEmpty | None = None
    completed_at: AwareDatetime | None = None
    language_understood: StrictBool = False
    reviewed_by: NonEmpty | None = None
    reviewed_at: AwareDatetime | None = None


def snapshot_hash(evaluation_input: EvaluationInput) -> str:
    # Model/provider settings are preserved for context but do not define a manual reference.
    return stable_hash({
        "conversation": evaluation_input.conversation.model_dump(mode="json"),
        "checklist": evaluation_input.checklist.model_dump(mode="json"),
    })


class AnnotationDraft(FrozenModel):
    evaluation_input: EvaluationInput
    input_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    scenario_group: NonEmpty
    split: Split
    answers: tuple[DraftAnswer, ...]
    review: ManualReview = Field(default_factory=ManualReview)

    @model_validator(mode="after")
    def validate_snapshots(self) -> AnnotationDraft:
        if self.input_hash != snapshot_hash(self.evaluation_input):
            raise ValueError("Draft input snapshot changed; prepare a new version")
        conversation = self.evaluation_input.conversation
        if conversation.content_hash != conversation_content_hash(conversation):
            raise ValueError("Conversation content hash does not match its snapshot")
        expected = [criterion.id for criterion in self.evaluation_input.checklist.criteria]
        if [answer.criterion_id for answer in self.answers] != expected:
            raise ValueError("Draft answers must list every checklist criterion exactly once in order")
        return self


def split_groups(groups: Sequence[str], seed: int, holdout_groups: int) -> dict[str, Split]:
    """Порядок входных диалогов не влияет на детерминированный hash-rank групп."""
    unique = sorted(set(groups))
    if any(not group.strip() or group != group.strip() for group in unique):
        raise ValueError("Scenario groups must be nonempty normalized strings")
    if not 1 <= holdout_groups < len(unique):
        raise ValueError("Choose at least one holdout group and leave at least one tune group")
    ranked = sorted(unique, key=lambda group: (stable_hash([seed, group]), group))
    held_out = set(ranked[:holdout_groups])
    return {group: "holdout" if group in held_out else "tune" for group in unique}


class DraftDocument(FrozenModel):
    schema_version: Literal["team-annotation-v1"] = "team-annotation-v1"
    origin: Literal["team_manual"] = "team_manual"
    status: Literal["draft"] = "draft"
    seed: Annotated[int, Field(strict=True)]
    holdout_groups: Annotated[int, Field(strict=True, ge=1)]
    records: Annotated[tuple[AnnotationDraft, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_partition(self) -> DraftDocument:
        pairs: set[tuple[str, str]] = set()
        conversation_groups: dict[str, str] = {}
        content_groups: dict[str, str] = {}
        version_contents: dict[tuple[str, str], str] = {}
        assignment = split_groups(
            [record.scenario_group for record in self.records], self.seed, self.holdout_groups
        )
        for record in self.records:
            conversation = record.evaluation_input.conversation
            pair = (conversation.id, record.evaluation_input.checklist.id)
            if pair in pairs:
                raise ValueError("Duplicate conversation/checklist snapshot pair")
            pairs.add(pair)
            if record.split != assignment[record.scenario_group]:
                raise ValueError("Draft split differs from its frozen seed/group assignment")
            old_group = conversation_groups.setdefault(conversation.id, record.scenario_group)
            if old_group != record.scenario_group:
                raise ValueError("One conversation version cannot have several scenario groups")
            duplicate_group = content_groups.setdefault(conversation.content_hash, record.scenario_group)
            if duplicate_group != record.scenario_group:
                raise ValueError("Exact duplicate contents must remain in the same scenario group")
            for kind, version in (("conversation", conversation), ("checklist", record.evaluation_input.checklist)):
                content = stable_hash(version.model_dump(mode="json"))
                if version_contents.setdefault((kind, version.id), content) != content:
                    raise ValueError("One snapshot version ID cannot describe different contents")
        return self


def prepare_drafts(
    inputs: Sequence[EvaluationInput],
    assignments: Sequence[ScenarioAssignment],
    *,
    seed: int,
    holdout_groups: int,
) -> DraftDocument:
    """Создаёт только пустые ответы: не использует LLM, mock или готовую разметку."""
    mapping = {item.conversation_version_id: item.scenario_group for item in assignments}
    if len(mapping) != len(assignments):
        raise ValueError("Duplicate conversation version IDs in scenario mapping")
    if set(mapping) != {item.conversation.id for item in inputs}:
        raise ValueError("Scenario mapping must match the input conversation versions exactly")
    splits = split_groups(list(mapping.values()), seed, holdout_groups)
    records = tuple(
        AnnotationDraft(
            evaluation_input=item,
            input_hash=snapshot_hash(item),
            scenario_group=mapping[item.conversation.id],
            split=splits[mapping[item.conversation.id]],
            answers=tuple(DraftAnswer(criterion_id=criterion.id) for criterion in item.checklist.criteria),
        )
        for item in sorted(inputs, key=lambda item: (item.conversation.id, item.checklist.id))
    )
    return DraftDocument(seed=seed, holdout_groups=holdout_groups, records=records)


def complete_reference(draft: AnnotationDraft) -> ReferenceRecord:
    """Валидирует человеческий ввод; не подтверждает его смысловую правильность."""
    draft = AnnotationDraft.model_validate(draft.model_dump(mode="json"))
    review = draft.review
    if review.annotated_by is None or review.completed_at is None or not review.language_understood:
        raise ValueError("Manual annotator, completion time and language understanding are required")
    if (review.reviewed_by is None) != (review.reviewed_at is None):
        raise ValueError("Reviewer and review time must be recorded together")
    if review.reviewed_by is not None:
        if review.reviewed_by.casefold() == review.annotated_by.casefold():
            raise ValueError("The second reviewer must differ from the annotator")
        assert review.reviewed_at is not None
        if review.reviewed_at < review.completed_at:
            raise ValueError("Review cannot precede completed annotation")
    conversation = draft.evaluation_input.conversation
    positions = {message.message_id: index for index, message in enumerate(conversation.messages)}
    completed: list[CriterionAnswer] = []
    for item in draft.answers:
        if item.answer is None or item.explanation is None or not item.evidence:
            raise ValueError("Complete every answer, explanation and evidence before export")
        if item.ambiguity_note is not None and (
            item.resolution_note is None or review.reviewed_by is None
        ):
            raise ValueError("Ambiguous labels require a resolution and a second human reviewer")
        for evidence in item.evidence:
            if evidence.conversation_version_id != conversation.id:
                raise ValueError("Manual evidence belongs to another conversation snapshot")
            start = positions.get(evidence.start_message_id)
            end = positions.get(evidence.end_message_id)
            if start is None or end is None or start > end:
                raise ValueError("Manual evidence must reference an existing ordered message range")
        completed.append(CriterionAnswer(
            criterion_id=item.criterion_id,
            answer=item.answer,
            explanation=item.explanation,
            evidence=item.evidence,
        ))
    result = score(draft.evaluation_input.checklist, completed)
    return ReferenceRecord(
        conversation_version_id=conversation.id,
        checklist_version_id=draft.evaluation_input.checklist.id,
        language=conversation.language,
        channel=conversation.channel,
        origin="team_manual",
        split=draft.split,
        scenario_group=draft.scenario_group,
        answers=tuple(ReferenceAnswer(criterion_id=item.criterion_id, answer=item.answer) for item in completed),
        total=result.total,
    )


def finalize_references(document: DraftDocument, split: Split) -> tuple[ReferenceRecord, ...]:
    # Revalidate even when a caller used model_copy, which bypasses Pydantic validation.
    document = DraftDocument.model_validate(document.model_dump(mode="json"))
    return tuple(complete_reference(record) for record in document.records if record.split == split)


def private_path(path: Path, repository_root: Path | None = None) -> Path:
    resolved = path.resolve()
    root = (repository_root or REPOSITORY_ROOT).resolve()
    # Do not resolve the allowed roots: a symlink from data/private outside the repo is rejected.
    allowed_roots = (root / "data" / "private", root / "artifacts" / "private")
    if not any(resolved.is_relative_to(allowed) and resolved != allowed for allowed in allowed_roots):
        raise ValueError("Use a file inside this repository's data/private or artifacts/private")
    return resolved


def write_private_json(
    path: Path, value: object, repository_root: Path | None = None
) -> None:
    target = private_path(path, repository_root)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as output:
        output.write(encoded)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="Создать пустую ручную разметку и фиксированное разбиение")
    prepare.add_argument("--inputs", type=Path, required=True)
    prepare.add_argument("--groups", type=Path, required=True)
    prepare.add_argument("--seed", type=int, required=True)
    prepare.add_argument("--holdout-groups", type=int, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    for command in ("validate", "finalize"):
        subparser = subparsers.add_parser(command, help="Проверить завершённую ручную разметку выбранной части")
        subparser.add_argument("--draft", type=Path, required=True)
        subparser.add_argument("--split", choices=("tune", "holdout"), required=True)
        if command == "finalize":
            subparser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "prepare":
            inputs = TypeAdapter(list[EvaluationInput]).validate_json(
                private_path(arguments.inputs).read_text(encoding="utf-8")
            )
            groups = TypeAdapter(list[ScenarioAssignment]).validate_json(
                private_path(arguments.groups).read_text(encoding="utf-8")
            )
            document = prepare_drafts(
                inputs, groups, seed=arguments.seed, holdout_groups=arguments.holdout_groups
            )
            write_private_json(arguments.output, document.model_dump(mode="json"))
            counts = {split: sum(record.split == split for record in document.records)
                      for split in ("tune", "holdout")}
            print(f"Создан draft: tune={counts['tune']}, holdout={counts['holdout']}. Ответы не заполнены.")
        else:
            document = DraftDocument.model_validate_json(
                private_path(arguments.draft).read_text(encoding="utf-8")
            )
            references = finalize_references(document, arguments.split)
            if arguments.command == "finalize":
                write_private_json(arguments.output, [record.model_dump(mode="json") for record in references])
            print(f"Проверено ручных эталонов команды ({arguments.split}): {len(references)}. Это не Ground Truth организаторов.")
    except ValidationError:
        parser.error("Некорректный JSON, snapshot, разбиение или формат ответа. Содержимое приватных данных не выводится.")
    except FileExistsError:
        parser.error("Выходной файл уже существует; выберите новое имя. Перезапись запрещена.")
    except OSError:
        parser.error("Не удалось прочитать или создать локальный приватный файл.")
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
