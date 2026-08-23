import pytest

from agents.brain4.falcon.detect import (
    MIN_LABELS,
    RELATIVE_INCREASE_THRESHOLD,
    WINDOW_MIN_EVENTS,
    detect_quality_anomaly,
    detect_systemic_anomaly,
)


def _summary(request_count=50, error_rate=0.01, latency_p95_ms=20.0):
    return {
        "request_count": request_count,
        "error_rate": error_rate,
        "latency_p50_ms": latency_p95_ms / 2,
        "latency_p95_ms": latency_p95_ms,
        "latency_p99_ms": latency_p95_ms * 1.1,
        "truncated": False,
        "eval_reference": None,
    }


def test_a_fifty_percent_error_rate_jump_trips_the_systemic_check():
    baseline = _summary(error_rate=0.02)
    recent = _summary(error_rate=0.02 * (1 + RELATIVE_INCREASE_THRESHOLD) + 0.001)

    anomaly = detect_systemic_anomaly(recent_summary=recent, baseline_summary=baseline)

    assert anomaly["metric"] == "error_rate"
    assert anomaly["baseline"] == 0.02
    assert anomaly["relative_increase"] > RELATIVE_INCREASE_THRESHOLD


def test_a_fifty_percent_latency_jump_trips_the_systemic_check():
    baseline = _summary(latency_p95_ms=20.0)
    recent = _summary(latency_p95_ms=20.0 * (1 + RELATIVE_INCREASE_THRESHOLD) + 1.0)

    anomaly = detect_systemic_anomaly(recent_summary=recent, baseline_summary=baseline)

    assert anomaly["metric"] == "latency_p95_ms"


def test_error_rate_is_checked_before_latency_when_both_have_jumped():
    baseline = _summary(error_rate=0.02, latency_p95_ms=20.0)
    recent = _summary(error_rate=0.05, latency_p95_ms=40.0)

    anomaly = detect_systemic_anomaly(recent_summary=recent, baseline_summary=baseline)

    assert anomaly["metric"] == "error_rate"


def test_normal_variance_does_not_trip_the_check():
    baseline = _summary(error_rate=0.02, latency_p95_ms=20.0)
    recent = _summary(error_rate=0.021, latency_p95_ms=20.5)

    assert detect_systemic_anomaly(recent_summary=recent, baseline_summary=baseline) is None


def test_a_window_below_the_minimum_event_count_is_never_flagged_even_if_the_numbers_look_bad():
    baseline = _summary(request_count=WINDOW_MIN_EVENTS - 1, error_rate=0.01)
    recent = _summary(request_count=WINDOW_MIN_EVENTS - 1, error_rate=0.9)

    # A handful of requests producing a scary-looking rate is noise, not signal.
    assert detect_systemic_anomaly(recent_summary=recent, baseline_summary=baseline) is None


def test_a_baseline_error_rate_of_zero_does_not_divide_by_zero():
    baseline = _summary(error_rate=0.0)
    recent = _summary(error_rate=0.1)

    # Any nonzero rate against a perfect baseline is worth flagging, not a ZeroDivisionError.
    anomaly = detect_systemic_anomaly(recent_summary=recent, baseline_summary=baseline)

    assert anomaly["metric"] == "error_rate"


def _threshold(metric, op, value):
    return {"metric": metric, "op": op, "value": value}


def test_a_real_threshold_failure_is_caught():
    thresholds = [_threshold("accuracy", ">=", 0.9)]

    anomaly = detect_quality_anomaly(
        metric_set=["accuracy"],
        thresholds=thresholds,
        y_true=[1, 0, 1, 0, 1, 0, 1, 0, 1, 0],
        y_pred=[1, 0, 1, 0, 1, 0, 1, 1, 0, 1],  # 3 wrong of 10 -> 0.7 accuracy
    )

    assert anomaly is not None
    assert anomaly["metric"] == "accuracy"


def test_a_clean_pass_returns_none():
    thresholds = [_threshold("accuracy", ">=", 0.5)]

    anomaly = detect_quality_anomaly(
        metric_set=["accuracy"],
        thresholds=thresholds,
        y_true=[1, 0, 1, 0],
        y_pred=[1, 0, 1, 0],
    )

    assert anomaly is None


def test_a_resource_threshold_in_the_mix_is_filtered_out_rather_than_causing_a_false_failure():
    # A resource.* threshold has no corresponding key in a quality-only scores dict.
    # Nat's own rule ("a threshold on a skipped metric is a failure, not a silent pass")
    # would otherwise report a quality alert caused by a systemic threshold that was
    # never given data to evaluate.
    thresholds = [
        _threshold("accuracy", ">=", 0.5),
        _threshold("resource.latency_p95_ms", "<=", 100.0),
    ]

    anomaly = detect_quality_anomaly(
        metric_set=["accuracy"],
        thresholds=thresholds,
        y_true=[1, 0, 1, 0],
        y_pred=[1, 0, 1, 0],
    )

    assert anomaly is None


def test_a_proba_only_metric_is_scored_when_y_proba_is_supplied():
    thresholds = [_threshold("roc_auc", ">=", 0.99)]

    anomaly = detect_quality_anomaly(
        metric_set=["roc_auc"],
        thresholds=thresholds,
        y_true=[0, 0, 1, 1],
        y_pred=[0, 0, 1, 1],
        y_proba=[[0.1, 0.9], [0.2, 0.8], [0.6, 0.4], [0.7, 0.3]],
    )

    assert anomaly is not None  # this proba ordering scores well below 0.99
