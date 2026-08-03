from agents.brain2.nat.evaluate import evaluate

FIXTURE = {
    "kind": "labeled_holdout",
    "uri": "seaweedfs://verity/abc123",
    "sha256": "abc123",
    "spec": {"n_samples": 4},
}
MANIFEST = {"framework": "sklearn", "model_class": "LogisticRegression"}
DATA = {"X": [[0.0], [1.0], [2.0], [3.0]], "y": [0, 0, 1, 1]}


def fake_clock():
    stamps = iter(["2026-08-03T10:00:00+00:00", "2026-08-03T10:00:02+00:00"])
    return lambda: next(stamps)


def passing_plan(**_):
    return {
        "task_type": "binary_classification",
        "metric_set": ["accuracy"],
        "thresholds": [
            {"metric": "accuracy", "op": ">=", "value": 0.7},
            {"metric": "resource.latency_p95_ms", "op": "<=", "value": 100},
        ],
        "rationale": "Binary target.",
    }


def perfect_execute(*, model_payload, X):
    return {
        "y_pred": [0, 0, 1, 1],
        "y_proba": None,
        "resource": {"latency_p95_ms": 0.4, "peak_memory_mb": 90.0, "gpu_memory_mb": None},
    }


def test_a_model_clearing_its_thresholds_produces_a_passing_eval_run():
    eval_run = evaluate(
        manifest=MANIFEST,
        fixture=FIXTURE,
        data=DATA,
        model_payload=b"pickled",
        execute_fn=perfect_execute,
        resolve_fn=passing_plan,
        now_fn=fake_clock(),
    )

    assert eval_run["mechanism"] == "labeled_holdout"
    assert eval_run["verdict"] == "pass"
    assert eval_run["failed_on"] == []
    assert eval_run["error"] is None
    assert eval_run["test_set_ref"] == "seaweedfs://verity/abc123"
    assert eval_run["fixture"] == FIXTURE
    assert eval_run["metric_set"] == {"resolved": ["accuracy"], "skipped": []}
    assert eval_run["thresholds"] == passing_plan()["thresholds"]
    assert eval_run["started_at"] == "2026-08-03T10:00:00+00:00"
    assert eval_run["finished_at"] == "2026-08-03T10:00:02+00:00"


def test_the_eval_run_records_quality_and_systemic_scores_side_by_side():
    eval_run = evaluate(
        manifest=MANIFEST,
        fixture=FIXTURE,
        data=DATA,
        model_payload=b"pickled",
        execute_fn=perfect_execute,
        resolve_fn=passing_plan,
        now_fn=fake_clock(),
    )

    assert eval_run["scores"] == {
        "accuracy": 1.0,
        "resource.latency_p95_ms": 0.4,
        "resource.peak_memory_mb": 90.0,
        "resource.gpu_memory_mb": None,
    }


def test_a_model_missing_a_threshold_fails_the_gate():
    def wrong_execute(*, model_payload, X):
        return {"y_pred": [1, 1, 0, 0], "y_proba": None, "resource": {"latency_p95_ms": 0.4}}

    eval_run = evaluate(
        manifest=MANIFEST,
        fixture=FIXTURE,
        data=DATA,
        model_payload=b"pickled",
        execute_fn=wrong_execute,
        resolve_fn=passing_plan,
        now_fn=fake_clock(),
    )

    assert eval_run["verdict"] == "fail"
    assert eval_run["failed_on"] == [
        {"metric": "accuracy", "op": ">=", "value": 0.7, "actual": 0.0}
    ]


def test_a_fixture_kind_with_no_mechanism_errors_instead_of_evaluating_the_wrong_way():
    eval_run = evaluate(
        manifest=MANIFEST,
        fixture={"kind": "corpus_index", "uri": "seaweedfs://verity/xyz"},
        data=DATA,
        model_payload=b"pickled",
        execute_fn=perfect_execute,
        resolve_fn=passing_plan,
        now_fn=fake_clock(),
    )

    assert eval_run["verdict"] == "error"
    assert "corpus_index" in eval_run["error"]["message"]
    assert eval_run["scores"] == {}
    assert eval_run["finished_at"] == "2026-08-03T10:00:02+00:00"


def test_a_crash_inside_the_sandbox_becomes_an_error_verdict_not_an_exception():
    def exploding_execute(*, model_payload, X):
        raise RuntimeError("sandbox exited with code 1")

    eval_run = evaluate(
        manifest=MANIFEST,
        fixture=FIXTURE,
        data=DATA,
        model_payload=b"pickled",
        execute_fn=exploding_execute,
        resolve_fn=passing_plan,
        now_fn=fake_clock(),
    )

    assert eval_run["verdict"] == "error"
    assert eval_run["error"]["type"] == "RuntimeError"
    assert "sandbox exited with code 1" in eval_run["error"]["message"]


def test_an_llm_failure_while_planning_becomes_an_error_verdict():
    def exploding_resolve(**_):
        raise ValueError("the model returned no metric_set")

    eval_run = evaluate(
        manifest=MANIFEST,
        fixture=FIXTURE,
        data=DATA,
        model_payload=b"pickled",
        execute_fn=perfect_execute,
        resolve_fn=exploding_resolve,
        now_fn=fake_clock(),
    )

    assert eval_run["verdict"] == "error"
    assert eval_run["error"]["type"] == "ValueError"


def test_the_dataset_profile_and_atlas_section_are_handed_to_the_planner():
    seen = {}

    def recording_resolve(**kwargs):
        seen.update(kwargs)
        return passing_plan()

    evaluate(
        manifest=MANIFEST,
        fixture=FIXTURE,
        data=DATA,
        model_payload=b"pickled",
        execute_fn=perfect_execute,
        resolve_fn=recording_resolve,
        now_fn=fake_clock(),
    )

    assert seen["manifest"] == MANIFEST
    assert seen["atlas_section"] == "ML"
    assert seen["profile"]["n_classes"] == 2
    assert seen["profile"]["n_samples"] == 4
    assert "latency_p95_ms" in seen["available_resource_metrics"]
