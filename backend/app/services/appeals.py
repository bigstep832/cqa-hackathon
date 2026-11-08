"""Append review decisions separately from original evaluation outputs."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Connection, ScalarResult, text

from app.contracts.models import (
    Appeal,
    AppealCreateRequest,
    AppealList,
    DecisionCreateRequest,
    DecisionResponse,
    Evaluation,
    ReviewDecision,
)
from app.domain.scoring import DomainValidationError, score
from app.storage.database import get_engine, write_transaction
from app.storage.workflow import checklist_snapshot, error, evaluation_snapshot, update_evaluation


def create_appeal(evaluation_id: str, request: AppealCreateRequest) -> Appeal:
    with write_transaction() as db:
        evaluation = evaluation_snapshot(db, evaluation_id)
        if evaluation.output is None:
            raise error(409, "NOT_EVALUATED", "Апелляция требует завершённой оценки.")
        if not any(
            answer.criterion_id == request.criterion_id for answer in evaluation.output.answers
        ):
            raise error(422, "VALIDATION_ERROR", "Критерий отсутствует в оценке.")
        existing = db.execute(
            text("""SELECT snapshot FROM appeals WHERE evaluation_id=:evaluation_id
            AND criterion_id=:criterion_id"""),
            {"evaluation_id": evaluation_id, "criterion_id": request.criterion_id},
        ).scalar_one_or_none()
        if existing is not None:
            appeal = Appeal.model_validate_json(existing)
            if appeal.comment == request.comment:
                return appeal
            raise error(409, "APPEAL_EXISTS", "Апелляция по этому критерию уже существует.")
        appeal = Appeal(
            id=str(uuid4()),
            evaluation_id=evaluation_id,
            criterion_id=request.criterion_id,
            comment=request.comment,
            status="open",
            created_at=datetime.now(UTC),
        )
        db.execute(
            text("""INSERT INTO appeals(id, evaluation_id, criterion_id, snapshot)
            VALUES (:id, :evaluation_id, :criterion_id, :snapshot)"""),
            {
                "id": appeal.id,
                "evaluation_id": evaluation_id,
                "criterion_id": request.criterion_id,
                "snapshot": appeal.model_dump_json(),
            },
        )
        return appeal


def all_appeals(db: Connection, evaluation_id: str) -> tuple[Appeal, ...]:
    rows: ScalarResult[str] = db.execute(
        text("SELECT snapshot FROM appeals WHERE evaluation_id=:id ORDER BY rowid"),
        {"id": evaluation_id},
    ).scalars()
    return tuple(Appeal.model_validate_json(row) for row in rows)


def list_appeals(evaluation_id: str) -> AppealList:
    with get_engine().connect() as db:
        evaluation_snapshot(db, evaluation_id)
        return AppealList(items=all_appeals(db, evaluation_id))


def decide_appeal(appeal_id: str, request: DecisionCreateRequest) -> DecisionResponse:
    with write_transaction() as db:
        replay = db.execute(
            text("""SELECT appeal_id, request, response FROM appeal_decisions
            WHERE idempotency_key=:key"""),
            {"key": request.idempotency_key},
        ).one_or_none()
        if replay is not None:
            if (
                replay.appeal_id == appeal_id
                and DecisionCreateRequest.model_validate_json(replay.request) == request
            ):
                return DecisionResponse.model_validate_json(replay.response)
            raise error(409, "IDEMPOTENCY_CONFLICT", "Ключ уже использован с другим запросом.")
        row = db.execute(
            text("SELECT snapshot FROM appeals WHERE id=:id"), {"id": appeal_id}
        ).scalar_one_or_none()
        if row is None:
            raise error(404, "NOT_FOUND", "Апелляция не найдена.")
        appeal = Appeal.model_validate_json(row)
        if appeal.status != "open":
            raise error(409, "ALREADY_RESOLVED", "Решение уже сохранено.")
        evaluation = evaluation_snapshot(db, appeal.evaluation_id)
        assert evaluation.output is not None
        checklist = checklist_snapshot(db, evaluation.checklist_version_id)
        decision = ReviewDecision(
            **request.model_dump(),
            id=str(uuid4()),
            appeal_id=appeal.id,
            created_at=datetime.now(UTC),
        )
        resolved = appeal.model_copy(update={"decision": decision, "status": "resolved"})
        reviews = {v.criterion_id: v.decision for v in all_appeals(db, evaluation.id)}
        reviews[appeal.criterion_id] = decision
        effective_answers = []
        for original in evaluation.output.answers:
            review = reviews.get(original.criterion_id)
            if review and review.override_answer is not None:
                effective_answers.append(
                    original.model_copy(update={"answer": review.override_answer})
                )
            else:
                effective_answers.append(original)
        try:
            effective_score = score(checklist, effective_answers)
        except DomainValidationError as exc:
            raise error(
                422, "INVALID_OVERRIDE", "Вариант ответа недопустим в этой версии чек-листа."
            ) from exc
        updated: Evaluation = evaluation.model_copy(update={"effective_score": effective_score})
        response = DecisionResponse(decision=decision, evaluation=updated)
        update_evaluation(db, updated)
        db.execute(
            text("UPDATE appeals SET snapshot=:snapshot WHERE id=:id"),
            {"snapshot": resolved.model_dump_json(), "id": appeal.id},
        )
        db.execute(
            text("""INSERT INTO appeal_decisions(idempotency_key, appeal_id, request, response)
            VALUES (:key, :id, :request, :response)"""),
            {
                "key": request.idempotency_key,
                "id": appeal.id,
                "request": request.model_dump_json(),
                "response": response.model_dump_json(),
            },
        )
        return response
