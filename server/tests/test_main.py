from fastapi.testclient import TestClient

from main import app, get_build_artifact


def test_ingest_parses_the_upload_and_returns_build_artifact_result():
    captured = {}

    def fake_build_artifact(payload, sha256, user_id, args):
        captured["call"] = (payload, sha256, user_id, args)
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
