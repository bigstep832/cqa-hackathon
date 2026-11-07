from app.contracts.models import EvaluationInput, EvaluationOutput
from app.domain.scoring import DomainValidationError, validate_answers
from app.domain.snapshots import conversation_content_hash


def validate_output(evaluation_input: EvaluationInput, output: EvaluationOutput) -> None:
    """Проверяет технические ссылки, полноту и типы; не доказывает смысл evidence."""
    conversation = evaluation_input.conversation
    if conversation.content_hash != conversation_content_hash(conversation):
        raise DomainValidationError("Conversation content hash does not match its snapshot")
    validate_answers(evaluation_input.checklist, output.answers)
    if output.metadata.mode != evaluation_input.settings.mode:
        raise DomainValidationError("Provider mode does not match requested mode")
    if output.metadata.provider != evaluation_input.settings.provider:
        raise DomainValidationError("Provider metadata does not match requested provider")
    if output.metadata.model != evaluation_input.settings.model:
        raise DomainValidationError("Model metadata does not match requested model")
    positions = {message.message_id: index for index, message in enumerate(conversation.messages)}
    for answer in output.answers:
        for evidence in answer.evidence:
            if evidence.conversation_version_id != conversation.id:
                raise DomainValidationError("Evidence belongs to another conversation version")
            if evidence.start_message_id not in positions or evidence.end_message_id not in positions:
                raise DomainValidationError("Evidence references a nonexistent message")
            if positions[evidence.start_message_id] > positions[evidence.end_message_id]:
                raise DomainValidationError("Evidence range is reversed")
