"""Export or verify official results offline. This script never calls an AI or remote API."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
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
    selection_lock,
    submission_lock,
    validate_file,
    validated_rationale_reviews,
    write_verified,
)
from app.services.submission_redaction import ReviewedRationaleRedaction  # noqa: E402
from app.storage.workflow import DispatchAttempt  # noqa: E402


def release_cutoff(
    db: Connection, selection: ReleaseSelection, jobs: dict[str, SelectedJob], *,
    capture: bool, require: bool,
) -> dict[str, tuple[ProviderCall | DispatchAttempt, ...]] | None:
    codec = TypeAdapter(dict[str, tuple[ProviderCall | DispatchAttempt, ...]])
    # Privacy review must never change the identity of the frozen job cutoff.
    # Excluding the new optional field also preserves historical selection hashes.
    fingerprint = stable_hash(selection.model_dump(mode="json", exclude={"rationale_redactions"}))
    review_hash = stable_hash([r.model_dump(mode="json") for r in selection.rationale_redactions])
    if capture:
        db.exec_driver_sql("""CREATE TABLE IF NOT EXISTS submission_cutoffs (
            selection_hash TEXT PRIMARY KEY, recorded_at TEXT NOT NULL, pending_calls TEXT NOT NULL,
            rationale_review_hash TEXT)""")
        columns = {row.name for row in db.exec_driver_sql("PRAGMA table_info(submission_cutoffs)")}
        if "rationale_review_hash" not in columns:
            db.exec_driver_sql("ALTER TABLE submission_cutoffs ADD COLUMN rationale_review_hash TEXT")
    table = db.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='submission_cutoffs'"
    ).scalar_one_or_none()
    if table:
        columns = {row.name for row in db.exec_driver_sql("PRAGMA table_info(submission_cutoffs)")}
        review_column = "rationale_review_hash" if "rationale_review_hash" in columns else "NULL"
        saved = db.execute(text(f"SELECT pending_calls,{review_column} FROM submission_cutoffs WHERE selection_hash=:hash"),
                           {"hash": fingerprint}).one_or_none()
        if saved is not None:
            saved_calls, saved_review = saved
            if (saved_review or stable_hash([])) != review_hash:
                raise SubmissionError("Rationale review changed after finalization; frozen jobs must not be reopened.")
            return codec.validate_json(saved_calls)
    if not capture:
        if require:
            raise SubmissionError("No recorded cutoff for this exact selection.")
        return None
    # Caller holds BEGIN IMMEDIATE across job reads and validation. A worker cannot
    # move a job or publish later calls partway through capturing this cutoff.
    pending: dict[str, tuple[ProviderCall | DispatchAttempt, ...]] = {
        key: (*item.calls, *item.dispatches) for key, item in jobs.items()
        if item.evaluation.status in {"queued", "running"}
    }
    db.execute(text("""INSERT INTO submission_cutoffs(selection_hash,recorded_at,pending_calls,rationale_review_hash)
                    VALUES(:hash,:at,:calls,:review)"""),
               {"hash": fingerprint, "at": datetime.now(UTC).isoformat(),
                "review": review_hash,
                "calls": codec.dump_json(pending).decode()})
    return pending


def release(action: str, selection_file: Path, final: bool) -> None:
    selection_file = selection_file.resolve()
    if not selection_file.is_relative_to(ROOT / "artifacts/private") or selection_file.suffix != ".json":
        raise SubmissionError("Keep the selection JSON inside artifacts/private.")
    # Always acquire in this order. Prepare needs only the first lock.
    with selection_lock(selection_file):
        selection = ReleaseSelection.model_validate(read_json(selection_file))
        lock_path = ROOT / "artifacts/private" / f"{selection.dataset}-publication.lock"
        with submission_lock(lock_path, "dataset"):
            release_selection(action, selection, final)


def selected_jobs(db: Connection, selection: ReleaseSelection) -> dict[str, SelectedJob]:
    from app.storage.workflow import calls_snapshot, dispatches_snapshot

    jobs = {}
    for job_id in selection.jobs.values():
        if job_id is None:
            continue
        row = db.execute(text("SELECT snapshot,request,calls,plan_id FROM evaluations WHERE id=:id"),
                         {"id": job_id}).one_or_none()
        if row is None:
            raise SubmissionError("A selected job does not exist.")
        jobs[job_id] = SelectedJob(Evaluation.model_validate_json(row.snapshot),
                                   EvaluationCreateRequest.model_validate_json(row.request),
                                   calls_snapshot(db, job_id, row.calls), row.plan_id,
                                   dispatches_snapshot(db, job_id))
    return jobs


def review_rationales(selection_file: Path, reviews_file: Path) -> None:
    """Apply an explicit private review atomically; no AI call or stored-answer edit."""
    from scripts.prepare_batch import save_selection

    from app.storage.database import get_engine

    selection_file, reviews_file = selection_file.resolve(), reviews_file.resolve()
    if any(not p.is_relative_to(ROOT / "artifacts/private") or p.suffix != ".json"
           for p in (selection_file, reviews_file)):
        raise SubmissionError("Keep selection and rationale reviews inside artifacts/private.")
    reviews = TypeAdapter(tuple[ReviewedRationaleRedaction, ...]).validate_python(read_json(reviews_file))
    with selection_lock(selection_file):
        selection = ReleaseSelection.model_validate(read_json(selection_file))
        updated = selection.model_copy(update={"rationale_redactions": reviews})
        lock_path = ROOT / "artifacts/private" / f"{selection.dataset}-publication.lock"
        with submission_lock(lock_path, "dataset"):
            with get_engine().connect() as db:
                db.exec_driver_sql("BEGIN")
                cutoff = release_cutoff(db, updated, {}, capture=False, require=False)
                validated_rationale_reviews(updated, selected_jobs(db, updated), cutoff)
            save_selection(selection_file, updated)
    print(f"Saved {len(reviews)} reviewed rationale redactions; stored AI answers are unchanged.")


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
        jobs = selected_jobs(db, selection)
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
    review = commands.add_parser("review-rationales", help="Apply explicit private redaction spans offline.")
    review.add_argument("--selection", type=Path, required=True)
    review.add_argument("--reviews", type=Path, required=True, help="Full reviewed redaction list, private JSON.")
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
        elif args.action == "review-rationales":
            os.chdir(ROOT)
            review_rationales(args.selection, args.reviews)
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
