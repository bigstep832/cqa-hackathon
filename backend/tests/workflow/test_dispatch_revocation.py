"""Prove revocation at SQLite COMMIT against both real adapters on loopback."""
import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from time import sleep

import pytest
from scripts import submission as cli
from sqlalchemy import event

from app.contracts.models import ConversationImportRequest, EvaluationCreateRequest
from app.evaluation import gemini
from app.evaluation.demo import load_synthetic_input
from app.services import jobs
from app.services.submission import ReleaseSelection, SubmissionError
from app.storage.conversations import import_conversations
from app.storage.database import get_engine
from app.storage.workflow import (
    get_calls,
    get_dispatches,
    get_evaluation,
    handoff_marker,
    initialize_workflow,
)


def import_manifest():
    records = [
        {"conversation_id": f"case-{i}", "language": "RU", "channel": "agent chat",
         "transcript": [{"speaker": "operator", "timestamp": "00:02", "text": "Учебный диалог."}]}
        for i in range(10)
    ]
    return import_conversations(ConversationImportRequest.model_validate({
        "dataset_group_id": "SYNTHETIC", "records": records,
    }), source="organizer").conversation_version_ids


def verify_export(tmp_path, monkeypatch, versions, job_id, provider, prior_posts):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    selection = ReleaseSelection(dataset="prod", group_id="SYNTHETIC", team_name="DT20",
                                 jobs={v: job_id if v == versions[0] else None for v in versions})
    output = tmp_path / "results/prod/results.json"
    if not prior_posts:
        with pytest.raises(SubmissionError, match="No actual model"):
            cli.release_selection("export", selection, True)
        assert not output.exists()
    else:
        cli.release_selection("export", selection, True)
        cli.release_selection("validate", selection, True)
        document = json.loads(output.read_text(encoding="utf-8"))
        assert document["model"]["name"] == f"{provider}/synthetic"
        assert len(document["results"]) == 10
        assert all(row["status"] == "error" for row in document["results"])


@pytest.mark.parametrize("provider", ["local", "gemini"])
@pytest.mark.parametrize("block_attempt,reason", [(1, "revoke"), (2, "revoke"), (1, "expire")])
def test_permission_covers_journal_commit(provider, block_attempt, reason, tmp_path, monkeypatch):
    seen = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            seen.append(self.path)
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    sqlite_path = tmp_path / "jobs.sqlite3"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + sqlite_path.as_posix())
    initialize_workflow()
    engine = get_engine()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    listener = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    listener.start()
    base = f"http://127.0.0.1:{server.server_port}"
    today = date.today().isoformat()
    plan = {
        "provider": provider, "model": "synthetic", "max_cases": 1,
        "max_requests": block_attempt, "max_attempts": block_attempt,
        "max_output_tokens": 32, "budget_usd": "0",
        "dataset_group_ids": ["SYNTHETIC"], "data_transfer_approved": True,
        "pricing": {"input_token_limit": 100, "output_token_limit": 32,
                    "input_usd_per_million": "0", "output_usd_per_million": "0",
                    "source_url": "https://example.org/test", "verified_on": today, "valid_until": today},
    }
    if provider == "local":
        plan["local_base_url"] = base + "/v1"
    else:
        monkeypatch.setattr(gemini, "GEMINI_BASE_URL", base + "/v1beta/models")
        monkeypatch.setenv("MODEL_API_KEY", "unused-loopback-test")
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setenv("LIVE_EVAL_ENABLED", "true")
    monkeypatch.setenv("CQA_LIVE_PLAN", str(path))
    resolve = jobs.resolve_settings
    monkeypatch.setattr(
        jobs, "resolve_settings",
        lambda *args: resolve(*args).model_copy(update={"timeout_seconds": 1 if reason == "expire" else 10}),
    )

    inserted, reading, committing = Event(), Event(), Event()
    writes = 0

    def after_statement(conn, cursor, statement, parameters, context, executemany):
        nonlocal writes
        if "INSERT INTO EVALUATION_DISPATCHES" in statement.upper():
            writes += 1
            if writes == block_attempt:
                conn.info["contested_dispatch"] = True
                inserted.set()
                assert reading.wait(5), "reader did not acquire the SQLite shared lock"

    def before_commit(conn):
        if conn.info.pop("contested_dispatch", False):
            committing.set()

    def hold_reader():
        assert inserted.wait(5), "selected dispatch was not journaled"
        # A SHARED lock allows the INSERT but delays COMMIT until this reader closes.
        with sqlite3.connect(sqlite_path) as reader:
            reader.execute("BEGIN")
            reader.execute("SELECT count(*) FROM evaluations").fetchone()
            reading.set()
            assert committing.wait(5), "writer did not attempt COMMIT"
            sleep(1.2 if reason == "expire" else 0.1)
            if reason == "revoke":
                plan["dataset_group_ids"] = ["REVOKED_GROUP"]
                replacement = tmp_path / "replacement.json"
                replacement.write_text(json.dumps(plan), encoding="utf-8")
                replacement.replace(path)

    event.listen(engine, "after_cursor_execute", after_statement)
    event.listen(engine, "commit", before_commit)
    try:
        fixture = load_synthetic_input()
        versions = import_manifest()
        job = jobs.create_evaluation(EvaluationCreateRequest(
            mode="live", conversation_version_id=versions[0],
            checklist_version_id=fixture.checklist.id,
        ))
        with ThreadPoolExecutor(max_workers=1) as pool:
            reader = pool.submit(hold_reader)
            asyncio.run(jobs.run_claimed(*jobs.claim_job()))
            reader.result(timeout=5)
        assert get_evaluation(job.id).status == "failed"
        assert len(seen) == block_attempt - 1, "HTTP POST was sent after revocation or timeout"
        expected = list(range(1, block_attempt))
        assert [item.attempt for item in get_dispatches(job.id)] == expected
        assert [item.attempt for item in get_calls(job.id)] == expected
        verify_export(tmp_path, monkeypatch, versions, job.id, provider, block_attempt - 1)
    finally:
        event.remove(engine, "after_cursor_execute", after_statement)
        event.remove(engine, "commit", before_commit)
        server.shutdown()
        listener.join()
        server.server_close()
        engine.dispose()

def test_upgrade_distinguishes_legacy_dispatch_from_unconfirmed_preparation(tmp_path, monkeypatch):
    sqlite_path = tmp_path / "upgrade.sqlite3"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + sqlite_path.as_posix())
    engine = get_engine()
    stamp = "2026-11-08T00:00:00.000000+00:00"
    with engine.begin() as db:
        db.exec_driver_sql("""CREATE TABLE evaluation_dispatches (
            evaluation_id TEXT, attempt INTEGER, provider TEXT, model TEXT,
            started_at TEXT, completed_call TEXT, PRIMARY KEY(evaluation_id, attempt))""")
        db.exec_driver_sql("INSERT INTO evaluation_dispatches VALUES ('job', 1, 'local', 'fixture', ?, NULL)",
                           (stamp,))
    assert [item.attempt for item in get_dispatches("job")] == [1]
    initialize_workflow()
    token = "123456789abcdef0123456789abcdef0"
    with engine.begin() as db:
        db.exec_driver_sql("INSERT INTO evaluation_dispatches VALUES ('job', 2, 'local', 'fixture', ?, NULL, ?)",
                           (stamp, token))
        marker = handoff_marker(db, token)
    marker.parent.mkdir()
    for contents in (b"", b"2026-11-08", b"!" * 32, b"\xff"):
        marker.write_bytes(contents)
        assert [item.attempt for item in get_dispatches("job")] == [1]
    marker.write_text(stamp, encoding="ascii")
    assert [item.attempt for item in get_dispatches("job")] == [1, 2]
    engine.dispose()
