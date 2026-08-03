from agents.brain2.nat.score import apply_thresholds, merge_resource


def test_merge_resource_namespaces_systemic_metrics_alongside_quality_ones():
    merged = merge_resource(
        {"accuracy": 0.9},
        {"latency_p95_ms": 1.1, "gpu_memory_mb": None},
    )

    assert merged == {
        "accuracy": 0.9,
        "resource.latency_p95_ms": 1.1,
        "resource.gpu_memory_mb": None,
    }


def test_a_run_clearing_every_threshold_passes():
    verdict, failed_on = apply_thresholds(
        scores={"accuracy": 0.91, "resource.latency_p95_ms": 1.1},
        thresholds=[
            {"metric": "accuracy", "op": ">=", "value": 0.7},
            {"metric": "resource.latency_p95_ms", "op": "<=", "value": 100},
        ],
    )

    assert verdict == "pass"
    assert failed_on == []


def test_a_missed_quality_threshold_fails_and_names_the_metric():
    verdict, failed_on = apply_thresholds(
        scores={"accuracy": 0.42, "f1": 0.95},
        thresholds=[
            {"metric": "accuracy", "op": ">=", "value": 0.7},
            {"metric": "f1", "op": ">=", "value": 0.7},
        ],
    )

    assert verdict == "fail"
    assert failed_on == [
        {"metric": "accuracy", "op": ">=", "value": 0.7, "actual": 0.42}
    ]


def test_a_missed_resource_threshold_gates_exactly_like_a_quality_one():
    verdict, failed_on = apply_thresholds(
        scores={"accuracy": 0.99, "resource.latency_p95_ms": 250.0},
        thresholds=[
            {"metric": "accuracy", "op": ">=", "value": 0.7},
            {"metric": "resource.latency_p95_ms", "op": "<=", "value": 100},
        ],
    )

    assert verdict == "fail"
    assert failed_on == [
        {"metric": "resource.latency_p95_ms", "op": "<=", "value": 100, "actual": 250.0}
    ]


def test_a_threshold_on_a_metric_that_was_never_scored_fails_rather_than_passing_silently():
    verdict, failed_on = apply_thresholds(
        scores={"accuracy": 0.99},
        thresholds=[{"metric": "roc_auc", "op": ">=", "value": 0.8}],
    )

    assert verdict == "fail"
    assert failed_on == [
        {"metric": "roc_auc", "op": ">=", "value": 0.8, "actual": None}
    ]


def test_a_threshold_on_an_unmeasurable_resource_metric_fails_rather_than_passing_silently():
    verdict, failed_on = apply_thresholds(
        scores={"resource.gpu_memory_mb": None},
        thresholds=[{"metric": "resource.gpu_memory_mb", "op": "<=", "value": 4096}],
    )

    assert verdict == "fail"
    assert failed_on == [
        {"metric": "resource.gpu_memory_mb", "op": "<=", "value": 4096, "actual": None}
    ]
