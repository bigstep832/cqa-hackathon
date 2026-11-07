"""Guard tracked/staged public files. Prints filenames, never matching secrets."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERNS = (
    re.compile(r"https?://[^\s\"'<>]+[?&](?:sig|token|api_key|access_token)=[^\s\"'<>]+", re.I),
    re.compile(r"(?:sk-[A-Za-z0-9_-]{25,}|gh[pousr]_[A-Za-z0-9]{30,})"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
PRIVATE_PARTS = {"node_modules", ".venv", "work"}
PRIVATE_SUFFIXES = {".pdf", ".mp3", ".wav", ".m4a", ".db", ".sqlite", ".sqlite3"}


def main() -> int:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    )
    files = [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]
    findings: list[str] = []
    for name in files:
        relative = Path(name)
        private = (
            any(part in PRIVATE_PARTS for part in relative.parts)
            or name.startswith(("data/private/", "artifacts/private/"))
            or relative.suffix.lower() in PRIVATE_SUFFIXES
            or (relative.name.startswith(".env") and relative.name != ".env.example")
        )
        if private:
            findings.append(f"Private file tracked: {name}")
            continue
        # Scan staged/index content, not only working tree: fixes must actually be staged.
        content = subprocess.run(
            ["git", "show", f":{name}"], cwd=ROOT, check=True, capture_output=True
        ).stdout
        text = content.decode("utf-8", errors="replace")
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            findings.append(f"Possible secret in: {name}")
    if findings:
        print("\n".join(findings))
        return 1
    print(f"Public-file guard passed for {len(files)} tracked/staged files.")
    print("Also review the staged diff manually; this guard is not a complete secret detector.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
