"""Falcon's two anomaly checks. Zero LLM calls — both are arithmetic over data that
already exists, reusing Nat's own metric and threshold machinery rather than
reimplementing gating a second time.
"""

from agents.brain2.nat.score import RESOURCE_PREFIX, apply_thresholds, score

WINDOW_MINUTES = 15             # recent = [now - WINDOW_MINUTES, now)
                                # baseline = [now - 2*WINDOW_MINUTES, now - WINDOW_MINUTES)
WINDOW_MIN_EVENTS = 20          # below this, normal variance looks like an anomaly
RELATIVE_INCREASE_THRESHOLD = 0.5   # 50% worse than the trailing window trips an alert
MIN_LABELS = 30                 # below this, a handful of delayed labels is noise, not signal

# Checked in this order: an error-rate jump is reported over a latency jump when both
# are present, because a request that errors is a worse outcome than one that's merely
# slow, and one alert is more actionable than two firing on the same underlying cause.
_SYSTEMIC_METRICS = ("error_rate", "latency_p95_ms")


def _relative_increase(recent, baseline):
    if baseline <= 0:
        # Any nonzero recent value against a perfect baseline is worth flagging outright,
        # not a ZeroDivisionError. Treated as "infinitely worse".
        return float("inf") if recent > 0 else 0.0
    return (recent - baseline) / baseline


def detect_systemic_anomaly(*, recent_summary, baseline_summary):
    """Compare two summarize() outputs for the same model_version_id.

    Never compares against eval_reference — that's a cold-sandbox estimate and always
    looks better than real traffic, so it would just manufacture false alarms. This
    compares a version against its own recent history instead.
    """
    if (
        recent_summary["request_count"] < WINDOW_MIN_EVENTS
        or baseline_summary["request_count"] < WINDOW_MIN_EVENTS
    ):
        return None

    for metric in _SYSTEMIC_METRICS:
        recent_value = recent_summary[metric]
        baseline_value = baseline_summary[metric]
        increase = _relative_increase(recent_value, baseline_value)
        if increase > RELATIVE_INCREASE_THRESHOLD:
            return {
                "metric": metric,
                "recent": recent_value,
                "baseline": baseline_value,
                "relative_increase": increase,
            }
    return None


def detect_quality_anomaly(*, metric_set, thresholds, y_true, y_pred, y_proba=None):
    """Recompute Nat's own metrics against accumulated labels and re-run the exact
    thresholds that gated promotion.

    Filters `thresholds` to non-resource ones first: a resource.* threshold has no
    corresponding key in this quality-only scores dict, and Nat's rule that a threshold
    on a skipped metric is a failure (not a silent pass) would otherwise report a
    quality alert caused by a systemic threshold that was never given data to evaluate.
    """
    outputs = {"y_true": y_true, "y_pred": y_pred, "y_proba": y_proba}
    scores, _skipped = score(section="ML", metric_set=metric_set, outputs=outputs)

    quality_thresholds = [
        t for t in thresholds if not t["metric"].startswith(RESOURCE_PREFIX)
    ]
    verdict, failed_on = apply_thresholds(scores=scores, thresholds=quality_thresholds)
    if verdict == "pass":
        return None
    return failed_on[0]
