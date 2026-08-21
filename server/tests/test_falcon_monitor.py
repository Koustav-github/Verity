from agents.brain4.falcon.monitor import METRICS, build_eval_reference, configure


class FakeMetadataStore:
    def __init__(self):
        self.saved = []

    def save_monitoring_config(self, *, model_version_id, eval_run_id, config):
        self.saved.append(
            {"model_version_id": model_version_id, "eval_run_id": eval_run_id, "config": config}
        )
        return "mcfg_1"


EVAL_RUN = {
    "verdict": "pass",
    "scores": {
        "accuracy": 1.0,
        "f1": 0.98,
        "resource.latency_p50_ms": 0.165,
        "resource.latency_p95_ms": 0.234,
        "resource.peak_memory_mb": 111.4,
        "resource.gpu_memory_mb": None,
    },
}


def test_the_reference_splits_resource_metrics_from_quality_scores():
    reference = build_eval_reference(eval_run_id="evr_1", scores=EVAL_RUN["scores"])

    assert reference["latency_p50_ms"] == 0.165
    assert reference["latency_p95_ms"] == 0.234
    assert reference["peak_memory_mb"] == 111.4
    assert reference["gpu_memory_mb"] is None
    assert reference["quality"] == {"accuracy": 1.0, "f1": 0.98}


def test_the_reference_is_labelled_as_sandbox_feasibility_not_a_production_baseline():
    reference = build_eval_reference(eval_run_id="evr_1", scores=EVAL_RUN["scores"])

    assert reference["basis"] == "sandbox_feasibility"
    assert reference["eval_run_id"] == "evr_1"


def test_configure_saves_a_config_carrying_the_fixed_v1_metric_set():
    store = FakeMetadataStore()

    config = configure(
        model_version_id="mv_1",
        eval_run_id="evr_1",
        eval_run=EVAL_RUN,
        metadata_store=store,
    )

    assert config["id"] == "mcfg_1"
    assert config["metrics"] == METRICS
    assert "request_count" in METRICS and "error_rate" in METRICS
    assert store.saved[0]["model_version_id"] == "mv_1"
    assert store.saved[0]["eval_run_id"] == "evr_1"


def test_configure_survives_an_eval_run_that_recorded_no_scores():
    store = FakeMetadataStore()

    config = configure(
        model_version_id="mv_1",
        eval_run_id="evr_1",
        eval_run={"verdict": "pass"},
        metadata_store=store,
    )

    assert config["eval_reference"]["quality"] == {}
    assert config["metrics"] == METRICS
