# Model Registry Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Verity's frontend from a single-upload-and-forget form into a browsable
registry — list a user's models, list a model's versions (including archived ones), view
one version's full detail, and download its artifact/fixture via presigned S3 URLs.

**Architecture:** Four new read-only GET routes in `server/main.py`, backed by a new
`server/registry.py` module and six new `SupabaseMetadataStore`/`S3BlobStore` query
methods. The detail route's response is deliberately shaped to match the existing
`IngestResult` type exactly, so the frontend's existing `EvidenceReport` component renders
it with zero changes. A new "Models" tab sits alongside the existing upload form; the
upload flow itself is untouched.

**Tech Stack:** Python 3.12, FastAPI, Supabase (Postgres via PostgREST), boto3 (S3
presigned URLs), Next.js 16 / React 19, TypeScript.

**Spec:** `docs/superpowers/specs/2026-08-25-model-registry-dashboard-design.md`

## Global Constraints

- No `unittest.mock` anywhere — hand-written fakes only, matching this codebase's
  standing convention. `server/tests/test_supabase.py`'s `FakeSupabaseClient`/`FakeTable`
  (supports `.select()/.eq()/.order()/.limit()/.execute()`) and
  `server/tests/test_s3_blob_store.py`'s `FakeS3Client` are the fakes to extend — read
  them before writing new tests, don't rebuild equivalents from scratch.
- Full-sentence test names, matching every existing test file in `server/tests/`.
- Presigned URL expiry: 15 minutes (`900` seconds) — exact value from the spec.
- Unknown `user_id` / `model_id` on the list routes return an **empty list**, not a 404.
  Unknown `model_version_id` on the detail/download-urls routes returns a **404**. This
  distinction is deliberate (spec's Error Handling section) — do not make all four routes
  behave the same way.
- The detail route's response shape must match the frontend's existing `IngestResult`
  TypeScript type (`client/src/lib/verity.ts`) exactly in the fields it shares —
  `model_version_id`, `artifact_uri`, `status`, `manifest`, `eval_run`, `model_id`,
  `monitoring_config`, `deployment`. `deduplicated`/`archived_model_version_id` are
  optional on that type and are correctly omitted by this route (they describe an ingest
  *call*, not a property of a version).
- Do not modify `server/orchestrator.py` or the existing `POST /ingest` route's behavior.
  This plan is additive only.
- Run server tests from `server/`: `uv run pytest`. Before any run, confirm the venv has
  the right packages: `uv run python -c "import supabase, boto3, docker"` — if that
  fails, run `uv sync --extra dev` first (`uv sync` alone has previously, silently,
  pruned pytest from this venv).
- Frontend has no test framework — verification is `npm run build` (type-checks) plus a
  live manual check, matching this codebase's standing practice.

---

### Task 1: Storage layer — six new read methods plus a presigned-URL method

**Files:**
- Modify: `server/storage/models/supabase.py`
- Modify: `server/storage/models/s3.py`
- Test: `server/tests/test_supabase.py`
- Test: `server/tests/test_s3_blob_store.py`

**Interfaces:**
- Consumes: nothing new — these are the leaf storage-layer methods.
- Produces (used by Task 2):
  - `SupabaseMetadataStore.find_models_by_user(*, user_id) -> list[dict]`
  - `SupabaseMetadataStore.find_model_versions(*, model_id) -> list[dict]`
  - `SupabaseMetadataStore.find_model_version(*, model_version_id) -> dict | None`
  - `SupabaseMetadataStore.find_manifest(*, model_version_id) -> dict | None`
  - `SupabaseMetadataStore.find_eval_run(*, model_version_id) -> dict | None`
  - `SupabaseMetadataStore.find_deployment(*, model_version_id) -> dict | None`
  - `S3BlobStore.presigned_url(sha256: str, expires_in: int = 900) -> str`

- [ ] **Step 1: Write the failing tests for the six `SupabaseMetadataStore` methods**

Append to `server/tests/test_supabase.py`:

```python
def test_find_models_by_user_returns_every_model_for_that_user():
    fake_client = FakeSupabaseClient(
        rows={
            "model": [
                {"id": "mdl_1", "user_id": "u_1", "name": "fraud"},
                {"id": "mdl_2", "user_id": "u_2", "name": "churn"},
            ]
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    models = store.find_models_by_user(user_id="u_1")

    assert models == [{"id": "mdl_1", "user_id": "u_1", "name": "fraud"}]
    assert fake_client.calls == [("model", "select", "*", [("user_id", "u_1")])]


def test_find_models_by_user_returns_an_empty_list_for_an_unknown_user():
    fake_client = FakeSupabaseClient(rows={"model": []})
    store = SupabaseMetadataStore(client=fake_client)

    assert store.find_models_by_user(user_id="nobody") == []


def test_find_model_versions_orders_newest_first():
    fake_client = FakeSupabaseClient(
        rows={
            "model_version": [
                {"id": "mv_1", "model_id": "mdl_1", "created_at": "2026-08-01T00:00:00+00:00"},
                {"id": "mv_2", "model_id": "mdl_1", "created_at": "2026-08-02T00:00:00+00:00"},
                {"id": "mv_3", "model_id": "mdl_2", "created_at": "2026-08-03T00:00:00+00:00"},
            ]
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    versions = store.find_model_versions(model_id="mdl_1")

    assert [v["id"] for v in versions] == ["mv_2", "mv_1"]
    assert fake_client.calls == [
        ("model_version", "select", "*", [("model_id", "mdl_1")])
    ]


def test_find_model_version_returns_the_row_by_id():
    fake_client = FakeSupabaseClient(
        rows={"model_version": [{"id": "mv_1", "status": "production"}]}
    )
    store = SupabaseMetadataStore(client=fake_client)

    assert store.find_model_version(model_version_id="mv_1") == {
        "id": "mv_1",
        "status": "production",
    }


def test_find_model_version_returns_none_when_missing():
    fake_client = FakeSupabaseClient(rows={"model_version": []})
    store = SupabaseMetadataStore(client=fake_client)

    assert store.find_model_version(model_version_id="mv_unknown") is None


def test_find_manifest_returns_the_row_for_that_version():
    fake_client = FakeSupabaseClient(
        rows={"manifest": [{"id": "mf_1", "model_version_id": "mv_1", "framework": "sklearn"}]}
    )
    store = SupabaseMetadataStore(client=fake_client)

    manifest = store.find_manifest(model_version_id="mv_1")

    assert manifest == {"id": "mf_1", "model_version_id": "mv_1", "framework": "sklearn"}


def test_find_manifest_returns_none_when_the_version_has_none():
    fake_client = FakeSupabaseClient(rows={"manifest": []})
    store = SupabaseMetadataStore(client=fake_client)

    assert store.find_manifest(model_version_id="mv_1") is None


def test_find_eval_run_returns_the_most_recent_row():
    fake_client = FakeSupabaseClient(
        rows={
            "eval_run": [
                {
                    "id": "evr_1",
                    "model_version_id": "mv_1",
                    "started_at": "2026-08-01T00:00:00+00:00",
                },
                {
                    "id": "evr_2",
                    "model_version_id": "mv_1",
                    "started_at": "2026-08-02T00:00:00+00:00",
                },
            ]
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    eval_run = store.find_eval_run(model_version_id="mv_1")

    assert eval_run["id"] == "evr_2"


def test_find_eval_run_returns_none_when_never_evaluated():
    fake_client = FakeSupabaseClient(rows={"eval_run": []})
    store = SupabaseMetadataStore(client=fake_client)

    assert store.find_eval_run(model_version_id="mv_1") is None


def test_find_deployment_returns_the_most_recent_row_regardless_of_status():
    fake_client = FakeSupabaseClient(
        rows={
            "deployment": [
                {
                    "id": "dep_1",
                    "model_version_id": "mv_1",
                    "status": "stopped",
                    "created_at": "2026-08-01T00:00:00+00:00",
                },
                {
                    "id": "dep_2",
                    "model_version_id": "mv_1",
                    "status": "live",
                    "created_at": "2026-08-02T00:00:00+00:00",
                },
            ]
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    deployment = store.find_deployment(model_version_id="mv_1")

    assert deployment["id"] == "dep_2"


def test_find_deployment_returns_none_when_never_deployed():
    fake_client = FakeSupabaseClient(rows={"deployment": []})
    store = SupabaseMetadataStore(client=fake_client)

    assert store.find_deployment(model_version_id="mv_1") is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd server; uv run pytest tests/test_supabase.py -v -k "find_models_by_user or find_model_versions or find_model_version or find_manifest or find_eval_run or find_deployment"`
Expected: FAIL — `AttributeError: 'SupabaseMetadataStore' object has no attribute 'find_models_by_user'` (and similarly for each new method).

- [ ] **Step 3: Implement the six methods**

Append to the `SupabaseMetadataStore` class in `server/storage/models/supabase.py` (place
near the other `find_*` methods, e.g. after `find_model_by_version`):

```python
    def find_models_by_user(self, *, user_id):
        result = self.client.table("model").select("*").eq("user_id", user_id).execute()
        return result.data or []

    def find_model_versions(self, *, model_id):
        result = (
            self.client.table("model_version")
            .select("*")
            .eq("model_id", model_id)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []

    def find_model_version(self, *, model_version_id):
        result = (
            self.client.table("model_version")
            .select("*")
            .eq("id", model_version_id)
            .execute()
        )
        return result.data[0] if result.data else None

    def find_manifest(self, *, model_version_id):
        result = (
            self.client.table("manifest")
            .select("*")
            .eq("model_version_id", model_version_id)
            .execute()
        )
        return result.data[0] if result.data else None

    def find_eval_run(self, *, model_version_id):
        # One eval_run per version in every path that exists today — ordering by
        # started_at is defensive, not because multiple rows are expected.
        result = (
            self.client.table("eval_run")
            .select("*")
            .eq("model_version_id", model_version_id)
            .order("started_at", desc=True)
            .execute()
        )
        return result.data[0] if result.data else None

    def find_deployment(self, *, model_version_id):
        result = (
            self.client.table("deployment")
            .select("*")
            .eq("model_version_id", model_version_id)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data[0] if result.data else None
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd server; uv run pytest tests/test_supabase.py -v`
Expected: all pass, including the pre-existing tests in this file.

- [ ] **Step 5: Write the failing test for `S3BlobStore.presigned_url`**

Append to `server/tests/test_s3_blob_store.py`:

```python
class FakeS3ClientWithPresign(FakeS3Client):
    def __init__(self):
        super().__init__()
        self.presign_calls = []

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.presign_calls.append(
            {"operation": operation, "Params": Params, "ExpiresIn": ExpiresIn}
        )
        return f"https://{Params['Bucket']}.s3.amazonaws.com/{Params['Key']}?X-Amz-Expires={ExpiresIn}"


def test_presigned_url_requests_a_get_object_url_for_the_sha256_key():
    fake_client = FakeS3ClientWithPresign()
    store = S3BlobStore(bucket="verity-artifacts", region="us-east-1", client=fake_client)

    url = store.presigned_url("abc123")

    assert fake_client.presign_calls == [
        {
            "operation": "get_object",
            "Params": {"Bucket": "verity-artifacts", "Key": "abc123"},
            "ExpiresIn": 900,
        }
    ]
    assert url == "https://verity-artifacts.s3.amazonaws.com/abc123?X-Amz-Expires=900"


def test_presigned_url_accepts_a_custom_expiry():
    fake_client = FakeS3ClientWithPresign()
    store = S3BlobStore(bucket="verity-artifacts", region="us-east-1", client=fake_client)

    store.presigned_url("abc123", expires_in=60)

    assert fake_client.presign_calls[0]["ExpiresIn"] == 60
```

- [ ] **Step 6: Run to verify it fails**

Run: `cd server; uv run pytest tests/test_s3_blob_store.py -v -k presigned_url`
Expected: FAIL — `AttributeError: 'S3BlobStore' object has no attribute 'presigned_url'`

- [ ] **Step 7: Implement `presigned_url`**

Add to the `S3BlobStore` class in `server/storage/models/s3.py`, after `put`:

```python
    def presigned_url(self, sha256: str, expires_in: int = 900) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": sha256},
            ExpiresIn=expires_in,
        )
```

- [ ] **Step 8: Run to verify it passes**

Run: `cd server; uv run pytest tests/test_s3_blob_store.py -v`
Expected: all pass.

- [ ] **Step 9: Run the full server suite**

Run: `cd server; uv run pytest -q`
Expected: all previously-passing tests still pass, plus the 13 new tests from this task
(11 for `SupabaseMetadataStore`, 2 for `S3BlobStore`).

- [ ] **Step 10: Commit**

```bash
git add server/storage/models/supabase.py server/storage/models/s3.py server/tests/test_supabase.py server/tests/test_s3_blob_store.py
git commit -m "Add registry read methods and S3 presigned URLs"
```

---

### Task 2: `registry.py` — assembling list/detail/download responses

**Files:**
- Create: `server/registry.py`
- Test: `server/tests/test_registry.py`

**Interfaces:**
- Consumes: the six `SupabaseMetadataStore` methods and `S3BlobStore.presigned_url` from
  Task 1 (via a hand-written fake `metadata_store`/`blob_store` in tests — this task
  never touches real Supabase/S3).
- Produces (used by Task 3):
  - `list_models(*, user_id, metadata_store) -> list[dict]`
  - `list_versions(*, model_id, metadata_store) -> list[dict]`
  - `get_version_detail(*, model_version_id, metadata_store) -> dict | None`
  - `get_download_urls(*, model_version_id, metadata_store, blob_store) -> dict | None`

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_registry.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd server; uv run pytest tests/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'registry'`

- [ ] **Step 3: Implement `server/registry.py`**

```python
"""Read-side registry queries: list models, list versions, one version's full detail,
and its downloadable-artifact URLs.

Separate from orchestrator.py deliberately: orchestrator.build_artifact() builds its
response from values it just computed mid-pipeline; these functions build the identical
*shape* by re-querying already-stored rows, for a version that may have been created any
time in the past. One assembles from memory during a write; these assemble from storage
for a read.
"""


def list_models(*, user_id, metadata_store):
    models = metadata_store.find_models_by_user(user_id=user_id)
    result = []
    for model in models:
        production_version = metadata_store.find_production_version(model_id=model["id"])
        result.append(
            {
                "id": model["id"],
                "name": model["name"],
                "model_class": model.get("model_class"),
                "task_type": model.get("task_type"),
                "created_at": model.get("created_at"),
                "production_version_id": production_version["id"] if production_version else None,
            }
        )
    return result


def list_versions(*, model_id, metadata_store):
    return metadata_store.find_model_versions(model_id=model_id)


def get_version_detail(*, model_version_id, metadata_store):
    version = metadata_store.find_model_version(model_version_id=model_version_id)
    if version is None:
        return None
    return {
        "model_version_id": model_version_id,
        "artifact_uri": version["artifact_uri"],
        "status": version["status"],
        "manifest": metadata_store.find_manifest(model_version_id=model_version_id),
        "eval_run": metadata_store.find_eval_run(model_version_id=model_version_id),
        "model_id": version["model_id"],
        "monitoring_config": metadata_store.find_monitoring_config(model_version_id=model_version_id),
        "deployment": metadata_store.find_deployment(model_version_id=model_version_id),
    }


def get_download_urls(*, model_version_id, metadata_store, blob_store):
    version = metadata_store.find_model_version(model_version_id=model_version_id)
    if version is None:
        return None

    artifact_url = blob_store.presigned_url(version["artifact_sha256"])

    fixture_url = None
    eval_run = metadata_store.find_eval_run(model_version_id=model_version_id)
    if eval_run and eval_run.get("fixture"):
        fixture_url = blob_store.presigned_url(eval_run["fixture"]["sha256"])

    return {
        "model_version_id": model_version_id,
        "artifact_url": artifact_url,
        "fixture_url": fixture_url,
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd server; uv run pytest tests/test_registry.py -v`
Expected: 10 passed.

- [ ] **Step 5: Run the full server suite**

Run: `cd server; uv run pytest -q`
Expected: all previously-passing tests plus the 10 new ones.

- [ ] **Step 6: Commit**

```bash
git add server/registry.py server/tests/test_registry.py
git commit -m "Add registry.py — list/detail/download-url assembly"
```

---

### Task 3: Four new routes in `server/main.py`

**Files:**
- Modify: `server/main.py`
- Modify: `server/tests/test_main_wiring.py`
- Test: `server/tests/test_main.py`

**Interfaces:**
- Consumes: `list_models`, `list_versions`, `get_version_detail`, `get_download_urls`
  from Task 2 (`server/registry.py`); `S3BlobStore` and `SupabaseMetadataStore` already
  imported in `main.py`.
- Produces: the four HTTP routes the frontend (Tasks 4-5) will call.

- [ ] **Step 1: Read the current `get_build_artifact` wiring and its existing test**

Before changing anything, read `server/main.py` lines 43-59 and
`server/tests/test_main_wiring.py` in full — the refactor in Step 3 below changes how
`get_build_artifact()` constructs its `blob_store`, and the existing test at
`test_main_wiring.py:6-18` asserts on that construction. It will need one line added
(clearing the new cache too), shown in Step 4.

- [ ] **Step 2: Write the failing tests for the four routes**

Append to `server/tests/test_main.py` (uses the same `app.dependency_overrides` pattern
as the existing `read_telemetry`/`read_alerts` tests already in this file — read one of
those first, e.g. `test_reading_telemetry_summarises_the_window_alongside_the_eval_reference`,
for the exact shape being matched):

```python
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
```

Add `get_blob_store` to this test file's existing import line from `main`
(`from main import TELEMETRY_TRACE_LIMIT, app, get_build_artifact, get_metadata_store`
becomes `from main import TELEMETRY_TRACE_LIMIT, app, get_blob_store, get_build_artifact, get_metadata_store`).

- [ ] **Step 3: Run to verify they fail**

Run: `cd server; uv run pytest tests/test_main.py -v -k "listing_models or listing_versions or reading_version_detail or reading_download_urls"`
Expected: FAIL — `ImportError: cannot import name 'get_blob_store' from 'main'`

- [ ] **Step 4: Extract `get_blob_store()` and add the four routes**

In `server/main.py`, replace:

```python
@lru_cache
def get_build_artifact():
    blob_store = S3BlobStore(
        bucket=os.environ["S3_BUCKET"],
        region=os.environ["S3_REGION"],
    )
    metadata_store = SupabaseMetadataStore()
    return partial(build_artifact, blob_store=blob_store, metadata_store=metadata_store)
```

with:

```python
@lru_cache
def get_blob_store():
    return S3BlobStore(bucket=os.environ["S3_BUCKET"], region=os.environ["S3_REGION"])


@lru_cache
def get_build_artifact():
    metadata_store = SupabaseMetadataStore()
    return partial(build_artifact, blob_store=get_blob_store(), metadata_store=metadata_store)
```

Add the import at the top of `server/main.py`, alongside the existing `from telemetry
import summarize` line:

```python
import registry
```

Add the four routes. Place them after `read_alerts` (which ends around line 184) and
before `predict`:

```python
@app.get("/users/{user_id}/models")
async def list_models(user_id: str, metadata_store=Depends(get_metadata_store)):
    return {
        "user_id": user_id,
        "models": registry.list_models(user_id=user_id, metadata_store=metadata_store),
    }


@app.get("/models/{model_id}/versions")
async def list_versions(model_id: str, metadata_store=Depends(get_metadata_store)):
    return {
        "model_id": model_id,
        "versions": registry.list_versions(model_id=model_id, metadata_store=metadata_store),
    }


@app.get("/model_versions/{model_version_id}")
async def read_version_detail(
    model_version_id: str, metadata_store=Depends(get_metadata_store)
):
    detail = registry.get_version_detail(
        model_version_id=model_version_id, metadata_store=metadata_store
    )
    if detail is None:
        raise HTTPException(404, f"no model version {model_version_id!r}")
    return detail


@app.get("/model_versions/{model_version_id}/download-urls")
async def read_download_urls(
    model_version_id: str,
    metadata_store=Depends(get_metadata_store),
    blob_store=Depends(get_blob_store),
):
    urls = registry.get_download_urls(
        model_version_id=model_version_id, metadata_store=metadata_store, blob_store=blob_store
    )
    if urls is None:
        raise HTTPException(404, f"no model version {model_version_id!r}")
    return urls
```

Note: the route function name `list_models` shadows nothing in this file (no existing
`list_models` symbol), but it does share a name with `registry.list_models` — this is
fine and intentional, matching how `read_telemetry` already wraps `summarize` under a
different local name; call the module function via its `registry.` prefix, as shown
above, never unqualified.

- [ ] **Step 5: Update the existing `get_build_artifact` wiring test for the new cache**

In `server/tests/test_main_wiring.py`, `get_build_artifact()` now calls the also-cached
`get_blob_store()` internally. Add a cache-clear for it, so this test can't read a stale
`S3BlobStore` left over from a different test's env vars. Change:

```python
def test_get_build_artifact_wires_real_s3_and_supabase_stores(monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "verity-artifacts")
    monkeypatch.setenv("S3_REGION", "us-east-1")
    monkeypatch.setenv("SUPABASE_URL", "http://supabase.test")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    main.get_build_artifact.cache_clear()
```

to:

```python
def test_get_build_artifact_wires_real_s3_and_supabase_stores(monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "verity-artifacts")
    monkeypatch.setenv("S3_REGION", "us-east-1")
    monkeypatch.setenv("SUPABASE_URL", "http://supabase.test")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    main.get_blob_store.cache_clear()
    main.get_build_artifact.cache_clear()
```

- [ ] **Step 6: Run to verify everything passes**

Run: `cd server; uv run pytest tests/test_main.py tests/test_main_wiring.py -v`
Expected: all pass, including the 7 new route tests.

- [ ] **Step 7: Run the full server suite**

Run: `cd server; uv run pytest -q`
Expected: all previously-passing tests plus the new ones from Tasks 1-3.

- [ ] **Step 8: Commit**

```bash
git add server/main.py server/tests/test_main.py server/tests/test_main_wiring.py
git commit -m "Add list/detail/download-url routes for the model registry"
```

---

### Task 4: Frontend data layer + `ModelList` / `VersionList`

**Files:**
- Modify: `client/src/lib/verity.ts`
- Create: `client/src/components/model-list.tsx`
- Create: `client/src/components/version-list.tsx`

**Interfaces:**
- Consumes: the three list/detail JSON shapes Task 3's routes return (`{user_id, models}`,
  `{model_id, versions}`; the download-urls shape is consumed in Task 5).
- Produces: `ModelSummary`, `VersionSummary` types; `fetchModels(userId)`,
  `fetchVersions(modelId)` functions; `<ModelList onSelect={(modelId) => void} />` and
  `<VersionList modelId={string} onSelect={(versionId) => void} onBack={() => void} />`
  components, used by Task 5's `ModelsBrowser`.

- [ ] **Step 1: Add the new types and fetch functions to `client/src/lib/verity.ts`**

Append, after the existing `fetchAlerts` function at the end of the file:

```typescript
export type ModelSummary = {
  id: string;
  name: string;
  model_class: string | null;
  task_type: string | null;
  created_at: string;
  production_version_id: string | null;
};

export async function fetchModels(userId: string): Promise<ModelSummary[]> {
  const response = await fetch(`${API_BASE}/users/${encodeURIComponent(userId)}/models`);
  if (!response.ok) {
    throw new IngestError(`Couldn't list models (${response.status}).`);
  }
  const body: { user_id: string; models: ModelSummary[] } = await response.json();
  return body.models;
}

export type VersionSummary = {
  id: string;
  status: string;
  created_at: string;
};

export async function fetchVersions(modelId: string): Promise<VersionSummary[]> {
  const response = await fetch(`${API_BASE}/models/${encodeURIComponent(modelId)}/versions`);
  if (!response.ok) {
    throw new IngestError(`Couldn't list versions (${response.status}).`);
  }
  const body: { model_id: string; versions: VersionSummary[] } = await response.json();
  return body.versions;
}

export async function fetchVersionDetail(modelVersionId: string): Promise<IngestResult> {
  const response = await fetch(
    `${API_BASE}/model_versions/${encodeURIComponent(modelVersionId)}`,
  );
  if (!response.ok) {
    throw new IngestError(`Couldn't read version detail (${response.status}).`);
  }
  return response.json();
}

export type DownloadUrls = {
  model_version_id: string;
  artifact_url: string;
  fixture_url: string | null;
};

export async function fetchDownloadUrls(modelVersionId: string): Promise<DownloadUrls> {
  const response = await fetch(
    `${API_BASE}/model_versions/${encodeURIComponent(modelVersionId)}/download-urls`,
  );
  if (!response.ok) {
    throw new IngestError(`Couldn't get download links (${response.status}).`);
  }
  return response.json();
}
```

- [ ] **Step 2: Create `client/src/components/model-list.tsx`**

Follows the same fetch/refresh/empty-state pattern as
`client/src/components/alerts-panel.tsx` (read that file first for the exact shape being
matched):

```tsx
"use client";

import { useEffect, useState } from "react";
import { fetchModels, type ModelSummary } from "@/lib/verity";

export function ModelList({
  userId,
  onSelect,
}: {
  userId: string;
  onSelect: (modelId: string) => void;
}) {
  const [models, setModels] = useState<ModelSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      setModels(await fetchModels(userId));
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  if (error) {
    return <p className="mt-6 font-mono text-xs text-fail">{error}</p>;
  }
  if (!models) {
    return <p className="mt-6 font-mono text-xs text-ink-soft">Reading models…</p>;
  }

  return (
    <div className="font-mono text-sm">
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-xs uppercase tracking-[0.2em] text-brass">
          Models for {userId} ({models.length})
        </h3>
        <button
          type="button"
          onClick={load}
          className="border border-ink px-2 py-1 text-[10px] uppercase tracking-[0.2em] hover:bg-ink hover:text-paper"
        >
          Refresh
        </button>
      </div>

      {models.length === 0 ? (
        <p className="text-xs text-ink-soft">
          No models uploaded yet under this user id.
        </p>
      ) : (
        <ul className="space-y-1">
          {models.map((model) => (
            <li key={model.id}>
              <button
                type="button"
                onClick={() => onSelect(model.id)}
                className="flex w-full items-baseline gap-2 py-1 text-left hover:text-brass"
              >
                <span className="shrink-0 font-medium">{model.name}</span>
                <span className="grow border-b border-dotted border-rule translate-y-[-3px]" />
                <span className="shrink-0 text-xs text-ink-soft">
                  {model.production_version_id ? "has a production version" : "no production version"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Create `client/src/components/version-list.tsx`**

```tsx
"use client";

import { useEffect, useState } from "react";
import { fetchVersions, type VersionSummary } from "@/lib/verity";

export function VersionList({
  modelId,
  onSelect,
  onBack,
}: {
  modelId: string;
  onSelect: (modelVersionId: string) => void;
  onBack: () => void;
}) {
  const [versions, setVersions] = useState<VersionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      setVersions(await fetchVersions(modelId));
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelId]);

  return (
    <div className="font-mono text-sm">
      <button
        type="button"
        onClick={onBack}
        className="mb-4 text-xs uppercase tracking-[0.2em] text-ink-soft hover:text-brass"
      >
        ← Back to models
      </button>

      {error && <p className="text-xs text-fail">{error}</p>}
      {!error && !versions && <p className="text-xs text-ink-soft">Reading versions…</p>}

      {versions && (
        <>
          <h3 className="mb-2 text-xs uppercase tracking-[0.2em] text-brass">
            Versions ({versions.length})
          </h3>
          {versions.length === 0 ? (
            <p className="text-xs text-ink-soft">No versions found.</p>
          ) : (
            <ul className="space-y-1">
              {versions.map((version) => (
                <li key={version.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(version.id)}
                    className="flex w-full items-baseline gap-2 py-1 text-left hover:text-brass"
                  >
                    <span className="shrink-0 font-medium">{version.id}</span>
                    <span className="grow border-b border-dotted border-rule translate-y-[-3px]" />
                    <span className="shrink-0 text-xs text-ink-soft">{version.status}</span>
                    <span className="shrink-0 text-xs text-ink-soft">
                      {new Date(version.created_at).toLocaleString()}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Type-check**

Run: `cd client; npm run build`
Expected: `Compiled successfully`, `Finished TypeScript` with no errors. These two
components aren't mounted anywhere yet (Task 5 wires them in) — this step is purely
confirming they compile in isolation.

- [ ] **Step 5: Commit**

```bash
git add client/src/lib/verity.ts client/src/components/model-list.tsx client/src/components/version-list.tsx
git commit -m "Add fetchModels/fetchVersions/fetchVersionDetail/fetchDownloadUrls and their list components"
```

---

### Task 5: `VersionDetail`, `DownloadButtons`, `ModelsBrowser`, and wiring into `page.tsx`

**Files:**
- Create: `client/src/components/version-detail.tsx`
- Create: `client/src/components/models-browser.tsx`
- Modify: `client/src/app/page.tsx`

**Interfaces:**
- Consumes: `ModelList`, `VersionList` from Task 4; `fetchVersionDetail`,
  `fetchDownloadUrls`, `DownloadUrls` from Task 4's `verity.ts` additions; the existing
  `EvidenceReport` component (`client/src/components/evidence-report.tsx`) — unchanged,
  imported as-is.
- Produces: `<ModelsBrowser userId={string} />`, mounted from `page.tsx`.

- [ ] **Step 1: Create `client/src/components/version-detail.tsx`**

```tsx
"use client";

import { useEffect, useState } from "react";
import { EvidenceReport } from "./evidence-report";
import { fetchDownloadUrls, fetchVersionDetail, type DownloadUrls, type IngestResult } from "@/lib/verity";

function DownloadButtons({ modelVersionId }: { modelVersionId: string }) {
  const [urls, setUrls] = useState<DownloadUrls | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchDownloadUrls(modelVersionId)
      .then(setUrls)
      .catch((err) => setError((err as Error).message));
  }, [modelVersionId]);

  if (error) {
    return <p className="mt-6 font-mono text-xs text-fail">{error}</p>;
  }
  if (!urls) {
    return <p className="mt-6 font-mono text-xs text-ink-soft">Preparing download links…</p>;
  }

  return (
    <div className="mt-6 border-t border-rule pt-4 font-mono text-sm">
      <h3 className="mb-2 text-xs uppercase tracking-[0.2em] text-brass">Downloads</h3>
      <div className="flex gap-3">
        <a
          href={urls.artifact_url}
          target="_blank"
          rel="noreferrer"
          className="border border-ink px-3 py-1 text-xs uppercase tracking-[0.2em] hover:bg-ink hover:text-paper"
        >
          Download artifact
        </a>
        {urls.fixture_url && (
          <a
            href={urls.fixture_url}
            target="_blank"
            rel="noreferrer"
            className="border border-ink px-3 py-1 text-xs uppercase tracking-[0.2em] hover:bg-ink hover:text-paper"
          >
            Download fixture
          </a>
        )}
      </div>
      <p className="mt-2 text-xs text-ink-soft">
        Links expire in 15 minutes — re-open this page to get fresh ones.
      </p>
    </div>
  );
}

export function VersionDetail({
  modelVersionId,
  onBack,
}: {
  modelVersionId: string;
  onBack: () => void;
}) {
  const [detail, setDetail] = useState<IngestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDetail(null);
    setError(null);
    fetchVersionDetail(modelVersionId)
      .then(setDetail)
      .catch((err) => setError((err as Error).message));
  }, [modelVersionId]);

  return (
    <div className="font-mono text-sm">
      <button
        type="button"
        onClick={onBack}
        className="mb-4 text-xs uppercase tracking-[0.2em] text-ink-soft hover:text-brass"
      >
        ← Back to versions
      </button>

      {error && <p className="text-xs text-fail">{error}</p>}
      {!error && !detail && <p className="text-xs text-ink-soft">Reading version detail…</p>}

      {detail && (
        <>
          <EvidenceReport result={detail} />
          <DownloadButtons modelVersionId={modelVersionId} />
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Create `client/src/components/models-browser.tsx`**

```tsx
"use client";

import { useState } from "react";
import { ModelList } from "./model-list";
import { VersionList } from "./version-list";
import { VersionDetail } from "./version-detail";

export function ModelsBrowser({ userId }: { userId: string }) {
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);

  if (selectedVersionId) {
    return (
      <VersionDetail
        modelVersionId={selectedVersionId}
        onBack={() => setSelectedVersionId(null)}
      />
    );
  }

  if (selectedModelId) {
    return (
      <VersionList
        modelId={selectedModelId}
        onSelect={setSelectedVersionId}
        onBack={() => setSelectedModelId(null)}
      />
    );
  }

  return <ModelList userId={userId} onSelect={setSelectedModelId} />;
}
```

- [ ] **Step 3: Wire a "Models" tab into `client/src/app/page.tsx`**

Read the current file in full first — this step only adds tab state and a conditional
render; every line of the existing upload form stays exactly as it is today, just moved
under the `view === "upload"` branch.

Add the import, alongside the existing ones:

```tsx
import { ModelsBrowser } from "@/components/models-browser";
```

Add one new piece of state, alongside the existing `useState` calls:

```tsx
const [view, setView] = useState<"upload" | "models">("upload");
```

Change the `return` statement's opening — wrap the existing `<header>...</header>` and
`<form>...</form>` and result-rendering blocks (everything currently between
`<main className="w-full max-w-xl">` and its closing `</main>`) in a conditional, and add
tab buttons above them:

```tsx
  return (
    <div className="flex flex-1 justify-center px-4 py-12 sm:py-20">
      <main className="w-full max-w-xl">
        <div className="mb-6 flex gap-2 font-mono text-xs uppercase tracking-[0.2em]">
          <button
            type="button"
            onClick={() => setView("upload")}
            className={`border-2 px-3 py-1 ${view === "upload" ? "border-ink bg-ink text-paper" : "border-ink text-ink-soft hover:text-ink"}`}
          >
            Upload
          </button>
          <button
            type="button"
            onClick={() => setView("models")}
            className={`border-2 px-3 py-1 ${view === "models" ? "border-ink bg-ink text-paper" : "border-ink text-ink-soft hover:text-ink"}`}
          >
            Models
          </button>
        </div>

        {view === "models" && <ModelsBrowser userId={userId} />}

        {view === "upload" && (
          <>
            <header className="mb-10 border-b-2 border-ink pb-4">
```

...and at the very end, close the added fragment right after the existing `{result &&
(...)}` block (which itself comes right after the existing `{error && (...)}` block —
both stay exactly as they are today, this only closes the new wrapper around them):

```tsx
        {error && (
          <p className="mt-6 border border-fail px-4 py-3 font-mono text-xs text-fail">
            {error}
          </p>
        )}

        {result && (
          <div className="mt-10">
            <EvidenceReport result={result} />
          </div>
        )}
          </>
        )}
      </main>
    </div>
  );
}
```

Everything between `<header className="mb-10...` and the `{result && (...)}` block shown
above — including the `<form>...</form>` and the `{error && (...)}` block — is untouched
content, only its indentation changes as it moves one level deeper inside the new
`{view === "upload" && (<>...</>)}` fragment. The two markers added are: `<>` right after
the tab buttons' closing `</div>`, and `</>` right after the existing `{result && (...)}`
block, both shown in their exact insertion points above.

- [ ] **Step 4: Type-check and build**

Run: `cd client; npm run build`
Expected: `Compiled successfully`, `Finished TypeScript` with no errors, and the existing
`Route (app)` output listing `/` as before.

- [ ] **Step 5: Commit**

```bash
git add client/src/components/version-detail.tsx client/src/components/models-browser.tsx client/src/app/page.tsx
git commit -m "Add Models tab: version detail, download buttons, and page wiring"
```

---

### Task 6: Live verification via the SDK, and docs

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/progression.md`

- [ ] **Step 1: Run the full server suite one more time**

Run: `cd server; uv run pytest -q`
Expected: all tests pass (Tasks 1-3's additions plus everything pre-existing).

- [ ] **Step 2: Start the server and client locally**

```powershell
cd server; uv run uvicorn main:app --port 8000   # one shell
cd client; npm run dev                            # another shell, defaults to :3000
```

- [ ] **Step 3: Upload at least two versions of the same model via the real SDK**

Per this plan's own instruction (not the browser's demo form, not raw curl) — use the
actual `verity` SDK/CLI, the way a real caller would:

```powershell
cd verity
uv run --extra dev python -m verity.cli --demo --user-id registry-check --name registry-check-model --endpoint http://127.0.0.1:8000
# run it a second time to get a second version under the same name
uv run --extra dev python -m verity.cli --demo --user-id registry-check --name registry-check-model --endpoint http://127.0.0.1:8000
```

- [ ] **Step 4: Verify in the browser**

Open `http://127.0.0.1:3000`, click the "Models" tab, enter `registry-check` as the user
id (or wire it to the same `userId` state already in `page.tsx` — it already defaults to
`demo-user`, so upload under that id instead if simpler), confirm:
- `registry-check-model` appears in the model list.
- Clicking it shows both versions, newest first.
- Clicking a version shows the same detail `EvidenceReport` already renders for a fresh
  upload — manifest, eval scores, system metrics, deployment, telemetry/traces/alerts
  panels (whichever apply).
- Both "Download artifact" and "Download fixture" buttons are present and each opens/
  downloads a real file from S3.

- [ ] **Step 5: Update `README.md`**

Add one paragraph to the "Current status" section (after the existing Fargate paragraph),
noting: the frontend now has a Models tab for browsing past uploads by user id, viewing
full version history (including archived versions), and downloading artifacts/fixtures
via presigned S3 URLs — closing the "where do I get the artifacts back out" gap that
existed before this work.

- [ ] **Step 6: Extend `docs/architecture.md`**

Add a new subsection under §10 ("The frontend — `client/`") documenting the four new
routes, `registry.py`'s role, the deliberate shape-match between the detail route and
`IngestResult` that lets `EvidenceReport` be reused unchanged, and the presigned-URL
download mechanism — following the same file:line-citation style as the rest of that
document.

- [ ] **Step 7: Add an entry to `docs/progression.md`**

Following the existing numbered-entry convention, describe what shipped: the four new
routes, the reused `EvidenceReport` component, presigned-URL downloads, and the real
verification results from Step 3-4 (two versions of one model, confirmed browsable and
downloadable).

- [ ] **Step 8: Commit**

```bash
git add README.md docs/architecture.md docs/progression.md
git commit -m "Document the model registry dashboard"
```

## Self-Review

**Spec coverage:** all four routes (Task 3), all six storage methods plus presigned URLs
(Task 1), `registry.py`'s four assembly functions (Task 2), the frontend's four new
components plus `page.tsx` wiring (Tasks 4-5), and the spec's explicit testing section
(hand-written fakes throughout, manual browser verification) are each covered by a task.
The spec's error-handling distinctions (empty list vs. 404) are asserted directly in
Task 3's tests. The spec's "reuse `EvidenceReport` unchanged" requirement is satisfied by
Task 5 importing it without modification — verified by the fact no task touches
`evidence-report.tsx`.

**Type consistency:** `ModelSummary`, `VersionSummary`, and `DownloadUrls` are defined
once (Task 4) and used identically in Tasks 4-5 with no renamed fields.
`get_version_detail`'s returned dict keys (Task 2) exactly match what Task 3's route
returns raw (no reshaping in the route) and what `fetchVersionDetail`'s `IngestResult`
return type expects (Task 4) — traced end to end, no drift.

**Known accepted simplification, not a plan defect:** `list_models`'s N+1 query pattern
(one `find_production_version` call per model) is named and justified in the spec itself,
not an oversight found here.
