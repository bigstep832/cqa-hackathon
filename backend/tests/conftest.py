"""Keep offline tests independent of operator configuration and private database state."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.sqlite3').as_posix()}")
    monkeypatch.setenv("LIVE_EVAL_ENABLED", "false")
    for name in ("ORGANIZER_API_URL", "MODEL_API_KEY", "CQA_LIVE_PLAN", "CQA_AUDIO_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
