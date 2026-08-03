import cloudpickle

from orchestrator import build_artifact


class FakeBlobStore:
    def __init__(self):
        self.blobs = {}

    def put(self, sha256: str, payload: bytes) -> str:
        self.blobs[sha256] = payload
        return f"s3://artifacts/{sha256}"


class FakeMetadataStore:
    def __init__(self):
        self.model_versions = {}
        self.manifests = {}
        self.eval_runs = {}
        self.status_updates = []

    def save_model_version(self, *, sha256, artifact_uri, user_id, args, status):
        self.model_versions[sha256] = {
            "artifact_uri": artifact_uri,
            "user_id": user_id,
            "args": args,
            "status": status,
        }
        return "mv_123"

    def save_manifest(self, *, model_version_id, manifest):
        self.manifests[model_version_id] = manifest
        return "mf_456"

    def save_eval_run(self, *, model_version_id, eval_run):
        self.eval_runs[model_version_id] = eval_run
        return "evr_789"

    def update_model_version_status(self, *, model_version_id, status):
        self.status_updates.append((model_version_id, status))


def fake_identify(model):
    return {"framework": "sklearn", "model_class": "FakeModel"}


def passing_eval(**kwargs):
    return {"verdict": "pass", "scores": {"accuracy": 1.0}, "seen": sorted(kwargs)}


def test_build_artifact_stores_bytes_metadata_and_manifest_then_returns_a_record():
    stored_blobs = {}
    stored_metadata = {}
    stored_manifests = {}
    identified_models = []

    class FakeBlobStore:
        def put(self, sha256: str, payload: bytes) -> str:
            stored_blobs[sha256] = payload
            return f"s3://artifacts/{sha256}"

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
    assert stored_metadata["abc123"]["artifact_uri"] == "s3://artifacts/abc123"
    assert stored_metadata["abc123"]["status"] == "pending"
    assert identified_models == [{"kind": "fake-model"}]
    assert stored_manifests["mv_123"] == {"framework": "sklearn", "model_class": "FakeModel"}
    assert result == {
        "model_version_id": "mv_123",
        "artifact_uri": "s3://artifacts/abc123",
        "status": "pending",
        "manifest": {"framework": "sklearn", "model_class": "FakeModel"},
        "eval_run": None,
    }


def test_a_fixture_supplied_at_ingest_is_stored_and_evaluated_in_the_same_pass():
    blob_store = FakeBlobStore()
    metadata_store = FakeMetadataStore()
    fixture_payload = cloudpickle.dumps({"X": [[0.0]], "y": [0]})

    result = build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256="abc123",
        user_id="u_1",
        args={},
        blob_store=blob_store,
        metadata_store=metadata_store,
        identify_fn=fake_identify,
        fixture_payload=fixture_payload,
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=passing_eval,
    )

    assert blob_store.blobs["def456"] == fixture_payload
    assert metadata_store.eval_runs["mv_123"]["verdict"] == "pass"
    assert result["eval_run"]["id"] == "evr_789"
    assert result["eval_run"]["verdict"] == "pass"


def test_the_fixture_descriptor_records_where_the_test_set_actually_landed():
    blob_store = FakeBlobStore()
    seen = {}

    def recording_eval(**kwargs):
        seen.update(kwargs)
        return {"verdict": "pass"}

    build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256="abc123",
        user_id="u_1",
        args={},
        blob_store=blob_store,
        metadata_store=FakeMetadataStore(),
        identify_fn=fake_identify,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=recording_eval,
    )

    assert seen["fixture"]["uri"] == "s3://artifacts/def456"
    assert seen["data"] == {"X": [[0.0]], "y": [0]}


def test_a_passing_verdict_moves_the_version_to_staging():
    metadata_store = FakeMetadataStore()

    result = build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256="abc123",
        user_id="u_1",
        args={},
        blob_store=FakeBlobStore(),
        metadata_store=metadata_store,
        identify_fn=fake_identify,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=passing_eval,
    )

    assert metadata_store.status_updates == [("mv_123", "staging")]
    assert result["status"] == "staging"


def test_a_failing_verdict_holds_the_version_at_staging_failed():
    metadata_store = FakeMetadataStore()

    result = build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256="abc123",
        user_id="u_1",
        args={},
        blob_store=FakeBlobStore(),
        metadata_store=metadata_store,
        identify_fn=fake_identify,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=lambda **_: {"verdict": "fail"},
    )

    assert metadata_store.status_updates == [("mv_123", "staging_failed")]
    assert result["status"] == "staging_failed"


def test_an_eval_that_errored_also_holds_the_version_rather_than_letting_it_through():
    metadata_store = FakeMetadataStore()

    result = build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256="abc123",
        user_id="u_1",
        args={},
        blob_store=FakeBlobStore(),
        metadata_store=metadata_store,
        identify_fn=fake_identify,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=lambda **_: {"verdict": "error", "error": {"message": "boom"}},
    )

    assert metadata_store.status_updates == [("mv_123", "staging_failed")]
    assert result["status"] == "staging_failed"


def test_without_a_fixture_nothing_is_evaluated_and_the_version_stays_pending():
    metadata_store = FakeMetadataStore()

    def must_not_run(**_):
        raise AssertionError("evaluate_fn must not be called without a fixture")

    result = build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256="abc123",
        user_id="u_1",
        args={},
        blob_store=FakeBlobStore(),
        metadata_store=metadata_store,
        identify_fn=fake_identify,
        evaluate_fn=must_not_run,
    )

    assert result["eval_run"] is None
    assert result["status"] == "pending"
    assert metadata_store.status_updates == []
    assert metadata_store.eval_runs == {}
