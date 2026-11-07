"""Synthetic-only release transformations. No test writes to the official result paths."""

import asyncio
import json
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal

import pytest

from app.contracts.models import ErrorDetail, EvaluationCreateRequest, ProviderCall
from app.domain.scoring import score
from app.evaluation.demo import build_demo_payload
from app.services.submission import (
    ROOT,
    ReleaseSelection,
    SelectedJob,
    SubmissionError,
    build_submission,
    read_json,
    validate_file,
    write_verified,
)


def batch(dataset="prod"):
    demo = asyncio.run(build_demo_payload())
    manifest, jobs, mapping = [], {}, {}
    count = 10 if dataset == "prod" else 65
    for i in range(count):
        # In-memory organizer stand-in for offline unit tests; never persisted/released.
        conversation = demo.conversation.model_copy(update={
            "id": f"fixture-version-{i}", "conversation_id": f"fixture-external-{i}",
            "dataset_group_id": "offline-test", "source": "organizer", "synthetic": False,
            "channel": "voice call" if i >= 60 else "agent chat",
        })
        from app.domain.snapshots import conversation_content_hash
        conversation = conversation.model_copy(update={"content_hash": conversation_content_hash(conversation)})
        job_id = f"fixture-job-{i}"
        mapping[conversation.id] = job_id
        manifest.append(conversation)
        call = ProviderCall(provider="local", model="offline-fixture", purpose="evaluate",
                            latency_ms=1.0, attempt=1)
        original = demo.evaluation.output
        answers = tuple(a.model_copy(update={"evidence": tuple(e.model_copy(update={
            "conversation_version_id": conversation.id}) for e in a.evidence)}) for a in original.answers)
        output = original.model_copy(update={"answers": answers, "metadata": original.metadata.model_copy(
            update={"mode": "live", "provider": call.provider, "model": call.model, "calls": (call,)})})
        evaluation = demo.evaluation.model_copy(update={
            "id": job_id, "conversation_version_id": conversation.id, "output": output})
        request = EvaluationCreateRequest(mode="live", provider=call.provider, model=call.model,
                                         checklist_version_id=demo.checklist.id, conversation_version_id=conversation.id)
        jobs[job_id] = SelectedJob(evaluation, request, (call,), "test-plan")
    selection = ReleaseSelection(dataset=dataset, group_id="offline-test", team_name="DT20", jobs=mapping)
    return selection, manifest, demo.checklist, jobs


@pytest.fixture
def package():
    return batch()


def change_job(jobs, **fields):
    old = jobs["fixture-job-0"]
    jobs["fixture-job-0"] = replace(old, evaluation=old.evaluation.model_copy(update=fields))


def test_pdf_vector_original_and_full_evidence(package, tmp_path):
    selection, manifest, checklist, jobs = package
    old = jobs["fixture-job-0"].evaluation
    change_job(jobs, effective_score=old.original_score.model_copy(update={"total": Decimal("0")}))
    result = build_submission(selection, manifest, checklist, jobs)
    row = result["results"][0]
    assert result["model"] == {"name": "local/offline-fixture", "mode": "local"}
    assert row["conversation_id"] == manifest[0].conversation_id
    assert row["score"] == {"final_score": 85.38, "critical_flag": False}
    assert row["criteria"][5]["value"] == 7
    assert row["criteria"][1]["value"] is None
    assert row["criteria"][2]["evidence_turns"] == [1, 2, 3, 4, 5]
    path = tmp_path / "rehearsal.json"
    write_verified(path, result)
    validate_file(path, result)


def test_critical_true_changes_only_flag(package):
    selection, manifest, checklist, jobs = package
    original = jobs["fixture-job-0"].evaluation.output
    answers = list(original.answers)
    last = answers[-1]
    answers[-1] = last.model_copy(update={"answer": last.answer.model_copy(update={"value": True})})
    output = original.model_copy(update={"answers": tuple(answers)})
    change_job(jobs, output=output, original_score=score(checklist, answers))
    row = build_submission(selection, manifest, checklist, jobs)["results"][0]
    assert row["score"] == {"final_score": 85.38, "critical_flag": True}


@pytest.mark.parametrize("problem", ["custom-weight", "wrong-snapshot", "manual", "synthetic", "duplicate",
                                     "wrong-channel", "missing", "changed-score", "pending", "no-calls", "no-plan", "mock"])
def test_rejects_wrong_data_or_provenance(package, problem):
    selection, manifest, checklist, jobs = package
    if problem == "custom-weight":
        criteria = list(checklist.criteria)
        criteria[0] = criteria[0].model_copy(update={"weight": Decimal("11")})
        checklist = checklist.model_copy(update={"criteria": tuple(criteria)})
    elif problem == "wrong-snapshot":
        change_job(jobs, checklist_version_id="custom")
    elif problem in {"manual", "synthetic"}:
        manifest[0] = manifest[0].model_copy(update={"source": problem, "synthetic": problem == "synthetic"})
    elif problem == "duplicate":
        manifest[0] = manifest[1]
    elif problem == "wrong-channel":
        manifest[0] = manifest[0].model_copy(update={"channel": "voice call"})
    elif problem == "missing":
        selection.jobs.pop(manifest[0].id)
    elif problem == "changed-score":
        old = jobs["fixture-job-0"].evaluation.original_score
        change_job(jobs, original_score=old.model_copy(update={"total": Decimal("85.37")}))
    elif problem == "pending":
        change_job(jobs, status="running", output=None, original_score=None, effective_score=None)
    elif problem == "no-calls":
        jobs["fixture-job-0"] = replace(jobs["fixture-job-0"], calls=())
    elif problem == "no-plan":
        jobs["fixture-job-0"] = replace(jobs["fixture-job-0"], plan_id=None)
    elif problem == "mock":
        old = jobs["fixture-job-0"]
        jobs["fixture-job-0"] = replace(old, request=old.request.model_copy(update={"mode": "mock"}))
    with pytest.raises(ValueError):
        build_submission(selection, manifest, checklist, jobs)


@pytest.mark.parametrize("change", ["score-string", "decimal-precision", "bool-scale", "string-na", "duplicate-id",
                                    "extra-field", "critical", "turn", "missing-criterion", "criterion-id"])
def test_file_rejects_modified_types_and_data(package, tmp_path, change):
    expected = build_submission(*package)
    raw = json.loads(json.dumps(expected))
    row = raw["results"][0]
    if change == "score-string":
        row["score"]["final_score"] = "85.38"
    elif change == "decimal-precision":
        row["score"]["final_score"] = 85.381
    elif change == "bool-scale":
        row["criteria"][5]["value"] = True
    elif change == "string-na":
        row["criteria"][1]["value"] = "null"
    elif change == "duplicate-id":
        row["conversation_id"] = raw["results"][1]["conversation_id"]
    elif change == "extra-field":
        raw["trace"] = "not public"
    elif change == "critical":
        row["score"]["critical_flag"] = 0
    elif change == "turn":
        row["criteria"][0]["evidence_turns"] = [0]
    elif change == "missing-criterion":
        row["criteria"].pop()
    else:
        row["criteria"][0]["criterion_id"] = "C09"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(SubmissionError):
        validate_file(path, expected)


@pytest.mark.parametrize("malformed", [b'{"a":0,"a":1}', b'{"a":NaN}', b'{"a":Infinity}', b'\xff', b'{"a":1,}'])
def test_strict_utf8_json(tmp_path, malformed):
    path = tmp_path / "invalid.json"
    path.write_bytes(malformed)
    with pytest.raises(ValueError):
        read_json(path)


def test_both_subsets_validated():
    assert len(build_submission(*batch("dev"))["results"]) == 65


def test_error_sanitized_and_failed_calls_in_model(package):
    selection, manifest, checklist, jobs = package
    old = jobs["fixture-job-0"]
    call = old.calls[0].model_copy(update={"provider": "openai", "model": "other-fixture"})
    jobs["fixture-job-0"] = replace(old, calls=(call,), request=old.request.model_copy(
        update={"provider": call.provider, "model": call.model}))
    change_job(jobs, status="failed", failure=ErrorDetail(code="TIMEOUT", message="PRIVATE RESPONSE"),
               output=None, original_score=None, effective_score=None)
    document = build_submission(selection, manifest, checklist, jobs)
    assert document["model"] == {"mode": "hybrid", "name": "local/offline-fixture, openai/other-fixture"}
    assert document["results"][0] == {"conversation_id": manifest[0].conversation_id, "status": "error",
                                      "criteria": [], "score": None, "error": "Истекло время ожидания ответа."}


def test_explicit_cutoff_and_model_provenance(package):
    selection, manifest, checklist, jobs = package
    selection.jobs[manifest[0].id] = None
    jobs.pop("fixture-job-0")
    with pytest.raises(SubmissionError):
        build_submission(selection, manifest, checklist, jobs)
    assert build_submission(selection, manifest, checklist, jobs, cutoff={})["results"][0]["status"] == "error"
    for key in selection.jobs:
        selection.jobs[key] = None
    with pytest.raises(SubmissionError, match="No actual model"):
        build_submission(selection, manifest, checklist, {}, cutoff={})


def test_job_succeeded_after_cutoff_keeps_failure_and_prior_model_provenance(package):
    selection, manifest, checklist, jobs = package
    old = jobs["fixture-job-0"]
    late_call = old.calls[0].model_copy(update={"provider": "openai", "model": "later-test-model"})
    jobs["fixture-job-0"] = replace(old, calls=(late_call,))
    document = build_submission(selection, manifest, checklist, jobs, cutoff={"fixture-job-0": ()})
    assert document["results"][0]["error"] == "Оценка не завершилась к сроку."
    assert document["model"] == {"mode": "local", "name": "local/offline-fixture"}


@pytest.mark.parametrize("name", ["DT-20", "dt20", "DT20 ", " DT20", "Synthetic rehearsal"])
def test_official_team_name_is_exact(package, name):
    selection, manifest, checklist, jobs = package
    fields = selection.model_dump()
    fields["team_name"] = name
    with pytest.raises(SubmissionError, match="exactly DT20"):
        build_submission(ReleaseSelection.model_validate(fields), manifest, checklist, jobs)


@pytest.mark.parametrize("text", ["Contact person@example.test", "Call +7 (700) 111 22 33",
                                  "https://example.test/private-path", "token=private",
                                  "github_pat_" + "x" * 60])
def test_sensitive_rationale_stops_before_writing(package, text):
    selection, manifest, checklist, jobs = package
    original = jobs["fixture-job-0"].evaluation.output
    answers = list(original.answers)
    answers[0] = answers[0].model_copy(update={"explanation": text})
    change_job(jobs, output=original.model_copy(update={"answers": tuple(answers)}))
    with pytest.raises(SubmissionError, match="sensitive"):
        build_submission(selection, manifest, checklist, jobs)


def test_private_id_never_rewritten_and_transcript_dump_blocked(package):
    selection, manifest, checklist, jobs = package
    conversation = manifest[0]
    manifest[0] = conversation.model_copy(update={"conversation_id": "name@example.test"})
    with pytest.raises(SubmissionError, match="Source ID"):
        build_submission(selection, manifest, checklist, jobs)
    manifest[0] = conversation
    output = jobs["fixture-job-0"].evaluation.output
    answers = list(output.answers)
    answers[0] = answers[0].model_copy(update={"explanation": " ".join(m.text for m in conversation.messages)})
    change_job(jobs, output=output.model_copy(update={"answers": tuple(answers)}))
    with pytest.raises(SubmissionError, match="transcript"):
        build_submission(selection, manifest, checklist, jobs)


@pytest.mark.parametrize("identifier", ["Bearer synthetic-fixture-credential",
                                         "github_pat_" + "x" * 60])
def test_secret_bearing_external_id_blocks_release(package, identifier):
    selection, manifest, checklist, jobs = package
    manifest[0] = manifest[0].model_copy(update={"conversation_id": identifier})
    with pytest.raises(SubmissionError, match="Source ID"):
        build_submission(selection, manifest, checklist, jobs)


def test_foreign_or_invalid_evidence_rejected(package):
    selection, manifest, checklist, jobs = package
    original = jobs["fixture-job-0"].evaluation.output
    answers = list(original.answers)
    evidence = answers[0].evidence[0].model_copy(update={"end_message_id": "absent"})
    answers[0] = answers[0].model_copy(update={"evidence": (evidence,)})
    change_job(jobs, output=original.model_copy(update={"answers": tuple(answers)}))
    with pytest.raises(ValueError):
        build_submission(selection, manifest, checklist, jobs)


def test_rehearsal_cli_and_atomic_rejection(tmp_path, monkeypatch):
    # The CLI itself always selects the ignored rehearsal destination.
    run = subprocess.run([sys.executable, str(ROOT / "scripts/submission.py"), "rehearse"],
                         cwd=ROOT, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert "9 mock results, 1 failure" in run.stdout
    assert read_json(ROOT / "work/rehearsal/results.json")["team_name"] == "Synthetic rehearsal"
    path = tmp_path / "results.json"
    path.write_text("previous verified result", encoding="utf-8")
    import app.services.submission as submission

    def reject(*args):
        raise SubmissionError("fault injection")

    monkeypatch.setattr(submission, "validate_file", reject)
    with pytest.raises(SubmissionError):
        write_verified(path, {})
    assert path.read_text() == "previous verified result"
    assert list(tmp_path.iterdir()) == [path]
