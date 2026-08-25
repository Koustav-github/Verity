from fastapi.testclient import TestClient

from main import TELEMETRY_TRACE_LIMIT, app, get_blob_store, get_build_artifact, get_metadata_store


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


def test_reading_telemetry_includes_recent_events_newest_first():
    class FakeStore:
        def find_telemetry_events(self, *, model_version_id, since, limit):
            # find_telemetry_events already orders newest-first (Supabase query,
            # .order("occurred_at", desc=True)) — the fake mirrors that ordering
            # rather than re-sorting, since the route must not assume otherwise.
            return [
                {
                    "occurred_at": "2026-08-25T10:02:00+00:00",
                    "latency_ms": 12.5,
                    "status": "ok",
                    "error_type": None,
                    "prediction_id": "pred_2",
                },
                {
                    "occurred_at": "2026-08-25T10:01:00+00:00",
                    "latency_ms": 40.0,
                    "status": "error",
                    "error_type": "ValueError",
                    "prediction_id": "pred_1",
                },
            ]

        def find_monitoring_config(self, *, model_version_id):
            return None

    app.dependency_overrides[get_metadata_store] = lambda: FakeStore()
    client = TestClient(app)

    response = client.get("/models/mv_1/telemetry")

    app.dependency_overrides.clear()

    body = response.json()
    assert response.status_code == 200
    assert body["recent_events"] == [
        {
            "occurred_at": "2026-08-25T10:02:00+00:00",
            "latency_ms": 12.5,
            "status": "ok",
            "error_type": None,
            "prediction_id": "pred_2",
        },
        {
            "occurred_at": "2026-08-25T10:01:00+00:00",
            "latency_ms": 40.0,
            "status": "error",
            "error_type": "ValueError",
            "prediction_id": "pred_1",
        },
    ]


def test_reading_telemetry_caps_recent_events_at_the_trace_display_limit():
    events = [
        {
            "occurred_at": f"2026-08-25T10:{i:02d}:00+00:00",
            "latency_ms": 1.0,
            "status": "ok",
            "error_type": None,
            "prediction_id": f"pred_{i}",
        }
        for i in range(80)
    ]

    class FakeStore:
        def find_telemetry_events(self, *, model_version_id, since, limit):
            return events

        def find_monitoring_config(self, *, model_version_id):
            return None

    app.dependency_overrides[get_metadata_store] = lambda: FakeStore()
    client = TestClient(app)

    response = client.get("/models/mv_1/telemetry")

    app.dependency_overrides.clear()

    body = response.json()
    assert len(body["recent_events"]) == TELEMETRY_TRACE_LIMIT
    assert body["recent_events"][0]["prediction_id"] == "pred_0"


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


def test_ingest_forwards_the_captured_environment_to_the_orchestrator():
    import json

    captured = {}

    def fake_build_artifact(**kwargs):
        captured.update(kwargs)
        return {"model_version_id": "mv_1"}

    app.dependency_overrides[get_build_artifact] = lambda: fake_build_artifact
    try:
        TestClient(app).post(
            "/ingest",
            files={"artifact": ("artifact", b"bytes")},
            data={
                "user_id": "u_1",
                "name": "m",
                "sha256": "abc",
                "environment": json.dumps(
                    {"python_version": "3.12", "packages": {"numpy": "2.3.5"}}
                ),
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert captured["environment"] == {
        "python_version": "3.12",
        "packages": {"numpy": "2.3.5"},
    }


def test_ingest_accepts_an_upload_from_an_sdk_that_captures_no_environment():
    captured = {}

    def fake_build_artifact(**kwargs):
        captured.update(kwargs)
        return {"model_version_id": "mv_1"}

    app.dependency_overrides[get_build_artifact] = lambda: fake_build_artifact
    try:
        TestClient(app).post(
            "/ingest",
            files={"artifact": ("artifact", b"bytes")},
            data={"user_id": "u_1", "name": "m", "sha256": "abc"},
        )
    finally:
        app.dependency_overrides.clear()

    # Older SDKs predate environment capture; the image then falls back to a default
    # Python and an empty pin set, which is worse but must not be fatal.
    assert captured["environment"] is None


def test_ingest_telemetry_calls_check_systemic_for_each_distinct_model_version():
    import main

    calls = []

    class NoopStore:
        def save_telemetry_events(self, *, events):
            return len(events)

    app.dependency_overrides[main.get_metadata_store] = lambda: NoopStore()
    app.dependency_overrides[main.get_systemic_check] = lambda: (lambda mv: calls.append(mv))
    try:
        TestClient(app).post(
            "/telemetry",
            json={
                "events": [
                    {"model_version_id": "mv_1", "occurred_at": "2026-08-23T00:00:00Z", "status": "ok"},
                    {"model_version_id": "mv_2", "occurred_at": "2026-08-23T00:00:00Z", "status": "ok"},
                ]
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert sorted(calls) == ["mv_1", "mv_2"]


def test_ingest_forwards_the_alert_email_to_the_orchestrator():
    captured = {}

    def fake_build_artifact(**kwargs):
        captured.update(kwargs)
        return {"model_version_id": "mv_1"}

    app.dependency_overrides[get_build_artifact] = lambda: fake_build_artifact
    try:
        TestClient(app).post(
            "/ingest",
            files={"artifact": ("artifact", b"bytes")},
            data={"user_id": "u_1", "name": "m", "sha256": "abc", "alert_email": "ops@example.com"},
        )
    finally:
        app.dependency_overrides.clear()

    assert captured["alert_email"] == "ops@example.com"


def test_get_alerts_returns_the_stored_rows_for_the_version():
    class FakeStore:
        def find_alert_events(self, *, model_version_id):
            return [{"id": "alrt_1", "model_version_id": model_version_id, "kind": "systemic"}]

    app.dependency_overrides[get_metadata_store] = lambda: FakeStore()
    try:
        response = TestClient(app).get("/models/mv_1/alerts")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["model_version_id"] == "mv_1"
    assert body["alerts"][0]["kind"] == "systemic"


def test_listing_models_returns_every_model_for_that_user():
    class FakeStore:
        def find_models_by_user(self, *, user_id):
            return [
                {
                    "id": "mdl_1",
                    "name": "fraud",
                    "model_class": "LogisticRegression",
                    "task_type": "classification",
                    "created_at": "2026-08-01T00:00:00+00:00",
                }
            ]

        def find_production_version(self, *, model_id):
            return {"id": "mv_prod"}

    app.dependency_overrides[get_metadata_store] = lambda: FakeStore()
    client = TestClient(app)

    response = client.get("/users/u_1/models")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "u_1"
    assert body["models"][0]["id"] == "mdl_1"
    assert body["models"][0]["production_version_id"] == "mv_prod"


def test_listing_models_returns_an_empty_list_for_an_unknown_user():
    class FakeStore:
        def find_models_by_user(self, *, user_id):
            return []

    app.dependency_overrides[get_metadata_store] = lambda: FakeStore()
    client = TestClient(app)

    response = client.get("/users/nobody/models")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["models"] == []


def test_listing_versions_returns_every_version_of_a_model():
    class FakeStore:
        def find_model_versions(self, *, model_id):
            return [
                {"id": "mv_2", "status": "production", "created_at": "2026-08-02T00:00:00+00:00"},
                {"id": "mv_1", "status": "archived", "created_at": "2026-08-01T00:00:00+00:00"},
            ]

    app.dependency_overrides[get_metadata_store] = lambda: FakeStore()
    client = TestClient(app)

    response = client.get("/models/mdl_1/versions")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["model_id"] == "mdl_1"
    assert [v["id"] for v in body["versions"]] == ["mv_2", "mv_1"]


def test_reading_version_detail_returns_the_full_bundle():
    class FakeStore:
        def find_model_version(self, *, model_version_id):
            return {
                "id": model_version_id,
                "artifact_uri": "s3://verity-artifacts/abc",
                "status": "production",
                "model_id": "mdl_1",
            }

        def find_manifest(self, *, model_version_id):
            return {"framework": "sklearn"}

        def find_eval_run(self, *, model_version_id):
            return {"id": "evr_1", "verdict": "pass"}

        def find_monitoring_config(self, *, model_version_id):
            return None

        def find_deployment(self, *, model_version_id):
            return None

    app.dependency_overrides[get_metadata_store] = lambda: FakeStore()
    client = TestClient(app)

    response = client.get("/model_versions/mv_1")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["model_version_id"] == "mv_1"
    assert body["manifest"] == {"framework": "sklearn"}
    assert body["eval_run"]["verdict"] == "pass"


def test_reading_version_detail_404s_for_an_unknown_version():
    class FakeStore:
        def find_model_version(self, *, model_version_id):
            return None

    app.dependency_overrides[get_metadata_store] = lambda: FakeStore()
    client = TestClient(app)

    response = client.get("/model_versions/mv_unknown")

    app.dependency_overrides.clear()

    assert response.status_code == 404


def test_reading_download_urls_returns_presigned_links():
    class FakeStore:
        def find_model_version(self, *, model_version_id):
            return {"id": model_version_id, "artifact_sha256": "artifact_hash", "model_id": "mdl_1"}

        def find_eval_run(self, *, model_version_id):
            return {"fixture": {"sha256": "fixture_hash"}}

    class FakeBlobStore:
        def presigned_url(self, sha256, expires_in=900):
            return f"https://fake-bucket.s3.amazonaws.com/{sha256}"

    app.dependency_overrides[get_metadata_store] = lambda: FakeStore()
    app.dependency_overrides[get_blob_store] = lambda: FakeBlobStore()
    client = TestClient(app)

    response = client.get("/model_versions/mv_1/download-urls")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["artifact_url"] == "https://fake-bucket.s3.amazonaws.com/artifact_hash"
    assert body["fixture_url"] == "https://fake-bucket.s3.amazonaws.com/fixture_hash"


def test_reading_download_urls_404s_for_an_unknown_version():
    class FakeStore:
        def find_model_version(self, *, model_version_id):
            return None

    class FakeBlobStore:
        def presigned_url(self, sha256, expires_in=900):
            return "unused"

    app.dependency_overrides[get_metadata_store] = lambda: FakeStore()
    app.dependency_overrides[get_blob_store] = lambda: FakeBlobStore()
    client = TestClient(app)

    response = client.get("/model_versions/mv_unknown/download-urls")

    app.dependency_overrides.clear()

    assert response.status_code == 404
