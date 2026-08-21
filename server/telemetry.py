import numpy as np


def summarize(*, events, eval_reference=None, limit=None):
    """Turn raw telemetry rows into the V1 metric set.

    Deliberately compares nothing: `eval_reference` is passed straight through for
    side-by-side display, never checked against the observed values. The reference is a
    sandbox feasibility figure and the observed values are production under real load —
    comparing them would produce false alarms, and alerting is V7 regardless.
    """
    count = len(events)
    latencies = [e["latency_ms"] for e in events if e.get("latency_ms") is not None]
    errors = sum(1 for e in events if e.get("status") != "ok")

    summary = {
        "request_count": count,
        "error_rate": (errors / count) if count else 0.0,
        "latency_p50_ms": None,
        "latency_p95_ms": None,
        "latency_p99_ms": None,
        # The read path caps how many rows it fetches (relational storage at V1; the
        # analytics store is V3). Say so rather than reporting a silently partial window.
        "truncated": limit is not None and count >= limit,
        "eval_reference": eval_reference,
    }

    if latencies:
        values = np.asarray(latencies)
        summary["latency_p50_ms"] = float(np.percentile(values, 50))
        summary["latency_p95_ms"] = float(np.percentile(values, 95))
        summary["latency_p99_ms"] = float(np.percentile(values, 99))

    return summary
