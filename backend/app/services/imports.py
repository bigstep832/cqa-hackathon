"""Import a complete organizer response from a server-configured signed URL."""

import os
from urllib.parse import parse_qsl, urlencode, urlunsplit

from app.contracts.models import ConversationImportRequest, DatasetImportRequest, ImportResponse
from app.services import source_http
from app.services.json_input import decode_json
from app.storage.conversations import import_conversations


def import_dataset(request: DatasetImportRequest) -> ImportResponse:
    try:
        target = source_http.https_target(os.environ.get("ORGANIZER_API_URL", ""))
        if any(
            key.casefold() == "group_id"
            for key, _ in parse_qsl(target.query, keep_blank_values=True)
        ):
            raise ValueError("Configured URL must omit group_id")
    except ValueError:
        raise source_http.failure(
            503, "SOURCE_NOT_CONFIGURED", "Источник импорта не настроен."
        ) from None
    query = "&".join(filter(None, (target.query, urlencode({"group_id": request.group_id}))))
    url = urlunsplit(target._replace(query=query))
    status, _, payload = source_http.fetch(url, max_bytes=16 * 1024 * 1024)
    try:
        if status != 200:
            raise ValueError("Unexpected upstream status")
        envelope = decode_json(payload)
        if envelope["isSuccess"] is not True or envelope.get("error") not in (None, ""):
            raise ValueError("Source reported failure")
        records = envelope["data"]["records"]
        batch = ConversationImportRequest(dataset_group_id=request.group_id, records=records)
        if any(
            record["conversation_id"] != item.conversation_id
            for record, item in zip(records, batch.records, strict=True)
        ):
            raise ValueError("External IDs must be preserved verbatim")
    except (ValueError, KeyError, TypeError, RecursionError):
        raise source_http.failure(
            502, "INVALID_SOURCE_DATA", "Источник вернул некорректный набор диалогов."
        ) from None
    return import_conversations(batch, source="organizer")
