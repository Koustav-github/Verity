from agents.brain4.falcon.notify import record_and_notify


class FakeMetadataStore:
    def __init__(self, model_with_email=None):
        self.saved_alerts = []
        self.updated_alerts = []
        self._model = model_with_email

    def save_alert_event(self, *, model_version_id, kind, metric, detail):
        self.saved_alerts.append(
            {"model_version_id": model_version_id, "kind": kind, "metric": metric, "detail": detail}
        )
        return "alrt_1"

    def update_alert_event(self, *, alert_event_id, emailed_at):
        self.updated_alerts.append({"alert_event_id": alert_event_id, "emailed_at": emailed_at})

    def find_model_by_version(self, *, model_version_id):
        return self._model


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
