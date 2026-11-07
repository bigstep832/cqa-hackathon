"""Run deterministic bootstrap checks without live APIs or private datasets."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if npm is None:
        print("npm is required. Install the supported Node.js LTS line.", file=sys.stderr)
        return 1
    commands = [
        [sys.executable, "-m", "ruff", "check", "backend", "scripts", "evals"],
        [sys.executable, "-m", "mypy", "backend/app"],
        [sys.executable, "-m", "pytest"],
        [sys.executable, "scripts/export_contracts.py", "--check"],
        [npm, "--prefix", "frontend", "run", "generate:api:check"],
        *[
            [npm, "--prefix", "frontend", "run", task]
            for task in ("typecheck", "lint", "test", "build")
        ],
        [sys.executable, "scripts/check_public_files.py"],
    ]
    for command in commands:
        print("Running: " + " ".join(command[1:]), flush=True)
        completed = subprocess.run(command, cwd=ROOT, check=False)
        if completed.returncode:
            return completed.returncode
    print("Bootstrap checks passed. Live AI and full MVP workflows are separate tasks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
