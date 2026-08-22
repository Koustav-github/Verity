import pytest
from fastapi.testclient import TestClient

from main import app, get_metadata_store, get_predict_transport, get_telemetry_sink
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
