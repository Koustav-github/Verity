from fastapi.testclient import TestClient

from main import app, get_build_artifact


def test_ingest_parses_the_upload_and_returns_build_artifact_result():
    captured = {}

    def fake_build_artifact(payload, sha256, user_id, args, **kwargs):
        captured["call"] = (payload, sha256, user_id, args)
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
            "args": '{"framework_hint": "sklearn"}',
        },
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"model_version_id": "mv_123", "status": "pending"}
    assert captured["call"] == (b"fake-bytes", "abc123", "u_1", {"framework_hint": "sklearn"})


def test_ingest_without_a_fixture_asks_for_no_evaluation():
    captured = {}

    def fake_build_artifact(payload, sha256, user_id, args, **kwargs):
        captured.update(kwargs)
        return {"model_version_id": "mv_123", "status": "pending"}

    app.dependency_overrides[get_build_artifact] = lambda: fake_build_artifact
    client = TestClient(app)

    client.post(
        "/ingest",
        files={"artifact": ("artifact", b"fake-bytes", "application/octet-stream")},
        data={"user_id": "u_1", "sha256": "abc123"},
    )

    app.dependency_overrides.clear()

    assert captured["fixture_payload"] is None
    assert captured["fixture_descriptor"] is None


def test_ingest_forwards_an_uploaded_fixture_and_its_descriptor():
    captured = {}

    def fake_build_artifact(payload, sha256, user_id, args, **kwargs):
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
