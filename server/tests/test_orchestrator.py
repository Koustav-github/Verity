from orchestrator import build_artifact


def test_build_artifact_stores_bytes_and_metadata_then_returns_a_record():
    stored_blobs = {}
    stored_metadata = {}

    class FakeBlobStore:
        def put(self, sha256: str, payload: bytes) -> str:
            stored_blobs[sha256] = payload
            return f"seaweedfs://artifacts/{sha256}"

    class FakeMetadataStore:
        def save_model_version(self, *, sha256, artifact_uri, user_id, args, status):
            stored_metadata[sha256] = {
                "artifact_uri": artifact_uri,
                "user_id": user_id,
                "args": args,
                "status": status,
            }
            return "mv_123"

    result = build_artifact(
        payload=b"fake-artifact-bytes",
        sha256="abc123",
        user_id="u_1",
        args={"framework_hint": "sklearn"},
        blob_store=FakeBlobStore(),
        metadata_store=FakeMetadataStore(),
    )

    assert stored_blobs["abc123"] == b"fake-artifact-bytes"
    assert stored_metadata["abc123"]["artifact_uri"] == "seaweedfs://artifacts/abc123"
    assert stored_metadata["abc123"]["status"] == "pending"
    assert result == {
        "model_version_id": "mv_123",
        "artifact_uri": "seaweedfs://artifacts/abc123",
        "status": "pending",
    }
