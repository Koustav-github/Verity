from fastapi.testclient import TestClient

from main import app, get_build_artifact, get_metadata_store


def test_ingest_allows_cross_origin_requests_from_the_local_frontend():
    app.dependency_overrides[get_build_artifact] = lambda: (lambda **_: {})
    client = TestClient(app)

    response = client.options(
        "/ingest",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )

    app.dependency_overrides.clear()

    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_ingest_parses_the_upload_and_returns_build_artifact_result():
    captured = {}

    def fake_build_artifact(payload, sha256, user_id, name, args, **kwargs):
        captured["call"] = (payload, sha256, user_id, name, args)
        captured["kwargs"] = kwargs
        return {"model_version_id": "mv_123", "status": "pending"}

    app.dependency_overrides[get_build_artifact] = lambda: fake_build_artifact
    client = TestClient(app)

    response = client.post(
        "/ingest",
        files={"artifact": ("artifact", b"fake-bytes", "application/octet-stream")},
        data={
            "user_id": "u_1",
            "sha256": "abc123",
            "name": "fraud-classifier",
            "args": '{"framework_hint": "sklearn"}',
        },
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"model_version_id": "mv_123", "status": "pending"}
    assert captured["call"] == (
        b"fake-bytes", "abc123", "u_1", "fraud-classifier", {"framework_hint": "sklearn"}
    )


def test_ingest_requires_a_name():
    app.dependency_overrides[get_build_artifact] = lambda: (lambda **_: {})
    client = TestClient(app)

    response = client.post(
        "/ingest",
        files={"artifact": ("artifact", b"fake-bytes", "application/octet-stream")},
        data={"user_id": "u_1", "sha256": "abc123"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 422


def test_ingest_without_a_fixture_asks_for_no_evaluation():
    captured = {}

    def fake_build_artifact(payload, sha256, user_id, name, args, **kwargs):
        captured.update(kwargs)
        return {"model_version_id": "mv_123", "status": "pending"}

    app.dependency_overrides[get_build_artifact] = lambda: fake_build_artifact
    client = TestClient(app)

    client.post(
        "/ingest",
        files={"artifact": ("artifact", b"fake-bytes", "application/octet-stream")},
        data={"user_id": "u_1", "sha256": "abc123", "name": "fraud-classifier"},
    )

    app.dependency_overrides.clear()

    assert captured["fixture_payload"] is None
    assert captured["fixture_descriptor"] is None


def test_ingest_forwards_an_uploaded_fixture_and_its_descriptor():
    captured = {}

    def fake_build_artifact(payload, sha256, user_id, name, args, **kwargs):
        captured.update(kwargs)
        return {"model_version_id": "mv_123", "status": "staging"}

    app.dependency_overrides[get_build_artifact] = lambda: fake_build_artifact
    client = TestClient(app)

    response = client.post(
        "/ingest",
        files={
            "artifact": ("artifact", b"fake-bytes", "application/octet-stream"),
            "fixture": ("fixture", b"fixture-bytes", "application/octet-stream"),
        },
        data={
            "user_id": "u_1",
            "sha256": "abc123",
            "name": "fraud-classifier",
            "fixture_descriptor": '{"kind": "labeled_holdout", "sha256": "def456"}',
        },
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert captured["fixture_payload"] == b"fixture-bytes"
    assert captured["fixture_descriptor"] == {
        "kind": "labeled_holdout",
        "sha256": "def456",
    }


def test_telemetry_ingestion_stores_the_whole_batch():
    captured = {}

    class FakeStore:
        def save_telemetry_events(self, *, events):
            captured["events"] = events
            return len(events)

    app.dependency_overrides[get_metadata_store] = lambda: FakeStore()
    client = TestClient(app)

    response = client.post(
        "/telemetry",
        json={
            "events": [
                {"model_version_id": "mv_1", "occurred_at": "2026-08-16T10:00:00+00:00",
                 "latency_ms": 1.5, "status": "ok"},
                {"model_version_id": "mv_1", "occurred_at": "2026-08-16T10:00:01+00:00",
                 "latency_ms": 9.0, "status": "error", "error_type": "ValueError"},
            ]
        },
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"written": 2}
    assert captured["events"][1]["error_type"] == "ValueError"


def test_telemetry_ingestion_rejects_an_event_missing_a_required_field():
    app.dependency_overrides[get_metadata_store] = lambda: None
    client = TestClient(app)

    response = client.post("/telemetry", json={"events": [{"latency_ms": 1.0}]})

    app.dependency_overrides.clear()

    assert response.status_code == 422


def test_telemetry_ingestion_accepts_an_empty_batch():
    class FakeStore:
        def save_telemetry_events(self, *, events):
            return 0

    app.dependency_overrides[get_metadata_store] = lambda: FakeStore()
    client = TestClient(app)

    response = client.post("/telemetry", json={"events": []})

    app.dependency_overrides.clear()

    assert response.json() == {"written": 0}


def test_telemetry_ingestion_rejects_an_event_with_an_invalid_status():
    app.dependency_overrides[get_metadata_store] = lambda: None
    client = TestClient(app)

    response = client.post(
        "/telemetry",
        json={
            "events": [
                {"model_version_id": "mv_1", "occurred_at": "2026-08-16T10:00:00+00:00",
                 "status": "succeeded"},
            ]
        },
    )

    app.dependency_overrides.clear()

    assert response.status_code == 422


def test_telemetry_ingestion_rejects_an_event_with_a_malformed_timestamp():
    app.dependency_overrides[get_metadata_store] = lambda: None
    client = TestClient(app)

    response = client.post(
        "/telemetry",
        json={
            "events": [
                {"model_version_id": "mv_1", "occurred_at": "not-a-timestamp",
                 "status": "ok"},
            ]
        },
    )

    app.dependency_overrides.clear()

    assert response.status_code == 422


def test_telemetry_ingestion_rejects_a_batch_over_the_size_limit():
    app.dependency_overrides[get_metadata_store] = lambda: None
    client = TestClient(app)

    events = [
        {"model_version_id": "mv_1", "occurred_at": "2026-08-16T10:00:00+00:00", "status": "ok"}
        for _ in range(1001)
    ]
    response = client.post("/telemetry", json={"events": events})

    app.dependency_overrides.clear()

    assert response.status_code == 422


def test_reading_telemetry_summarises_the_window_alongside_the_eval_reference():
    captured = {}

    class FakeStore:
        def find_telemetry_events(self, *, model_version_id, since, limit):
            captured["model_version_id"] = model_version_id
            captured["since"] = since
            captured["limit"] = limit
            return [
                {"latency_ms": 10.0, "status": "ok"},
                {"latency_ms": 20.0, "status": "error"},
            ]

        def find_monitoring_config(self, *, model_version_id):
            return {"eval_reference": {"basis": "sandbox_feasibility", "latency_p95_ms": 0.2}}

    app.dependency_overrides[get_metadata_store] = lambda: FakeStore()
    client = TestClient(app)

    response = client.get("/models/mv_1/telemetry")

    app.dependency_overrides.clear()

    body = response.json()
    assert response.status_code == 200
    assert body["model_version_id"] == "mv_1"
    assert body["request_count"] == 2
    assert body["error_rate"] == 0.5
    assert body["eval_reference"]["basis"] == "sandbox_feasibility"
    assert captured["model_version_id"] == "mv_1"


def test_reading_telemetry_defaults_to_a_24_hour_window_and_accepts_an_override():
    seen = {}

    class FakeStore:
        def find_telemetry_events(self, *, model_version_id, since, limit):
            seen["since"] = since
            return []

        def find_monitoring_config(self, *, model_version_id):
            return None

    app.dependency_overrides[get_metadata_store] = lambda: FakeStore()
    client = TestClient(app)

    default_response = client.get("/models/mv_1/telemetry")
    assert default_response.json()["hours"] == 24.0

    override_response = client.get("/models/mv_1/telemetry?hours=1")
    assert override_response.json()["hours"] == 1.0

    app.dependency_overrides.clear()


def test_reading_telemetry_for_an_unmonitored_version_still_returns_a_summary():
    class FakeStore:
        def find_telemetry_events(self, *, model_version_id, since, limit):
            return []

        def find_monitoring_config(self, *, model_version_id):
            return None

    app.dependency_overrides[get_metadata_store] = lambda: FakeStore()
    client = TestClient(app)

    response = client.get("/models/mv_unknown/telemetry")

    app.dependency_overrides.clear()

    body = response.json()
    assert body["request_count"] == 0
    assert body["eval_reference"] is None
