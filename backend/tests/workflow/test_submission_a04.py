"""Independent arithmetic oracle for every legal official value combination.

The values and weights are transcribed from page 3 of the Results Appendix.
All conversations are synthetic. This does not establish semantic AI accuracy.
"""

import asyncio
from decimal import ROUND_HALF_UP, Decimal
from itertools import product

from app.contracts.models import (
    BooleanAnswer,
    EvaluationCreateRequest,
    NotApplicableAnswer,
    ScaleAnswer,
)
from app.domain.scoring import score
from app.domain.snapshots import conversation_content_hash
from app.evaluation.demo import build_demo_payload
from app.services.submission import ReleaseSelection, SelectedJob, build_submission


def test_all_5184_official_vectors_export_raw_values_and_independent_score() -> None:
    demo = asyncio.run(build_demo_payload())
    assert demo.evaluation.output is not None
    base_output = demo.evaluation.output
    conversations = []
    for index in range(10):
        conversation = demo.conversation.model_copy(update={
            "id": f"a04-version-{index}", "conversation_id": f"a04-example-{index}",
        })
        conversations.append(conversation.model_copy(update={
            "content_hash": conversation_content_hash(conversation),
        }))
    mapping = {c.id: f"a04-job-{i}" for i, c in enumerate(conversations)}
    selection = ReleaseSelection(dataset="prod", group_id=demo.conversation.dataset_group_id,
                                 team_name="DT20", jobs=mapping)
    vectors = list(product(
        (False, True), (False, True, None), (False, True, None),
        (0, 5, 10, 15, 20, None), (False, True), (0, 4, 7, 10),
        (False, True, None), (False, True),
    ))
    assert len(vectors) == 5184
    weights = (10, 15, 20, 20, 15, 15, 5)
    maxima = (1, 1, 1, 20, 1, 10, 1)
    public_ids = ("contact", "discovery", "identification", "consultation",
                  "empathy", "dialogue", "closure", "critical")
    checked = 0
    for offset in range(0, len(vectors), 10):
        selected_vectors = vectors[offset:offset + 10]
        jobs = {}
        # The public document needs ten records; padding is not counted as coverage.
        for index, conversation in enumerate(conversations):
            values = selected_vectors[index % len(selected_vectors)]
            answers = []
            for original, value in zip(base_output.answers, values, strict=True):
                if value is None:
                    answer = NotApplicableAnswer(type="na", reason="Синтетическая проверка N/A")
                elif type(value) is bool:
                    answer = BooleanAnswer(type="boolean", value=value)
                else:
                    answer = ScaleAnswer(type="scale", value=value)
                answers.append(original.model_copy(update={
                    "answer": answer,
                    "evidence": tuple(e.model_copy(update={"conversation_version_id": conversation.id})
                                      for e in original.evidence),
                }))
            output = base_output.model_copy(update={"answers": tuple(answers)})
            calculated = score(demo.checklist, answers)
            job_id = mapping[conversation.id]
            evaluation = demo.evaluation.model_copy(update={
                "id": job_id, "conversation_version_id": conversation.id,
                "output": output, "original_score": calculated, "effective_score": calculated,
            })
            request = EvaluationCreateRequest(
                conversation_version_id=conversation.id, checklist_version_id=demo.checklist.id,
                mode="mock", provider=output.metadata.provider, model=output.metadata.model,
            )
            jobs[job_id] = SelectedJob(evaluation, request, (), None)
        document = build_submission(selection, conversations, demo.checklist, jobs, rehearsal=True)
        for values, row in zip(selected_vectors, document["results"]):
            denominator = sum(w for value, w in zip(values[:7], weights, strict=True) if value is not None)
            numerator = sum(
                Decimal(int(value)) * w / maximum
                for value, w, maximum in zip(values[:7], weights, maxima, strict=True)
                if value is not None
            )
            expected = (100 * numerator / denominator).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            assert Decimal(str(row["score"]["final_score"])) == expected
            assert row["score"]["critical_flag"] is values[7]
            assert tuple(c["criterion_id"] for c in row["criteria"]) == public_ids
            exported = tuple(c["value"] for c in row["criteria"])
            assert exported == values
            assert all(type(a) is type(b) for a, b in zip(exported, values, strict=True))
            checked += 1
    assert checked == 5184
