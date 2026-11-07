from typing import Protocol

from app.contracts.models import EvaluationInput, EvaluationOutput, ProviderCall


class EvaluationProvider(Protocol):
    async def evaluate(self, evaluation_input: EvaluationInput) -> EvaluationOutput:
        """Ответы по каждому критерию; итоговый балл вычисляется отдельно scorer."""
        ...


class ProviderError(RuntimeError):
    """Ошибка выполнения, не оценка и не повод автоматически включить mock."""


class MockInputNotSupported(ProviderError):
    pass


class ProviderExecutionError(ProviderError):
    """Безопасная ошибка с учётом выполненных попыток, без ответов модели/ключей."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        calls: tuple[ProviderCall, ...] = (),
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.calls = calls
        self.status_code = status_code
