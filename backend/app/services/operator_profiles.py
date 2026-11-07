"""Operator analytics over the latest immutable snapshot of each assigned conversation."""

import json
import re
from collections import Counter
from datetime import datetime, timedelta
from statistics import fmean

from sqlalchemy import text

from app.api.errors import ApiError
from app.api.operator_models import (
    OperatorConversation,
    OperatorCriterion,
    OperatorDirectory,
    OperatorDirectoryItem,
    OperatorProfile,
)
from app.contracts.models import (
    ChecklistVersion,
    ConversationVersion,
    ErrorDetail,
    Message,
    ScoreResult,
)
from app.storage.conversations import initialize_conversations
from app.storage.database import get_engine

# External IDs can recur across different groups or sources. Attribution belongs to the family.
_LATEST = """
WITH latest AS (
    SELECT MAX(sequence) AS seq FROM conversation_versions
    GROUP BY source, group_id, conversation_id
)
"""
_FAMILIES = """
FROM latest JOIN conversation_versions AS v ON v.sequence = latest.seq
LEFT JOIN operator_assignments AS a ON a.source = v.source AND a.group_id = v.group_id
                                    AND a.conversation_id = v.conversation_id
"""
_CLOCK = re.compile(r"(?:([0-9]{2}):)?([0-9]{2}):([0-5][0-9])(?:\.([0-9]{1,6}))?")
_ISO = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt ][0-9]{2}:[0-9]{2}")


def list_operators() -> OperatorDirectory:
    initialize_conversations()
    with get_engine().connect() as db:
        rows = db.execute(
            text(
                _LATEST
                + "SELECT a.operator_id, COUNT(*) AS n "
                + _FAMILIES
                + " GROUP BY a.operator_id ORDER BY a.operator_id"
            )
        ).all()
    return OperatorDirectory(
        items=tuple(
            OperatorDirectoryItem(operator_id=operator_id, conversation_count=n)
            for operator_id, n in rows
            if operator_id is not None
        ),
        unassigned_count=next((n for operator_id, n in rows if operator_id is None), 0),
    )


def _timestamp(value: str) -> datetime | timedelta | None:
    match = _CLOCK.fullmatch(value)
    if match:
        hours, minutes, seconds, micros = match.groups()
        if hours is not None and int(minutes) >= 60:
            return None
        return timedelta(
            hours=int(hours or 0),
            minutes=int(minutes),
            seconds=int(seconds),
            microseconds=int((micros or "").ljust(6, "0")),
        )
    if _ISO.match(value):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return None


def _span_seconds(messages: tuple[Message, ...]) -> float | None:
    """Elapsed time between distinct turns with comparable, ordered timestamps throughout."""
    if len(messages) < 2:
        return None
    clocks = [_timestamp(msg.timestamp_raw) for msg in messages]
    if any(clock is None for clock in clocks):
        return None
    if all(isinstance(clock, datetime) for clock in clocks):
        dates = [clock for clock in clocks if isinstance(clock, datetime)]
        if len({date.tzinfo is None for date in dates}) > 1 or dates != sorted(dates):
            return None
        return (dates[-1] - dates[0]).total_seconds()
    if all(isinstance(clock, timedelta) for clock in clocks):
        elapsed = [clock for clock in clocks if isinstance(clock, timedelta)]
        if elapsed == sorted(elapsed):
            return (elapsed[-1] - elapsed[0]).total_seconds()
    return None


def first_reply(messages: tuple[Message, ...]) -> tuple[bool, float | None]:
    """Return (unanswered, measured delay). An unreadable clock never implies no reply."""
    first = next((i for i, msg in enumerate(messages) if msg.speaker == "client"), None)
    if first is None:
        return False, None
    reply = next(
        (i for i in range(first + 1, len(messages)) if messages[i].speaker == "operator"), None
    )
    if reply is None:
        return True, None
    return False, _span_seconds(messages[first : reply + 1])


def get_operator_profile(operator_id: str) -> OperatorProfile:
    initialize_conversations()
    sql = (
        _LATEST
        + """
    SELECT v.snapshot, a.outcome, a.duration_seconds, e.id AS evaluation_id,
           json_extract(e.snapshot, '$.effective_score') AS effective_score,
           json_extract(e.snapshot, '$.checklist_version_id') AS checklist_id
    """
        + _FAMILIES
        + """
    LEFT JOIN evaluations AS e ON e.rowid = (
        SELECT job.rowid FROM evaluations AS job
        WHERE json_extract(job.request, '$.conversation_version_id') = v.id
          AND job.status = 'succeeded' AND json_extract(job.request, '$.mode') = 'live'
          AND json_extract(job.snapshot, '$.output.metadata.mode') = 'live'
          AND json_type(job.snapshot, '$.effective_score') = 'object'
        ORDER BY job.rowid DESC LIMIT 1
    )
    WHERE a.operator_id = :operator_id
    ORDER BY v.conversation_id, v.group_id, v.source
    """
    )
    with get_engine().connect() as db:
        db.exec_driver_sql("BEGIN")
        rows = db.execute(text(sql), {"operator_id": operator_id}).all()
        if not rows:
            raise ApiError(404, ErrorDetail(code="NOT_FOUND", message="Оператор не найден."))
        checklist_ids = {row.checklist_id for row in rows if row.checklist_id is not None}
        checklists: dict[str, ChecklistVersion] = {
            row.id: ChecklistVersion.model_validate_json(row.snapshot)
            for row in db.execute(
                text("""
                SELECT id, snapshot FROM checklist_versions
                WHERE id IN (SELECT value FROM json_each(:ids))
            """),
                {"ids": json.dumps(sorted(checklist_ids))},
            )
        }

    conversations = []
    speakers: Counter[str] = Counter()
    reply_delays: list[float] = []
    call_spans: list[float] = []
    qualities: dict[tuple[str, str], list[float]] = {}
    unanswered_count = 0
    flagged_count = 0
    for row in rows:
        version = ConversationVersion.model_validate_json(row.snapshot)
        if version.channel == "voice call":
            call_span = _span_seconds(version.messages)
            if call_span is not None:
                call_spans.append(call_span)
        speakers.update(msg.speaker for msg in version.messages)
        unanswered, delay = first_reply(version.messages)
        unanswered_count += unanswered
        if delay is not None:
            reply_delays.append(delay)
        total = None
        if row.effective_score is not None:
            score = ScoreResult.model_validate_json(row.effective_score)
            flagged_count += bool(score.flags)
            if score.total is not None:
                total = float(score.total)
            for item in score.criteria:
                if item.applicable and item.quality is not None:
                    qualities.setdefault((row.checklist_id, item.criterion_id), []).append(
                        float(item.quality * 100)
                    )
        conversations.append(
            OperatorConversation(
                version_id=version.id,
                conversation_id=version.conversation_id,
                channel=version.channel,
                language=version.language,
                outcome=row.outcome,
                duration_seconds=row.duration_seconds,
                evaluation_id=row.evaluation_id,
                effective_score=total,
            )
        )

    calls = [c for c in conversations if c.channel == "voice call"]
    outcomes = Counter(c.outcome for c in calls)
    durations = [c.duration_seconds for c in calls if c.duration_seconds is not None]
    scores = [c.effective_score for c in conversations if c.effective_score is not None]
    criteria = []
    for checklist_id in sorted(checklists):
        for definition in checklists[checklist_id].criteria:
            if definition.kind == "scored":
                values = qualities.get((checklist_id, definition.id), [])
                criteria.append(
                    OperatorCriterion(
                        checklist_version_id=checklist_id,
                        criterion_id=definition.id,
                        title=definition.title,
                        assessed_count=len(values),
                        average_quality=fmean(values) if values else None,
                    )
                )
    return OperatorProfile(
        operator_id=operator_id,
        conversation_count=len(conversations),
        call_count=len(calls),
        chat_count=len(conversations) - len(calls),
        successful_calls=outcomes["successful"],
        unsuccessful_calls=outcomes["unsuccessful"],
        unknown_calls=outcomes[None],
        measured_calls=len(durations),
        total_call_seconds=sum(durations),
        average_call_seconds=fmean(durations) if durations else None,
        timed_call_spans=len(call_spans),
        average_call_span_seconds=fmean(call_spans) if call_spans else None,
        operator_messages=speakers["operator"],
        client_messages=speakers["client"],
        first_reply_count=len(reply_delays),
        unanswered_conversations=unanswered_count,
        average_first_reply_seconds=fmean(reply_delays) if reply_delays else None,
        scored_conversations=len(scores),
        flagged_conversations=flagged_count,
        average_effective_score=fmean(scores) if scores else None,
        criteria=tuple(criteria),
        conversations=tuple(conversations),
    )
