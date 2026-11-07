"""Контентные fingerprints исключают технические ID, сохраняя исходный порядок."""

import hashlib
import json

from app.contracts.models import ChecklistVersion, ConversationVersion


def stable_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def conversation_content_hash(conversation: ConversationVersion) -> str:
    return stable_hash({
        "language": conversation.language,
        "channel": conversation.channel,
        "messages": [
            {"speaker": message.speaker, "text": message.text,
             "timestamp_raw": message.timestamp_raw, "order": message.order,
             "audio_offset_seconds": message.audio_offset_seconds}
            for message in conversation.messages
        ],
        "audio_url": str(conversation.audio_url) if conversation.audio_url is not None else None,
    })


def checklist_content_hash(checklist: ChecklistVersion) -> str:
    return stable_hash(checklist.model_dump(
        mode="json", exclude={"id", "checklist_id", "version", "created_at"}
    ))
