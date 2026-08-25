import pytest

from registry import get_download_urls, get_version_detail, list_models, list_versions


class FakeMetadataStore:
    def __init__(
        self,
        models=None,
        versions=None,
        model_versions=None,
        manifests=None,
        eval_runs=None,
        deployments=None,
        monitoring_configs=None,
        production_versions=None,
    ):
        self.models = models or {}
        self.versions = versions or {}
        self.model_versions = model_versions or {}
        self.manifests = manifests or {}
        self.eval_runs = eval_runs or {}
        self.deployments = deployments or {}
        self.monitoring_configs = monitoring_configs or {}
        self.production_versions = production_versions or {}

    def find_models_by_user(self, *, user_id):
        return self.models.get(user_id, [])

    def find_production_version(self, *, model_id):
        return self.production_versions.get(model_id)

    def find_model_versions(self, *, model_id):
        return self.versions.get(model_id, [])

    def find_model_version(self, *, model_version_id):
        return self.model_versions.get(model_version_id)

    def find_manifest(self, *, model_version_id):
        return self.manifests.get(model_version_id)

    def find_eval_run(self, *, model_version_id):
        return self.eval_runs.get(model_version_id)

    def find_deployment(self, *, model_version_id):
        return self.deployments.get(model_version_id)

    def find_monitoring_config(self, *, model_version_id):
        return self.monitoring_configs.get(model_version_id)


class FakeBlobStore:
    def __init__(self):
        self.calls = []

    def presigned_url(self, sha256, expires_in=900):
        self.calls.append({"sha256": sha256, "expires_in": expires_in})
        return f"https://fake-bucket.s3.amazonaws.com/{sha256}"


def test_list_models_reports_each_models_current_production_version():
    store = FakeMetadataStore(
        models={
            "u_1": [
                {
                    "id": "mdl_1",
                    "name": "fraud",
                    "model_class": "LogisticRegression",
                    "task_type": "classification",
                    "created_at": "2026-08-01T00:00:00+00:00",
                }
            ]
        },
        production_versions={"mdl_1": {"id": "mv_prod"}},
    )

    models = list_models(user_id="u_1", metadata_store=store)

    assert models == [
        {
            "id": "mdl_1",
            "name": "fraud",
            "model_class": "LogisticRegression",
            "task_type": "classification",
            "created_at": "2026-08-01T00:00:00+00:00",
            "production_version_id": "mv_prod",
        }
    ]


def test_list_models_reports_null_production_version_when_none_is_live():
    store = FakeMetadataStore(
        models={
            "u_1": [
                {
                    "id": "mdl_1",
                    "name": "fraud",
                    "model_class": None,
                    "task_type": None,
                    "created_at": "2026-08-01T00:00:00+00:00",
                }
            ]
        },
        production_versions={},
    )

    models = list_models(user_id="u_1", metadata_store=store)

    assert models[0]["production_version_id"] is None


def test_list_models_returns_an_empty_list_for_an_unknown_user():
    store = FakeMetadataStore()

    assert list_models(user_id="nobody", metadata_store=store) == []


def test_list_versions_returns_the_stores_ordering_unchanged():
    store = FakeMetadataStore(
        versions={
            "mdl_1": [
                {"id": "mv_2", "status": "production", "created_at": "2026-08-02T00:00:00+00:00"},
                {"id": "mv_1", "status": "archived", "created_at": "2026-08-01T00:00:00+00:00"},
            ]
        }
    )

    versions = list_versions(model_id="mdl_1", metadata_store=store)

    assert versions == [
        {"id": "mv_2", "status": "production", "created_at": "2026-08-02T00:00:00+00:00"},
        {"id": "mv_1", "status": "archived", "created_at": "2026-08-01T00:00:00+00:00"},
    ]


def test_get_version_detail_assembles_every_piece():
    store = FakeMetadataStore(
        model_versions={
            "mv_1": {
                "id": "mv_1",
                "artifact_uri": "s3://verity-artifacts/abc",
                "status": "production",
                "model_id": "mdl_1",
            }
        },
        manifests={"mv_1": {"framework": "sklearn"}},
        eval_runs={"mv_1": {"id": "evr_1", "verdict": "pass"}},
        deployments={"mv_1": {"id": "dep_1", "status": "live"}},
        monitoring_configs={"mv_1": {"id": "mcfg_1"}},
    )

    detail = get_version_detail(model_version_id="mv_1", metadata_store=store)

    assert detail == {
        "model_version_id": "mv_1",
        "artifact_uri": "s3://verity-artifacts/abc",
        "status": "production",
        "manifest": {"framework": "sklearn"},
        "eval_run": {"id": "evr_1", "verdict": "pass"},
        "model_id": "mdl_1",
        "monitoring_config": {"id": "mcfg_1"},
        "deployment": {"id": "dep_1", "status": "live"},
    }


def test_get_version_detail_returns_none_slots_for_a_never_evaluated_version():
    store = FakeMetadataStore(
        model_versions={
            "mv_2": {
                "id": "mv_2",
                "artifact_uri": "s3://verity-artifacts/def",
                "status": "pending",
                "model_id": "mdl_1",
            }
        }
    )

    detail = get_version_detail(model_version_id="mv_2", metadata_store=store)

    assert detail["manifest"] is None
    assert detail["eval_run"] is None
    assert detail["monitoring_config"] is None
    assert detail["deployment"] is None


def test_get_version_detail_returns_none_for_an_unknown_version():
    store = FakeMetadataStore()

    assert get_version_detail(model_version_id="mv_unknown", metadata_store=store) is None


def test_get_download_urls_includes_both_artifact_and_fixture():
    store = FakeMetadataStore(
        model_versions={
            "mv_1": {"id": "mv_1", "artifact_sha256": "artifact_hash", "model_id": "mdl_1"}
        },
        eval_runs={"mv_1": {"fixture": {"sha256": "fixture_hash"}}},
    )
    blob_store = FakeBlobStore()

    urls = get_download_urls(model_version_id="mv_1", metadata_store=store, blob_store=blob_store)

    assert urls == {
        "model_version_id": "mv_1",
        "artifact_url": "https://fake-bucket.s3.amazonaws.com/artifact_hash",
        "fixture_url": "https://fake-bucket.s3.amazonaws.com/fixture_hash",
    }
    assert {"sha256": "artifact_hash", "expires_in": 900} in blob_store.calls
    assert {"sha256": "fixture_hash", "expires_in": 900} in blob_store.calls


def test_get_download_urls_reports_null_fixture_when_never_evaluated():
    store = FakeMetadataStore(
        model_versions={
            "mv_1": {"id": "mv_1", "artifact_sha256": "artifact_hash", "model_id": "mdl_1"}
        },
    )
    blob_store = FakeBlobStore()

    urls = get_download_urls(model_version_id="mv_1", metadata_store=store, blob_store=blob_store)

    assert urls["fixture_url"] is None
    assert len(blob_store.calls) == 1


def test_get_download_urls_returns_none_for_an_unknown_version():
    store = FakeMetadataStore()
    blob_store = FakeBlobStore()

    result = get_download_urls(
        model_version_id="mv_unknown", metadata_store=store, blob_store=blob_store
    )

    assert result is None
