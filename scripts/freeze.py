"""Record the clean code revision and explicitly selected versions at stop-code."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--instruction-version", required=True)
    parser.add_argument("--pipeline-version", required=True)
    parser.add_argument("--checklist-version-id", required=True)
    parser.add_argument("--checklist-snapshot", type=Path, required=True)
    args = parser.parse_args()
    if git("status", "--porcelain"):
        parser.error("Commit or set aside pending code changes before recording the freeze.")
    snapshot_path = args.checklist_snapshot.resolve()
    snapshot = snapshot_path.read_bytes()
    # Validate JSON structure before recording its digest, but never export its contents.
    json.loads(snapshot)
    payload = {
        "freeze_commit_sha": git("rev-parse", "HEAD"),
        "recorded_at": datetime.now(UTC).isoformat(),
        "provider": args.provider,
        "model": args.model,
        "instruction_version": args.instruction_version,
        "pipeline_version": args.pipeline_version,
        "checklist_version_id": args.checklist_version_id,
        "checklist_sha256": hashlib.sha256(snapshot).hexdigest(),
    }
    output = ROOT / "artifacts/private/freeze.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("Freeze metadata recorded in artifacts/private/freeze.json (excluded from Git).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
