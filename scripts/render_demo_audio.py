"""Render the built-in synthetic chat locally, with sample-accurate transcript cues."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/synthetic/conversation.json"
DEST = ROOT / "frontend/public/demo-audio"


def render() -> None:
    dialogue = json.loads(FIXTURE.read_text(encoding="utf-8"))
    turns = dialogue["messages"]
    manifest = DEST / "track.json"
    fingerprint = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    if manifest.exists():
        prior = json.loads(manifest.read_text(encoding="utf-8"))
        if prior["fixture"] == fingerprint and (DEST / prior["file"]).is_file():
            return
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        if os.name == "nt":
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "RemoteSigned",
                 "-File", str(ROOT / "scripts/render_demo_voice.ps1"),
                 str(FIXTURE), str(folder)], check=True,
            )
        else:
            engine = shutil.which("espeak-ng") or shutil.which("espeak")
            if not engine:
                raise RuntimeError("Install espeak-ng to generate Russian demo audio.")
            for turn in turns:
                subprocess.run([engine, "-v", "ru", "-s", "155", "-w",
                                str(folder / f"{turn['order']}.wav"), turn["text"]], check=True)
        pcm = bytearray()
        cues: dict[str, float] = {}
        rate = 0
        for turn in turns:
            with wave.open(str(folder / f"{turn['order']}.wav"), "rb") as clip:
                if clip.getsampwidth() != 2 or clip.getnchannels() != 1:
                    raise ValueError("Expected 16-bit mono PCM.")
                if rate and rate != clip.getframerate():
                    raise ValueError("Sample rates differ.")
                rate = clip.getframerate()
                cues[turn["message_id"]] = len(pcm) / (2 * rate)
                pcm.extend(clip.readframes(clip.getnframes()))
                pcm.extend(bytes(round(rate * 0.18) * 2))
        samples = array("h")
        samples.frombytes(pcm)
        if sys.byteorder != "little":
            samples.byteswap()
        peaks = [max(map(abs, samples[i * len(samples) // 150:(i + 1) * len(samples) // 150]))
                 for i in range(150)]
        DEST.mkdir(exist_ok=True, parents=True)
        name = f"chat-{hashlib.sha256(pcm).hexdigest()[:16]}.wav"
        with wave.open(str(DEST / name), "wb") as track:
            track.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            track.writeframes(pcm)
        pending = manifest.with_suffix(".tmp")
        pending.write_text(json.dumps({
            "fixture": fingerprint, "version": dialogue["id"], "hash": dialogue["content_hash"],
            "file": name, "duration": len(samples) / rate, "cues": cues,
            "peaks": [round(value / max(peaks), 3) for value in peaks],
        }), encoding="utf-8")
        pending.replace(manifest)


if __name__ == "__main__":
    try:
        render()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        if "--optional" not in sys.argv:
            raise
        print(f"Demo narration unavailable: {error}", file=sys.stderr)
