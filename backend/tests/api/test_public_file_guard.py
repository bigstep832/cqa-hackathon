"""Check the staged privacy guard in a disposable repository with invented markers."""

import importlib.util
import subprocess
from pathlib import Path


def test_staged_provider_keys_signed_urls_media_and_db_sidecars(tmp_path, capsys):
    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "public_guard", root / "scripts/check_public_files.py"
    )
    assert spec and spec.loader
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    guard.ROOT = tmp_path
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    files = {
        "notes.md": "ordinary public note",
        "provider.md": "test-key=" + "AI" + "za" + "Z" * 35,
        "github.md": "test-key=" + "github_pat_" + "FAKE_" * 12,
        "storage.md": "https://storage.invalid/object?" + "X-Amz-Signature=" + "invented",
        "storage2.md": "https://media.invalid/object?alt=media&" + "X-Goog-Signature=" + "invented",
        "demo.sqlite3-wal": "synthetic private row",
        "demo.db-shm": "synthetic private row",
        "Demo.SQLite-JOURNAL": "synthetic private row",
        "training.OPUS": "synthetic media bytes",
    }
    for name, content in files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "--", *files], cwd=tmp_path, check=True)
    (tmp_path / "provider.md").write_text("unstaged edit", encoding="utf-8")
    assert guard.main() == 1
    output = capsys.readouterr().out
    assert all(name in output for name in files if name != "notes.md")
    assert all(content not in output for content in files.values())
    subprocess.run(
        [
            "git",
            "rm",
            "--cached",
            "-q",
            "-f",
            "--",
            *(name for name in files if name != "notes.md"),
        ],
        cwd=tmp_path,
        check=True,
    )
    assert guard.main() == 0
