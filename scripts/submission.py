"""Export or verify official results offline. This script never calls an AI or remote API."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

from dotenv import load_dotenv  # noqa: E402
from pydantic import TypeAdapter, ValidationError  # noqa: E402
from sqlalchemy import Connection, text  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from app.api.errors import ApiError  # noqa: E402
from app.contracts.models import (  # noqa: E402
    ErrorDetail,
    Evaluation,
    EvaluationCreateRequest,
    ProviderCall,
)
from app.domain.scoring import score  # noqa: E402
from app.domain.snapshots import stable_hash  # noqa: E402
from app.evaluation.demo import load_synthetic_input  # noqa: E402
from app.evaluation.mock import MockEvaluationProvider  # noqa: E402
from app.services.submission import (  # noqa: E402
    OFFICIAL_ID,
    ReleaseSelection,
    SelectedJob,
    SubmissionError,
    build_submission,
    read_json,
    validate_file,
    write_verified,
)


def release_cutoff(
    db: Connection, selection: ReleaseSelection, jobs: dict[str, SelectedJob], *,
    capture: bool, require: bool,
) -> dict[str, tuple[ProviderCall, ...]] | None:
    codec = TypeAdapter(dict[str, tuple[ProviderCall, ...]])
    fingerprint = stable_hash(selection.model_dump(mode="json"))
    if capture:
        db.exec_driver_sql("""CREATE TABLE IF NOT EXISTS submission_cutoffs (
            selection_hash TEXT PRIMARY KEY, recorded_at TEXT NOT NULL, pending_calls TEXT NOT NULL)""")
    table = db.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='submission_cutoffs'"
    ).scalar_one_or_none()
    if table:
        saved = db.execute(text("SELECT pending_calls FROM submission_cutoffs WHERE selection_hash=:hash"),
                           {"hash": fingerprint}).scalar_one_or_none()
        if saved is not None:
            return codec.validate_json(saved)
    if not capture:
        if require:
            raise SubmissionError("No recorded cutoff for this exact selection.")
        return None
    # Caller holds BEGIN IMMEDIATE across job reads and validation. A worker cannot
    # move a job or publish later calls partway through capturing this cutoff.
    pending = {key: item.calls for key, item in jobs.items()
               if item.evaluation.status in {"queued", "running"}}
    db.execute(text("""INSERT INTO submission_cutoffs(selection_hash,recorded_at,pending_calls)
                    VALUES(:hash,:at,:calls)"""),
               {"hash": fingerprint, "at": datetime.now(UTC).isoformat(),
                "calls": codec.dump_json(pending).decode()})
    return pending


def release(action: str, selection_file: Path, final: bool) -> None:
    from app.services.jobs import exclusive_file_lock

    if not selection_file.resolve().is_relative_to(ROOT / "artifacts/private"):
        raise SubmissionError("Keep the selection file inside artifacts/private.")
    selection = ReleaseSelection.model_validate(read_json(selection_file))
    lock_path = ROOT / "artifacts/private" / f"{selection.dataset}-publication.lock"
    # Two different selections can share one official path. Acquire before reading
    # the DB snapshot and release only after the verified file has been replaced.
    with ExitStack() as stack:
        if action == "export":
            try:
                stack.enter_context(exclusive_file_lock(lock_path))
            except OSError:
                raise SubmissionError(
                    "Cannot lock this dataset for export. Check private directory permissions "
                    "or retry after the current export."
                ) from None
        release_selection(action, selection, final)


def release_selection(action: str, selection: ReleaseSelection, final: bool) -> None:
    from app.storage.conversations import group_versions_snapshot, initialize_conversations
    from app.storage.database import get_engine, write_transaction
    from app.storage.workflow import checklist_snapshot

    initialize_conversations()
    capture = action == "export" and final
    with (write_transaction() if capture else get_engine().connect()) as db:
        if not capture:
            db.exec_driver_sql("BEGIN")  # Pin one SQLite snapshot for all release inputs.
        manifest = group_versions_snapshot(db, selection.group_id)
        checklist = checklist_snapshot(db, OFFICIAL_ID)
        jobs = {}
        calls_codec = TypeAdapter(tuple[ProviderCall, ...])
        for job_id in selection.jobs.values():
            if job_id is None:
                continue
            row = db.execute(text("SELECT snapshot,request,calls,plan_id FROM evaluations WHERE id=:id"),
                             {"id": job_id}).one_or_none()
            if row is None:
                raise SubmissionError("A selected job does not exist.")
            jobs[job_id] = SelectedJob(Evaluation.model_validate_json(row.snapshot),
                                       EvaluationCreateRequest.model_validate_json(row.request),
                                       calls_codec.validate_json(row.calls), row.plan_id)
        cutoff = release_cutoff(db, selection, jobs, capture=capture, require=final)
        document = build_submission(selection, manifest, checklist, jobs, cutoff=cutoff)
    path = ROOT / "results" / selection.dataset / "results.json"
    if path.resolve() != path:
        raise SubmissionError("Official destination must not be redirected by a link.")
    if action == "export":
        write_verified(path, document)
    else:
        validate_file(path, document)
    failures = sum(row["status"] == "error" for row in document["results"])
    print(f"Verified {path.relative_to(ROOT)}: {len(manifest)} records, {failures} errors.")


async def rehearse() -> None:
    """Exercise the official layout with the bundled, explicitly synthetic mock only."""
    case = load_synthetic_input()
    provider = MockEvaluationProvider()
    manifest, jobs, selection_jobs = [], {}, {}
    for n in range(10):
        c = case.conversation.model_copy(update={
            "id": f"rehearsal-version-{n}", "conversation_id": f"rehearsal-dialogue-{n}"})
        manifest.append(c)
        job_id = f"rehearsal-job-{n}"
        selection_jobs[c.id] = job_id
        if n == 9:
            evaluation = Evaluation(
                id=job_id, conversation_version_id=c.id, checklist_version_id=case.checklist.id,
                created_at=c.imported_at, status="failed",
                failure=ErrorDetail(code="TIMEOUT", message="Synthetic timeout."),
            )
        else:
            output = await provider.evaluate(case.model_copy(update={"conversation": c}))
            result = score(case.checklist, output.answers)
            evaluation = Evaluation(
                id=job_id, conversation_version_id=c.id, checklist_version_id=case.checklist.id,
                created_at=c.imported_at, status="succeeded", output=output,
                original_score=result, effective_score=result,
            )
        req = EvaluationCreateRequest(conversation_version_id=c.id, checklist_version_id=case.checklist.id,
                                      mode="mock", provider="fixture", model="synthetic-v1")
        jobs[job_id] = SelectedJob(evaluation, req, ())
    selection = ReleaseSelection(dataset="prod", group_id=case.conversation.dataset_group_id,
                                 team_name="Synthetic rehearsal", jobs=selection_jobs)
    document = build_submission(selection, manifest, case.checklist, jobs, rehearsal=True)
    path = ROOT / "work/rehearsal/results.json"
    if path.resolve() != path:
        raise SubmissionError("Rehearsal destination must not be redirected by a link.")
    write_verified(path, document)
    print("Synthetic rehearsal verified in work/rehearsal/results.json: 9 mock results, 1 failure.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("rehearse", help="Write only an ignored synthetic rehearsal.")
    for action in ("export", "validate"):
        sub = commands.add_parser(action)
        sub.add_argument("--selection", type=Path, required=True, help="Private group and pinned job map.")
        sub.add_argument("--finalize-incomplete", action="store_true",
                         help="Export: freeze the cutoff for this selection. Validate: require its saved cutoff.")
    args = parser.parse_args()
    try:
        load_dotenv(ROOT / ".env", override=False)
        if args.action == "rehearse":
            asyncio.run(rehearse())
        else:
            selection_file = args.selection.resolve()
            os.chdir(ROOT)  # Keep relative SQLite URLs aligned with the backend launcher.
            release(args.action, selection_file, args.finalize_incomplete)
    except SubmissionError as exc:
        print(f"Verification failed: {exc}", file=sys.stderr)
        return 1
    except (ApiError, ValidationError, ValueError, OSError, SQLAlchemyError):
        print("Invalid data or inaccessible private inputs. No source values are logged.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
