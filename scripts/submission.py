"""Export or verify official results offline. This script never calls an AI or remote API."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

from dotenv import load_dotenv  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from app.api.errors import ApiError  # noqa: E402
from app.contracts.models import (  # noqa: E402
    ErrorDetail,
    Evaluation,
    EvaluationCreateRequest,
)
from app.domain.scoring import score  # noqa: E402
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


def release(action: str, selection_file: Path, final: bool) -> None:
    from app.storage.conversations import group_versions
    from app.storage.workflow import (
        get_calls,
        get_checklist_version,
        get_evaluation,
        get_plan_id,
        get_request,
    )

    if not selection_file.resolve().is_relative_to(ROOT / "artifacts/private"):
        raise SubmissionError("Keep the selection file inside artifacts/private.")
    selection = ReleaseSelection.model_validate(read_json(selection_file))
    manifest = group_versions(selection.group_id)
    checklist = get_checklist_version(OFFICIAL_ID)
    jobs = {job_id: SelectedJob(get_evaluation(job_id), get_request(job_id), get_calls(job_id),
                                get_plan_id(job_id))
            for job_id in selection.jobs.values() if job_id is not None}
    document = build_submission(selection, manifest, checklist, jobs, final=final)
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
        sub.add_argument("--finalize-incomplete", action="store_true", help="Include explicitly unfinished jobs as errors.")
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
