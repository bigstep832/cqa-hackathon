"""Transactional immutable versions and exact organizer group manifests."""

import os
from collections.abc import Sequence
from datetime import UTC, datetime
from functools import cache, lru_cache
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import AnyHttpUrl
from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    and_,
    delete,
    func,
    insert,
    select,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine

from app.api.errors import ApiError
from app.contracts.models import (
    Channel,
    ConversationImportRequest,
    ConversationList,
    ConversationSummary,
    ConversationVersion,
    ErrorDetail,
    ImportResponse,
    Language,
    Message,
    OperatorAssignment,
    SourceConversation,
)
from app.domain.snapshots import conversation_content_hash
from app.evaluation.demo import load_synthetic_input
from app.storage.database import get_engine, write_transaction

_schema = MetaData()
_assignments = Table(
    "operator_assignments",
    _schema,
    Column("conversation_id", String, primary_key=True),
    Column("group_id", String, primary_key=True),
    Column("source", String, primary_key=True),
    Column("operator_id", String),
    Column("outcome", String),
    Column("duration_seconds", Integer),
)
Index("ix_assignment_operator", _assignments.c.operator_id)
_versions = Table(
    "conversation_versions",
    _schema,
    Column("sequence", Integer, primary_key=True),
    Column("id", String, nullable=False, unique=True),
    Column("conversation_id", String, nullable=False),
    Column("group_id", String, nullable=False),
    Column("source", String, nullable=False),
    Column("hash", String, nullable=False),
    Column("language", String, nullable=False),
    Column("channel", String, nullable=False),
    Column("message_count", Integer, nullable=False),
    Column("has_audio", Boolean, nullable=False),
    Column("source_audio_url", String),
    Column("snapshot", String, nullable=False),
)
Index("ix_conversation_latest", _versions.c.conversation_id, _versions.c.sequence)
_members = Table(
    "organizer_group_versions",
    _schema,
    Column("group_id", String, primary_key=True),
    Column("position", Integer, primary_key=True),
    Column("version_id", String, ForeignKey("conversation_versions.id"), nullable=False),
    UniqueConstraint("group_id", "version_id"),
)


@cache
def _synthetic_fixture() -> ConversationVersion:
    return load_synthetic_input().conversation


def _not_found() -> ApiError:
    return ApiError(404, ErrorDetail(code="NOT_FOUND", message="Диалог или версия не найдены."))


def _snapshot(
    record: SourceConversation, group_id: str, source: Literal["organizer", "manual"]
) -> ConversationVersion:
    snapshot = ConversationVersion(
        id=f"cv-{uuid4().hex}",
        conversation_id=record.conversation_id,
        dataset_group_id=group_id,
        source=source,
        language=record.language,
        channel=record.channel,
        content_hash="0" * 64,
        imported_at=datetime.now(UTC),
        audio_url=record.audio_url,
        messages=tuple(
            Message(
                message_id=f"m{index + 1}",
                speaker=item.speaker,
                text=item.text,
                timestamp_raw=item.timestamp,
                order=index,
            )
            for index, item in enumerate(record.transcript)
        ),
    )
    return snapshot.model_copy(update={"content_hash": conversation_content_hash(snapshot)})


def _public_snapshot(snapshot: ConversationVersion) -> ConversationVersion:
    if snapshot.audio_url is None:
        return snapshot
    api = os.getenv("CQA_PUBLIC_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    try:
        origin = urlsplit(api)
        if (
            origin.scheme not in ("http", "https")
            or not origin.hostname
            or origin.username is not None
            or origin.password is not None
            or origin.query
            or origin.fragment
        ):
            raise ValueError
        _ = origin.port
    except ValueError:
        raise ApiError(
            503,
            ErrorDetail(code="MEDIA_NOT_CONFIGURED", message="Публичный адрес аудио не настроен."),
        ) from None
    url = AnyHttpUrl(f"{api}/api/v1/conversation-versions/{snapshot.id}/audio")
    public = snapshot.model_copy(update={"audio_url": url})
    return public.model_copy(update={"content_hash": conversation_content_hash(public)})


def _save(connection: Connection, item: ConversationVersion) -> tuple[str, bool]:
    previous = connection.execute(
        select(_versions.c.id, _versions.c.hash)
        .where(
            _versions.c.conversation_id == item.conversation_id,
            _versions.c.group_id == item.dataset_group_id,
            _versions.c.source == item.source,
        )
        .order_by(_versions.c.sequence.desc())
        .limit(1)
    ).first()
    if previous and previous.hash == item.content_hash:
        return previous.id, False
    connection.execute(
        insert(_versions).values(
            id=item.id,
            conversation_id=item.conversation_id,
            group_id=item.dataset_group_id,
            source=item.source,
            hash=item.content_hash,
            language=item.language,
            channel=item.channel,
            message_count=len(item.messages),
            has_audio=item.audio_url is not None,
            source_audio_url=str(item.audio_url) if item.audio_url is not None else None,
            snapshot=_public_snapshot(item).model_dump_json(),
        )
    )
    return item.id, True


def import_conversations(
    request: ConversationImportRequest, source: Literal["organizer", "manual"] = "manual"
) -> ImportResponse:
    items = [_snapshot(record, request.dataset_group_id, source) for record in request.records]
    initialize_conversations()
    with write_transaction() as connection:
        saved = [_save(connection, item) for item in items]
        for record in request.records:
            family = dict(
                conversation_id=record.conversation_id,
                group_id=request.dataset_group_id,
                source=source,
            )
            if "operator" in record.model_fields_set:
                _write_assignment(connection, family, record.operator or OperatorAssignment())
            elif record.channel == "agent chat":
                # A source correction from voice to chat invalidates call-only facts.
                connection.execute(
                    _assignments.update()
                    .filter_by(**family)
                    .values(outcome=None, duration_seconds=None)
                )
        if source == "organizer":
            connection.execute(
                delete(_members).where(_members.c.group_id == request.dataset_group_id)
            )
            connection.execute(
                insert(_members),
                [
                    {
                        "group_id": request.dataset_group_id,
                        "position": idx,
                        "version_id": version_id,
                    }
                    for idx, (version_id, _) in enumerate(saved)
                ],
            )
    count = sum(changed for _, changed in saved)
    return ImportResponse(
        dataset_group_id=request.dataset_group_id,
        imported_count=count,
        unchanged_count=len(saved) - count,
        conversation_version_ids=tuple(version_id for version_id, _ in saved),
    )


def _write_assignment(db: Connection, family: dict[str, str], facts: OperatorAssignment) -> None:
    values = facts.model_dump()
    if not any(value is not None for value in values.values()):
        db.execute(delete(_assignments).filter_by(**family))
        return
    statement = sqlite_insert(_assignments).values(**family, **values)
    db.execute(
        statement.on_conflict_do_update(
            index_elements=["conversation_id", "group_id", "source"], set_=values
        )
    )


def _operator_anchor(db: Connection, version_id: str) -> tuple[dict[str, str], Channel]:
    row = (
        db.execute(
            select(
                _versions.c.conversation_id,
                _versions.c.group_id,
                _versions.c.source,
                _versions.c.channel,
            ).where(_versions.c.id == version_id)
        )
        .mappings()
        .first()
    )
    if row is None:
        raise _not_found()
    return {key: row[key] for key in ("conversation_id", "group_id", "source")}, row["channel"]


def get_operator_assignment(version_id: str) -> OperatorAssignment:
    initialize_conversations()
    with get_engine().connect() as db:
        db.exec_driver_sql("BEGIN")
        family, _ = _operator_anchor(db, version_id)
        row = (
            db.execute(
                select(
                    _assignments.c.operator_id,
                    _assignments.c.outcome,
                    _assignments.c.duration_seconds,
                ).filter_by(**family)
            )
            .mappings()
            .first()
        )
    return OperatorAssignment.model_validate(row) if row is not None else OperatorAssignment()


def set_operator_assignment(version_id: str, facts: OperatorAssignment) -> OperatorAssignment:
    initialize_conversations()
    with write_transaction() as db:
        family, channel = _operator_anchor(db, version_id)
        latest = db.scalar(
            select(_versions.c.id)
            .filter_by(**family)
            .order_by(_versions.c.sequence.desc())
            .limit(1)
        )
        if latest != version_id:
            raise ApiError(
                409,
                ErrorDetail(
                    code="STALE_VERSION", message="Диалог обновлён. Откройте последнюю версию."
                ),
            )
        if not facts.valid_for(channel):
            raise ApiError(
                422,
                ErrorDetail(
                    code="CALL_FIELDS_REQUIRE_VOICE",
                    message="Исход и длительность доступны только для звонка.",
                ),
            )
        _write_assignment(db, family, facts)
    return facts


@lru_cache(maxsize=8)
def _initialize(engine: Engine) -> None:
    # BEGIN IMMEDIATE also protects initial DDL if two requests arrive together.
    with engine.begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        _schema.create_all(connection)


def initialize_conversations() -> None:
    _initialize(get_engine())


def _read_snapshot(value: str | None) -> ConversationVersion:
    if value is None:
        raise _not_found()
    return ConversationVersion.model_validate_json(value)


def get_version(version_id: str) -> ConversationVersion:
    fixture = _synthetic_fixture()
    if version_id == fixture.id:
        return fixture
    initialize_conversations()
    with get_engine().connect() as connection:
        value = connection.scalar(select(_versions.c.snapshot).where(_versions.c.id == version_id))
    return _read_snapshot(value)


def audio_source(version_id: str) -> str:
    initialize_conversations()
    with get_engine().connect() as connection:
        url = connection.scalar(
            select(_versions.c.source_audio_url).where(_versions.c.id == version_id)
        )
    if url is None:
        raise _not_found()
    return url


def get_conversation(conversation_id: str) -> ConversationVersion:
    initialize_conversations()
    with get_engine().connect() as connection:
        value = connection.scalar(
            select(_versions.c.snapshot)
            .where(_versions.c.conversation_id == conversation_id)
            .order_by(_versions.c.sequence.desc())
            .limit(1)
        )
    if value is None:
        fixture = _synthetic_fixture()
        if conversation_id == fixture.conversation_id:
            return fixture
    return _read_snapshot(value)


def organizer_version_is_current(connection: Connection, group_id: str, version_id: str) -> bool:
    member = select(_members.c.version_id).where(
        _members.c.group_id == group_id,
        _members.c.version_id == version_id,
    )
    return connection.scalar(member) is not None


def group_versions(group_id: str) -> tuple[ConversationVersion, ...]:
    initialize_conversations()
    with get_engine().connect() as connection:
        return group_versions_snapshot(connection, group_id)


def group_versions_snapshot(connection: Connection, group_id: str) -> tuple[ConversationVersion, ...]:
    rows: Sequence[str] = connection.scalars(
        select(_versions.c.snapshot)
        .join(_members)
        .where(_members.c.group_id == group_id)
        .order_by(_members.c.position)
    ).all()
    if not rows:
        raise _not_found()
    return tuple(_read_snapshot(value) for value in rows)


def list_conversations(
    offset: int = 0,
    limit: int = 25,
    language: Language | None = None,
    channel: Channel | None = None,
) -> ConversationList:
    initialize_conversations()
    latest = (
        select(func.max(_versions.c.sequence).label("seq"))
        .group_by(_versions.c.conversation_id, _versions.c.group_id, _versions.c.source)
        .subquery()
    )
    filters = []
    if language:
        filters.append(_versions.c.language == language)
    if channel:
        filters.append(_versions.c.channel == channel)
    query = select(
        _versions.c.conversation_id,
        _versions.c.id.label("latest_version_id"),
        _versions.c.language,
        _versions.c.channel,
        _versions.c.message_count,
        _versions.c.has_audio,
    ).join(latest, _versions.c.sequence == latest.c.seq)
    if filters:
        query = query.where(and_(*filters))
    with get_engine().connect() as connection:
        connection.exec_driver_sql("BEGIN")
        total = connection.scalar(select(func.count()).select_from(query.subquery())) or 0
        if offset >= total:
            return ConversationList(items=(), total=total, offset=offset, limit=limit)
        rows = connection.execute(
            query.order_by(_versions.c.conversation_id, _versions.c.group_id, _versions.c.source)
            .offset(offset).limit(limit)
        )
        summaries = tuple(ConversationSummary.model_validate(row._mapping) for row in rows)
    return ConversationList(items=summaries, total=total, offset=offset, limit=limit)
