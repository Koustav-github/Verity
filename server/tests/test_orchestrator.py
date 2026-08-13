import hashlib

import cloudpickle
import pytest

from orchestrator import build_artifact

# All the fixture-model tests below share this exact payload, so its real digest is a
# fixed constant. build_artifact now verifies the claimed sha256 against the payload's
# actual bytes, so tests need a genuinely matching pair rather than an arbitrary label.
FAKE_MODEL_PAYLOAD = cloudpickle.dumps({"kind": "fake-model"})
FAKE_MODEL_SHA256 = hashlib.sha256(FAKE_MODEL_PAYLOAD).hexdigest()


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


def no_existing(**kwargs):
    return None


def fake_register(**kwargs):
    verdict = kwargs["verdict"]
    if verdict == "pass":
        status = "production"
    elif verdict is not None:
        status = "staging_failed"
    else:
        status = "pending"
    return {"model_id": "mdl_123", "status": status, "archived_model_version_id": None}


def test_build_artifact_stores_bytes_metadata_and_manifest_then_returns_a_record():
    blob_store = FakeBlobStore()
    metadata_store = FakeMetadataStore()
    identified_models = []

    def recording_identify(model):
        identified_models.append(model)
        return {"framework": "sklearn", "model_class": "FakeModel"}

    payload = cloudpickle.dumps({"kind": "fake-model"})

    result = build_artifact(
        payload=payload,
        sha256=FAKE_MODEL_SHA256,
        user_id="u_1",
        name="fake-model",
        args={"framework_hint": "sklearn"},
        blob_store=blob_store,
        metadata_store=metadata_store,
        identify_fn=recording_identify,
        find_existing_fn=no_existing,
        register_fn=fake_register,
    )

    assert blob_store.blobs[FAKE_MODEL_SHA256] == payload
    assert metadata_store.model_versions[FAKE_MODEL_SHA256]["artifact_uri"] == f"s3://artifacts/{FAKE_MODEL_SHA256}"
    assert metadata_store.model_versions[FAKE_MODEL_SHA256]["status"] == "pending"
    assert identified_models == [{"kind": "fake-model"}]
    assert metadata_store.manifests["mv_123"] == {"framework": "sklearn", "model_class": "FakeModel"}
    assert result == {
        "model_version_id": "mv_123",
        "artifact_uri": f"s3://artifacts/{FAKE_MODEL_SHA256}",
        "status": "pending",
        "manifest": {"framework": "sklearn", "model_class": "FakeModel"},
        "eval_run": None,
        "model_id": "mdl_123",
        "deduplicated": False,
        "archived_model_version_id": None,
    }


def test_a_fixture_supplied_at_ingest_is_stored_and_evaluated_in_the_same_pass():
    blob_store = FakeBlobStore()
    metadata_store = FakeMetadataStore()
    fixture_payload = cloudpickle.dumps({"X": [[0.0]], "y": [0]})

    result = build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256=FAKE_MODEL_SHA256,
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=blob_store,
        metadata_store=metadata_store,
        identify_fn=fake_identify,
        find_existing_fn=no_existing,
        register_fn=fake_register,
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
        sha256=FAKE_MODEL_SHA256,
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=blob_store,
        metadata_store=FakeMetadataStore(),
        identify_fn=fake_identify,
        find_existing_fn=no_existing,
        register_fn=fake_register,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=recording_eval,
    )

    assert seen["fixture"]["uri"] == "s3://artifacts/def456"
    assert seen["data"] == {"X": [[0.0]], "y": [0]}


def test_without_a_fixture_nothing_is_evaluated_and_the_version_stays_pending():
    metadata_store = FakeMetadataStore()

    def must_not_run(**_):
        raise AssertionError("evaluate_fn must not be called without a fixture")

    result = build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256=FAKE_MODEL_SHA256,
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=FakeBlobStore(),
        metadata_store=metadata_store,
        identify_fn=fake_identify,
        find_existing_fn=no_existing,
        register_fn=fake_register,
        evaluate_fn=must_not_run,
    )

    assert result["eval_run"] is None
    assert result["status"] == "pending"
    assert metadata_store.eval_runs == {}


def test_an_exact_repeat_of_name_and_hash_short_circuits_before_anything_else_runs():
    existing_record = {
        "id": "mv_existing",
        "artifact_uri": f"s3://artifacts/{FAKE_MODEL_SHA256}",
        "status": "production",
        "model_id": "mdl_existing",
    }

    def found_existing(**kwargs):
        return existing_record

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("nothing downstream should run on a dedup hit")

    result = build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256=FAKE_MODEL_SHA256,
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=type("ExplodingBlobStore", (), {"put": must_not_run})(),
        metadata_store=type("ExplodingMetadataStore", (), {})(),
        identify_fn=must_not_run,
        find_existing_fn=found_existing,
        register_fn=must_not_run,
    )

    assert result == {
        "model_version_id": "mv_existing",
        "artifact_uri": f"s3://artifacts/{FAKE_MODEL_SHA256}",
        "status": "production",
        "manifest": None,
        "eval_run": None,
        "deduplicated": True,
        "model_id": "mdl_existing",
    }


def test_build_artifact_rejects_a_payload_whose_actual_digest_does_not_match_the_claimed_sha256():
    payload = cloudpickle.dumps({"kind": "fake-model"})

    with pytest.raises(ValueError):
        build_artifact(
            payload=payload,
            sha256="not-the-real-digest-of-this-payload",
            user_id="u_1",
            name="fake-model",
            args={},
            blob_store=FakeBlobStore(),
            metadata_store=FakeMetadataStore(),
            identify_fn=fake_identify,
            find_existing_fn=no_existing,
            register_fn=fake_register,
        )


def test_an_upload_with_a_fixture_is_not_deduped_even_if_an_identical_no_fixture_upload_exists():
    existing_record = {"id": "mv_existing", "artifact_uri": f"s3://artifacts/{FAKE_MODEL_SHA256}", "status": "pending"}

    def found_existing(**kwargs):
        return existing_record

    blob_store = FakeBlobStore()
    metadata_store = FakeMetadataStore()
    identify_calls = []

    def recording_identify(model):
        identify_calls.append(model)
        return {"framework": "sklearn", "model_class": "FakeModel"}

    result = build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256=FAKE_MODEL_SHA256,
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=blob_store,
        metadata_store=metadata_store,
        identify_fn=recording_identify,
        find_existing_fn=found_existing,
        register_fn=fake_register,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=passing_eval,
    )

    assert identify_calls == [{"kind": "fake-model"}]
    assert metadata_store.eval_runs["mv_123"]["verdict"] == "pass"
    assert result.get("deduplicated") is not True
    assert result["status"] == "production"


def test_the_dedup_check_is_given_the_users_id_hash_and_name():
    seen = {}

    def recording_find_existing(**kwargs):
        seen.update(kwargs)
        return None

    build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256=FAKE_MODEL_SHA256,
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=FakeBlobStore(),
        metadata_store=FakeMetadataStore(),
        identify_fn=fake_identify,
        find_existing_fn=recording_find_existing,
        register_fn=fake_register,
    )

    assert seen["user_id"] == "u_1"
    assert seen["sha256"] == FAKE_MODEL_SHA256
    assert seen["name"] == "fake-model"


def test_a_passing_verdict_promotes_the_version_to_production():
    metadata_store = FakeMetadataStore()

    result = build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256=FAKE_MODEL_SHA256,
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=FakeBlobStore(),
        metadata_store=metadata_store,
        identify_fn=fake_identify,
        find_existing_fn=no_existing,
        register_fn=fake_register,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=passing_eval,
    )

    assert result["status"] == "production"


def test_a_failing_verdict_holds_the_version_at_staging_failed():
    metadata_store = FakeMetadataStore()

    result = build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256=FAKE_MODEL_SHA256,
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=FakeBlobStore(),
        metadata_store=metadata_store,
        identify_fn=fake_identify,
        find_existing_fn=no_existing,
        register_fn=fake_register,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=lambda **_: {"verdict": "fail"},
    )

    assert result["status"] == "staging_failed"


def test_an_eval_that_errored_also_holds_the_version_at_staging_failed():
    metadata_store = FakeMetadataStore()

    result = build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256=FAKE_MODEL_SHA256,
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=FakeBlobStore(),
        metadata_store=metadata_store,
        identify_fn=fake_identify,
        find_existing_fn=no_existing,
        register_fn=fake_register,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=lambda **_: {"verdict": "error", "error": {"message": "boom"}},
    )

    assert result["status"] == "staging_failed"


def test_register_is_called_even_when_no_fixture_was_supplied():
    seen = {}

    def recording_register(**kwargs):
        seen.update(kwargs)
        return {"model_id": "mdl_1", "status": "pending", "archived_model_version_id": None}

    build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256=FAKE_MODEL_SHA256,
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=FakeBlobStore(),
        metadata_store=FakeMetadataStore(),
        identify_fn=fake_identify,
        find_existing_fn=no_existing,
        register_fn=recording_register,
    )

    assert seen["verdict"] is None
    assert seen["eval_run_id"] is None
    assert seen["name"] == "fake-model"


def test_register_receives_the_eval_run_id_and_verdict_when_an_eval_ran():
    seen = {}

    def recording_register(**kwargs):
        seen.update(kwargs)
        return {"model_id": "mdl_1", "status": "production", "archived_model_version_id": None}

    build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256=FAKE_MODEL_SHA256,
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=FakeBlobStore(),
        metadata_store=FakeMetadataStore(),
        identify_fn=fake_identify,
        find_existing_fn=no_existing,
        register_fn=recording_register,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=passing_eval,
    )

    assert seen["verdict"] == "pass"
    assert seen["eval_run_id"] == "evr_789"


def test_the_final_response_surfaces_which_version_was_archived_by_a_promotion():
    def register_that_displaces_an_incumbent(**kwargs):
        return {"model_id": "mdl_1", "status": "production", "archived_model_version_id": "mv_old"}

    result = build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256=FAKE_MODEL_SHA256,
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=FakeBlobStore(),
        metadata_store=FakeMetadataStore(),
        identify_fn=fake_identify,
        find_existing_fn=no_existing,
        register_fn=register_that_displaces_an_incumbent,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=passing_eval,
    )

    assert result["archived_model_version_id"] == "mv_old"
