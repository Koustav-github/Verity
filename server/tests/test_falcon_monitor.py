from datetime import datetime, timedelta, timezone

from agents.brain4.falcon.detect import MIN_LABELS, RELATIVE_INCREASE_THRESHOLD, WINDOW_MIN_EVENTS
from agents.brain4.falcon.monitor import (
    METRICS,
    build_alert_thresholds,
    build_eval_reference,
    check_quality,
    check_systemic,
    configure,
)


class FakeMetadataStore:
    def __init__(self):
        self.saved = []
        self.telemetry_windows = []   # each `since` value the check asked for, in call order
        self.events = []              # the full window's events, spanning both halves
        self.monitoring_configs = {}
        self.labeled_outcomes = {}

    def save_monitoring_config(self, *, model_version_id, eval_run_id, config):
        self.saved.append(
            {"model_version_id": model_version_id, "eval_run_id": eval_run_id, "config": config}
        )
        return "mcfg_1"

    def find_monitoring_config(self, *, model_version_id):
        return self.monitoring_configs.get(model_version_id)

    def find_labeled_outcomes(self, *, model_version_id, limit=1000):
        return self.labeled_outcomes.get(model_version_id, [])

    def find_telemetry_events(self, *, model_version_id, since, limit=10_000):
        self.telemetry_windows.append(since)
        return self.events


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


FIXED_NOW = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)


def _events_in_window(start_minutes_ago, end_minutes_ago, count, status="ok"):
    """`count` events evenly spaced between `end_minutes_ago` and `start_minutes_ago`
    minutes before FIXED_NOW -- a slice `check_systemic` will carve out of one combined
    result once it splits by `occurred_at` against its recent/baseline boundary."""
    step = (start_minutes_ago - end_minutes_ago) / count
    return [
        {
            "status": status,
            "latency_ms": 10.0,
            "occurred_at": (FIXED_NOW - timedelta(minutes=end_minutes_ago + i * step)).isoformat(),
        }
        for i in range(count)
    ]


def test_build_alert_thresholds_carries_the_eval_runs_own_thresholds_forward():
    eval_run = {
        "metric_set": {"resolved": ["accuracy", "f1"], "skipped": []},
        "thresholds": [{"metric": "accuracy", "op": ">=", "value": 0.9}],
    }

    thresholds = build_alert_thresholds(eval_run=eval_run)

    assert thresholds["quality_metric_set"] == ["accuracy", "f1"]
    assert thresholds["quality_thresholds"] == eval_run["thresholds"]
    assert thresholds["error_rate_relative_increase"] == RELATIVE_INCREASE_THRESHOLD
    assert thresholds["latency_p95_relative_increase"] == RELATIVE_INCREASE_THRESHOLD


def test_configure_writes_alert_thresholds_alongside_the_eval_reference():
    store = FakeMetadataStore()

    config = configure(
        model_version_id="mv_1", eval_run_id="evr_1",
        eval_run={
            "verdict": "pass", "scores": {},
            "metric_set": {"resolved": ["accuracy"], "skipped": []},
            "thresholds": [{"metric": "accuracy", "op": ">=", "value": 0.9}],
        },
        metadata_store=store,
    )

    assert config["alert_thresholds"]["quality_thresholds"] == [
        {"metric": "accuracy", "op": ">=", "value": 0.9}
    ]
    assert store.saved[0]["config"]["alert_thresholds"] == config["alert_thresholds"]


def test_check_systemic_notifies_on_a_real_jump():
    store = FakeMetadataStore()
    store.events = [
        *_events_in_window(30, 15, WINDOW_MIN_EVENTS),                # baseline: clean
        *_events_in_window(15, 0, WINDOW_MIN_EVENTS, status="error"), # recent: all errors
    ]
    notified = []

    check_systemic(
        model_version_id="mv_1", metadata_store=store,
        now_fn=lambda: FIXED_NOW, notify_fn=lambda **kwargs: notified.append(kwargs),
    )

    assert notified
    assert notified[0]["kind"] == "systemic"
    assert notified[0]["model_version_id"] == "mv_1"
    # The single-call design: find_telemetry_events has no upper time bound, so a second
    # call with a different `since` would silently make the windows overlap. Exactly one
    # call, split into recent/baseline in Python, is the whole point of that design.
    assert len(store.telemetry_windows) == 1


def test_check_systemic_does_not_notify_when_nothing_is_wrong():
    store = FakeMetadataStore()
    store.events = [
        *_events_in_window(30, 15, WINDOW_MIN_EVENTS),
        *_events_in_window(15, 0, WINDOW_MIN_EVENTS),
    ]
    notified = []

    check_systemic(
        model_version_id="mv_1", metadata_store=store,
        now_fn=lambda: FIXED_NOW, notify_fn=lambda **kwargs: notified.append(kwargs),
    )

    assert notified == []


def test_check_systemic_swallows_a_raising_notify_fn():
    store = FakeMetadataStore()
    store.events = [
        *_events_in_window(30, 15, WINDOW_MIN_EVENTS),
        *_events_in_window(15, 0, WINDOW_MIN_EVENTS, status="error"),
    ]

    def boom(**kwargs):
        raise RuntimeError("email provider down")

    # Must not raise: a broken notification path cannot break telemetry recording.
    check_systemic(
        model_version_id="mv_1", metadata_store=store,
        now_fn=lambda: FIXED_NOW, notify_fn=boom,
    )


def test_check_quality_is_a_noop_below_the_minimum_label_count():
    store = FakeMetadataStore()
    store.monitoring_configs["mv_1"] = {
        "alert_thresholds": {
            "quality_metric_set": ["accuracy"],
            "quality_thresholds": [{"metric": "accuracy", "op": ">=", "value": 0.99}],
        }
    }
    store.labeled_outcomes["mv_1"] = [
        {"y_true": 1, "y_pred": 0, "y_proba": None} for _ in range(MIN_LABELS - 1)
    ]
    notified = []

    check_quality(
        model_version_id="mv_1", metadata_store=store,
        notify_fn=lambda **kwargs: notified.append(kwargs),
    )

    assert notified == []


def test_check_quality_notifies_on_a_real_threshold_failure():
    store = FakeMetadataStore()
    store.monitoring_configs["mv_1"] = {
        "alert_thresholds": {
            "quality_metric_set": ["accuracy"],
            "quality_thresholds": [{"metric": "accuracy", "op": ">=", "value": 0.99}],
        }
    }
    store.labeled_outcomes["mv_1"] = [
        {"y_true": 1, "y_pred": 0, "y_proba": None} for _ in range(MIN_LABELS)
    ]
    notified = []

    check_quality(
        model_version_id="mv_1", metadata_store=store,
        notify_fn=lambda **kwargs: notified.append(kwargs),
    )

    assert notified
    assert notified[0]["kind"] == "quality"


def test_check_quality_is_a_noop_when_the_version_has_no_monitoring_config():
    store = FakeMetadataStore()
    notified = []

    # A version predating this feature, or one whose config write failed, must not crash
    # the outcome-reporting endpoint that calls this.
    check_quality(
        model_version_id="mv_never_configured", metadata_store=store,
        notify_fn=lambda **kwargs: notified.append(kwargs),
    )

    assert notified == []
