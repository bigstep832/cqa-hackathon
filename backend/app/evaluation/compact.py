"""Опциональное представление запросов без сокращения рубрики и диалога.

Модуль не выбирает provider, не выполняет HTTP и не меняет форму ответа. Подключение
к OpenAI/Groq и версия pipeline задаются вызывающим кодом явно; Gemini не затронут.
Сжатие размера запроса не гарантирует прохождение TPM или точность оценки.
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from os.path import commonprefix
from typing import Any, Literal

from app.contracts.models import ChecklistVersion, EvaluationInput
from app.evaluation.structured import build_response_schema, build_user_payload

COMPACT_WIRE_VERSION = "cqa-compact-v1"
RequestEncoding = Literal["canonical", "compact-v1"]
COMPACT_FORMAT_INSTRUCTION = """
Компактное представление без сокращения содержимого: массивы sections, criteria и вложенных options читаются по sections_columns, criteria_columns и option_columns. Массивы messages читаются по message_columns. При order_start порядок сообщения равен order_start + индексу строки с нуля. timestamp_fragment восстанавливается как timestamp_format.prefix + fragment + timestamp_format.suffix; null остаётся null. message_id указан явно и без изменений: используй его в evidence.
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _table(rows: list[dict[str, Any]]) -> tuple[list[str], list[list[Any]]]:
    columns = list(rows[0]) if rows else []
    if any(set(row) != set(columns) for row in rows):
        raise ValueError("Table rows must have the same fields")
    return columns, [[row[key] for key in columns] for row in rows]


def _records(columns: list[str], rows: list[list[Any]]) -> list[dict[str, Any]]:
    if len(columns) != len(set(columns)):
        raise ValueError("Table columns must be unique")
    return [dict(zip(columns, row, strict=True)) for row in rows]


def pack_rubric(rubric: dict[str, Any]) -> dict[str, Any]:
    """Все поля criteria/options/sections остаются в таблицах, включая descriptions."""
    result = copy.deepcopy(rubric)
    for field in ("sections", "criteria"):
        result[field + "_columns"], result[field] = _table(result[field])
    options_index = result["criteria_columns"].index("options")
    options = [option for row in result["criteria"] for option in row[options_index]]
    option_columns, _ = _table(options)
    result["option_columns"] = option_columns
    for row in result["criteria"]:
        row[options_index] = [
            [option[key] for key in option_columns] for option in row[options_index]
        ]
    return result


def unpack_rubric(packed: dict[str, Any]) -> dict[str, Any]:
    """Обратное преобразование для воспроизводимой проверки полного snapshot."""
    result = copy.deepcopy(packed)
    option_columns = result.pop("option_columns")
    for field in ("sections", "criteria"):
        result[field] = _records(result.pop(field + "_columns"), result[field])
    for criterion in result["criteria"]:
        criterion["options"] = _records(option_columns, criterion["options"])
    return result


def pack_dialogue_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Не парсит даты, не меняет текст/ID/роли; общий prefix/suffix — точные строки."""
    result = copy.deepcopy(payload)
    dialogue = result["untrusted_dialogue"]
    messages = dialogue.pop("messages")
    columns, _ = _table(messages)

    # В валидном ConversationVersion order всегда 0..n-1. Для произвольного
    # локального payload неденсный порядок сохраняется отдельным столбцом.
    orders = [message["order"] for message in messages]
    if all(type(order) is int for order in orders) and orders == list(range(len(messages))):
        columns.remove("order")
        dialogue["order_start"] = 0

    timestamps = [
        message["timestamp_raw"] for message in messages if message["timestamp_raw"] is not None
    ]
    if any(not isinstance(timestamp, str) for timestamp in timestamps):
        raise ValueError("Raw timestamps must be strings or null")
    prefix = commonprefix(timestamps)
    # Suffix вычисляется только после удаления prefix: пересечение невозможно,
    # в том числе для одинаковых строк и строки, целиком равной prefix.
    suffix = commonprefix([timestamp[len(prefix):][::-1] for timestamp in timestamps])[::-1]
    dialogue["timestamp_format"] = {"prefix": prefix, "suffix": suffix}
    columns[columns.index("timestamp_raw")] = "timestamp_fragment"
    for message in messages:
        timestamp = message.pop("timestamp_raw")
        if timestamp is not None:
            timestamp = timestamp[len(prefix):]
            if suffix:
                timestamp = timestamp[:-len(suffix)]
        message["timestamp_fragment"] = timestamp
    dialogue["message_columns"] = columns
    dialogue["messages"] = [[message[key] for key in columns] for message in messages]
    return result


def unpack_dialogue_payload(packed: dict[str, Any]) -> dict[str, Any]:
    """Восстанавливает исходные пять полей каждой реплики без нормализации."""
    result = copy.deepcopy(packed)
    dialogue = result["untrusted_dialogue"]
    messages = _records(dialogue.pop("message_columns"), dialogue["messages"])
    if "order_start" in dialogue:
        start = dialogue.pop("order_start")
        for index, message in enumerate(messages):
            message["order"] = start + index
    timestamp_format = dialogue.pop("timestamp_format")
    for message in messages:
        fragment = message.pop("timestamp_fragment")
        message["timestamp_raw"] = (
            None if fragment is None
            else timestamp_format["prefix"] + fragment + timestamp_format["suffix"]
        )
    dialogue["messages"] = messages
    return result


def build_compact_system_instruction(evaluation_input: EvaluationInput, *, instruction: str) -> str:
    """instruction — выбранный исходный профиль без уже добавленной рубрики."""
    rubric = evaluation_input.checklist.model_dump(
        mode="json", include={"name", "sections", "criteria"}
    )
    return (
        instruction + COMPACT_FORMAT_INSTRUCTION
        + "\nАвторитетная конфигурация рубрики:\n" + _json(pack_rubric(rubric))
    )


def build_compact_user_payload(evaluation_input: EvaluationInput) -> str:
    # Канонический builder определяет раскрываемые поля: technical IDs/hash/audio
    # не добавляются, их исключение совпадает с обычным структурированным запросом.
    payload = json.loads(build_user_payload(evaluation_input))
    return _json(pack_dialogue_payload(payload))


def _without_descriptions(schema: Any) -> Any:
    if isinstance(schema, list):
        return [_without_descriptions(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    return {
        key: (
            {name: _without_descriptions(child) for name, child in value.items()}
            if key in {"properties", "$defs", "definitions"}
            else _without_descriptions(value)
        )
        for key, value in schema.items() if key != "description"
    }


def build_compact_response_schema(checklist: ChecklistVersion) -> dict[str, Any]:
    """Дедупликация одинаковых schema; ограничения и формат ответа неизменны.

    Удаляются только annotation descriptions, уже полностью переданные в rubric.
    В частности, required/additionalProperties, minItems, типы, enum и разрешённый
    N/A не ослабляются. Существующий parser продолжает проверять ответ/evidence.
    """
    result: dict[str, Any] = _without_descriptions(build_response_schema(checklist))
    criteria = result["properties"]["answers"]["properties"]
    definitions: dict[str, Any] = {}
    evidence_shapes = {
        key: criterion["properties"]["evidence"]["items"] for key, criterion in criteria.items()
    }
    evidence_counts = Counter(_json(shape) for shape in evidence_shapes.values())
    evidence_names: dict[str, str] = {}
    for key, shape in evidence_shapes.items():
        encoded = _json(shape)
        if evidence_counts[encoded] < 2:
            continue
        if encoded not in evidence_names:
            name = "evidence" if not evidence_names else f"evidence{len(evidence_names)}"
            evidence_names[encoded] = name
            definitions[name] = shape
        criteria[key]["properties"]["evidence"]["items"] = {
            "$ref": f"#/$defs/{evidence_names[encoded]}"
        }

    counts = Counter(_json(criterion) for criterion in criteria.values())
    names: dict[str, str] = {}
    for key, criterion in list(criteria.items()):
        encoded = _json(criterion)
        if counts[encoded] < 2:
            continue
        if encoded not in names:
            name = f"criterion{len(names)}"
            names[encoded] = name
            definitions[name] = criterion
        criteria[key] = {"$ref": f"#/$defs/{names[encoded]}"}
    if definitions:
        result["$defs"] = definitions
    return result
