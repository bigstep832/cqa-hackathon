"""Единый scorer для исходных ответов и будущих решений по апелляциям."""

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal, localcontext

from app.contracts.models import (
    Answer,
    AnswerOption,
    ChecklistVersion,
    CriterionAnswer,
    CriterionDefinition,
    CriterionScore,
    NotApplicableAnswer,
    ScoreResult,
    SectionScore,
    value_key,
)


class DomainValidationError(ValueError):
    """Недопустимый ответ: никогда не превращается в нулевой балл."""


def resolve_option(criterion: CriterionDefinition, answer: Answer) -> AnswerOption | None:
    if isinstance(answer, NotApplicableAnswer):
        if not criterion.allow_na:
            raise DomainValidationError(f"{criterion.id}: N/A is not allowed")
        return None
    if answer.type != criterion.answer_type:
        raise DomainValidationError(f"{criterion.id}: answer type does not match the checklist")
    key = value_key(answer.value)
    for option in criterion.options:
        if value_key(option.value) == key:
            return option
    raise DomainValidationError(f"{criterion.id}: answer is not a configured option")


def validate_answers(
    checklist: ChecklistVersion, answers: Sequence[CriterionAnswer]
) -> dict[str, CriterionAnswer]:
    indexed = {answer.criterion_id: answer for answer in answers}
    if len(indexed) != len(answers):
        raise DomainValidationError("Duplicate criterion answers")
    expected = {criterion.id for criterion in checklist.criteria}
    if set(indexed) != expected:
        raise DomainValidationError("Answers must match exactly the active checklist criteria")
    for criterion in checklist.criteria:
        resolve_option(criterion, indexed[criterion.id].answer)
    return indexed


def score(checklist: ChecklistVersion, answers: Sequence[CriterionAnswer]) -> ScoreResult:
    """Веса и quality перемножаются точно; округляется только итог до сотых.

    HALF_UP — инженерное допущение. 50 значащих цифр используются лишь при
    представлении бесконечных нормированных дробей, а не для округления весов.
    """
    indexed = validate_answers(checklist, answers)
    raw: list[tuple[CriterionDefinition, AnswerOption | None, Decimal | None]] = []
    total_weight = Decimal(0)
    total_earned = Decimal(0)
    flags: list[str] = []
    # Веса/quality имеют ограниченную практическую точность; высокую precision
    # сохраняем и для умножения, чтобы не потерять значимые десятичные разряды.
    with localcontext() as context:
        context.prec = 50
        for criterion in checklist.criteria:
            option = resolve_option(criterion, indexed[criterion.id].answer)
            earned: Decimal | None = None
            if option is not None and criterion.kind == "scored":
                assert criterion.weight is not None and option.quality is not None
                earned = criterion.weight * option.quality
                total_weight += criterion.weight
                total_earned += earned
            if option is not None and criterion.kind == "flag" and option.flagged:
                flags.append(criterion.id)
            raw.append((criterion, option, earned))

        def normalized(value: Decimal) -> Decimal | None:
            return Decimal(100) * value / total_weight if total_weight else None

        criteria = tuple(
            CriterionScore(
                criterion_id=criterion.id,
                applicable=option is not None,
                weight=criterion.weight,
                quality=option.quality if option is not None else None,
                earned_points=earned,
                normalized_points=normalized(earned) if earned is not None else None,
                flagged=bool(option is not None and option.flagged),
            )
            for criterion, option, earned in raw
        )
        sections: list[SectionScore] = []
        for section in checklist.sections:
            section_earned = sum(
                (earned for criterion, _, earned in raw
                 if criterion.section_id == section.id and earned is not None),
                Decimal(0),
            )
            section_weight = sum(
                (criterion.weight for criterion, option, _ in raw
                 if criterion.section_id == section.id and option is not None
                 and criterion.kind == "scored" and criterion.weight is not None),
                Decimal(0),
            )
            sections.append(SectionScore(
                section_id=section.id,
                title=section.title,
                earned_points=section_earned,
                applicable_weight=section_weight,
                normalized_points=normalized(section_earned),
                normalized_maximum=normalized(section_weight),
            ))
        unrounded = normalized(total_earned)
        return ScoreResult(
            total=unrounded.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if unrounded is not None else None,
            applicable_weight=total_weight,
            earned_points=total_earned,
            reason=None if total_weight else "Нет применимых критериев с положительным весом",
            criteria=criteria,
            sections=tuple(sections),
            flags=tuple(flags),
        )
