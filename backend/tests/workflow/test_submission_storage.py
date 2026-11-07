"""Acceptance through the real SQLite queue with an offline, failing HTTP transport."""

import asyncio
import json
from contextlib import contextmanager
from datetime import date

import httpx
import pytest
from scripts import prepare_batch
from scripts import submission as cli

from app.contracts.models import ConversationImportRequest
from app.evaluation.factory import create_provider
from app.services import jobs, live_workflow
from app.services.submission import ReleaseSelection, SubmissionError, read_json
from app.storage.conversations import import_conversations
from app.storage.database import get_engine
from app.storage.workflow import get_evaluation


@pytest.fixture
def completed_submission(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path.as_posix()}/synthetic.sqlite3")
    private = tmp_path / "artifacts/private"
    private.mkdir(parents=True)
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(prepare_batch, "ROOT", tmp_path)
    selection_file = private / "selection.json"
    path = tmp_path / "results/prod/results.json"
    today = date.today().isoformat()
    plan = {"provider": "local", "model": "synthetic-transport", "local_base_url": "http://127.0.0.1:1/v1",
            "dataset_group_ids": ["TEST"], "max_cases": 10, "max_requests": 10,
            "max_output_tokens": 20, "budget_usd": "0", "data_transfer_approved": True,
            "pricing": {"input_token_limit": 9999, "output_token_limit": 20,
                        "input_usd_per_million": "0", "output_usd_per_million": "0",
                        "source_url": "https://example.test/test-only", "verified_on": today,
                        "valid_until": today}}
    plan_path = private / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setenv("LIVE_EVAL_ENABLED", "true")
    monkeypatch.setenv("CQA_LIVE_PLAN", str(plan_path))

    async def exercise():
        def reject(request):
            return httpx.Response(503, json={"error": "test-private-fragment"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(reject)) as test_client:
            monkeypatch.setattr(live_workflow, "create_provider",
                                lambda config, *, on_attempt: create_provider(
                                    config, client=test_client, on_attempt=on_attempt))
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
