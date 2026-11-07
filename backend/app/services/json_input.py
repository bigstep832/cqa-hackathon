"""Decode inbound JSON without silently choosing between conflicting values."""

import json
from typing import Any, NoReturn


def unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError("Duplicate JSON field")
    return fields


def reject_constant(_: str) -> NoReturn:
    raise ValueError("Non-finite JSON number")


def decode_json(data: bytes) -> Any:
    return json.loads(data, object_pairs_hook=unique_fields, parse_constant=reject_constant)
