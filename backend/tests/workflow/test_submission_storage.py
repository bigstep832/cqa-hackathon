"""Acceptance through the real SQLite queue with an offline, failing HTTP transport."""

import asyncio
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date
from hashlib import sha256
from pathlib import Path
from threading import Event
from time import monotonic, sleep

import httpx
import pytest
from scripts import prepare_batch
from scripts import submission as cli
from sqlalchemy import text

from app.contracts.models import ConversationImportRequest
from app.domain.snapshots import stable_hash
from app.evaluation.demo import load_synthetic_answers
from app.services import jobs, live_workflow
from app.services.submission import (
    ReleaseSelection,
    SubmissionError,
    read_json,
    selection_lock,
    submission_lock,
)
from app.storage.conversations import import_conversations
from app.storage.database import get_engine, write_transaction
from app.storage.workflow import get_calls, get_dispatches, get_evaluation, initialize_workflow


def test_reviewed_name_redaction_keeps_sqlite_original_and_frozen_export(tmp_path, monkeypatch):
    selection_file, path, selected = prepare_release(tmp_path, monkeypatch)
    original = "Оператор представился как УчебноеИмя и установил контакт."
    replies = []

    def complete(request):
        answers = {
            item.criterion_id: {
                "answer": item.answer.model_dump(mode="json"),
                "explanation": original if not replies and item.criterion_id == "C01" else item.explanation,
                "evidence": [{"start_message_id": "m1", "end_message_id": "m1"}],
            } for item in load_synthetic_answers()
        }
        replies.append(request.url.path)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": json.dumps({"answers": answers}),
        }}]})

    async def consume(count):
        for _ in range(count):
            await jobs.run_claimed(*jobs.claim_job())

    monkeypatch.setattr(live_workflow, "outbound_transport", lambda: httpx.MockTransport(complete))
    try:
        asyncio.run(consume(1))
        job_id = next(iter(selected.jobs.values()))
        evaluated = get_evaluation(job_id)
        assert evaluated.status == "succeeded"
        assert evaluated.output.answers[0].explanation == original
        original_snapshot = evaluated.model_dump_json()
        start = original.index("УчебноеИмя")
        review = {"evaluation_id": job_id, "criterion_id": "C01",
                  "original_sha256": sha256(original.encode("utf-8")).hexdigest(),
                  "reviewed_by": "Codex, review authorized by A", "reason": "personal_data",
                  "spans": [{"start": start, "end": start + len("УчебноеИмя")}]}
        reviews_file = selection_file.with_name("reviews.json")
        before_review = selection_file.read_bytes()
        for invalid in ([{**review, "original_sha256": "0" * 64}],
                        [{**review, "evaluation_id": "foreign-job"}], [review, review]):
            reviews_file.write_text(json.dumps(invalid), encoding="utf-8")
            with pytest.raises(SubmissionError):
                cli.review_rationales(selection_file, reviews_file)
            assert selection_file.read_bytes() == before_review
        reviews_file.write_text(json.dumps([review]), encoding="utf-8")
        with selection_lock(selection_file), pytest.raises(SubmissionError, match="lock this selection"):
            cli.review_rationales(selection_file, reviews_file)
        with submission_lock(selection_file.with_name("prod-publication.lock"), "dataset"):
            with pytest.raises(SubmissionError, match="lock this dataset"):
                cli.review_rationales(selection_file, reviews_file)
        assert selection_file.read_bytes() == before_review
        # A review may be installed while other selected jobs are still pending.
        cli.review_rationales(selection_file, reviews_file)
        reviewed = selection_file.read_bytes()
        assert prepare_batch.prepare(selection_file, "prod", "TEST", "DT20", enqueue=True) == 10
        assert selection_file.read_bytes() == reviewed
        asyncio.run(consume(8))
        cli.release("export", selection_file, True)
        exported = read_json(path)
        row = exported["results"][0]
        assert row["criteria"][0]["rationale"] == "Оператор представился как [данные скрыты] и установил контакт."
        assert row["score"]["final_score"] == evaluated.original_score.total
        assert row["criteria"][0]["value"] == evaluated.output.answers[0].answer.value
        assert row["criteria"][0]["evidence_turns"] == [1]
        assert "УчебноеИмя" not in path.read_text(encoding="utf-8")
        assert get_evaluation(job_id).model_dump_json() == original_snapshot
        frozen = path.read_bytes()
        asyncio.run(consume(1))  # A late success must stay frozen as an error.
        cli.release("validate", selection_file, False)
        cli.release("export", selection_file, True)
        assert path.read_bytes() == frozen
        cli.review_rationales(selection_file, reviews_file)  # Identical review remains idempotent.
        reviews_file.write_text("[]", encoding="utf-8")
        with pytest.raises(SubmissionError, match="changed after finalization"):
            cli.review_rationales(selection_file, reviews_file)
        assert selection_file.read_bytes() == reviewed and path.read_bytes() == frozen
        # Even manually changing the manifest cannot bypass its saved review hash.
        raw = read_json(selection_file)
        raw["rationale_redactions"] = []
        selection_file.write_text(json.dumps(raw), encoding="utf-8")
        for action in ("export", "validate"):
            with pytest.raises(SubmissionError, match="changed after finalization"):
                cli.release(action, selection_file, False)
        assert path.read_bytes() == frozen
    finally:
        get_engine().dispose()


def test_legacy_cutoff_hash_survives_redaction_schema_upgrade(tmp_path, monkeypatch):
    _, _, selected = prepare_release(tmp_path, monkeypatch)
    fingerprint = stable_hash(selected.model_dump(mode="json", exclude={"rationale_redactions"}))
    pending = {next(iter(selected.jobs.values())): ()}
    encoded = json.dumps(pending)
    try:
        with write_transaction() as db:
            db.exec_driver_sql("CREATE TABLE submission_cutoffs (selection_hash TEXT PRIMARY KEY, "
                               "recorded_at TEXT NOT NULL, pending_calls TEXT NOT NULL)")
            db.execute(text("INSERT INTO submission_cutoffs VALUES (:hash,'synthetic-time',:calls)"),
                       {"hash": fingerprint, "calls": encoded})
        with get_engine().connect() as db:
            assert cli.release_cutoff(db, selected, {}, capture=False, require=True) == pending
        with write_transaction() as db:
            assert cli.release_cutoff(db, selected, {}, capture=True, require=True) == pending
            assert db.exec_driver_sql("SELECT COUNT(*) FROM submission_cutoffs").scalar_one() == 1
            assert "rationale_review_hash" in {r.name for r in db.exec_driver_sql("PRAGMA table_info(submission_cutoffs)")}
    finally:
        get_engine().dispose()


def prepare_release(tmp_path, monkeypatch, attempts=1):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path.as_posix()}/synthetic.sqlite3")
    private = tmp_path / "artifacts/private"
    private.mkdir(parents=True)
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(prepare_batch, "ROOT", tmp_path)
    selection_file = private / "selection.json"
    path = tmp_path / "results/prod/results.json"
    today = date.today().isoformat()
    plan = {"provider": "local", "model": "synthetic-transport", "local_base_url": "http://127.0.0.1:1/v1",
            "dataset_group_ids": ["TEST"], "max_cases": 10, "max_requests": 10 * attempts, "max_attempts": attempts,
            "max_output_tokens": 20, "budget_usd": "0", "data_transfer_approved": True,
            "pricing": {"input_token_limit": 9999, "output_token_limit": 20,
                        "input_usd_per_million": "0", "output_usd_per_million": "0",
                        "source_url": "https://example.test/test-only", "verified_on": today,
                        "valid_until": today}}
    plan_path = private / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setenv("LIVE_EVAL_ENABLED", "true")
    monkeypatch.setenv("CQA_LIVE_PLAN", str(plan_path))

    imported = import_conversations(ConversationImportRequest.model_validate({
        "dataset_group_id": "TEST",
        "records": [{"conversation_id": f"test-{n}", "channel": "agent chat", "language": "RU",
                     "transcript": [{"speaker": "operator", "timestamp": "00:02",
                                     "text": "Учебный диалог."}]} for n in range(10)]}),
        source="organizer")
    args = (selection_file, "prod", "TEST", "DT20")
    assert prepare_batch.prepare(*args, enqueue=False) == 0
    assert prepare_batch.prepare(*args, enqueue=True) == 10
    selected = ReleaseSelection.model_validate(read_json(selection_file))
    assert set(selected.jobs) == set(imported.conversation_version_ids)
    assert len(set(selected.jobs.values())) == 10
    assert prepare_batch.prepare(*args, enqueue=True) == 10
    assert ReleaseSelection.model_validate(read_json(selection_file)) == selected
    return selection_file, path, selected


@pytest.fixture
def completed_submission(tmp_path, monkeypatch):
    selection_file, path, _ = prepare_release(tmp_path, monkeypatch)

    async def exercise():
        def reject(request):
            return httpx.Response(503, json={"error": "test-private-fragment"})

        monkeypatch.setattr(live_workflow, "outbound_transport", lambda: httpx.MockTransport(reject))
        with pytest.raises(SubmissionError, match="still pending"):
            cli.release("export", selection_file, False)
        with pytest.raises(SubmissionError, match="No actual model"):
            cli.release("export", selection_file, True)
        assert not path.exists()
        for _ in range(9):
            await jobs.run_claimed(*jobs.claim_job())
        cli.release("export", selection_file, True)
        sealed = path.read_bytes()
        last = jobs.claim_job()
        assert read_json(path)["results"][-1]["error"] == "Оценка не завершилась к сроку."
        await jobs.run_claimed(*last)
        assert jobs.claim_job() is None and get_evaluation(last[0].id).status == "failed"
        cli.release("validate", selection_file, True)
        cli.release("validate", selection_file, False)
        cli.release("export", selection_file, True)
        assert path.read_bytes() == sealed

    try:
        asyncio.run(exercise())
        yield selection_file, path
    finally:
        get_engine().dispose()


def test_crash_at_http_dispatch_keeps_model_and_freezes_pending_errors(tmp_path, monkeypatch):
    selection_file, path, selected = prepare_release(tmp_path, monkeypatch, attempts=2)
    # The child exits abruptly inside the transport; no worker cleanup or final save runs.
    script = """
import asyncio, os, sys
import httpx
from app.services import live_workflow, jobs
received = 0
def terminate_on_dispatch(request):
    global received
    received += 1
    if received == int(sys.argv[1]):
        os._exit(73)
    return httpx.Response(503)
live_workflow.outbound_transport = lambda: httpx.MockTransport(terminate_on_dispatch)
asyncio.run(jobs.run_claimed(*jobs.claim_job()))
"""
    root = Path(__file__).resolve().parents[3]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(root / "backend"), str(root)])}
    ids = list(selected.jobs.values())
    try:
        with pytest.raises(SubmissionError, match="No actual model"):
            cli.release("export", selection_file, True)
        for ordinal in (1, 2):
            process = subprocess.run([sys.executable, "-c", script, str(ordinal)], cwd=root,
                                     env=env, timeout=15, capture_output=True)
            assert process.returncode == 73, process.stderr
            job_id = ids[ordinal - 1]
            assert [d.attempt for d in get_dispatches(job_id)] == list(range(1, ordinal + 1))
            assert [c.attempt for c in get_calls(job_id)] == list(range(1, ordinal))
            if ordinal == 1:
                # No measured ProviderCall exists anywhere at this cutoff yet.
                cli.release("export", selection_file, True)
                sealed = path.read_bytes()
                rows = read_json(path)
                assert rows["model"] == {"mode": "local", "name": "local/synthetic-transport"}
                assert len(rows["results"]) == 10 and all(r["status"] == "error" for r in rows["results"])
            jobs.recover_interrupted_jobs()
            assert get_evaluation(job_id).failure.code == "WORKER_INTERRUPTED"
        cli.release("validate", selection_file, True)
        cli.release("export", selection_file, True)
        assert path.read_bytes() == sealed
    finally:
        get_engine().dispose()


def test_cancellation_delayed_by_sqlite_keeps_completed_and_interrupted_calls(tmp_path, monkeypatch):
    selection_file, path, selected = prepare_release(tmp_path, monkeypatch, attempts=2)
    received = []

    async def cancel_during_contention():
        streaming = asyncio.Event()

        class StalledBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                streaming.set()
                await asyncio.Event().wait()
                yield b""

        def reply(request):
            received.append(request.url.path)
            if len(received) == 1:
                # An invalid completion triggers the adapter's normal retry; its measured usage survives.
                return httpx.Response(200, json={"usage": {"prompt_tokens": 17, "completion_tokens": 3}})
            return httpx.Response(200, stream=StalledBody())

        monkeypatch.setattr(live_workflow, "outbound_transport", lambda: httpx.MockTransport(reply))
        job = asyncio.create_task(jobs.run_claimed(*jobs.claim_job()))
        await asyncio.wait_for(streaming.wait(), timeout=5)

        writer_acquired, wait_started = Event(), Event()

        def hold_write_lock():
            with write_transaction():
                writer_acquired.set()
                assert wait_started.wait(5)
                sleep(2)

        with ThreadPoolExecutor(max_workers=1) as pool:
            holder = pool.submit(hold_write_lock)
            assert await asyncio.to_thread(writer_acquired.wait, 3)
            # The job's own 60s budget is unchanged. This explicit caller cancellation
            # becomes runnable while SQLite is synchronously waiting for the writer.
            asyncio.get_running_loop().call_later(0.05, job.cancel)
            start = monotonic()
            wait_started.set()
            with write_transaction():
                assert monotonic() - start >= 1.8
            holder.result()
        with pytest.raises(asyncio.CancelledError):
            await job

    try:
        asyncio.run(cancel_during_contention())
        jobs.recover_interrupted_jobs()
        job_id = next(iter(selected.jobs.values()))
        assert get_evaluation(job_id).failure.code == "WORKER_INTERRUPTED"
        assert len(received) == 2
        first, second = get_calls(job_id)
        assert [first.attempt, second.attempt] == [1, 2]
        assert (first.input_tokens, first.output_tokens) == (17, 3)
        assert second.latency_ms >= 1800 and second.input_tokens is None and second.output_tokens is None
        assert [d.attempt for d in get_dispatches(job_id)] == [1, 2]
        with get_engine().connect() as db:
            assert tuple(db.exec_driver_sql("SELECT cases, attempts FROM live_limits").one()) == (1, 2)
        cli.release("export", selection_file, True)
        cli.release("validate", selection_file, True)
        assert all(row["status"] == "error" for row in read_json(path)["results"])
    finally:
        get_engine().dispose()


def test_export_ten_body_timeouts_with_two_wire_attempts_each(tmp_path, monkeypatch):
    selection_file, path, selected = prepare_release(tmp_path, monkeypatch, attempts=2)
    resolve = jobs.resolve_settings
    monkeypatch.setattr(jobs, "resolve_settings",
                        lambda *args: resolve(*args).model_copy(update={"timeout_seconds": 1}))
    received = []

    class StalledBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(15)
            yield b""

    def stall(request):
        received.append(request.url.path)
        return httpx.Response(200, stream=StalledBody())

    monkeypatch.setattr(live_workflow, "outbound_transport", lambda: httpx.MockTransport(stall))

    async def consume():
        for _ in range(5):
            await asyncio.gather(*(jobs.run_claimed(*jobs.claim_job()) for _ in range(2)))

    try:
        asyncio.run(consume())
        assert len(received) == 20
        for job_id in selected.jobs.values():
            assert get_evaluation(job_id).failure.code == "PROVIDER_TIMEOUT"
            assert [call.attempt for call in get_calls(job_id)] == [1, 2]
            assert [d.attempt for d in get_dispatches(job_id)] == [1, 2]
        cli.release("export", selection_file, False)
        cli.release("validate", selection_file, False)
        document = read_json(path)
        assert document["model"] == {"mode": "local", "name": "local/synthetic-transport"}
        assert len(document["results"]) == 10 and all(r["status"] == "error" for r in document["results"])
    finally:
        get_engine().dispose()


def test_offline_persisted_failures_export_and_validate(completed_submission, monkeypatch):
    selection_file, path = completed_submission
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["team_name"] == "DT20"
    assert len(raw["results"]) == 10 and all(row["status"] == "error" for row in raw["results"])
    assert raw["model"] == {"mode": "local", "name": "local/synthetic-transport"}
    assert "test-private-fragment" not in path.read_text(encoding="utf-8")
    raw["results"].pop()
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(SubmissionError):
        cli.release("validate", selection_file, False)

    # An organizer reimport wins the race immediately before the cutoff transaction.
    from app.storage import database

    transaction = database.write_transaction

    @contextmanager
    def reimport_before_transaction():
        import_conversations(ConversationImportRequest.model_validate({
            "dataset_group_id": "TEST",
            "records": [{"conversation_id": "updated", "channel": "agent chat", "language": "RU",
                         "transcript": [{"speaker": "operator", "timestamp": "00:01",
                                         "text": "Изменённый тестовый набор."}]}],
        }), source="organizer")
        with transaction() as db:
            yield db

    monkeypatch.setattr(database, "write_transaction", reimport_before_transaction)
    previous = path.read_bytes()
    with pytest.raises(SubmissionError, match="manifest"):
        cli.release("export", selection_file, True)
    assert path.read_bytes() == previous

def test_older_export_cannot_overwrite_newer_publication(completed_submission, monkeypatch):
    selection_file, path = completed_submission
    selection = ReleaseSelection.model_validate(read_json(selection_file))
    new_selection_file = selection_file.with_name("replacement.json")
    write_verified = cli.write_verified
    competing_publications = []

    def pause_old_publication(destination, document):
        # Interleave a real organizer update and a second export before the older os.replace.
        monkeypatch.setattr(cli, "write_verified", write_verified)
        records = [
            {"conversation_id": f"test-{n}" if n < 9 else "replacement-9", "channel": "agent chat",
             "language": "RU", "transcript": [{"speaker": "operator", "timestamp": "00:02",
                                               "text": "Учебный диалог."}]}
            for n in range(10)
        ]
        imported = import_conversations(ConversationImportRequest.model_validate({
            "dataset_group_id": selection.group_id, "records": records,
        }), source="organizer")
        updated = selection.model_copy(update={"jobs": {
            version: selection.jobs.get(version) for version in imported.conversation_version_ids
        }})
        new_selection_file.write_text(updated.model_dump_json(), encoding="utf-8")
        try:
            cli.release("export", new_selection_file, True)
        except SubmissionError as exc:
            assert "Cannot lock this dataset" in str(exc)
        else:
            competing_publications.append(read_json(path)["results"][-1]["conversation_id"])
        write_verified(destination, document)

    monkeypatch.setattr(cli, "write_verified", pause_old_publication)
    cli.release("export", selection_file, True)
    assert read_json(path)["results"][-1]["conversation_id"] == "test-9"
    assert not competing_publications, f"Overwrote already published {competing_publications}"
    cli.release("export", new_selection_file, True)
    cli.release("validate", new_selection_file, False)
    assert read_json(path)["results"][-1]["conversation_id"] == "replacement-9"


def test_enqueuing_partial_selection_cannot_interleave_with_export(completed_submission, monkeypatch):
    selection_file, path = completed_submission
    complete = ReleaseSelection.model_validate(read_json(selection_file))
    pending_version = list(complete.jobs)[-1]
    partial = complete.model_copy(update={"jobs": {**complete.jobs, pending_version: None}})
    prepare_batch.save_selection(selection_file, partial)
    args = (selection_file, "prod", complete.group_id, complete.team_name)
    busy = []

    def resume_after_selection_read(source):
        data = read_json(source)
        try:
            prepare_batch.prepare(*args, enqueue=True)
        except SubmissionError as exc:
            busy.append(str(exc))
        return data

    with monkeypatch.context() as patch:
        patch.setattr(cli, "read_json", resume_after_selection_read)
        cli.release("export", selection_file, True)
    published = path.read_bytes()
    assert read_json(path)["results"][-1]["error"] == "Оценка не была запущена к сроку."
    cli.release("validate", selection_file, False)
    assert path.read_bytes() == published
    assert busy and "Cannot lock this selection" in busy[0]

    assert prepare_batch.prepare(*args, enqueue=True) == 10
    assert ReleaseSelection.model_validate(read_json(selection_file)) == complete
    cli.release("export", selection_file, True)
    cli.release("validate", selection_file, False)
    assert path.read_bytes() != published


def test_busy_selection_fails_before_reading_group_or_published_file(completed_submission, monkeypatch):
    selection_file, path = completed_submission
    published = path.read_bytes()

    def forbid_read(*args):
        pytest.fail("Read release inputs before acquiring the selection lock")

    monkeypatch.setattr(prepare_batch, "group_versions", forbid_read)
    monkeypatch.setattr(cli, "read_json", forbid_read)
    with jobs.exclusive_file_lock(selection_file.with_suffix(".batch.lock")):
        with pytest.raises(SubmissionError, match="Cannot lock this selection"):
            prepare_batch.prepare(selection_file, "prod", "TEST", "DT20", enqueue=True)
        with pytest.raises(SubmissionError, match="Cannot lock this selection"):
            cli.release("validate", selection_file, False)
    assert path.read_bytes() == published


def test_validate_does_not_race_publication_from_another_selection(completed_submission):
    selection_file, path = completed_submission
    alternate_selection = selection_file.with_name("other-selection.json")
    alternate_selection.write_bytes(selection_file.read_bytes())
    previous = path.read_bytes()
    with jobs.exclusive_file_lock(selection_file.parent / "prod-publication.lock"):
        with pytest.raises(SubmissionError, match="Cannot lock this dataset"):
            cli.release("validate", alternate_selection, False)
    cli.release("validate", alternate_selection, False)
    assert path.read_bytes() == previous


def test_pre_journal_database_and_cutoff_are_still_readable(completed_submission):
    selection_file, path = completed_submission
    expected = path.read_bytes()
    with write_transaction() as db:
        db.exec_driver_sql("DROP TABLE evaluation_dispatches")
    cli.release("validate", selection_file, True)
    initialize_workflow()
    cli.release("export", selection_file, True)
    assert path.read_bytes() == expected
