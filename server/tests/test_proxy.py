import pytest
from fastapi.testclient import TestClient

from main import app, get_metadata_store, get_predict_transport, get_quality_check, get_telemetry_sink
from serving.sink import TelemetrySink


class FakeStore:
    def __init__(self, version=None, deployment=None):
        self.version = version
        self.deployment = deployment

    def find_production_version_by_name(self, *, user_id, name):
        return self.version

    def find_live_deployment(self, *, model_version_id):
        return self.deployment


class FakeSink:
    def __init__(self, explode=False):
        self.events = []
        self.explode = explode

    def record(self, event):
        if self.explode:
            raise RuntimeError("sink is broken")
        self.events.append(event)


class FakeTransport:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {"predictions": [1], "probabilities": None}
        self.error = error
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json})
        if self.error:
            raise self.error
        payload = self.payload

        class Response:
            @staticmethod
            def json():
                return payload

        return Response()


def _client(store, sink, transport):
    app.dependency_overrides[get_metadata_store] = lambda: store
    app.dependency_overrides[get_telemetry_sink] = lambda: sink
    app.dependency_overrides[get_predict_transport] = lambda: transport
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


LIVE = {"id": "dep_1", "host_port": 49312, "endpoint_url": "http://localhost:49312"}
VERSION = {"id": "mv_1"}


def test_predict_forwards_the_body_to_the_live_container():
    transport = FakeTransport()
    client = _client(FakeStore(VERSION, LIVE), FakeSink(), transport)

    body = client.post(
        "/users/u_1/models/fraud/predict", json={"instances": [[1.0, 2.0]]}
    ).json()

    assert body["predictions"] == [1]
    assert transport.calls[0]["url"] == "http://localhost:49312/predict"
    assert transport.calls[0]["json"] == {"instances": [[1.0, 2.0]]}


def test_predict_404s_distinctly_when_the_model_name_is_unknown():
    client = _client(FakeStore(None, None), FakeSink(), FakeTransport())

    response = client.post("/users/u_1/models/nope/predict", json={"instances": [[1.0]]})

    assert response.status_code == 404
    assert "no production version" in response.json()["detail"].lower()


def test_predict_404s_distinctly_when_the_version_is_promoted_but_not_deployed():
    client = _client(FakeStore(VERSION, None), FakeSink(), FakeTransport())

    response = client.post("/users/u_1/models/fraud/predict", json={"instances": [[1.0]]})

    assert response.status_code == 404
    # One undifferentiated 404 would conflate "wrong name" with "deploy failed", which
    # are opposite problems with opposite fixes.
    assert "not deployed" in response.json()["detail"].lower()


def test_a_successful_prediction_is_recorded_as_telemetry():
    sink = FakeSink()
    client = _client(FakeStore(VERSION, LIVE), sink, FakeTransport())

    client.post("/users/u_1/models/fraud/predict", json={"instances": [[1.0, 2.0]]})

    event = sink.events[0]
    assert event["model_version_id"] == "mv_1"
    assert event["status"] == "ok"
    assert event["latency_ms"] > 0
    # The whole reason api-fication unblocks drift detection: Verity finally sees the
    # inputs. These two columns have existed and stayed null since Falcon shipped.
    assert event["inputs"] == {"instances": [[1.0, 2.0]]}
    assert event["prediction"] == {"predictions": [1], "probabilities": None}


def test_a_failing_prediction_is_recorded_as_an_error_event():
    sink = FakeSink()
    transport = FakeTransport(error=ConnectionError("container is gone"))
    client = _client(FakeStore(VERSION, LIVE), sink, transport)

    response = client.post("/users/u_1/models/fraud/predict", json={"instances": [[1.0]]})

    assert response.status_code == 502
    assert sink.events[0]["status"] == "error"
    assert sink.events[0]["error_type"] == "ConnectionError"


def test_a_broken_telemetry_sink_cannot_break_a_working_prediction():
    client = _client(FakeStore(VERSION, LIVE), FakeSink(explode=True), FakeTransport())

    # Falcon's governing rule, applied to the proxy: monitoring must never be why
    # inference fails.
    response = client.post("/users/u_1/models/fraud/predict", json={"instances": [[1.0]]})

    assert response.status_code == 200
    assert response.json()["predictions"] == [1]


# --- the sink itself ---------------------------------------------------------------

class RecordingStore:
    def __init__(self):
        self.batches = []

    def save_telemetry_events(self, *, events):
        self.batches.append(events)
        return len(events)


def test_the_sink_drops_rather_than_blocking_when_its_queue_is_full():
    # flush_interval is parked high so no background thread races the assertion; the
    # sink is never started here, so nothing drains it.
    sink = TelemetrySink(metadata_store=RecordingStore(), maxsize=2, flush_interval=3600)

    for _ in range(5):
        sink.record({"model_version_id": "mv_1"})

    assert sink.dropped == 3


def test_the_sink_writes_queued_events_on_flush():
    store = RecordingStore()
    sink = TelemetrySink(metadata_store=store, maxsize=10, flush_interval=3600)
    sink.record({"model_version_id": "mv_1"})
    sink.record({"model_version_id": "mv_2"})

    assert sink.flush() == 2
    assert len(store.batches[0]) == 2


def test_flushing_an_empty_sink_does_not_call_the_store():
    store = RecordingStore()
    sink = TelemetrySink(metadata_store=store, maxsize=10, flush_interval=3600)

    assert sink.flush() == 0
    assert store.batches == []


def test_a_store_that_raises_on_flush_does_not_propagate():
    class BrokenStore:
        def save_telemetry_events(self, *, events):
            raise RuntimeError("database down")

    sink = TelemetrySink(metadata_store=BrokenStore(), maxsize=10, flush_interval=3600)
    sink.record({"model_version_id": "mv_1"})

    assert sink.flush() == 0  # must not raise


def test_stop_drains_whatever_is_still_queued():
    store = RecordingStore()
    sink = TelemetrySink(metadata_store=store, maxsize=10, flush_interval=3600)
    sink.start()
    sink.record({"model_version_id": "mv_1"})

    sink.stop()

    assert store.batches and store.batches[-1][0]["model_version_id"] == "mv_1"


def test_flush_calls_detect_fn_once_per_distinct_model_version_in_the_batch():
    store = RecordingStore()
    calls = []
    sink = TelemetrySink(
        metadata_store=store, maxsize=10, flush_interval=3600,
        detect_fn=lambda model_version_id: calls.append(model_version_id),
    )
    sink.record({"model_version_id": "mv_1"})
    sink.record({"model_version_id": "mv_1"})
    sink.record({"model_version_id": "mv_2"})

    sink.flush()

    assert sorted(calls) == ["mv_1", "mv_2"]


def test_a_raising_detect_fn_does_not_stop_flush_from_returning_its_count():
    store = RecordingStore()

    def boom(model_version_id):
        raise RuntimeError("detection is broken")

    sink = TelemetrySink(metadata_store=store, maxsize=10, flush_interval=3600, detect_fn=boom)
    sink.record({"model_version_id": "mv_1"})

    assert sink.flush() == 1  # the save succeeded; detection failing must not undo that


def test_flush_with_no_events_never_calls_detect_fn():
    calls = []
    sink = TelemetrySink(
        metadata_store=RecordingStore(), maxsize=10, flush_interval=3600,
        detect_fn=lambda model_version_id: calls.append(model_version_id),
    )

    sink.flush()

    assert calls == []


# --- prediction_id and the outcomes route -------------------------------------------

def test_predict_returns_a_prediction_id():
    client = _client(FakeStore(VERSION, LIVE), FakeSink(), FakeTransport())

    body = client.post(
        "/users/u_1/models/fraud/predict", json={"instances": [[1.0, 2.0]]}
    ).json()

    assert body["prediction_id"].startswith("pred_")


def test_the_prediction_id_is_recorded_on_the_telemetry_event():
    sink = FakeSink()
    client = _client(FakeStore(VERSION, LIVE), sink, FakeTransport())

    body = client.post(
        "/users/u_1/models/fraud/predict", json={"instances": [[1.0]]}
    ).json()

    assert sink.events[0]["prediction_id"] == body["prediction_id"]


class FakeOutcomeStore:
    def __init__(self, event=None):
        self.event = event
        self.saved_labels = []
        self.checked = []

    def find_telemetry_event_by_prediction_id(self, *, prediction_id):
        return self.event

    def save_label_event(self, *, telemetry_event_id, instance_index, actual):
        self.saved_labels.append(
            {"telemetry_event_id": telemetry_event_id, "instance_index": instance_index, "actual": actual}
        )
        return "lbl_1"


def test_outcomes_404s_for_an_unknown_prediction_id():
    client = _client(FakeOutcomeStore(event=None), FakeSink(), FakeTransport())

    response = client.post(
        "/predictions/pred_nope/outcomes", json={"outcomes": [{"index": 0, "actual": 1}]}
    )

    assert response.status_code == 404


def test_outcomes_422s_for_an_index_outside_the_recorded_predictions():
    event = {"id": 1, "model_version_id": "mv_1", "prediction": {"predictions": [1]}}
    client = _client(FakeOutcomeStore(event=event), FakeSink(), FakeTransport())

    response = client.post(
        "/predictions/pred_abc/outcomes", json={"outcomes": [{"index": 5, "actual": 1}]}
    )

    assert response.status_code == 422


def test_a_valid_outcome_is_saved_and_triggers_the_quality_check():
    event = {"id": 1, "model_version_id": "mv_1", "prediction": {"predictions": [1]}}
    store = FakeOutcomeStore(event=event)
    app.dependency_overrides[get_metadata_store] = lambda: store
    app.dependency_overrides[get_quality_check] = lambda: (
        lambda model_version_id: store.checked.append(model_version_id)
    )
    try:
        response = TestClient(app).post(
            "/predictions/pred_abc/outcomes", json={"outcomes": [{"index": 0, "actual": 1}]}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert store.saved_labels[0]["telemetry_event_id"] == 1
    assert store.checked == ["mv_1"]
