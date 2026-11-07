"""Versioned checklists and durable evaluation/appeal records in the shared SQLite database."""

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import TypeAdapter
from sqlalchemy import Connection, ScalarResult, text

from app.api.errors import ApiError
from app.contracts.models import (
    ChecklistCreateRequest,
    ChecklistList,
    ChecklistSummary,
    ChecklistVersion,
    ChecklistVersionCreateRequest,
    ErrorDetail,
    Evaluation,
    EvaluationCreateRequest,
    ProviderCall,
)
from app.domain.snapshots import checklist_content_hash
from app.evaluation.demo import load_synthetic_input
from app.storage.database import get_engine, write_transaction


def error(status: int, code: str, message: str) -> ApiError:
    return ApiError(status, ErrorDetail(code=code, message=message))


def initialize_workflow() -> None:
    canonical = load_synthetic_input().checklist
    with write_transaction() as db:
        db.exec_driver_sql("""CREATE TABLE IF NOT EXISTS checklist_versions (
            id TEXT PRIMARY KEY, checklist_id TEXT NOT NULL, version INTEGER NOT NULL,
            snapshot TEXT NOT NULL, UNIQUE(checklist_id, version))""")
        db.exec_driver_sql("""CREATE TABLE IF NOT EXISTS evaluations (
            id TEXT PRIMARY KEY, status TEXT NOT NULL, request TEXT NOT NULL, snapshot TEXT NOT NULL,
            plan_id TEXT, calls TEXT NOT NULL DEFAULT '[]', idempotency_key TEXT UNIQUE,
            original_request TEXT NOT NULL)""")
        db.exec_driver_sql("CREATE INDEX IF NOT EXISTS evaluation_queue ON evaluations(status)")
        db.exec_driver_sql("""CREATE TABLE IF NOT EXISTS appeals (
            id TEXT PRIMARY KEY, evaluation_id TEXT NOT NULL REFERENCES evaluations(id),
            criterion_id TEXT NOT NULL, snapshot TEXT NOT NULL, UNIQUE(evaluation_id, criterion_id))""")
        db.exec_driver_sql("""CREATE TABLE IF NOT EXISTS appeal_decisions (
            idempotency_key TEXT PRIMARY KEY, appeal_id TEXT UNIQUE NOT NULL REFERENCES appeals(id),
            request TEXT NOT NULL, response TEXT NOT NULL)""")
        db.exec_driver_sql("""CREATE TABLE IF NOT EXISTS live_limits (
            plan_id TEXT PRIMARY KEY, cases INTEGER NOT NULL, attempts INTEGER NOT NULL)""")
        existing = db.execute(
            text("SELECT snapshot FROM checklist_versions WHERE id=:id"), {"id": canonical.id}
        ).scalar_one_or_none()
        if existing is None:
            insert_checklist(db, canonical)
        elif ChecklistVersion.model_validate_json(existing) != canonical:
            raise RuntimeError("Bundled checklist version ID cannot change content")


def insert_checklist(db: Connection, checklist: ChecklistVersion) -> None:
    db.execute(
        text("""INSERT INTO checklist_versions(id, checklist_id, version, snapshot)
        VALUES (:id, :checklist_id, :version, :snapshot)"""),
        {
            "id": checklist.id,
            "checklist_id": checklist.checklist_id,
            "version": checklist.version,
            "snapshot": checklist.model_dump_json(),
        },
    )


def checklist_snapshot(db: Connection, version_id: str) -> ChecklistVersion:
    row = db.execute(
        text("SELECT snapshot FROM checklist_versions WHERE id=:id"), {"id": version_id}
    ).scalar_one_or_none()
    if row is None:
        raise error(404, "NOT_FOUND", "Версия чек-листа не найдена.")
    return ChecklistVersion.model_validate_json(row)


def get_checklist_version(version_id: str) -> ChecklistVersion:
    with get_engine().connect() as db:
        return checklist_snapshot(db, version_id)


def list_checklists() -> ChecklistList:
    with get_engine().connect() as db:
        rows: ScalarResult[str] = db.execute(
            text("""SELECT snapshot FROM checklist_versions v WHERE
            version=(SELECT MAX(version) FROM checklist_versions WHERE checklist_id=v.checklist_id)
            ORDER BY checklist_id""")
        ).scalars()
        latest = (ChecklistVersion.model_validate_json(row) for row in rows)
        return ChecklistList(
            items=tuple(
                ChecklistSummary(
                    checklist_id=v.checklist_id,
                    latest_version_id=v.id,
                    name=v.name,
                    version=v.version,
                )
                for v in latest
            )
        )


def save_checklist(
    definition: ChecklistCreateRequest | ChecklistVersionCreateRequest,
    checklist_id: str | None = None,
) -> ChecklistVersion:
    with write_transaction() as db:
        previous = None
        if checklist_id is not None:
            row = db.execute(
                text("""SELECT snapshot FROM checklist_versions
                WHERE checklist_id=:id ORDER BY version DESC LIMIT 1"""),
                {"id": checklist_id},
            ).scalar_one_or_none()
            if row is None:
                raise error(404, "NOT_FOUND", "Чек-лист не найден.")
            previous = ChecklistVersion.model_validate_json(row)
        version = ChecklistVersion(
            **definition.model_dump(),
            id=str(uuid4()),
            checklist_id=checklist_id or str(uuid4()),
            version=previous.version + 1 if previous else 1,
            created_at=datetime.now(UTC),
        )
        if previous and checklist_content_hash(previous) == checklist_content_hash(version):
            return previous
        insert_checklist(db, version)
        return version


def evaluation_snapshot(db: Connection, evaluation_id: str) -> Evaluation:
    row = db.execute(
        text("SELECT snapshot FROM evaluations WHERE id=:id"), {"id": evaluation_id}
    ).scalar_one_or_none()
    if row is None:
        raise error(404, "NOT_FOUND", "Оценка не найдена.")
    return Evaluation.model_validate_json(row)


def get_evaluation(evaluation_id: str) -> Evaluation:
    with get_engine().connect() as db:
        return evaluation_snapshot(db, evaluation_id)


def get_request(evaluation_id: str) -> EvaluationCreateRequest:
    with get_engine().connect() as db:
        row = db.execute(
            text("SELECT request FROM evaluations WHERE id=:id"), {"id": evaluation_id}
        ).scalar_one_or_none()
        if row is None:
            raise error(404, "NOT_FOUND", "Оценка не найдена.")
        return EvaluationCreateRequest.model_validate_json(row)


def update_evaluation(db: Connection, evaluation: Evaluation) -> None:
    db.execute(
        text("UPDATE evaluations SET status=:status, snapshot=:snapshot WHERE id=:id"),
        {
            "id": evaluation.id,
            "status": evaluation.status,
            "snapshot": evaluation.model_dump_json(),
        },
    )


def get_calls(evaluation_id: str) -> tuple[ProviderCall, ...]:
    with get_engine().connect() as db:
        row = db.execute(
            text("SELECT calls FROM evaluations WHERE id=:id"), {"id": evaluation_id}
        ).scalar_one_or_none()
        if row is None:
            raise error(404, "NOT_FOUND", "Оценка не найдена.")
        return TypeAdapter(tuple[ProviderCall, ...]).validate_json(row)
