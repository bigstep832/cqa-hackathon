"""Exercise the real annotation controller with delayed synthetic API replies."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
NODE = shutil.which("node")


@pytest.mark.skipif(
    NODE is None or not (ROOT / "frontend/node_modules/jsdom/package.json").is_file(),
    reason="Annotation UI regression requires Node and npm --prefix frontend ci (installed in CI).",
)
def test_annotation_ui_preserves_evidence_during_save() -> None:
    assert NODE is not None
    result = subprocess.run(
        [NODE, "--test", str(ROOT / "evals/tests/review_ui.test.cjs")],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
