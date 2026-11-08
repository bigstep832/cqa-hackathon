"""Find persisted versions and evaluations from any immutable version in the family."""

from sqlalchemy import text

from app.api.history_models import (
    EvaluationHistoryItem,
    EvaluationHistoryList,
    VersionHistoryItem,
    VersionHistoryList,
)
from app.contracts.models import ConversationVersion
from app.storage.conversations import get_version
from app.storage.database import get_engine

FAMILY = """FROM conversation_versions
    WHERE conversation_id=:external_id AND group_id=:group_id AND source=:source"""


def family_parameters(anchor: ConversationVersion) -> dict[str, str]:
    return {
        "external_id": anchor.conversation_id,
        "group_id": anchor.dataset_group_id,
        "source": anchor.source,
    }


def list_versions(version_id: str, offset: int, limit: int) -> VersionHistoryList:
    anchor = get_version(version_id)
    if anchor.synthetic:
        item = VersionHistoryItem(id=anchor.id, imported_at=anchor.imported_at)
        return VersionHistoryList(
            items=(item,) if offset == 0 else (), total=1, offset=offset, limit=limit
        )
    params = family_parameters(anchor)
    with get_engine().connect() as db:
        db.exec_driver_sql("BEGIN")
        total: int = db.execute(text(f"SELECT count(*) {FAMILY}"), params).scalar_one()
        items: tuple[VersionHistoryItem, ...] = ()
        if offset < total:
            rows = db.execute(
                text(f"""SELECT id, json_extract(snapshot,'$.imported_at') AS imported_at
                {FAMILY} ORDER BY sequence DESC LIMIT :limit OFFSET :offset"""),
                {**params, "limit": limit, "offset": offset},
            )
            items = tuple(VersionHistoryItem.model_validate(row) for row in rows.mappings())
    return VersionHistoryList(items=items, total=total, offset=offset, limit=limit)


def list_evaluations(version_id: str, offset: int, limit: int) -> EvaluationHistoryList:
    anchor = get_version(version_id)
    if anchor.synthetic:
        # The built-in snapshot is not stored in conversation_versions.
        version_ids, params = "SELECT :anchor_id", {"anchor_id": anchor.id}
    else:
        version_ids, params = f"SELECT id {FAMILY}", family_parameters(anchor)
    filtered = f"""FROM evaluations
        WHERE json_extract(request,'$.conversation_version_id') IN ({version_ids})"""
    with get_engine().connect() as db:
        db.exec_driver_sql("BEGIN")
        total: int = db.execute(text(f"SELECT count(*) {filtered}"), params).scalar_one()
        items: tuple[EvaluationHistoryItem, ...] = ()
        if offset < total:
            rows = db.execute(
                text(f"""SELECT id, status,
                json_extract(request,'$.conversation_version_id') AS conversation_version_id,
                json_extract(request,'$.checklist_version_id') AS checklist_version_id,
                json_extract(request,'$.mode') AS mode,
                json_extract(snapshot,'$.created_at') AS created_at
                {filtered} ORDER BY rtrim(created_at,'Z') DESC, rowid DESC LIMIT :limit OFFSET :offset"""),
                {**params, "limit": limit, "offset": offset},
            )
            items = tuple(EvaluationHistoryItem.model_validate(row) for row in rows.mappings())
    return EvaluationHistoryList(items=items, total=total, offset=offset, limit=limit)
