"""Generate canonical OpenAPI and synthetic demo; --check never writes files."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]


def main() -> int:
    from app.evaluation.demo import build_demo_payload
    from app.main import app

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    payload = asyncio.run(build_demo_payload())
    artifacts = {
        ROOT / "contracts/openapi.json": app.openapi(),
        ROOT / "fixtures/synthetic/demo.json": payload.model_dump(mode="json"),
    }
    changed: list[str] = []
    for path, data in artifacts.items():
        text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                changed.append(path.relative_to(ROOT).as_posix())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"Generated {path.relative_to(ROOT).as_posix()}")
    if changed:
        print("Stale generated files: " + ", ".join(changed), file=sys.stderr)
        return 1
    if args.check:
        print("OpenAPI and synthetic demo match their canonical sources.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
