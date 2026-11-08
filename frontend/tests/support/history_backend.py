"""Isolated real HTTP/SQLite history check; only upstream transports are synthetic.

Never imported by the application. No provider socket, organizer socket or credentials.
Database and plan are unique, ignored frontend/test-results artifacts.
"""
import json
import os
import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = ROOT / "frontend" / "test-results" / ("history-db-" + uuid4().hex)
ARTIFACTS.mkdir(parents=True)
os.environ["DATABASE_URL"] = "sqlite:///" + (ARTIFACTS / "history.sqlite3").as_posix()
os.environ["ORGANIZER_API_URL"] = "https://source.invalid/b04"
os.environ["LIVE_EVAL_ENABLED"] = "true"
os.environ.pop("MODEL_API_KEY", None)
today = date.today().isoformat()
plan = {
    "provider": "local", "model": "synthetic-transport",
    "local_base_url": "http://127.0.0.1:1/v1",
    "max_cases": 2, "max_requests": 2, "max_output_tokens": 8192,
    "budget_usd": "0", "dataset_group_ids": ["B04_SYNTHETIC"],
    "data_transfer_approved": True,
    "pricing": {"input_token_limit": 100000, "output_token_limit": 8192,
                "input_usd_per_million": "0", "output_usd_per_million": "0",
                "source_url": "https://example.org/synthetic-test",
                "verified_on": today, "valid_until": today},
}
plan_path = ARTIFACTS / "plan.json"
plan_path.write_text(json.dumps(plan), encoding="utf-8")
os.environ["CQA_LIVE_PLAN"] = str(plan_path)
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

import httpx
import uvicorn
from app.evaluation.demo import load_synthetic_answers, load_synthetic_input
from app.services import live_workflow, source_http
from app.main import app

fixture = load_synthetic_input()
imports = 0


def synthetic_source_fetch(url, *, max_bytes):
    global imports
    assert url == "https://source.invalid/b04?group_id=B04_SYNTHETIC"
    assert max_bytes > 0
    imports += 1
    transcript = [
        {"timestamp": m.timestamp_raw, "speaker": m.speaker, "text": m.text}
        for m in fixture.conversation.messages
    ]
    if imports > 1:
        transcript[2]["text"] = "B04 SYNTHETIC: changed operator message in v2."
    envelope = {"isSuccess": True, "error": None, "data": {
        "dataset_group_id": "B04_SYNTHETIC", "total_records": 1,
        "records": [{"conversation_id": "B04/synthetic-history", "language": "RU",
                     "channel": "agent chat", "audio_url": None, "transcript": transcript}],
    }}
    return 200, {}, json.dumps(envelope).encode("utf-8")


def synthetic_completion(request):
    assert str(request.url) == "http://127.0.0.1:1/v1/chat/completions"
    body = json.loads(request.content)
    assert body["model"] == "synthetic-transport"
    expected = body["response_format"]["json_schema"]["schema"]["properties"]["answers"]["required"]
    answers = {
        item.criterion_id: {
            "answer": item.answer.model_dump(mode="json"),
            "explanation": item.explanation,
            "evidence": [e.model_dump(mode="json", exclude={"conversation_version_id"})
                         for e in item.evidence],
        } for item in load_synthetic_answers()
    }
    assert set(expected) == set(answers)
    return httpx.Response(200, json={"choices": [{
        "finish_reason": "stop",
        "message": {"role": "assistant", "content": json.dumps({"answers": answers})},
    }]})


# The real importer, permission checks, queue, adapter validation, scorer,
# immutable snapshots, history queries and appeal workflow remain untouched.
source_http.fetch = synthetic_source_fetch
live_workflow.outbound_transport = lambda: httpx.MockTransport(synthetic_completion)

if __name__ == "__main__":
    print("B04 isolated SQLite:", ARTIFACTS / "history.sqlite3", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="warning")
