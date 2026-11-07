"""Human annotation workflow; every transcript and answer here is synthetic."""

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from backend.tests.evaluation.test_reference_preparation import sample_case
from evals import prepare
from evals.review import ReviewWorkspace, create_app
from fastapi.testclient import TestClient

from app.domain.snapshots import conversation_content_hash
from app.evaluation.demo import load_synthetic_answers


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ReviewWorkspace]:
    monkeypatch.setattr(prepare, "REPOSITORY_ROOT", tmp_path)
    cases = [sample_case("ru"), sample_case("kz", "KZ"), sample_case("en", "EN")]
    raw = cases[0].model_dump(mode="json")
    raw["conversation"]["audio_url"] = "https://example.org/synthetic.wav?test_marker=PRIVATE_SYNTHETIC"
    case = type(cases[0]).model_validate(raw)
    raw["conversation"]["content_hash"] = conversation_content_hash(case.conversation)
    cases[0] = type(case).model_validate(raw)
    inputs = tmp_path / "data/private/inputs.json"
    prepare.write_private_json(inputs, [case.model_dump(mode="json") for case in cases])
    work = ReviewWorkspace(inputs, tmp_path / "data/private/review")
    yield work
    work.close()


@pytest.fixture
def client(workspace: ReviewWorkspace) -> Iterator[TestClient]:
    with TestClient(create_app(workspace), base_url="http://127.0.0.1:8787") as client:
        page = client.get("/")
        token = re.search(r'name="review-token" content="([^"]+)"', page.text)
        assert token is not None
        client.headers.update({"X-Review-Token": token.group(1)})
        yield client


def selection(client: TestClient) -> dict:
    state = client.get("/api/state").json()
    return {"revision": state["revision"], "seed": 17, "holdout_groups": 1,
            "grouped_by": "Synthetic group reviewer", "selections": [
                {"conversation_version_id": row["conversation_version_id"], "selected": True,
                 "scenario_group": "other" if row["language"] == "EN" else "translated"}
                for row in state["rows"]]}


def finish_body(client: TestClient, version: str) -> dict:
    answers = [answer.model_dump(mode="json") for answer in load_synthetic_answers()]
    for answer in answers:
        for evidence in answer["evidence"]:
            evidence["conversation_version_id"] = version
    return {"revision": client.get("/api/state").json()["revision"],
            "conversation_version_id": version, "answers": answers,
            "annotated_by": "Synthetic annotator", "language_understood": True, "finish": True}


def test_selection_freeze_and_restart_preserve_empty_answers(
    client: TestClient, workspace: ReviewWorkspace,
) -> None:
    body = selection(client)
    assert client.post("/api/selection", json=body).status_code == 200
    body["revision"] += 1
    frozen = client.post("/api/freeze", json=body)
    assert frozen.status_code == 200
    state = frozen.json()
    assert state["frozen"] and state["revision"] == 2
    assert len({row["split"] for row in state["rows"] if row["language"] != "EN"}) == 1
    for row in state["rows"]:
        record = client.get(f'/api/record/{row["conversation_version_id"]}').json()
        assert "audio_url" not in record["conversation"]
        assert all(answer["answer"] is None for answer in record["annotation"]["answers"])
    assert not any(row["complete"] for row in state["rows"])
    workspace.close()
    restarted = ReviewWorkspace(workspace.directory.parent / "inputs.json", workspace.directory)
    try:
        assert restarted.summary() == state
    finally:
        restarted.close()


def test_stale_revision_and_frozen_selection_do_not_overwrite(client: TestClient) -> None:
    body = selection(client)
    assert client.post("/api/freeze", json=body).status_code == 200
    assert client.post("/api/selection", json=body).status_code == 409
    body["revision"] += 1
    assert client.post("/api/selection", json=body).status_code == 409
    assert client.get("/api/state").json()["revision"] == 1


def test_partition_preview_is_read_only_and_matches_freeze(client: TestClient) -> None:
    body = selection(client)
    preview = client.post("/api/preview", json=body)
    assert preview.status_code == 200
    assert client.get("/api/state").json()["revision"] == 0
    state = client.post("/api/freeze", json=body).json()
    for split in ("tune", "holdout"):
        rows = [row for row in state["rows"] if row["split"] == split]
        assert preview.json()[split]["count"] == len(rows)
        assert sum(preview.json()[split]["languages"].values()) == len(rows)
        assert preview.json()[split]["channels"] == {"agent chat": len(rows)}


@pytest.mark.parametrize("defect", ["unknown", "duplicate", "missing_group", "no_reviewer", "too_many_groups"])
def test_invalid_freeze_has_no_persisted_effect(client: TestClient, defect: str) -> None:
    body = selection(client)
    if defect == "unknown":
        body["selections"][0]["conversation_version_id"] = "unknown"
    elif defect == "duplicate":
        body["selections"][0] = body["selections"][1]
    elif defect == "missing_group":
        body["selections"][0]["scenario_group"] = None
    elif defect == "no_reviewer":
        body["grouped_by"] = None
    else:
        body["holdout_groups"] = 2
    assert client.post("/api/freeze", json=body).status_code == 422
    assert client.get("/api/state").json()["revision"] == 0


def test_complete_export_one_split_keeps_other_split_out(client: TestClient) -> None:
    assert client.post("/api/freeze", json=selection(client)).status_code == 200
    state = client.get("/api/state").json()
    tune = [row for row in state["rows"] if row["split"] == "tune"]
    holdout = {row["conversation_version_id"] for row in state["rows"] if row["split"] == "holdout"}
    for row in tune:
        result = client.post("/api/annotation", json=finish_body(client, row["conversation_version_id"]))
        assert result.status_code == 200
    revision = client.get("/api/state").json()["revision"]
    exported = client.post("/api/export", json={"revision": revision, "split": "tune"})
    assert exported.status_code == 200
    folder = Path(exported.json()["folder"])
    references = json.loads((folder / "references.json").read_text(encoding="utf-8"))
    assert len(references) == len(tune)
    assert {item["total"] for item in references} == {"85.38"}
    for name in ("references.json", "annotations.json", "inputs.json"):
        content = (folder / name).read_text(encoding="utf-8")
        assert all(version not in content for version in holdout)
    assert client.post("/api/export", json={"revision": revision, "split": "holdout"}).status_code == 422


@pytest.mark.parametrize("defect", ["scale", "na", "reverse", "foreign", "empty", "language"])
def test_invalid_labels_or_unconfirmed_human_never_complete(client: TestClient, defect: str) -> None:
    assert client.post("/api/freeze", json=selection(client)).status_code == 200
    body = finish_body(client, "version-ru")
    if defect == "scale":
        body["answers"][3]["answer"]["value"] = 17
    elif defect == "na":
        body["answers"][0]["answer"] = {"type": "na", "reason": "synthetic invalid"}
    elif defect == "reverse":
        body["answers"][0]["evidence"][0].update(start_message_id="m3", end_message_id="m1")
    elif defect == "foreign":
        body["answers"][0]["evidence"][0]["conversation_version_id"] = "version-kz"
    elif defect == "empty":
        body["answers"][0]["explanation"] = None
    else:
        body["language_understood"] = False
    assert client.post("/api/annotation", json=body).status_code == 422
    assert client.get("/api/state").json()["revision"] == 1


def test_partial_save_and_second_person_review(client: TestClient) -> None:
    assert client.post("/api/freeze", json=selection(client)).status_code == 200
    body = finish_body(client, "version-ru")
    body["answers"][0]["ambiguity_note"] = "Synthetic disputed greeting"
    body["answers"][0]["resolution_note"] = "Synthetic resolved evidence"
    assert client.post("/api/annotation", json=body).status_code == 422
    body["finish"] = False
    assert client.post("/api/annotation", json=body).status_code == 200
    body.update(revision=2, finish=True, reviewed_by="Synthetic annotator", review_confirmed=True)
    assert client.post("/api/annotation", json=body).status_code == 422
    body["reviewed_by"] = "Independent synthetic reviewer"
    assert client.post("/api/annotation", json=body).status_code == 200
    record = client.get("/api/record/version-ru").json()
    assert record["annotation"]["review"]["reviewed_by"] == "Independent synthetic reviewer"
    assert record["annotation"]["review"]["reviewed_at"] >= record["annotation"]["review"]["completed_at"]


def test_http_boundary_and_validation_do_not_expose_private_inputs(client: TestClient) -> None:
    assert client.get("/api/state", headers={"Host": "attacker.example"}).status_code == 403
    assert client.get("/api/state", headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.post("/api/selection", json=selection(client), headers={"X-Review-Token": "wrong"}).status_code == 403
    response = client.post("/api/selection", json={"private": "DO_NOT_ECHO_SYNTHETIC"})
    assert response.status_code == 422 and "DO_NOT_ECHO_SYNTHETIC" not in response.text
    response = client.post("/api/selection", content=b"x" * 1_048_577)
    assert response.status_code == 413
    response = client.get("/api/record/version-ru")
    assert "PRIVATE_SYNTHETIC" not in response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert client.get("/assets/inputs.json").status_code == 404


def test_exclusive_writer_and_changed_inputs_are_rejected(workspace: ReviewWorkspace) -> None:
    inputs = workspace.directory.parent / "inputs.json"
    with pytest.raises(OSError):
        ReviewWorkspace(inputs, workspace.directory)
    workspace.close()
    raw = json.loads(inputs.read_text(encoding="utf-8"))
    raw[0]["settings"]["model"] = "changed-input-snapshot"
    inputs.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="snapshots"):
        ReviewWorkspace(inputs, workspace.directory)


def test_missing_selected_state_integrity_fails_on_restart(workspace: ReviewWorkspace) -> None:
    workspace.close()
    saved = workspace.directory / "revision-00000000.json"
    raw = json.loads(saved.read_text(encoding="utf-8"))
    raw["selections"].pop()
    saved.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="выборка"):
        ReviewWorkspace(workspace.directory.parent / "inputs.json", workspace.directory)


def test_workspace_must_be_private(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="private"):
        ReviewWorkspace(tmp_path / "inputs.json", tmp_path / "public")


def test_disk_failure_keeps_previous_revision(
    client: TestClient, workspace: ReviewWorkspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = selection(client)
    def fail_rename(*args, **kwargs):
        raise OSError("synthetic disk failure")
    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "rename", fail_rename)
        assert client.post("/api/selection", json=body).status_code == 503
    assert workspace.state.revision == 0
    assert not (workspace.directory / "revision-00000001.json").exists()
    assert client.post("/api/selection", json=body).status_code == 200


def test_dynamic_ninth_criterion_and_edit_reset_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prepare, "REPOSITORY_ROOT", tmp_path)
    cases = []
    for name in ("ru", "en"):
        raw = sample_case(name, "RU" if name == "ru" else "EN").model_dump(mode="json")
        raw["checklist"]["id"] = "synthetic-custom-v2"
        ninth = dict(raw["checklist"]["criteria"][0], id="C09", title="Synthetic ninth criterion")
        raw["checklist"]["criteria"].append(ninth)
        cases.append(raw)
    inputs = tmp_path / "data/private/inputs.json"
    prepare.write_private_json(inputs, cases)
    workspace = ReviewWorkspace(inputs, tmp_path / "data/private/review")
    try:
        with TestClient(create_app(workspace), base_url="http://127.0.0.1:8787") as client:
            token = re.search(r'name="review-token" content="([^"]+)"', client.get("/").text)
            assert token is not None
            client.headers["X-Review-Token"] = token.group(1)
            assert client.post("/api/freeze", json=selection(client)).status_code == 200
            record = client.get("/api/record/version-ru").json()
            assert len(record["checklist"]["criteria"]) == len(record["annotation"]["answers"]) == 9
            body = finish_body(client, "version-ru")
            body["answers"].append(dict(body["answers"][0], criterion_id="C09"))
            response = client.post("/api/annotation", json=body)
            assert response.status_code == 200
            assert next(row for row in response.json()["rows"] if row["language"] == "RU")["complete"]
            body.update(revision=response.json()["revision"], finish=False)
            response = client.post("/api/annotation", json=body)
            assert response.status_code == 200
            assert not next(row for row in response.json()["rows"] if row["language"] == "RU")["complete"]
            assert client.get("/api/record/version-ru").json()["annotation"]["review"]["completed_at"] is None
    finally:
        workspace.close()
