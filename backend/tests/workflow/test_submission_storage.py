"""Acceptance through the real SQLite queue with an offline, failing HTTP transport."""

import asyncio
import json
from datetime import date

import httpx
import pytest
from scripts import submission as cli

from app.contracts.models import ConversationImportRequest, EvaluationCreateRequest
from app.evaluation.factory import create_provider
from app.services import jobs, live_workflow
from app.services.submission import OFFICIAL_ID, ReleaseSelection, SubmissionError
from app.storage.conversations import import_conversations
from app.storage.database import get_engine
from app.storage.workflow import initialize_workflow


def test_offline_persisted_failures_export_and_validate(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path.as_posix()}/synthetic.sqlite3")
    private = tmp_path / "artifacts/private"
    private.mkdir(parents=True)
    monkeypatch.setattr(cli, "ROOT", tmp_path)
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
            initialize_workflow()
            pinned = {}
            for version_id in imported.conversation_version_ids:
                queued = jobs.create_evaluation(EvaluationCreateRequest(
                    conversation_version_id=version_id, checklist_version_id=OFFICIAL_ID, mode="live"))
                await jobs.run_claimed(*jobs.claim_job())
                pinned[version_id] = queued.id
            return pinned

    try:
        selection = ReleaseSelection(dataset="prod", group_id="TEST", team_name="Synthetic tests",
                                     jobs=asyncio.run(exercise()))
        selection_file = private / "selection.json"
        selection_file.write_text(selection.model_dump_json(), encoding="utf-8")
        cli.release("export", selection_file, False)
        cli.release("validate", selection_file, False)
        path = tmp_path / "results/prod/results.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert len(raw["results"]) == 10 and all(row["status"] == "error" for row in raw["results"])
        assert raw["model"] == {"mode": "local", "name": "local/synthetic-transport"}
        assert "test-private-fragment" not in path.read_text(encoding="utf-8")
        raw["results"].pop()
        path.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(SubmissionError):
            cli.release("validate", selection_file, False)
    finally:
        get_engine().dispose()
