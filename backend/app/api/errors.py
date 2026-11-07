"""Consistent, sanitized HTTP errors without echoing transcripts or credentials."""

from typing import NoReturn

from app.contracts.models import ErrorDetail


class ApiError(Exception):
    def __init__(self, status_code: int, error: ErrorDetail) -> None:
        self.status_code = status_code
        self.error = error
        super().__init__(error.code)


def not_implemented(task_id: str) -> NoReturn:
    raise ApiError(
        501,
        ErrorDetail(
            code="NOT_IMPLEMENTED",
            message=f"Операция входит в задачу {task_id}; на этапе 0 доступен только контракт.",
        ),
    )
