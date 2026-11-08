"""Pin an imported organizer group and enqueue its official rubric jobs for the local worker."""

# ruff: noqa: E402 -- scripts run directly from a source checkout.

import argparse
import os
import sys
from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

from app.api.errors import ApiError
from app.contracts.models import EvaluationCreateRequest
from app.domain.snapshots import stable_hash
from app.services.jobs import create_evaluation
from app.services.submission import (
    OFFICIAL_ID,
    TEAM_NAME,
    ReleaseSelection,
    SubmissionError,
    read_json,
    selection_lock,
)
from app.storage.conversations import group_versions
from app.storage.workflow import initialize_workflow


def save_selection(path: Path, selection: ReleaseSelection) -> None:
    with NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        temporary.write_text(selection.model_dump_json(indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare(
    path: Path, dataset: Literal["dev", "prod"], group: str, team: str, enqueue: bool
) -> int:
    path = path.resolve()
    if not path.is_relative_to(ROOT / "artifacts/private") or path.suffix != ".json":
        raise ValueError("Выборку нужно сохранить в JSON внутри artifacts/private.")
    if team != TEAM_NAME:
        raise ValueError("Название команды расходится с подтверждённым названием.")
    with selection_lock(path):
        versions = group_versions(group)
        expected = {"agent chat": 60, "voice call": 5} if dataset == "dev" else {"agent chat": 10}
        if Counter(v.channel for v in versions) != expected or any(
            v.source != "organizer" or v.synthetic or v.dataset_group_id != group for v in versions
        ):
            raise ValueError("Состав импортированной группы не соответствует набору.")
        selection = ReleaseSelection(
            dataset=dataset, group_id=group, team_name=team, jobs={v.id: None for v in versions}
        )
        if path.exists():
            previous = ReleaseSelection.model_validate(read_json(path))
            if (
                previous.model_dump(exclude={"jobs", "rationale_redactions"})
                != selection.model_dump(exclude={"jobs", "rationale_redactions"})
                or previous.jobs.keys() != selection.jobs.keys()
            ):
                raise ValueError("Файл относится к другой выборке или версии импорта.")
            selection = previous
        else:
            save_selection(path, selection)
        if enqueue:
            initialize_workflow()
            for version_id, job_id in selection.jobs.items():
                if job_id is not None:
                    continue
                request = EvaluationCreateRequest(
                    conversation_version_id=version_id,
                    checklist_version_id=OFFICIAL_ID,
                    mode="live",
                )
                # Persist the key independently of the outcome of the prior write to disk.
                key = stable_hash(["submission", path.relative_to(ROOT).as_posix(), version_id])
                job = create_evaluation(request, key)
                selection.jobs[version_id] = job.id
                save_selection(path, selection)
    return sum(job is not None for job in selection.jobs.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, choices=("dev", "prod"))
    parser.add_argument("--group", required=True)
    parser.add_argument("--team", required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument(
        "--enqueue", action="store_true", help="Queue live jobs under the approved plan"
    )
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=False)
    try:
        queued = prepare(args.selection, args.dataset, args.group, args.team, args.enqueue)
    except ApiError as exc:
        print(exc.error.code, file=sys.stderr)
        return 1
    except SubmissionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (ValueError, OSError):
        print("Проверьте состав группы, путь выборки, параметры или блокировку.", file=sys.stderr)
        return 1
    print(f"Выборка закреплена. Заданий с сохранёнными ID: {queued}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
