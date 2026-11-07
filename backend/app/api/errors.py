"""Consistent, sanitized HTTP errors without echoing transcripts or credentials."""

from app.contracts.models import ErrorDetail


class ApiError(Exception):
    def __init__(self, status_code: int, error: ErrorDetail) -> None:
        self.status_code = status_code
        self.error = error
        super().__init__(error.code)
