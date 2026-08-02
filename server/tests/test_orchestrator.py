import cloudpickle

from orchestrator import build_artifact


def test_build_artifact_stores_bytes_metadata_and_manifest_then_returns_a_record():
    stored_blobs = {}
    stored_metadata = {}
    stored_manifests = {}
    identified_models = []

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

        def save_manifest(self, *, model_version_id, manifest):
            stored_manifests[model_version_id] = manifest
            return "mf_456"

    def fake_identify(model):
        identified_models.append(model)
        return {"framework": "sklearn", "model_class": "FakeModel"}

    payload = cloudpickle.dumps({"kind": "fake-model"})

    result = build_artifact(
        payload=payload,
        sha256="abc123",
        user_id="u_1",
        args={"framework_hint": "sklearn"},
        blob_store=FakeBlobStore(),
        metadata_store=FakeMetadataStore(),
        identify_fn=fake_identify,
    )

    assert stored_blobs["abc123"] == payload
    assert stored_metadata["abc123"]["artifact_uri"] == "seaweedfs://artifacts/abc123"
    assert stored_metadata["abc123"]["status"] == "pending"
    assert identified_models == [{"kind": "fake-model"}]
    assert stored_manifests["mv_123"] == {"framework": "sklearn", "model_class": "FakeModel"}
    assert result == {
        "model_version_id": "mv_123",
        "artifact_uri": "seaweedfs://artifacts/abc123",
        "status": "pending",
        "manifest": {"framework": "sklearn", "model_class": "FakeModel"},
    }
