from typing import Protocol

from app.contracts.models import EvaluationInput, EvaluationOutput


class EvaluationProvider(Protocol):
    async def evaluate(self, evaluation_input: EvaluationInput) -> EvaluationOutput:
        """Ответы по каждому критерию; итоговый балл вычисляется отдельно scorer."""
        ...


class ProviderError(RuntimeError):
    """Ошибка выполнения, не оценка и не повод автоматически включить mock."""


class MockInputNotSupported(ProviderError):
    pass
