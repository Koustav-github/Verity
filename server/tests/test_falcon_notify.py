from datetime import datetime, timedelta, timezone

from agents.brain4.falcon.notify import ALERT_COOLDOWN_MINUTES, record_and_notify


class FakeMetadataStore:
    def __init__(self, model_with_email=None, existing_alerts=None):
        self.saved_alerts = []
        self.updated_alerts = []
        self._model = model_with_email
        # Newest-first, matching the real store's contract for find_alert_events.
        self._existing_alerts = existing_alerts or []

    def save_alert_event(self, *, model_version_id, kind, metric, detail):
        self.saved_alerts.append(
            {"model_version_id": model_version_id, "kind": kind, "metric": metric, "detail": detail}
        )
        return f"alrt_{len(self.saved_alerts)}"

    def update_alert_event(self, *, alert_event_id, emailed_at):
        self.updated_alerts.append({"alert_event_id": alert_event_id, "emailed_at": emailed_at})

    def find_model_by_version(self, *, model_version_id):
        return self._model

    def find_alert_events(self, *, model_version_id):
        return [a for a in self._existing_alerts if a["model_version_id"] == model_version_id]


def test_record_and_notify_writes_the_alert_row_first():
    store = FakeMetadataStore(model_with_email={"alert_email": None})

    alert_id = record_and_notify(
        model_version_id="mv_1", kind="systemic", metric="error_rate",
        detail={"recent": 0.3}, metadata_store=store,
        email_fn=lambda **kwargs: None,
    )

    assert alert_id == "alrt_1"
    assert store.saved_alerts[0]["kind"] == "systemic"


def test_record_and_notify_skips_email_when_no_address_is_configured():
    store = FakeMetadataStore(model_with_email={"alert_email": None})
    sent = []

    record_and_notify(
        model_version_id="mv_1", kind="systemic", metric="error_rate",
        detail={}, metadata_store=store, email_fn=lambda **kwargs: sent.append(kwargs),
    )

    assert sent == []
    assert store.updated_alerts == []


def test_record_and_notify_sends_email_when_an_address_is_configured():
    store = FakeMetadataStore(model_with_email={"alert_email": "ops@example.com"})
    sent = []

    record_and_notify(
        model_version_id="mv_1", kind="quality", metric="accuracy",
        detail={"actual": 0.5}, metadata_store=store,
        email_fn=lambda **kwargs: sent.append(kwargs),
    )

    assert sent[0]["to"] == "ops@example.com"
    assert "accuracy" in sent[0]["subject"]
    assert store.updated_alerts[0]["alert_event_id"] == "alrt_1"


def test_a_raising_email_fn_does_not_prevent_the_alert_from_being_recorded():
    store = FakeMetadataStore(model_with_email={"alert_email": "ops@example.com"})

    def boom(**kwargs):
        raise RuntimeError("SES is down")

    # Must not raise: the alert row already landed, which is the source of truth.
    alert_id = record_and_notify(
        model_version_id="mv_1", kind="systemic", metric="latency_p95_ms",
        detail={}, metadata_store=store, email_fn=boom,
    )

    assert alert_id == "alrt_1"
    assert store.updated_alerts == []  # never confirmed sent


def test_record_and_notify_survives_no_model_found_for_the_version():
    store = FakeMetadataStore(model_with_email=None)
    sent = []

    # A version whose model lookup fails for any reason still gets its in-app row.
    alert_id = record_and_notify(
        model_version_id="mv_1", kind="systemic", metric="error_rate",
        detail={}, metadata_store=store, email_fn=lambda **kwargs: sent.append(kwargs),
    )

    assert alert_id == "alrt_1"
    assert sent == []


# --- cooldown / dedup ---------------------------------------------------------------

FIXED_NOW = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)


def _minutes_ago(n):
    return (FIXED_NOW - timedelta(minutes=n)).isoformat()


def test_a_second_alert_of_the_same_kind_within_the_cooldown_is_suppressed():
    store = FakeMetadataStore(
        model_with_email={"alert_email": "ops@example.com"},
        existing_alerts=[
            {"model_version_id": "mv_1", "kind": "systemic", "created_at": _minutes_ago(5)},
        ],
    )
    sent = []

    result = record_and_notify(
        model_version_id="mv_1", kind="systemic", metric="error_rate",
        detail={}, metadata_store=store, email_fn=lambda **kwargs: sent.append(kwargs),
        now_fn=lambda: FIXED_NOW,
    )

    assert result is None
    assert store.saved_alerts == []
    assert sent == []


def test_a_different_kind_on_the_same_version_is_not_suppressed():
    store = FakeMetadataStore(
        model_with_email={"alert_email": "ops@example.com"},
        existing_alerts=[
            {"model_version_id": "mv_1", "kind": "systemic", "created_at": _minutes_ago(5)},
        ],
    )
    sent = []

    result = record_and_notify(
        model_version_id="mv_1", kind="quality", metric="accuracy",
        detail={}, metadata_store=store, email_fn=lambda **kwargs: sent.append(kwargs),
        now_fn=lambda: FIXED_NOW,
    )

    assert result is not None
    assert store.saved_alerts[0]["kind"] == "quality"
    assert sent


def test_an_alert_after_the_cooldown_has_elapsed_is_allowed_through():
    store = FakeMetadataStore(
        model_with_email={"alert_email": "ops@example.com"},
        existing_alerts=[
            {
                "model_version_id": "mv_1", "kind": "systemic",
                "created_at": _minutes_ago(ALERT_COOLDOWN_MINUTES + 1),
            },
        ],
    )
    sent = []

    result = record_and_notify(
        model_version_id="mv_1", kind="systemic", metric="error_rate",
        detail={}, metadata_store=store, email_fn=lambda **kwargs: sent.append(kwargs),
        now_fn=lambda: FIXED_NOW,
    )

    assert result is not None
    assert store.saved_alerts[0]["kind"] == "systemic"
    assert sent


def test_the_first_alert_ever_for_a_version_is_never_suppressed():
    store = FakeMetadataStore(model_with_email={"alert_email": "ops@example.com"}, existing_alerts=[])
    sent = []

    result = record_and_notify(
        model_version_id="mv_1", kind="systemic", metric="error_rate",
        detail={}, metadata_store=store, email_fn=lambda **kwargs: sent.append(kwargs),
        now_fn=lambda: FIXED_NOW,
    )

    assert result is not None
    assert store.saved_alerts
    assert sent


def test_five_detections_ten_seconds_apart_produce_exactly_one_alert_and_one_email():
    # The 180-alert scenario from the review, compressed: a persisting regression
    # detected repeatedly in quick succession must write exactly one row and send
    # exactly one email, not one per detection.
    store = FakeMetadataStore(model_with_email={"alert_email": "ops@example.com"})
    sent = []
    clock = {"now": FIXED_NOW}

    def now_fn():
        return clock["now"]

    def email_fn(**kwargs):
        sent.append(kwargs)

    results = []
    for _ in range(5):
        results.append(
            record_and_notify(
                model_version_id="mv_1", kind="systemic", metric="error_rate",
                detail={}, metadata_store=store, email_fn=email_fn, now_fn=now_fn,
            )
        )
        # Advance the fake clock 10 seconds, and mirror what a real store would now
        # contain: the alert just written, if any.
        if store.saved_alerts and not store._existing_alerts:
            store._existing_alerts = [
                {"model_version_id": "mv_1", "kind": "systemic", "created_at": clock["now"].isoformat()}
            ]
        clock["now"] = clock["now"] + timedelta(seconds=10)

    assert len(store.saved_alerts) == 1
    assert len(sent) == 1
    assert results == [results[0], None, None, None, None]
