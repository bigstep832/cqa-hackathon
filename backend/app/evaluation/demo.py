"""Загрузка только публичных синтетических fixtures, без fallback для live."""

import json
from pathlib import Path

from app.contracts.models import (
    ChecklistVersion,
    ConversationVersion,
    CriterionAnswer,
    DemoPayload,
    Evaluation,
    EvaluationInput,
    EvaluationSettings,
)
from app.domain.scoring import score
from app.evaluation.validation import validate_output

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def load_synthetic_input() -> EvaluationInput:
    return EvaluationInput(
        conversation=ConversationVersion.model_validate_json(
            (REPOSITORY_ROOT / "fixtures/synthetic/conversation.json").read_text(encoding="utf-8")
        ),
        checklist=ChecklistVersion.model_validate_json(
            (REPOSITORY_ROOT / "config/checklists/default.v1.json").read_text(encoding="utf-8")
        ),
        settings=EvaluationSettings(mode="mock", provider="fixture", model="synthetic-v1"),
    )


def load_synthetic_answers() -> tuple[CriterionAnswer, ...]:
    data = json.loads((REPOSITORY_ROOT / "fixtures/synthetic/answers.json").read_text(encoding="utf-8"))
    return tuple(CriterionAnswer.model_validate(answer) for answer in data)


async def build_demo_payload() -> DemoPayload:
    from app.evaluation.mock import MockEvaluationProvider

    evaluation_input = load_synthetic_input()
    output = await MockEvaluationProvider().evaluate(evaluation_input)
    validate_output(evaluation_input, output)
    result = score(evaluation_input.checklist, output.answers)
    evaluation = Evaluation(
        id="synthetic-evaluation-v1",
        conversation_version_id=evaluation_input.conversation.id,
        checklist_version_id=evaluation_input.checklist.id,
        status="succeeded",
        created_at=evaluation_input.conversation.imported_at,
        output=output,
        original_score=result,
        effective_score=result,
    )
    return DemoPayload(
        conversation=evaluation_input.conversation,
        checklist=evaluation_input.checklist,
        evaluation=evaluation,
    )
