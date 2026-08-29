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
        self.monitoring_configs = {}

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

    def save_monitoring_config(self, *, model_version_id, eval_run_id, config):
        self.monitoring_configs[model_version_id] = config
        return "mcfg_fake"


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
    stored = metadata_store.manifests["mv_123"]
    assert stored["framework"] == "sklearn"
    assert stored["model_class"] == "FakeModel"
    # api-fication adds two keys identification never produced: the measured input
    # surface and the client-captured training environment.
    assert "io_schema" in stored
    assert stored["environment"] is None
    assert result == {
        "model_version_id": "mv_123",
        "artifact_uri": f"s3://artifacts/{FAKE_MODEL_SHA256}",
        "status": "pending",
        "manifest": stored,
        "eval_run": None,
        "model_id": "mdl_123",
        "deduplicated": False,
        "archived_model_version_id": None,
        "monitoring_config": None,
        "deployment": None,
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
        "monitoring_config": None,
        "deployment": None,
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


def fake_configure(**kwargs):
    return {"id": "mcfg_1", "metrics": ["error_rate"], "eval_reference": {"basis": "sandbox_feasibility"}}


def test_a_promoted_version_gets_monitoring_configured():
    seen = {}

    def recording_configure(**kwargs):
        seen.update(kwargs)
        return fake_configure()

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
        register_fn=fake_register,
        configure_fn=recording_configure,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=passing_eval,
    )

    assert result["monitoring_config"]["id"] == "mcfg_1"
    assert seen["model_version_id"] == "mv_123"
    assert seen["eval_run_id"] == "evr_789"


def test_a_version_that_was_not_promoted_gets_no_monitoring_config():
    def must_not_run(**_):
        raise AssertionError("configure_fn must not run for a non-production version")

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
        register_fn=fake_register,
        configure_fn=must_not_run,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=lambda **_: {"verdict": "fail"},
    )

    assert result["monitoring_config"] is None
    assert result["status"] == "staging_failed"


def test_a_falcon_failure_does_not_lose_a_promotion_that_already_succeeded():
    def exploding_configure(**_):
        raise RuntimeError("supabase is down")

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
        register_fn=fake_register,
        configure_fn=exploding_configure,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=passing_eval,
    )

    assert result["status"] == "production"
    assert result["monitoring_config"] is None


def test_an_upload_with_no_fixture_is_never_monitored():
    def must_not_run(**_):
        raise AssertionError("configure_fn must not run without an eval")

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
        register_fn=fake_register,
        configure_fn=must_not_run,
    )

    assert result["monitoring_config"] is None
    assert result["status"] == "pending"


# --- api-fication: introspection at ingest, deploy on promotion --------------------

def _build(**overrides):
    """Run build_artifact with every collaborator faked, overriding as needed."""
    payload = overrides.pop("payload", cloudpickle.dumps({"kind": "fake-model"}))
    kwargs = {
        "payload": payload,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "user_id": "u_1",
        "name": "fake-model",
        "args": {},
        "blob_store": FakeBlobStore(),
        "metadata_store": FakeMetadataStore(),
        "identify_fn": fake_identify,
        "introspect_fn": lambda p: {"n_features": 2, "feature_names": None,
                                    "classes": [0, 1], "has_predict_proba": True},
        "find_existing_fn": no_existing,
        "register_fn": fake_register,
        "deploy_fn": lambda **kw: {"id": "dep_1", "status": "live"},
    }
    kwargs.update(overrides)
    return kwargs["metadata_store"], build_artifact(**kwargs)


def test_the_manifest_carries_the_introspected_io_schema():
    store, _ = _build()

    assert store.manifests["mv_123"]["io_schema"]["n_features"] == 2


def test_the_manifest_carries_the_environment_the_client_captured():
    store, _ = _build(environment={"python_version": "3.12", "packages": {"numpy": "2.3.5"}})

    assert store.manifests["mv_123"]["environment"]["python_version"] == "3.12"


def test_a_failing_introspection_does_not_stop_the_pipeline():
    def boom(payload):
        raise RuntimeError("unreadable artifact")

    _, result = _build(introspect_fn=boom)

    # Identification and evaluation are still worth having without a serving schema.
    # The version simply cannot be deployed, which the null io_schema records exactly.
    assert result["manifest"]["io_schema"] is None
    assert result["status"] == "pending"


def test_deploy_fires_when_a_version_reaches_production():
    calls = []

    def recording_deploy(**kwargs):
        calls.append(kwargs)
        return {"id": "dep_1", "status": "live"}

    def register_with_archive(**kwargs):
        return {"model_id": "mdl_123", "status": "production",
                "archived_model_version_id": "mv_old"}

    _, result = _build(
        register_fn=register_with_archive,
        evaluate_fn=passing_eval,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "f" * 64},
        configure_fn=lambda **kw: {"id": "mcfg_1"},
        deploy_fn=recording_deploy,
    )

    assert result["deployment"] == {"id": "dep_1", "status": "live"}
    assert calls[0]["archived_model_version_id"] == "mv_old"
    assert calls[0]["io_schema"]["n_features"] == 2


def test_deploy_receives_the_manifests_framework_so_it_can_scope_requirements():
    # deploy() uses this to decide whether xgboost/lightgbm belong in this model's
    # image at all -- see server/serving/build.py's _FRAMEWORK_ONLY_PACKAGES.
    calls = []

    def register_with_archive(**kwargs):
        return {"model_id": "mdl_123", "status": "production",
                "archived_model_version_id": "mv_old"}

    _build(
        register_fn=register_with_archive,
        evaluate_fn=passing_eval,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "f" * 64},
        configure_fn=lambda **kw: {"id": "mcfg_1"},
        deploy_fn=lambda **kw: calls.append(kw) or {"id": "dep_1", "status": "live"},
    )

    assert calls[0]["framework"] == "sklearn"


def test_deploy_does_not_fire_for_a_version_that_was_not_promoted():
    calls = []

    _, result = _build(deploy_fn=lambda **kw: calls.append(kw))

    assert calls == []
    assert result["deployment"] is None


def test_deploy_is_skipped_when_the_input_surface_could_not_be_read():
    calls = []

    _, result = _build(
        introspect_fn=lambda p: None,
        register_fn=lambda **kw: {"model_id": "mdl_123", "status": "production",
                                  "archived_model_version_id": None},
        configure_fn=lambda **kw: {"id": "mcfg_1"},
        deploy_fn=lambda **kw: calls.append(kw),
    )

    # Serving a model whose input contract is unknown would mean guessing it, and a
    # wrong guess returns confident nonsense rather than an error.
    assert calls == []
    assert result["deployment"] is None
    assert result["status"] == "production"


def test_a_failing_deploy_does_not_fail_a_promotion_that_succeeded():
    def boom(**kwargs):
        raise RuntimeError("docker daemon unreachable")

    _, result = _build(
        register_fn=lambda **kw: {"model_id": "mdl_123", "status": "production",
                                  "archived_model_version_id": None},
        configure_fn=lambda **kw: {"id": "mcfg_1"},
        deploy_fn=boom,
    )

    # The version genuinely IS production. Reporting the request as failed would be a
    # lie about what happened — the same reasoning as _configure_monitoring.
    assert result["status"] == "production"
    assert result["deployment"] is None


def test_alert_email_is_forwarded_to_register_fn():
    captured = {}

    def recording_register(**kwargs):
        captured.update(kwargs)
        return {"model_id": "mdl_123", "status": "pending", "archived_model_version_id": None}

    _build(register_fn=recording_register, alert_email="ops@example.com")

    assert captured["alert_email"] == "ops@example.com"
