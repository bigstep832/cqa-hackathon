from app.contracts.models import (
    CriterionAnswer,
    EvaluationInput,
    EvaluationMetadata,
    EvaluationOutput,
    EvidenceRef,
)
from app.domain.snapshots import checklist_content_hash, conversation_content_hash
from app.evaluation.demo import load_synthetic_answers, load_synthetic_input
from app.evaluation.provider import MockInputNotSupported
from app.evaluation.validation import validate_output


class MockEvaluationProvider:
    """Жёстко ограниченный учебный пример, не оценщик произвольных диалогов."""

    async def evaluate(self, evaluation_input: EvaluationInput) -> EvaluationOutput:
        trusted = load_synthetic_input()
        conversation = evaluation_input.conversation
        if (
            evaluation_input.settings.mode != "mock"
            or evaluation_input.settings.provider != "fixture"
            or evaluation_input.settings.model != "synthetic-v1"
            or not conversation.synthetic
            or conversation.source != "synthetic"
            or conversation.dataset_group_id != "SYNTHETIC"
            or conversation_content_hash(conversation) != conversation_content_hash(trusted.conversation)
            or conversation.content_hash != conversation_content_hash(conversation)
            or checklist_content_hash(evaluation_input.checklist) != checklist_content_hash(trusted.checklist)
        ):
            raise MockInputNotSupported("Mock supports only the bundled synthetic example in mock mode")
        # Технические ID могут меняться; evidence связывается по сохранённому порядку.
        id_map = {
            source.message_id: target.message_id
            for source, target in zip(trusted.conversation.messages, conversation.messages, strict=True)
        }
        answers = tuple(
            CriterionAnswer(
                criterion_id=item.criterion_id,
                answer=item.answer,
                explanation=item.explanation,
                evidence=tuple(EvidenceRef(
                    conversation_version_id=conversation.id,
                    start_message_id=id_map[evidence.start_message_id],
                    end_message_id=id_map[evidence.end_message_id],
                ) for evidence in item.evidence),
            )
            for item in load_synthetic_answers()
        )
        output = EvaluationOutput(
            answers=answers,
            metadata=EvaluationMetadata(
                mode="mock", provider="fixture", model="synthetic-v1",
                instruction_version="synthetic-v1", pipeline_version="bootstrap-v1", calls=(),
            ),
        )
        validate_output(evaluation_input, output)
        return output
