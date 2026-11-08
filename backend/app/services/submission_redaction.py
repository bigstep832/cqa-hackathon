"""Explicit reviewed redactions of public explanations, never of stored AI answers."""

from __future__ import annotations

from hashlib import sha256
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.contracts.models import FrozenModel, NonEmpty

REDACTION_MARKER = "[данные скрыты]"


class RedactionSpan(FrozenModel):
    # Python Unicode code-point positions in the original, before normalization.
    start: Annotated[int, Field(strict=True, ge=0)]
    end: Annotated[int, Field(strict=True, gt=0)]

    @model_validator(mode="after")
    def ordered(self) -> RedactionSpan:
        if self.end <= self.start:
            raise ValueError("Redaction span must be nonempty and ordered")
        return self


class ReviewedRationaleRedaction(FrozenModel):
    evaluation_id: NonEmpty
    criterion_id: Literal["C01", "C02", "C03", "C04", "C05", "C06", "C07", "C08"]
    original_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    reviewed_by: NonEmpty
    reason: Literal["personal_data", "secret", "private_quote"]
    spans: Annotated[tuple[RedactionSpan, ...], Field(min_length=1, max_length=64)]

    @model_validator(mode="after")
    def disjoint(self) -> ReviewedRationaleRedaction:
        if any(a.end > b.start for a, b in zip(self.spans, self.spans[1:])):
            raise ValueError("Redaction spans must be sorted and non-overlapping")
        return self


def redact_rationale(original: str, review: ReviewedRationaleRedaction) -> str:
    if sha256(original.encode("utf-8")).hexdigest() != review.original_sha256:
        raise ValueError("Reviewed rationale hash differs from the original")
    pieces: list[str] = []
    visible: list[str] = []
    previous = 0
    for span in review.spans:
        if span.end > len(original) or not original[span.start:span.end].strip():
            raise ValueError("Redaction span is outside the original or contains only whitespace")
        visible.append(original[previous:span.start])
        pieces.extend((original[previous:span.start], REDACTION_MARKER))
        previous = span.end
    visible.append(original[previous:])
    pieces.append(original[previous:])
    if not any(character.isalpha() for character in "".join(visible)):
        raise ValueError("Redaction must retain the explanation outside private spans")
    return "".join(pieces)
