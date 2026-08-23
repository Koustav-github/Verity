from agents.brain2.nat.score import RESOURCE_PREFIX
from agents.brain4.falcon.detect import (
    MIN_LABELS,
    RELATIVE_INCREASE_THRESHOLD,
    WINDOW_MIN_EVENTS,
    WINDOW_MINUTES,
    detect_quality_anomaly,
    detect_systemic_anomaly,
)

TELEMETRY_QUERY_LIMIT = 10_000  # matches find_telemetry_events's own default; named explicitly
                                  # so check_systemic can detect truncation rather than silently
                                  # miscounting a busy model's baseline window

# Incremented when check_systemic/check_quality catch an exception in their non-fatal
# try/except — that contract must never break a caller, but a permanently broken
# detector must not be silently indistinguishable from "all clear". Mirrors
# server/serving/sink.py's TelemetrySink.dropped: a module-level counter for exactly
# this kind of "something is silently failing, count it" case. No lock: unlike
# TelemetrySink.dropped, which is written from multiple threads, check_systemic and
# check_quality are called from request-handling contexts, so this level of simplicity
# matches the codebase's own risk tolerance elsewhere.
detection_errors = 0


def _record_detection_error():
    global detection_errors
    detection_errors += 1


# The V1 metric set, fixed. Falcon does not choose these per model — the README's V1 scope
# for Falcon is exactly "request count, latency percentiles, error rate", and unlike Nat's
# quality metrics there is nothing task-dependent about them: every served model has
# requests, latency, and errors.
METRICS = [
    "request_count",
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "error_rate",
]


def build_eval_reference(*, eval_run_id, scores):
    """Split an eval_run's flat score map into resource values and quality values.

    The `basis` marker is not decoration. These numbers come from a single-process,
    single-client, cold sandbox — they are a feasibility reference, NOT a production
    baseline, and production latency under real concurrency will be materially higher.
    Nothing in V1 compares against them; they are recorded for context and for V7's rule
    engine, which must be able to tell what kind of number it is reading.
    """
    reference = {"basis": "sandbox_feasibility", "eval_run_id": eval_run_id, "quality": {}}
    for key, value in (scores or {}).items():
        if key.startswith(RESOURCE_PREFIX):
            reference[key[len(RESOURCE_PREFIX):]] = value
        else:
            reference["quality"][key] = value
    return reference


def build_alert_thresholds(*, eval_run):
    """Freeze the exact metric_set and thresholds that gated this promotion, the same
    "as applied" philosophy as eval_run.thresholds: a later change to the detection
    defaults must not retroactively change what an already-promoted version is watched
    against."""
    return {
        "error_rate_relative_increase": RELATIVE_INCREASE_THRESHOLD,
        "latency_p95_relative_increase": RELATIVE_INCREASE_THRESHOLD,
        "quality_metric_set": eval_run.get("metric_set", {}).get("resolved", []),
        "quality_thresholds": eval_run.get("thresholds", []),
    }


def configure(*, model_version_id, eval_run_id, eval_run, metadata_store):
    """Switch monitoring on for a version that just reached production.

    Deterministic, like Fury: the reference is lifted from evidence that already exists
    (the eval_run that promoted this version), so there is nothing to guess and no LLM
    call to make.
    """
    config = {
        "metrics": METRICS,
        "eval_reference": build_eval_reference(
            eval_run_id=eval_run_id, scores=eval_run.get("scores", {})
        ),
        "alert_thresholds": build_alert_thresholds(eval_run=eval_run),
    }
    config_id = metadata_store.save_monitoring_config(
        model_version_id=model_version_id, eval_run_id=eval_run_id, config=config
    )
    return {"id": config_id, **config}


def check_systemic(*, model_version_id, metadata_store, now_fn=None, notify_fn=None):
    """Compare the two adjacent WINDOW_MINUTES windows for this version and notify on an
    anomaly. Non-fatal: called from a background flush and a request path, neither of
    which may fail because a detection bug did."""
    now_fn = now_fn or _default_now
    notify_fn = notify_fn or _default_notify

    try:
        from telemetry import summarize  # server/telemetry.py — reachable because this only ever
                                           # runs inside the server process (uvicorn's cwd, or
                                           # pytest's pythonpath=[".", ".."]); a failure here is
                                           # caught by this function's own try/except and counted
                                           # by detection_errors above, not silent.

        now = now_fn()
        recent_since = now - _minutes(WINDOW_MINUTES)
        baseline_since = now - _minutes(2 * WINDOW_MINUTES)

        # find_telemetry_events has no upper bound (`since` only) — calling it twice
        # with two different `since` values would make the "baseline" window also
        # include every "recent" event. One call over the full span, split here.
        events = metadata_store.find_telemetry_events(
            model_version_id=model_version_id, since=_iso(baseline_since),
            limit=TELEMETRY_QUERY_LIMIT,
        )
        if len(events) >= TELEMETRY_QUERY_LIMIT:
            # Above roughly 5.5 sustained requests/second, ALL returned rows (ordered
            # occurred_at descending, then limited) fall within the most recent
            # WINDOW_MINUTES, so the Python-side split below would silently produce an
            # empty baseline_events and make the check less likely to fire the busier a
            # model gets — the opposite of intended. A limit-truncated window can't be
            # trusted to represent the whole span, so bail out explicitly instead.
            return None
        # Strict `>` for recent (not `>=`): an event occurring exactly at the
        # recent/baseline boundary is the last tick of the baseline window, not the
        # first of the recent one.
        recent_events = [e for e in events if _occurred_at(e) > recent_since]
        baseline_events = [e for e in events if _occurred_at(e) <= recent_since]
        anomaly = detect_systemic_anomaly(
            recent_summary=summarize(events=recent_events),
            baseline_summary=summarize(events=baseline_events),
        )
        if anomaly is None:
            return None
        notify_fn(
            model_version_id=model_version_id, kind="systemic",
            metric=anomaly["metric"], detail=anomaly, metadata_store=metadata_store,
        )
        return anomaly
    except Exception:  # noqa: BLE001
        _record_detection_error()
        return None


def check_quality(*, model_version_id, metadata_store, notify_fn=None):
    """Below MIN_LABELS, a no-op. Otherwise re-score against the exact thresholds that
    gated this version's promotion and notify on a failure. Non-fatal for the same
    reason as check_systemic."""
    notify_fn = notify_fn or _default_notify

    try:
        config = metadata_store.find_monitoring_config(model_version_id=model_version_id)
        if not config or not config.get("alert_thresholds"):
            return None

        outcomes = metadata_store.find_labeled_outcomes(model_version_id=model_version_id)
        if len(outcomes) < MIN_LABELS:
            return None

        thresholds = config["alert_thresholds"]
        anomaly = detect_quality_anomaly(
            metric_set=thresholds["quality_metric_set"],
            thresholds=thresholds["quality_thresholds"],
            y_true=[o["y_true"] for o in outcomes],
            y_pred=[o["y_pred"] for o in outcomes],
            y_proba=[o["y_proba"] for o in outcomes] if any(o["y_proba"] for o in outcomes) else None,
        )
        if anomaly is None:
            return None
        notify_fn(
            model_version_id=model_version_id, kind="quality",
            metric=anomaly["metric"], detail=anomaly, metadata_store=metadata_store,
        )
        return anomaly
    except Exception:  # noqa: BLE001
        _record_detection_error()
        return None


def _minutes(n):
    from datetime import timedelta

    return timedelta(minutes=n)


def _iso(dt):
    return dt.isoformat()


def _occurred_at(event):
    """Parse a telemetry_event's occurred_at back into a comparable datetime.

    Handles a trailing "Z" (some ISO producers use it, Python's own isoformat() output
    from _iso() above doesn't) so a real Postgres-returned timestamp and a test fixture
    built with _iso() compare correctly either way.
    """
    from datetime import datetime

    value = event["occurred_at"]
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def _default_now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _default_notify(**kwargs):
    from agents.brain4.falcon.notify import record_and_notify

    return record_and_notify(**kwargs)
