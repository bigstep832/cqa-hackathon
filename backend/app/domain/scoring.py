"""Единый scorer для исходных ответов и будущих решений по апелляциям."""

from collections.abc import Sequence
from decimal import Decimal, localcontext
from fractions import Fraction

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


def _finite_decimal(value: Fraction) -> Decimal:
    """Точное Decimal-представление конечной десятичной дроби без context."""
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        twos += 1
        denominator //= 2
    while denominator % 5 == 0:
        fives += 1
        denominator //= 5
    if denominator != 1:
        raise ValueError("Expected a finite decimal fraction")
    places = max(twos, fives)
    coefficient = value.numerator * 2 ** (places - twos) * 5 ** (places - fives)
    parts = Decimal(coefficient).as_tuple()
    return Decimal((parts.sign, parts.digits, -places))


def _percentage_half_up(earned: Fraction, weight: Fraction) -> Decimal:
    """Авторитетный итог: точное HALF_UP до сотых целочисленным divmod."""
    cents = earned * 10000 / weight
    quotient, remainder = divmod(cents.numerator, cents.denominator)
    rounded = quotient + int(2 * remainder >= cents.denominator)
    return Decimal((0, Decimal(rounded).as_tuple().digits, -2))


def score(checklist: ChecklistVersion, answers: Sequence[CriterionAnswer]) -> ScoreResult:
    """Веса, quality, произведения и суммы вычисляются как точные дроби.

    HALF_UP — инженерное допущение. 50 значащих цифр используются только для
    отображаемых нормированных вкладов; итог от их приближения не зависит.
    """
    indexed = validate_answers(checklist, answers)
    raw: list[tuple[CriterionDefinition, AnswerOption | None, Fraction | None]] = []
    total_weight = Fraction(0)
    total_earned = Fraction(0)
    flags: list[str] = []
    for criterion in checklist.criteria:
        option = resolve_option(criterion, indexed[criterion.id].answer)
        earned: Fraction | None = None
        if option is not None and criterion.kind == "scored":
            assert criterion.weight is not None and option.quality is not None
            earned = Fraction(criterion.weight) * Fraction(option.quality)
            total_weight += Fraction(criterion.weight)
            total_earned += earned
        if option is not None and criterion.kind == "flag" and option.flagged:
            flags.append(criterion.id)
        raw.append((criterion, option, earned))

    # Только отображение повторяющихся дробей ограничено 50 цифрами.
    with localcontext() as context:
        context.prec = 50

        def normalized(value: Fraction) -> Decimal | None:
            if not total_weight:
                return None
            fraction = 100 * value / total_weight
            return Decimal(fraction.numerator) / Decimal(fraction.denominator)

        criteria = tuple(
            CriterionScore(
                criterion_id=criterion.id,
                applicable=option is not None,
                weight=criterion.weight,
                quality=option.quality if option is not None else None,
                earned_points=_finite_decimal(earned) if earned is not None else None,
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
                Fraction(0),
            )
            section_weight = sum(
                (Fraction(criterion.weight) for criterion, option, _ in raw
                 if criterion.section_id == section.id and option is not None
                 and criterion.kind == "scored" and criterion.weight is not None),
                Fraction(0),
            )
            sections.append(SectionScore(
                section_id=section.id,
                title=section.title,
                earned_points=_finite_decimal(section_earned),
                applicable_weight=_finite_decimal(section_weight),
                normalized_points=normalized(section_earned),
                normalized_maximum=normalized(section_weight),
            ))
        return ScoreResult(
            total=_percentage_half_up(total_earned, total_weight) if total_weight else None,
            applicable_weight=_finite_decimal(total_weight),
            earned_points=_finite_decimal(total_earned),
            reason=None if total_weight else "Нет применимых критериев с положительным весом",
            criteria=criteria,
            sections=tuple(sections),
            flags=tuple(flags),
        )
