import pytest

from telemetry import summarize


def event(latency_ms, status="ok"):
    return {"latency_ms": latency_ms, "status": status}


def test_an_empty_window_reports_zeroes_rather_than_dividing_by_zero():
    summary = summarize(events=[])

    assert summary["request_count"] == 0
    assert summary["error_rate"] == 0.0
    assert summary["latency_p50_ms"] is None
    assert summary["latency_p95_ms"] is None
    assert summary["latency_p99_ms"] is None


def test_percentiles_are_computed_over_the_observed_latencies():
    events = [event(float(n)) for n in range(1, 101)]

    summary = summarize(events=events)

    assert summary["request_count"] == 100
    assert summary["latency_p50_ms"] == pytest.approx(50.5)
    assert summary["latency_p95_ms"] == pytest.approx(95.05)
    assert summary["latency_p99_ms"] == pytest.approx(99.01)


def test_error_rate_counts_every_non_ok_status():
    events = [event(1.0), event(1.0, "error"), event(1.0, "timeout"), event(1.0)]

    summary = summarize(events=events)

    assert summary["error_rate"] == pytest.approx(0.5)


def test_an_errored_event_with_no_latency_still_counts_as_a_request():
    events = [event(2.0), {"latency_ms": None, "status": "error"}]

    summary = summarize(events=events)

    assert summary["request_count"] == 2
    assert summary["error_rate"] == pytest.approx(0.5)
    assert summary["latency_p50_ms"] == pytest.approx(2.0)


def test_hitting_the_read_limit_is_reported_rather_than_silently_truncating():
    events = [event(1.0) for _ in range(10)]

    assert summarize(events=events, limit=10)["truncated"] is True
    assert summarize(events=events, limit=100)["truncated"] is False
    assert summarize(events=events)["truncated"] is False


def test_the_eval_reference_is_passed_through_untouched_for_side_by_side_display():
    reference = {"basis": "sandbox_feasibility", "latency_p95_ms": 0.234}

    summary = summarize(events=[event(50.0)], eval_reference=reference)

    assert summary["eval_reference"] == reference
