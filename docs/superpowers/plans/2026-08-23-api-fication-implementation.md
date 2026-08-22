# api-fication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A version promoted to `production` is automatically built into a container image, started, health-checked, and served behind a Verity-proxied `/predict` endpoint that records every request as telemetry.

**Architecture:** The SDK captures its own training environment at `assemble()` time and ships it with the artifact. The server introspects the fitted estimator inside the existing credential-scrubbed sandbox, storing the result as `manifest.io_schema`. On promotion, a build context is rendered (Dockerfile + pinned requirements + a checked-in app template + the artifact) and handed to a `ContainerRuntime` — local Docker at V1, behind an interface. Verity proxies inference; the container's port is never public.

**Tech Stack:** Python 3.12, FastAPI, pydantic v2, cloudpickle, `docker` SDK, Alembic, Supabase (Postgres), pytest.

**Spec:** `docs/superpowers/specs/2026-08-23-api-fication-design.md`

## Global Constraints

- **Do not commit and do not push.** Standing instruction from the user. Every task ends by running the suite, not by committing. Leave all work in the working tree.
- **No `unittest.mock` anywhere.** Hand-written fakes only, matching every existing test file.
- Every collaborator is injected with a lazy real default: `param=None` → `param or _default_param` → deferred import inside the default.
- Dispatch on a dict, never an `if` chain.
- Tabular / classical ML only — scikit-learn, XGBoost, LightGBM. No other framework is in scope.
- Full-sentence test names describing behaviour, e.g. `test_capture_records_the_installed_scikit_learn_version`.
- `server/` subpackages have **no `__init__.py`** (see `server/execution/`, `server/storage/`). `server/serving/` follows suit.
- Default test suite must stay offline and fast. Anything needing the Docker daemon is marked `@pytest.mark.docker` and skips when it is unreachable.
- Run server tests from `server/` with `uv run pytest`; SDK tests from `verity/` with `uv run pytest`.

**Environment note:** Docker Desktop is installed on this machine but its daemon is **not running** (`npipe:////./pipe/dockerDesktopLinuxEngine` unreachable). Tasks 1–10 are fully implementable and testable without it. Task 11's live verification requires starting Docker Desktop first.

---

### Task 1: SDK captures the training environment

**Files:**
- Create: `verity/src/verity/environment.py`
- Create: `verity/tests/test_environment.py`
- Modify: `verity/src/verity/client.py`
- Modify: `verity/src/verity/transport.py`
- Modify: `verity/tests/test_transport.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `verity.environment.capture() -> dict` shaped `{"python_version": str, "packages": {str: str}}`. `transport.upload(...)` gains keyword `environment: dict | None = None`, sent as multipart form field `environment` (JSON).

- [ ] **Step 1: Write the failing tests**

```python
# verity/tests/test_environment.py
import sys

from verity.environment import capture


def test_capture_reports_the_running_python_minor_version():
    assert capture()["python_version"] == f"{sys.version_info.major}.{sys.version_info.minor}"


def test_capture_records_the_installed_scikit_learn_version():
    import sklearn

    assert capture()["packages"]["scikit-learn"] == sklearn.__version__


def test_capture_omits_packages_that_are_not_installed():
    packages = capture()["packages"]

    # The allowlist is deliberately wider than any one project's dependencies; absent
    # ones must be skipped rather than recorded as null, so the rendered
    # requirements.txt never asks pip for a package that was never there.
    assert all(version is not None for version in packages.values())
    assert "cloudpickle" in packages


def test_capture_includes_cloudpickle_because_the_container_unpickles_the_artifact():
    # Not a framework, but the format the artifact is written in. A container with a
    # different cloudpickle major cannot be relied on to load the model at all.
    assert "cloudpickle" in capture()["packages"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd verity; uv run pytest tests/test_environment.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'verity.environment'`

- [ ] **Step 3: Implement `environment.py`**

```python
# verity/src/verity/environment.py
"""Capture the environment the model was trained in.

This runs client-side and must stay there. The server introspecting its own installed
versions would describe the wrong machine entirely: a pickle is only reliably loadable
against the library versions that wrote it, and those live here, not on the server.
"""

import sys

# Distribution names as pip knows them, not import names — `importlib.metadata` and the
# rendered requirements.txt both key on the distribution.
PACKAGES = (
    "scikit-learn",
    "numpy",
    "scipy",
    "pandas",
    "xgboost",
    "lightgbm",
    # The artifact's serialization format, not a modelling library. The serving
    # container is what unpickles it, and cloudpickle's format is not guaranteed
    # stable across majors.
    "cloudpickle",
)


def capture() -> dict:
    packages = {}
    for name in PACKAGES:
        found = _version(name)
        if found is not None:
            packages[name] = found
    return {
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "packages": packages,
    }


def _version(name):
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(name)
    except PackageNotFoundError:
        return None
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd verity; uv run pytest tests/test_environment.py -v`
Expected: 4 passed

- [ ] **Step 5: Wire it through transport**

In `verity/src/verity/transport.py`, add the parameter and the form field:

```python
def upload(
        payload: bytes,
        sha256: str,
        user_id: str,
        name: str,
        args: dict,
        endpoint: str,
        client: httpx.Client | None = None,
        fixture_payload: bytes | None = None,
        fixture_descriptor: dict | None = None,
        environment: dict | None = None,
) -> dict:
```

and after the existing fixture block:

```python
    # Sent whenever the caller captured one. The server needs it to build a serving
    # image against the versions that wrote the pickle, not the ones it happens to run.
    if environment is not None:
        data["environment"] = json.dumps(environment)
```

- [ ] **Step 6: Wire it through the client**

In `verity/src/verity/client.py`, add the import and pass it down:

```python
from verity.environment import capture
```

then inside `assemble`, after `payload, sha256 = serialize(model)`:

```python
    return upload(
        payload=payload,
        sha256=sha256,
        user_id=user_id,
        name=name,
        args=args,
        endpoint=endpoint,
        client=client,
        fixture_payload=fixture_payload,
        fixture_descriptor=fixture_descriptor,
        environment=capture(),
    )
```

- [ ] **Step 7: Add the transport test**

Append to `verity/tests/test_transport.py`, following the fake-client convention already in that file:

```python
def test_upload_sends_the_environment_as_a_json_form_field():
    client = FakeClient()

    upload(
        payload=b"bytes",
        sha256="abc",
        user_id="u_1",
        name="m",
        args={},
        endpoint="http://test",
        client=client,
        environment={"python_version": "3.12", "packages": {"numpy": "2.3.5"}},
    )

    assert json.loads(client.calls[0]["data"]["environment"]) == {
        "python_version": "3.12",
        "packages": {"numpy": "2.3.5"},
    }


def test_upload_omits_the_environment_field_when_none_was_captured():
    client = FakeClient()

    upload(
        payload=b"bytes", sha256="abc", user_id="u_1", name="m", args={},
        endpoint="http://test", client=client,
    )

    assert "environment" not in client.calls[0]["data"]
```

- [ ] **Step 8: Run the whole SDK suite**

Run: `cd verity; uv run pytest -q`
Expected: all pass (38 existing + 6 new = 44)

---

### Task 2: Introspect the estimator inside the sandbox

**Files:**
- Modify: `server/execution/runner.py`
- Modify: `server/execution/sandbox.py`
- Create: `server/tests/test_introspect.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `execution.sandbox.introspect(*, model_payload, timeout=30) -> dict` returning `{"estimator_class": str, "n_features": int | None, "feature_names": list[str] | None, "classes": list | None, "has_predict_proba": bool}`. Raises `SandboxError` on failure.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_introspect.py
import numpy as np
import pytest
from sklearn.linear_model import LinearRegression, LogisticRegression

from execution.sandbox import SandboxError, introspect
from verity.serialize import serialize


def _fitted_classifier_on_arrays():
    X = np.array([[0.0, 1.0], [1.0, 0.0], [0.5, 0.5], [0.2, 0.8]])
    y = np.array([0, 1, 0, 1])
    return LogisticRegression().fit(X, y)


def test_introspect_reports_the_feature_count_of_a_fitted_estimator():
    payload, _ = serialize(_fitted_classifier_on_arrays())

    assert introspect(model_payload=payload)["n_features"] == 2


def test_introspect_reports_no_feature_names_when_the_model_was_fit_on_arrays():
    payload, _ = serialize(_fitted_classifier_on_arrays())

    # scikit-learn only sets feature_names_in_ when fit on a DataFrame. Reporting None
    # is what makes the generated API fall back to positional instances rather than
    # inventing column names.
    assert introspect(model_payload=payload)["feature_names"] is None


def test_introspect_reports_feature_names_when_the_model_was_fit_on_a_dataframe():
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame({"age": [1.0, 2.0, 3.0, 4.0], "fare": [4.0, 3.0, 2.0, 1.0]})
    model = LogisticRegression().fit(frame, np.array([0, 1, 0, 1]))
    payload, _ = serialize(model)

    assert introspect(model_payload=payload)["feature_names"] == ["age", "fare"]


def test_introspect_reports_the_class_labels_as_plain_json_safe_values():
    payload, _ = serialize(_fitted_classifier_on_arrays())

    classes = introspect(model_payload=payload)["classes"]

    # numpy ints would break the JSON contract written into the image.
    assert classes == [0, 1]
    assert all(type(value) is int for value in classes)


def test_introspect_reports_whether_the_estimator_can_produce_probabilities():
    classifier, _ = serialize(_fitted_classifier_on_arrays())
    regressor, _ = serialize(
        LinearRegression().fit(np.array([[1.0], [2.0], [3.0]]), np.array([1.0, 2.0, 3.0]))
    )

    assert introspect(model_payload=classifier)["has_predict_proba"] is True
    assert introspect(model_payload=regressor)["has_predict_proba"] is False


def test_introspect_names_the_estimator_class():
    payload, _ = serialize(_fitted_classifier_on_arrays())

    assert introspect(model_payload=payload)["estimator_class"] == "LogisticRegression"


def test_introspect_raises_sandbox_error_when_the_payload_is_not_a_model():
    with pytest.raises(SandboxError):
        introspect(model_payload=b"not a pickle at all")


def test_introspection_cannot_read_credentials_from_the_parent(monkeypatch):
    # The same security claim test_sandbox.py makes for execute(): introspection loads
    # untrusted bytes too, so it must inherit the identical credential allowlist.
    monkeypatch.setenv("SUPABASE_KEY", "leaked")
    payload, _ = serialize(_fitted_classifier_on_arrays())

    result = introspect(model_payload=payload)

    assert result["estimator_class"] == "LogisticRegression"
    # Proven properly by test_sandbox.py's dedicated probe; asserted here so a future
    # refactor that gives introspect() its own subprocess call can't quietly skip the
    # scrubbed environment.
    import execution.sandbox as sandbox_module

    assert "SUPABASE_KEY" not in sandbox_module._child_env()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd server; uv run pytest tests/test_introspect.py -v`
Expected: FAIL — `ImportError: cannot import name 'introspect' from 'execution.sandbox'`

- [ ] **Step 3: Add mode dispatch to the runner**

In `server/execution/runner.py`, replace `run_job` with a dispatching version and add the introspection body. Keep the existing predict logic verbatim, renamed:

```python
def _to_list(value):
    """numpy arrays and scalars -> plain JSON-safe Python."""
    if value is None:
        return None
    if hasattr(value, "tolist"):
        return value.tolist()
    return list(value)


def _run_predict(model, job):
    X = job["X"]

    batch_started = time.perf_counter()
    y_pred = model.predict(X)
    batch_elapsed = time.perf_counter() - batch_started

    y_proba = model.predict_proba(X) if hasattr(model, "predict_proba") else None

    rows = _n_rows(X)
    timings_ms = []
    for i in range(min(rows, LATENCY_SAMPLE_ROWS)):
        started = time.perf_counter()
        model.predict(X[i : i + 1])
        timings_ms.append((time.perf_counter() - started) * 1000)

    resource = _percentiles(timings_ms)
    resource["throughput_rps"] = rows / batch_elapsed if batch_elapsed > 0 else 0.0
    usage = _memory_and_cpu()
    resource["peak_memory_mb"] = usage["peak_memory_mb"] / (1024 * 1024)
    resource["cpu_time_s"] = usage["cpu_time_s"]
    resource["gpu_memory_mb"] = _gpu_memory_mb()

    return {
        "y_pred": y_pred,
        "y_proba": y_proba.tolist() if hasattr(y_proba, "tolist") else y_proba,
        "resource": resource,
    }


def _run_introspect(model, job):
    """Read the fitted estimator's surface. No prediction, no instrumentation.

    Everything here is measured off the object rather than inferred, which is why it
    belongs beside identification and not in the language model's prompt.
    """
    n_features = getattr(model, "n_features_in_", None)
    return {
        "estimator_class": type(model).__name__,
        "n_features": int(n_features) if n_features is not None else None,
        # Only set when the estimator was fit on a DataFrame. None is a real answer,
        # not a failure: it means the served API must be positional.
        "feature_names": _to_list(getattr(model, "feature_names_in_", None)),
        "classes": _to_list(getattr(model, "classes_", None)),
        "has_predict_proba": hasattr(model, "predict_proba"),
    }


# Dict dispatch rather than a branch, matching every other extension point in the
# pipeline: a third mode is a new entry, not a new `elif`.
_MODES = {
    "predict": _run_predict,
    "introspect": _run_introspect,
}


def run_job(job):
    # Unpickling customer bytes IS the job here, and it is deliberate: this process is
    # the containment boundary. It runs with a scrubbed environment (no store, blob, or
    # LLM credentials), holds no network clients, and is killed at a timeout by the
    # parent. Never call run_job() in-process — that would remove the only protection.
    model = cloudpickle.loads(job["model_bytes"])
    # Default keeps every existing caller working unchanged.
    return _MODES[job.get("mode", "predict")](model, job)
```

- [ ] **Step 4: Extract the subprocess body in `sandbox.py` and add `introspect`**

Replace `execute` with a shared `_run` plus two thin entry points:

```python
def _run(job, *, timeout):
    blob = cloudpickle.dumps(job)

    try:
        completed = subprocess.run(
            [sys.executable, "-m", "execution.runner"],
            input=blob,
            capture_output=True,
            timeout=timeout,
            cwd=str(_SERVER_DIR),
            env=_child_env(),
        )
    except subprocess.TimeoutExpired:
        raise SandboxError(f"model execution timed out after {timeout}s") from None

    if not completed.stdout:
        stderr = completed.stderr.decode(errors="replace").strip()
        raise SandboxError(
            f"sandbox exited with code {completed.returncode} and no output: {stderr}"
        )

    # Trusted direction: these bytes were written by our own runner, not by the model.
    result = cloudpickle.loads(completed.stdout)
    if "error" in result:
        error = result["error"]
        raise SandboxError(f"{error['type']}: {error['message']}")
    return result


def execute(*, model_payload, X, timeout=60):
    """Run a model against X in an isolated child process.

    Returns {"y_pred", "y_proba", "resource"}. Raises SandboxError if the child
    crashed, timed out, or returned something unreadable — Nat turns that into an
    `error` verdict rather than letting it escape.
    """
    return _run({"model_bytes": model_payload, "X": X}, timeout=timeout)


def introspect(*, model_payload, timeout=30):
    """Read the fitted estimator's input/output surface, without predicting.

    Same containment as execute(): loading the artifact is arbitrary code execution
    whether or not anything is predicted afterwards, so it happens behind the same
    credential allowlist. Shorter default timeout because nothing here does real work.
    """
    return _run({"model_bytes": model_payload, "mode": "introspect"}, timeout=timeout)
```

- [ ] **Step 5: Run the new tests and the existing sandbox tests together**

Run: `cd server; uv run pytest tests/test_introspect.py tests/test_sandbox.py -v`
Expected: all pass — the sandbox tests confirm the `_run` extraction changed no behaviour.

- [ ] **Step 6: Run the full server suite**

Run: `cd server; uv run pytest -q`
Expected: 132 existing + 8 new = 140 passed

---

### Task 3: Migrations for `deployment` and the manifest columns

**Files:**
- Create: `server/migrations/versions/<rev>_create_deployment_table.py`
- Create: `server/migrations/versions/<rev>_add_serving_columns_to_manifest.py`

**Interfaces:**
- Consumes: nothing.
- Produces: table `deployment` and columns `manifest.io_schema` (JSON), `manifest.environment` (JSON), `manifest.serving_pattern` (Text).

- [ ] **Step 1: Find the current head**

Run: `cd server; uv run alembic heads`
Expected: a single head — `e91a3d7c5b28` unless something landed since. Chain the first new revision off whatever this prints.

- [ ] **Step 2: Write the manifest columns migration**

Generate the file with `uv run alembic revision -m "add serving columns to manifest"`, then replace its body:

```python
"""add serving columns to manifest

io_schema and serving_pattern were specced in Schemas.md from the start and never
created. environment is an addition to that spec, for the same reason eval_run.fixture
was: without it, io_schema describes a model whose runtime requirements are unrecorded,
and a serving image could not be rebuilt reproducibly from stored rows alone.

All three are nullable — every manifest written before api-fication legitimately has
none of them.
"""
from alembic import op
import sqlalchemy as sa

revision: str = "<generated>"
down_revision = "e91a3d7c5b28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("manifest", sa.Column("io_schema", sa.JSON(), nullable=True))
    op.add_column("manifest", sa.Column("environment", sa.JSON(), nullable=True))
    op.add_column("manifest", sa.Column("serving_pattern", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("manifest", "serving_pattern")
    op.drop_column("manifest", "environment")
    op.drop_column("manifest", "io_schema")
```

- [ ] **Step 3: Write the deployment table migration**

Generate with `uv run alembic revision -m "create deployment table"`, chain it off the revision from Step 2, and replace the body:

```python
"""create deployment table

Where a promoted version actually runs. Listed under V6 in Schemas.md until
api-fication pulled it forward to V1, because V1 now serves.

A version that is promoted, replaced, and re-promoted gets a NEW row each time rather
than reusing one: the history of what served when is worth keeping. Only `status` and
`stopped_at` are ever updated in place.
"""
from alembic import op
import sqlalchemy as sa

revision: str = "<generated>"
down_revision = "<step 2's revision>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deployment",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "model_version_id",
            sa.Text(),
            sa.ForeignKey("model_version.id"),
            nullable=False,
        ),
        sa.Column("image_tag", sa.Text(), nullable=False),
        # Null while `building`, and stays null if the build never produced a container.
        sa.Column("container_id", sa.Text(), nullable=True),
        sa.Column("host_port", sa.Integer(), nullable=True),
        sa.Column("endpoint_url", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        # Why a `failed` deployment failed. A build failure is not a metric missing a
        # threshold, so it does not belong anywhere near eval_run.failed_on.
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_deployment_model_version_id", "deployment", ["model_version_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_deployment_model_version_id", table_name="deployment")
    op.drop_table("deployment")
```

- [ ] **Step 4: Apply and verify**

Run: `cd server; uv run alembic upgrade head`
Then: `uv run alembic current`
Expected: current matches the deployment-table revision. Confirm in Supabase that `deployment` exists and `manifest` has the three new columns.

---

### Task 4: Storage methods for deployments

**Files:**
- Modify: `server/storage/models/supabase.py`
- Modify: `server/tests/test_supabase.py`

**Interfaces:**
- Consumes: Task 3's schema.
- Produces on `SupabaseMetadataStore`:
  - `save_deployment(*, model_version_id, image_tag, status) -> str` (returns `dep_…`)
  - `update_deployment(*, deployment_id, **fields) -> None`
  - `find_live_deployment(*, model_version_id) -> dict | None`
  - `save_manifest` gains passthrough of `io_schema`, `environment`, `serving_pattern` when present in the manifest dict.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_supabase.py`, using the `FakeSupabaseClient` already defined there:

```python
def test_save_deployment_inserts_a_building_row_with_a_prefixed_id():
    client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=client)

    deployment_id = store.save_deployment(
        model_version_id="mv_1", image_tag="verity-model:mv_1", status="building"
    )

    assert deployment_id.startswith("dep_")
    inserted = client.tables["deployment"].inserted[0]
    assert inserted["model_version_id"] == "mv_1"
    assert inserted["image_tag"] == "verity-model:mv_1"
    assert inserted["status"] == "building"


def test_update_deployment_writes_only_the_fields_it_was_given():
    client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=client)

    store.update_deployment(
        deployment_id="dep_1", status="live", host_port=49312,
        endpoint_url="http://localhost:49312",
    )

    updated = client.tables["deployment"].updated[0]
    assert updated["payload"] == {
        "status": "live",
        "host_port": 49312,
        "endpoint_url": "http://localhost:49312",
    }
    assert updated["eq"] == ("id", "dep_1")


def test_find_live_deployment_returns_none_when_the_version_has_never_deployed():
    client = FakeSupabaseClient()
    client.tables["deployment"].rows = []
    store = SupabaseMetadataStore(client=client)

    assert store.find_live_deployment(model_version_id="mv_1") is None


def test_find_live_deployment_returns_the_live_row():
    client = FakeSupabaseClient()
    client.tables["deployment"].rows = [
        {"id": "dep_1", "model_version_id": "mv_1", "status": "live", "host_port": 49312}
    ]
    store = SupabaseMetadataStore(client=client)

    assert store.find_live_deployment(model_version_id="mv_1")["id"] == "dep_1"


def test_save_manifest_persists_the_serving_columns_when_present():
    client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=client)

    store.save_manifest(
        model_version_id="mv_1",
        manifest={
            "framework": "sklearn",
            "io_schema": {"n_features": 2},
            "environment": {"python_version": "3.12", "packages": {}},
        },
    )

    inserted = client.tables["manifest"].inserted[0]
    assert inserted["io_schema"] == {"n_features": 2}
    assert inserted["environment"] == {"python_version": "3.12", "packages": {}}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd server; uv run pytest tests/test_supabase.py -v -k deployment`
Expected: FAIL — `AttributeError: 'SupabaseMetadataStore' object has no attribute 'save_deployment'`

> If `FakeSupabaseClient` does not yet track `updated` payloads or per-table `rows`, extend it in the same file before implementing — it is a hand-written fake and extending it is expected. Do not reach for `unittest.mock`.

- [ ] **Step 3: Implement the store methods**

Add to `server/storage/models/supabase.py`, matching the existing style exactly:

```python
    def save_deployment(self, *, model_version_id, image_tag, status):
        deployment_id = f"dep_{uuid.uuid4().hex}"
        self.client.table("deployment").insert(
            {
                "id": deployment_id,
                "model_version_id": model_version_id,
                "image_tag": image_tag,
                "status": status,
            }
        ).execute()
        return deployment_id

    def update_deployment(self, *, deployment_id, **fields):
        # Only status and stopped_at are ever expected to change after `live`; the
        # signature stays open so the build path can fill container_id/host_port in the
        # same round trip that flips the status.
        self.client.table("deployment").update(fields).eq("id", deployment_id).execute()

    def find_live_deployment(self, *, model_version_id):
        response = (
            self.client.table("deployment")
            .select("*")
            .eq("model_version_id", model_version_id)
            .eq("status", "live")
            .limit(1)
            .execute()
        )
        return response.data[0] if response.data else None
```

and in `save_manifest`, carry the new keys through only when the manifest actually has them (a pre-api-fication manifest legitimately has none):

```python
        for column in ("io_schema", "environment", "serving_pattern"):
            if manifest.get(column) is not None:
                row[column] = manifest[column]
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd server; uv run pytest tests/test_supabase.py -v`
Expected: all pass

- [ ] **Step 5: Run the full server suite**

Run: `cd server; uv run pytest -q`
Expected: 140 + 5 = 145 passed

---

### Task 5: The serving app template

**Files:**
- Create: `server/serving/app_template.py`
- Create: `server/tests/test_serving_app.py`

**Interfaces:**
- Consumes: nothing at runtime — the app reads `contract.json` and `model.pkl` from its own directory.
- Produces: `GET /health` → `{"status": "ok"}`; `POST /predict` with `{"instances": [...]}` → `{"predictions": [...], "probabilities": [[...]] | null}`. Contract file shape: `{"n_features": int, "feature_names": list[str] | null, "classes": list | null, "has_predict_proba": bool}`.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_serving_app.py
import json
import sys

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sklearn.linear_model import LinearRegression, LogisticRegression

import cloudpickle


def _build_app(tmp_path, model, contract, monkeypatch):
    """Materialise a serving directory and import the template against it.

    The template resolves its own directory, so the test writes a real one and points
    the module at it — the same arrangement the built image produces.
    """
    (tmp_path / "model.pkl").write_bytes(cloudpickle.dumps(model))
    (tmp_path / "contract.json").write_text(json.dumps(contract))
    monkeypatch.setenv("VERITY_SERVING_DIR", str(tmp_path))
    sys.modules.pop("serving.app_template", None)
    from serving import app_template

    return TestClient(app_template.app)


def _classifier():
    X = np.array([[0.0, 1.0], [1.0, 0.0], [0.5, 0.5], [0.2, 0.8]])
    return LogisticRegression().fit(X, np.array([0, 1, 0, 1]))


POSITIONAL = {"n_features": 2, "feature_names": None, "classes": [0, 1],
              "has_predict_proba": True}
NAMED = {"n_features": 2, "feature_names": ["age", "fare"], "classes": [0, 1],
         "has_predict_proba": True}


def test_health_reports_ok_once_the_model_has_loaded(tmp_path, monkeypatch):
    client = _build_app(tmp_path, _classifier(), POSITIONAL, monkeypatch)

    assert client.get("/health").json() == {"status": "ok"}


def test_predict_accepts_positional_instances_when_there_are_no_feature_names(tmp_path, monkeypatch):
    client = _build_app(tmp_path, _classifier(), POSITIONAL, monkeypatch)

    body = client.post("/predict", json={"instances": [[0.0, 1.0], [1.0, 0.0]]}).json()

    assert len(body["predictions"]) == 2
    assert len(body["probabilities"]) == 2


def test_predict_accepts_named_instances_and_orders_them_by_the_contract(tmp_path, monkeypatch):
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame({"age": [0.0, 1.0, 0.5, 0.2], "fare": [1.0, 0.0, 0.5, 0.8]})
    model = LogisticRegression().fit(frame, np.array([0, 1, 0, 1]))
    client = _build_app(tmp_path, model, NAMED, monkeypatch)

    # Deliberately supplied out of order: the contract's order is what must win, because
    # column order IS the input contract for an estimator that takes a bare array.
    body = client.post("/predict", json={"instances": [{"fare": 1.0, "age": 0.0}]}).json()

    assert len(body["predictions"]) == 1


def test_predict_rejects_a_positional_instance_of_the_wrong_length(tmp_path, monkeypatch):
    client = _build_app(tmp_path, _classifier(), POSITIONAL, monkeypatch)

    response = client.post("/predict", json={"instances": [[1.0]]})

    assert response.status_code == 422
    assert "2" in response.json()["detail"]


def test_predict_rejects_a_named_instance_missing_a_feature(tmp_path, monkeypatch):
    client = _build_app(tmp_path, _classifier(), NAMED, monkeypatch)

    response = client.post("/predict", json={"instances": [{"age": 1.0}]})

    assert response.status_code == 422
    assert "fare" in response.json()["detail"]


def test_predict_rejects_an_unknown_feature_name(tmp_path, monkeypatch):
    client = _build_app(tmp_path, _classifier(), NAMED, monkeypatch)

    response = client.post("/predict", json={"instances": [{"age": 1.0, "fare": 2.0, "nope": 3.0}]})

    assert response.status_code == 422
    assert "nope" in response.json()["detail"]


def test_predict_returns_null_probabilities_for_an_estimator_without_predict_proba(tmp_path, monkeypatch):
    model = LinearRegression().fit(np.array([[1.0, 2.0], [3.0, 4.0]]), np.array([1.0, 2.0]))
    contract = {"n_features": 2, "feature_names": None, "classes": None,
                "has_predict_proba": False}
    client = _build_app(tmp_path, model, contract, monkeypatch)

    body = client.post("/predict", json={"instances": [[1.0, 2.0]]}).json()

    assert body["probabilities"] is None
    assert len(body["predictions"]) == 1


def test_predict_rejects_an_empty_instances_list(tmp_path, monkeypatch):
    client = _build_app(tmp_path, _classifier(), POSITIONAL, monkeypatch)

    assert client.post("/predict", json={"instances": []}).status_code == 422
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd server; uv run pytest tests/test_serving_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'serving'`

- [ ] **Step 3: Implement the template**

```python
# server/serving/app_template.py
"""The serving app baked into every model-version image.

This is a real, checked-in, unit-testable file — not a string the server renders. Only
requirements.txt and one Dockerfile line are templated. Everything that varies per model
arrives as data in contract.json, which means this app can be tested directly against a
temp directory without Docker anywhere in the loop.
"""

import json
import os
from pathlib import Path

import cloudpickle
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# The image lays these down next to this file; the env var exists so tests can point at
# a temp directory instead.
SERVING_DIR = Path(os.getenv("VERITY_SERVING_DIR", Path(__file__).parent))

CONTRACT = json.loads((SERVING_DIR / "contract.json").read_text())
FEATURE_NAMES = CONTRACT.get("feature_names")
N_FEATURES = CONTRACT["n_features"]
HAS_PROBA = CONTRACT.get("has_predict_proba", False)

# Loaded at import on purpose. If the artifact cannot load in this image, the container
# never reports healthy and the deploy fails loudly — rather than succeeding and then
# failing on a customer's first request.
MODEL = cloudpickle.loads((SERVING_DIR / "model.pkl").read_bytes())

app = FastAPI(title="Verity model service")


class PredictRequest(BaseModel):
    instances: list


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _row_from_named(instance, index):
    if not isinstance(instance, dict):
        raise HTTPException(422, f"instance {index}: expected an object with keys {FEATURE_NAMES}")
    missing = [name for name in FEATURE_NAMES if name not in instance]
    if missing:
        raise HTTPException(422, f"instance {index}: missing feature(s) {missing}")
    unknown = [key for key in instance if key not in FEATURE_NAMES]
    if unknown:
        raise HTTPException(422, f"instance {index}: unknown feature(s) {unknown}")
    # The contract's order wins. For an estimator taking a bare array, column order IS
    # the input contract, and a wrong order produces confidently wrong answers instead
    # of an error.
    return [instance[name] for name in FEATURE_NAMES]


def _row_from_positional(instance, index):
    if isinstance(instance, dict):
        raise HTTPException(
            422, f"instance {index}: this model has no feature names; send a list of {N_FEATURES} values"
        )
    if len(instance) != N_FEATURES:
        raise HTTPException(
            422, f"instance {index}: expected {N_FEATURES} values, got {len(instance)}"
        )
    return list(instance)


def _encode(instances):
    if not instances:
        raise HTTPException(422, "instances must not be empty")
    build_row = _row_from_named if FEATURE_NAMES else _row_from_positional
    return np.asarray([build_row(instance, i) for i, instance in enumerate(instances)], dtype=float)


@app.post("/predict")
def predict(body: PredictRequest) -> dict:
    X = _encode(body.instances)
    predictions = MODEL.predict(X)
    probabilities = MODEL.predict_proba(X) if HAS_PROBA else None
    return {
        "predictions": predictions.tolist(),
        "probabilities": probabilities.tolist() if probabilities is not None else None,
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd server; uv run pytest tests/test_serving_app.py -v`
Expected: 8 passed

- [ ] **Step 5: Run the full server suite**

Run: `cd server; uv run pytest -q`
Expected: 145 + 8 = 153 passed

---

### Task 6: Render the build context

**Files:**
- Create: `server/serving/build.py`
- Create: `server/tests/test_build_context.py`

**Interfaces:**
- Consumes: `serving/app_template.py` (copied, not imported).
- Produces: `serving.build.render_context(*, dest, payload, io_schema, environment) -> None` and `serving.build.image_tag(model_version_id) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_build_context.py
import json

from serving.build import image_tag, render_context

ENVIRONMENT = {
    "python_version": "3.12",
    "packages": {"scikit-learn": "1.7.2", "numpy": "2.3.5", "cloudpickle": "3.1.2"},
}
IO_SCHEMA = {"n_features": 2, "feature_names": ["age", "fare"], "classes": [0, 1],
             "has_predict_proba": True}


def test_render_context_writes_every_file_the_image_needs(tmp_path):
    render_context(dest=tmp_path, payload=b"model-bytes", io_schema=IO_SCHEMA,
                   environment=ENVIRONMENT)

    for name in ("Dockerfile", "requirements.txt", "app.py", "contract.json", "model.pkl"):
        assert (tmp_path / name).exists(), name


def test_the_contract_carries_the_io_schema_verbatim(tmp_path):
    render_context(dest=tmp_path, payload=b"m", io_schema=IO_SCHEMA, environment=ENVIRONMENT)

    assert json.loads((tmp_path / "contract.json").read_text()) == IO_SCHEMA


def test_requirements_pin_the_captured_versions_exactly(tmp_path):
    render_context(dest=tmp_path, payload=b"m", io_schema=IO_SCHEMA, environment=ENVIRONMENT)

    lines = (tmp_path / "requirements.txt").read_text().splitlines()

    # `==` not `>=`: the whole point is to reproduce the environment that wrote the
    # pickle, and a floating pin reintroduces exactly the skew this design removes.
    assert "scikit-learn==1.7.2" in lines
    assert "numpy==2.3.5" in lines
    assert "cloudpickle==3.1.2" in lines


def test_requirements_include_the_serving_stack_verity_controls(tmp_path):
    render_context(dest=tmp_path, payload=b"m", io_schema=IO_SCHEMA, environment=ENVIRONMENT)

    text = (tmp_path / "requirements.txt").read_text()

    for package in ("fastapi==", "uvicorn==", "pydantic=="):
        assert package in text


def test_the_dockerfile_uses_the_captured_python_version(tmp_path):
    render_context(dest=tmp_path, payload=b"m", io_schema=IO_SCHEMA, environment=ENVIRONMENT)

    assert "FROM python:3.12-slim" in (tmp_path / "Dockerfile").read_text()


def test_the_dockerfile_installs_dependencies_before_copying_the_model(tmp_path):
    render_context(dest=tmp_path, payload=b"m", io_schema=IO_SCHEMA, environment=ENVIRONMENT)

    text = (tmp_path / "Dockerfile").read_text()

    # This ordering is the entire reason a second model on the same dependency set
    # builds in seconds: Docker reuses the cached pip layer. Asserted rather than
    # assumed, because a well-meaning reorder would silently cost minutes per deploy.
    assert text.index("RUN pip install") < text.index("COPY model.pkl")


def test_the_dockerfile_runs_as_a_non_root_user(tmp_path):
    render_context(dest=tmp_path, payload=b"m", io_schema=IO_SCHEMA, environment=ENVIRONMENT)

    assert "USER appuser" in (tmp_path / "Dockerfile").read_text()


def test_the_model_bytes_are_written_untouched(tmp_path):
    render_context(dest=tmp_path, payload=b"exact-bytes", io_schema=IO_SCHEMA,
                   environment=ENVIRONMENT)

    assert (tmp_path / "model.pkl").read_bytes() == b"exact-bytes"


def test_image_tag_is_derived_from_the_model_version_id():
    assert image_tag("mv_abc123") == "verity-model:mv_abc123"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd server; uv run pytest tests/test_build_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'serving.build'`

- [ ] **Step 3: Implement `build.py`**

```python
# server/serving/build.py
"""Render the directory Docker builds a model-version image from.

Pure filesystem work: no Docker, no network, no credentials. That is what makes the
whole build path testable without a daemon.
"""

import json
import shutil
from pathlib import Path

_TEMPLATE = Path(__file__).parent / "app_template.py"

# Pinned by Verity, not by the customer: these are our serving stack, and a customer's
# environment has no opinion about them.
SERVING_REQUIREMENTS = (
    "fastapi==0.116.1",
    "uvicorn[standard]==0.35.0",
    "pydantic==2.12.5",
)

_DOCKERFILE = """FROM python:{python_version}-slim

ENV PYTHONUNBUFFERED=1 \\
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencies in their own layer, before the model: a second model on the same
# dependency set then reuses this layer and builds in seconds instead of minutes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY contract.json .
COPY model.pkl .

RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# curl isn't in the slim image, so probe /health with stdlib urllib instead.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \\
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
"""


def image_tag(model_version_id: str) -> str:
    return f"verity-model:{model_version_id}"


def render_context(*, dest, payload, io_schema, environment) -> None:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    (dest / "model.pkl").write_bytes(payload)
    (dest / "contract.json").write_text(json.dumps(io_schema))
    shutil.copyfile(_TEMPLATE, dest / "app.py")
    (dest / "requirements.txt").write_text(_render_requirements(environment))
    (dest / "Dockerfile").write_text(
        _DOCKERFILE.format(python_version=environment.get("python_version", "3.12"))
    )


def _render_requirements(environment) -> str:
    # `==` throughout: reproducing the environment that wrote the pickle is the entire
    # purpose. A floating pin reintroduces the version skew this design exists to remove.
    pins = [f"{name}=={version}" for name, version in sorted(environment.get("packages", {}).items())]
    return "\n".join([*pins, *SERVING_REQUIREMENTS]) + "\n"
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd server; uv run pytest tests/test_build_context.py -v`
Expected: 9 passed

- [ ] **Step 5: Run the full server suite**

Run: `cd server; uv run pytest -q`
Expected: 153 + 9 = 162 passed

---

### Task 7: The container runtime

**Files:**
- Create: `server/serving/runtime.py`
- Create: `server/tests/test_runtime_docker.py`
- Modify: `server/pyproject.toml`

**Interfaces:**
- Consumes: `serving.build.render_context`, `serving.build.image_tag`.
- Produces: `serving.runtime.DockerRuntime` with `build(*, context_dir, tag) -> None`, `run(*, tag) -> {"container_id": str, "host_port": int}`, `stop(*, container_id) -> None`; `serving.runtime.RuntimeError_` alias `ContainerRuntimeError`; and `serving.runtime.wait_healthy(*, url, timeout=60.0, client=None) -> bool`.

- [ ] **Step 1: Add the dependency**

In `server/pyproject.toml`, add to `dependencies`:

```toml
    # Local container runtime for api-fication. Behind serving/runtime.py's interface,
    # so a cloud runner later is a new class rather than a dependency change here.
    "docker>=7.1.0",
```

Run: `cd server; uv sync`

- [ ] **Step 2: Write the failing tests**

```python
# server/tests/test_runtime_docker.py
import pytest

from serving.runtime import ContainerRuntimeError, DockerRuntime, wait_healthy


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class FakeHttpClient:
    """Returns each queued response in turn, then repeats the last one."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        if not self.statuses:
            raise ConnectionError("refused")
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if status is None:
            raise ConnectionError("refused")
        return FakeResponse(status)


def test_wait_healthy_returns_true_as_soon_as_health_answers_ok():
    client = FakeHttpClient([200])

    assert wait_healthy(url="http://localhost:1/health", timeout=5.0, client=client) is True


def test_wait_healthy_keeps_polling_while_the_container_is_still_starting():
    # A container that refuses connections for a moment is normal, not a failure.
    client = FakeHttpClient([None, None, 200])

    assert wait_healthy(url="http://localhost:1/health", timeout=5.0, client=client) is True
    assert client.calls == 3


def test_wait_healthy_gives_up_at_the_timeout():
    client = FakeHttpClient([None])

    assert wait_healthy(url="http://localhost:1/health", timeout=0.3, client=client) is False


def test_docker_runtime_reads_the_assigned_host_port_back_after_starting():
    class FakeContainer:
        id = "container-abc"
        ports = {"8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "49312"}]}

        def reload(self):
            pass

    class FakeContainers:
        def __init__(self):
            self.run_kwargs = None

        def run(self, tag, **kwargs):
            self.run_kwargs = {"tag": tag, **kwargs}
            return FakeContainer()

    class FakeDocker:
        def __init__(self):
            self.containers = FakeContainers()

    fake = FakeDocker()
    runtime = DockerRuntime(client=fake)

    result = runtime.run(tag="verity-model:mv_1")

    assert result == {"container_id": "container-abc", "host_port": 49312}
    # Ephemeral port: Docker assigns, we read back. No port registry to drift.
    assert fake.containers.run_kwargs["ports"] == {"8000/tcp": None}
    assert fake.containers.run_kwargs["detach"] is True


def test_docker_runtime_wraps_a_build_failure_in_a_container_runtime_error():
    class FakeImages:
        def build(self, **kwargs):
            raise ValueError("dependency resolution failed")

    class FakeDocker:
        images = FakeImages()

    runtime = DockerRuntime(client=FakeDocker())

    with pytest.raises(ContainerRuntimeError) as excinfo:
        runtime.build(context_dir="/tmp/ctx", tag="verity-model:mv_1")

    assert "dependency resolution failed" in str(excinfo.value)


@pytest.mark.docker
def test_a_real_image_builds_starts_and_answers_health(tmp_path):
    """The only test that needs a Docker daemon. Skipped when one isn't reachable."""
    import numpy as np
    from sklearn.linear_model import LogisticRegression

    from serving.build import image_tag, render_context
    from verity.serialize import serialize

    model = LogisticRegression().fit(
        np.array([[0.0, 1.0], [1.0, 0.0], [0.5, 0.5], [0.2, 0.8]]), np.array([0, 1, 0, 1])
    )
    payload, _ = serialize(model)
    render_context(
        dest=tmp_path,
        payload=payload,
        io_schema={"n_features": 2, "feature_names": None, "classes": [0, 1],
                   "has_predict_proba": True},
        environment={"python_version": "3.12",
                     "packages": {"scikit-learn": "1.7.2", "numpy": "2.3.5",
                                  "cloudpickle": "3.1.2"}},
    )

    runtime = DockerRuntime()
    tag = image_tag("mv_itest")
    runtime.build(context_dir=str(tmp_path), tag=tag)
    started = runtime.run(tag=tag)
    try:
        assert wait_healthy(
            url=f"http://localhost:{started['host_port']}/health", timeout=90.0
        )
    finally:
        runtime.stop(container_id=started["container_id"])
```

- [ ] **Step 3: Register the marker**

In `server/pyproject.toml`, under `[tool.pytest.ini_options]`:

```toml
markers = [
    "docker: needs a running Docker daemon; skipped automatically when unreachable",
]
```

- [ ] **Step 4: Add the auto-skip**

Create `server/tests/conftest.py` (or extend it if one exists):

```python
import pytest


def pytest_runtest_setup(item):
    """Skip daemon-dependent tests when there is no daemon.

    Keeps the default suite offline and fast. The alternative — failing when Docker
    isn't running — would make the suite lie about the health of code that is fine.
    """
    if "docker" not in item.keywords:
        return
    try:
        import docker

        docker.from_env().ping()
    except Exception as exc:  # noqa: BLE001 - any failure to reach the daemon is a skip
        pytest.skip(f"docker daemon unavailable: {type(exc).__name__}")
```

- [ ] **Step 5: Run to verify it fails**

Run: `cd server; uv run pytest tests/test_runtime_docker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'serving.runtime'`

- [ ] **Step 6: Implement `runtime.py`**

```python
# server/serving/runtime.py
"""Where a model container actually runs.

Everything above this file talks to the three-method interface, never to Docker. An ECS
or Fargate runtime later is a new class here and one wiring change in deploy.py — that
is the entire reason this seam exists at V1, when there is only one implementation.
"""

import time


class ContainerRuntimeError(Exception):
    """The container runtime could not build, start, or stop an image."""


class DockerRuntime:
    """Local Docker, via the docker SDK."""

    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = self._real_client()
        return self._client

    @staticmethod
    def _real_client():
        try:
            import docker

            return docker.from_env()
        except Exception as exc:  # noqa: BLE001
            raise ContainerRuntimeError(f"cannot reach the Docker daemon: {exc}") from exc

    def build(self, *, context_dir, tag):
        try:
            self.client.images.build(path=str(context_dir), tag=tag, rm=True)
        except Exception as exc:  # noqa: BLE001 - the docker SDK raises several types
            raise ContainerRuntimeError(f"image build failed: {exc}") from exc

    def run(self, *, tag):
        try:
            # Ephemeral host port: Docker picks, we read it back. A fixed-port registry
            # would be one more thing that can disagree with reality.
            container = self.client.containers.run(
                tag, detach=True, ports={"8000/tcp": None}
            )
            container.reload()
            binding = container.ports["8000/tcp"][0]
            return {"container_id": container.id, "host_port": int(binding["HostPort"])}
        except Exception as exc:  # noqa: BLE001
            raise ContainerRuntimeError(f"container failed to start: {exc}") from exc

    def stop(self, *, container_id):
        try:
            self.client.containers.get(container_id).stop(timeout=10)
        except Exception as exc:  # noqa: BLE001
            raise ContainerRuntimeError(f"container failed to stop: {exc}") from exc


def wait_healthy(*, url, timeout=60.0, client=None, interval=0.5):
    """Poll /health until it answers 200 or the timeout expires.

    A refused connection is the normal state of a container that is still starting, so
    it is a reason to keep waiting rather than to fail.
    """
    if client is None:
        import httpx

        client = httpx.Client(timeout=2.0)

    deadline = time.monotonic() + timeout
    while True:
        try:
            if client.get(url, timeout=2.0).status_code == 200:
                return True
        except Exception:  # noqa: BLE001 - still starting
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)
```

- [ ] **Step 7: Run to verify it passes**

Run: `cd server; uv run pytest tests/test_runtime_docker.py -v`
Expected: 5 passed, 1 skipped (`docker daemon unavailable`)

- [ ] **Step 8: Run the full server suite**

Run: `cd server; uv run pytest -q`
Expected: 162 + 5 passed, 1 skipped

---

### Task 8: Deploy orchestration

**Files:**
- Create: `server/serving/deploy.py`
- Create: `server/tests/test_deploy.py`

**Interfaces:**
- Consumes: `serving.build.render_context`/`image_tag`, `serving.runtime.DockerRuntime`/`wait_healthy`/`ContainerRuntimeError`, the store methods from Task 4.
- Produces: `serving.deploy.deploy(*, model_version_id, payload, io_schema, environment, metadata_store, archived_model_version_id=None, runtime=None, render_fn=None, wait_healthy_fn=None, tempdir_fn=None) -> dict` returning `{"id", "status", "host_port", "endpoint_url", "image_tag"}`. Raises `DeployError` after recording a `failed` row.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_deploy.py
import pytest

from serving.deploy import DeployError, deploy

IO_SCHEMA = {"n_features": 2, "feature_names": None, "classes": [0, 1], "has_predict_proba": True}
ENVIRONMENT = {"python_version": "3.12", "packages": {"numpy": "2.3.5"}}


class FakeStore:
    def __init__(self, live=None):
        self.saved = []
        self.updates = []
        self._live = live

    def save_deployment(self, *, model_version_id, image_tag, status):
        self.saved.append({"model_version_id": model_version_id, "image_tag": image_tag,
                           "status": status})
        return f"dep_{len(self.saved)}"

    def update_deployment(self, *, deployment_id, **fields):
        self.updates.append({"deployment_id": deployment_id, **fields})

    def find_live_deployment(self, *, model_version_id):
        return self._live


class FakeRuntime:
    def __init__(self, build_error=None):
        self.built = []
        self.ran = []
        self.stopped = []
        self.build_error = build_error

    def build(self, *, context_dir, tag):
        if self.build_error:
            raise self.build_error
        self.built.append(tag)

    def run(self, *, tag):
        self.ran.append(tag)
        return {"container_id": "c_1", "host_port": 49312}

    def stop(self, *, container_id):
        self.stopped.append(container_id)


def _deploy(store, runtime, *, healthy=True, archived=None):
    return deploy(
        model_version_id="mv_1",
        payload=b"bytes",
        io_schema=IO_SCHEMA,
        environment=ENVIRONMENT,
        metadata_store=store,
        archived_model_version_id=archived,
        runtime=runtime,
        render_fn=lambda **kwargs: None,
        wait_healthy_fn=lambda **kwargs: healthy,
    )


def test_a_successful_deploy_records_building_then_live():
    store, runtime = FakeStore(), FakeRuntime()

    result = _deploy(store, runtime)

    assert store.saved[0]["status"] == "building"
    assert store.updates[0]["status"] == "live"
    assert result["status"] == "live"
    assert result["host_port"] == 49312
    assert result["endpoint_url"] == "http://localhost:49312"


def test_a_successful_deploy_builds_and_runs_the_version_tagged_image():
    store, runtime = FakeStore(), FakeRuntime()

    _deploy(store, runtime)

    assert runtime.built == ["verity-model:mv_1"]
    assert runtime.ran == ["verity-model:mv_1"]


def test_a_build_failure_records_a_failed_row_carrying_the_reason():
    store = FakeStore()
    runtime = FakeRuntime(build_error=RuntimeError("no space left on device"))

    with pytest.raises(DeployError):
        _deploy(store, runtime)

    assert store.updates[0]["status"] == "failed"
    assert "no space left on device" in store.updates[0]["error"]["message"]


def test_a_container_that_never_becomes_healthy_is_a_failed_deploy():
    store, runtime = FakeStore(), FakeRuntime()

    with pytest.raises(DeployError):
        _deploy(store, runtime, healthy=False)

    assert store.updates[0]["status"] == "failed"
    # The container was started, so it must not be left running behind a failed row.
    assert runtime.stopped == ["c_1"]


def test_promoting_a_new_version_stops_the_container_it_replaced():
    store = FakeStore(live={"id": "dep_old", "container_id": "c_old"})
    runtime = FakeRuntime()

    _deploy(store, runtime, archived="mv_0")

    assert "c_old" in runtime.stopped
    stopped_row = [u for u in store.updates if u.get("status") == "stopped"]
    assert stopped_row and stopped_row[0]["deployment_id"] == "dep_old"


def test_archival_teardown_does_not_fail_the_new_deploy_when_the_old_container_is_gone():
    class StubbornRuntime(FakeRuntime):
        def stop(self, *, container_id):
            raise RuntimeError("no such container")

    store = FakeStore(live={"id": "dep_old", "container_id": "c_old"})

    # The new version is already live and serving; failing its deploy because a
    # already-dead old container could not be stopped would be strictly worse.
    result = _deploy(store, StubbornRuntime(), archived="mv_0")

    assert result["status"] == "live"


def test_nothing_is_torn_down_when_no_version_was_archived():
    store, runtime = FakeStore(), FakeRuntime()

    _deploy(store, runtime, archived=None)

    assert runtime.stopped == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd server; uv run pytest tests/test_deploy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'serving.deploy'`

- [ ] **Step 3: Implement `deploy.py`**

```python
# server/serving/deploy.py
"""Turn a promoted model version into a running container.

Every step is recorded on the `deployment` row as it happens, so a failure leaves an
explanation behind rather than a log line. The caller (the orchestrator) treats a raised
DeployError as non-fatal — see its comment for why.
"""

import tempfile

BUILD_TIMEOUT_SECONDS = 300
HEALTH_TIMEOUT_SECONDS = 60


class DeployError(Exception):
    """The version could not be stood up. The deployment row says why."""


def deploy(
    *,
    model_version_id,
    payload,
    io_schema,
    environment,
    metadata_store,
    archived_model_version_id=None,
    runtime=None,
    render_fn=None,
    wait_healthy_fn=None,
    tempdir_fn=None,
):
    runtime = runtime or _default_runtime()
    render_fn = render_fn or _default_render
    wait_healthy_fn = wait_healthy_fn or _default_wait_healthy
    tempdir_fn = tempdir_fn or tempfile.TemporaryDirectory

    tag = _image_tag(model_version_id)
    deployment_id = metadata_store.save_deployment(
        model_version_id=model_version_id, image_tag=tag, status="building"
    )

    container_id = None
    try:
        with tempdir_fn() as context_dir:
            render_fn(
                dest=context_dir,
                payload=payload,
                io_schema=io_schema,
                environment=environment,
            )
            runtime.build(context_dir=context_dir, tag=tag)

        started = runtime.run(tag=tag)
        container_id = started["container_id"]
        endpoint_url = f"http://localhost:{started['host_port']}"

        if not wait_healthy_fn(
            url=f"{endpoint_url}/health", timeout=HEALTH_TIMEOUT_SECONDS
        ):
            raise DeployError(
                f"container did not report healthy within {HEALTH_TIMEOUT_SECONDS}s — "
                "the most likely cause is the artifact failing to load in the built image"
            )

        metadata_store.update_deployment(
            deployment_id=deployment_id,
            status="live",
            container_id=container_id,
            host_port=started["host_port"],
            endpoint_url=endpoint_url,
        )
    except Exception as exc:
        # A started-but-unhealthy container must not be left behind a `failed` row.
        if container_id is not None:
            _quietly_stop(runtime, container_id)
        metadata_store.update_deployment(
            deployment_id=deployment_id,
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        raise DeployError(str(exc)) from exc

    # Only after the replacement is confirmed live. Doing it earlier would create a
    # window with nothing serving at all.
    if archived_model_version_id:
        _tear_down(
            metadata_store=metadata_store,
            runtime=runtime,
            model_version_id=archived_model_version_id,
        )

    return {
        "id": deployment_id,
        "status": "live",
        "host_port": started["host_port"],
        "endpoint_url": endpoint_url,
        "image_tag": tag,
    }


def _tear_down(*, metadata_store, runtime, model_version_id):
    """Stop the container the newly promoted version displaced.

    Deliberately best-effort. The new version is already live and serving; failing its
    deploy because an already-dead old container could not be stopped would be strictly
    worse than leaving a stale row.
    """
    try:
        previous = metadata_store.find_live_deployment(model_version_id=model_version_id)
        if not previous:
            return
        if previous.get("container_id"):
            _quietly_stop(runtime, previous["container_id"])
        metadata_store.update_deployment(
            deployment_id=previous["id"], status="stopped"
        )
    except Exception:  # noqa: BLE001
        return


def _quietly_stop(runtime, container_id):
    try:
        runtime.stop(container_id=container_id)
    except Exception:  # noqa: BLE001
        pass


def _image_tag(model_version_id):
    from serving.build import image_tag

    return image_tag(model_version_id)


def _default_render(**kwargs):
    from serving.build import render_context

    return render_context(**kwargs)


def _default_runtime():
    from serving.runtime import DockerRuntime

    return DockerRuntime()


def _default_wait_healthy(**kwargs):
    from serving.runtime import wait_healthy

    return wait_healthy(**kwargs)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd server; uv run pytest tests/test_deploy.py -v`
Expected: 7 passed

- [ ] **Step 5: Run the full server suite**

Run: `cd server; uv run pytest -q`
Expected: 167 + 7 passed, 1 skipped

---

### Task 9: Wire introspection and deploy into the pipeline

**Files:**
- Modify: `server/orchestrator.py`
- Modify: `server/main.py`
- Modify: `server/tests/test_orchestrator.py`
- Modify: `server/tests/test_main.py`

**Interfaces:**
- Consumes: `execution.sandbox.introspect` (Task 2), `serving.deploy.deploy` (Task 8).
- Produces: `build_artifact(...)` gains `environment=None`, `introspect_fn=None`, `deploy_fn=None`; its return dict gains `"deployment"`. `/ingest` gains form field `environment`.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_orchestrator.py`, reusing that file's existing fakes:

```python
def test_the_manifest_carries_the_introspected_io_schema():
    metadata_store = FakeMetadataStore()

    build_artifact(
        payload=PAYLOAD, sha256=SHA256, user_id="u_1", name="m", args={},
        blob_store=FakeBlobStore(), metadata_store=metadata_store,
        identify_fn=lambda model: {"framework": "sklearn"},
        introspect_fn=lambda payload: {"n_features": 2, "feature_names": None,
                                       "classes": [0, 1], "has_predict_proba": True},
        find_existing_fn=lambda **kwargs: None,
        register_fn=lambda **kwargs: {"model_id": "mdl_1", "status": "pending",
                                      "archived_model_version_id": None},
    )

    assert metadata_store.manifests[0]["manifest"]["io_schema"]["n_features"] == 2


def test_the_manifest_carries_the_environment_the_client_captured():
    metadata_store = FakeMetadataStore()

    build_artifact(
        payload=PAYLOAD, sha256=SHA256, user_id="u_1", name="m", args={},
        blob_store=FakeBlobStore(), metadata_store=metadata_store,
        environment={"python_version": "3.12", "packages": {"numpy": "2.3.5"}},
        identify_fn=lambda model: {"framework": "sklearn"},
        introspect_fn=lambda payload: {},
        find_existing_fn=lambda **kwargs: None,
        register_fn=lambda **kwargs: {"model_id": "mdl_1", "status": "pending",
                                      "archived_model_version_id": None},
    )

    assert metadata_store.manifests[0]["manifest"]["environment"]["python_version"] == "3.12"


def test_a_failing_introspection_does_not_stop_the_pipeline():
    # Identification and evaluation are still worth having without a serving schema.
    # The version simply cannot be deployed, which the null io_schema records.
    metadata_store = FakeMetadataStore()

    def boom(payload):
        raise RuntimeError("unreadable artifact")

    result = build_artifact(
        payload=PAYLOAD, sha256=SHA256, user_id="u_1", name="m", args={},
        blob_store=FakeBlobStore(), metadata_store=metadata_store,
        identify_fn=lambda model: {"framework": "sklearn"},
        introspect_fn=boom,
        find_existing_fn=lambda **kwargs: None,
        register_fn=lambda **kwargs: {"model_id": "mdl_1", "status": "pending",
                                      "archived_model_version_id": None},
    )

    assert result["manifest"]["io_schema"] is None


def test_deploy_fires_when_a_version_reaches_production():
    calls = []

    result = build_artifact(
        payload=PAYLOAD, sha256=SHA256, user_id="u_1", name="m", args={},
        blob_store=FakeBlobStore(), metadata_store=FakeMetadataStore(),
        identify_fn=lambda model: {"framework": "sklearn"},
        introspect_fn=lambda payload: {"n_features": 2},
        find_existing_fn=lambda **kwargs: None,
        register_fn=lambda **kwargs: {"model_id": "mdl_1", "status": "production",
                                      "archived_model_version_id": "mv_old"},
        configure_fn=lambda **kwargs: {"id": "mcfg_1"},
        deploy_fn=lambda **kwargs: calls.append(kwargs) or {"id": "dep_1", "status": "live"},
    )

    assert result["deployment"] == {"id": "dep_1", "status": "live"}
    assert calls[0]["archived_model_version_id"] == "mv_old"


def test_deploy_does_not_fire_for_a_version_that_was_not_promoted():
    calls = []

    result = build_artifact(
        payload=PAYLOAD, sha256=SHA256, user_id="u_1", name="m", args={},
        blob_store=FakeBlobStore(), metadata_store=FakeMetadataStore(),
        identify_fn=lambda model: {"framework": "sklearn"},
        introspect_fn=lambda payload: {},
        find_existing_fn=lambda **kwargs: None,
        register_fn=lambda **kwargs: {"model_id": "mdl_1", "status": "pending",
                                      "archived_model_version_id": None},
        deploy_fn=lambda **kwargs: calls.append(kwargs),
    )

    assert calls == []
    assert result["deployment"] is None


def test_a_failing_deploy_does_not_fail_a_promotion_that_succeeded():
    def boom(**kwargs):
        raise RuntimeError("docker daemon unreachable")

    result = build_artifact(
        payload=PAYLOAD, sha256=SHA256, user_id="u_1", name="m", args={},
        blob_store=FakeBlobStore(), metadata_store=FakeMetadataStore(),
        identify_fn=lambda model: {"framework": "sklearn"},
        introspect_fn=lambda payload: {},
        find_existing_fn=lambda **kwargs: None,
        register_fn=lambda **kwargs: {"model_id": "mdl_1", "status": "production",
                                      "archived_model_version_id": None},
        configure_fn=lambda **kwargs: {"id": "mcfg_1"},
        deploy_fn=boom,
    )

    # The version genuinely IS production. Reporting the request as failed would be
    # a lie about what happened — same reasoning as _configure_monitoring.
    assert result["status"] == "production"
    assert result["deployment"] is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd server; uv run pytest tests/test_orchestrator.py -v -k "introspect or deploy or environment"`
Expected: FAIL — `TypeError: build_artifact() got an unexpected keyword argument 'introspect_fn'`

- [ ] **Step 3: Implement the orchestrator changes**

In `server/orchestrator.py`, add the parameters:

```python
    environment=None,
    introspect_fn=None,
    deploy_fn=None,
```

and the defaults alongside the existing ones:

```python
    introspect_fn = introspect_fn or _default_introspect
    deploy_fn = deploy_fn or _default_deploy
```

After `manifest = identify_fn(model)`, enrich the manifest:

```python
    # Structure is measured, not inferred. Hawkeye's LLM call answers "what kind of
    # model is this"; this answers "what does its predict() actually take", which is a
    # fact about the object and must never be guessed.
    manifest["io_schema"] = _introspect(introspect_fn=introspect_fn, payload=payload)
    manifest["environment"] = environment
```

with the helper:

```python
def _introspect(*, introspect_fn, payload):
    """Never fatal. A model whose surface can't be read is still worth identifying and
    evaluating; it simply cannot be served, which a null io_schema records exactly."""
    try:
        return introspect_fn(payload)
    except Exception:  # noqa: BLE001
        return None
```

Then after the `_configure_monitoring` block:

```python
    deployment = None
    if registration["status"] == "production":
        deployment = _deploy(
            deploy_fn=deploy_fn,
            model_version_id=model_version_id,
            payload=payload,
            manifest=manifest,
            metadata_store=metadata_store,
            archived_model_version_id=registration["archived_model_version_id"],
        )
```

```python
def _deploy(*, deploy_fn, model_version_id, payload, manifest, metadata_store,
            archived_model_version_id):
    """Stand the promoted version up, but never at the cost of the promotion itself.

    Identical contract to _configure_monitoring, for an identical reason: this runs
    AFTER the version is already `production`. A build failure must not 500 a request
    whose promotion genuinely succeeded. The caller gets a null deployment — visible
    rather than fabricated — and a `failed` row records why.
    """
    if not manifest.get("io_schema"):
        return None
    try:
        return deploy_fn(
            model_version_id=model_version_id,
            payload=payload,
            io_schema=manifest["io_schema"],
            environment=manifest.get("environment") or {},
            metadata_store=metadata_store,
            archived_model_version_id=archived_model_version_id,
        )
    except Exception:  # noqa: BLE001
        return None
```

Add `"deployment": deployment` to the returned dict, and `"deployment": None` to the dedup short-circuit return. Add the lazy defaults:

```python
def _default_introspect(payload):
    from execution.sandbox import introspect

    return introspect(model_payload=payload)


def _default_deploy(**kwargs):
    from serving.deploy import deploy

    return deploy(**kwargs)
```

- [ ] **Step 4: Accept `environment` at the route**

In `server/main.py`'s `/ingest` signature add `environment: str | None = Form(None)`, and in the call add:

```python
        environment=json.loads(environment) if environment else None,
```

- [ ] **Step 5: Add the route test**

Append to `server/tests/test_main.py`, using its existing `app.dependency_overrides` pattern:

```python
def test_ingest_forwards_the_captured_environment_to_the_orchestrator():
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
                "user_id": "u_1", "name": "m", "sha256": "abc",
                "environment": json.dumps({"python_version": "3.12", "packages": {}}),
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert captured["environment"] == {"python_version": "3.12", "packages": {}}
```

- [ ] **Step 6: Run the full server suite**

Run: `cd server; uv run pytest -q`
Expected: 174 + 7 passed, 1 skipped

---

### Task 10: The inference proxy and its non-blocking telemetry

**Files:**
- Create: `server/serving/sink.py`
- Create: `server/tests/test_proxy.py`
- Modify: `server/main.py`
- Modify: `server/storage/models/supabase.py`

**Interfaces:**
- Consumes: `find_live_deployment` (Task 4).
- Produces: route `POST /users/{user_id}/models/{name}/predict`; `serving.sink.TelemetrySink` with `record(event)`, `flush()`, `stop()`; store method `find_production_version_by_name(*, user_id, name) -> dict | None`.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_proxy.py
import json

import pytest
from fastapi.testclient import TestClient

from main import app, get_metadata_store, get_predict_transport, get_telemetry_sink


class FakeStore:
    def __init__(self, version=None, deployment=None):
        self.version = version
        self.deployment = deployment

    def find_production_version_by_name(self, *, user_id, name):
        return self.version

    def find_live_deployment(self, *, model_version_id):
        return self.deployment


class FakeSink:
    def __init__(self, explode=False):
        self.events = []
        self.explode = explode

    def record(self, event):
        if self.explode:
            raise RuntimeError("sink is broken")
        self.events.append(event)


class FakeTransport:
    def __init__(self, status_code=200, payload=None, error=None):
        self.status_code = status_code
        self.payload = payload or {"predictions": [1], "probabilities": None}
        self.error = error
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json})
        if self.error:
            raise self.error

        class Response:
            status_code = self.status_code
            def json(_self):
                return self.payload

        return Response()


def _override(store, sink, transport):
    app.dependency_overrides[get_metadata_store] = lambda: store
    app.dependency_overrides[get_telemetry_sink] = lambda: sink
    app.dependency_overrides[get_predict_transport] = lambda: transport
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


LIVE = {"id": "dep_1", "host_port": 49312, "endpoint_url": "http://localhost:49312"}
VERSION = {"id": "mv_1"}


def test_predict_forwards_the_body_to_the_live_container():
    transport = FakeTransport()
    client = _override(FakeStore(VERSION, LIVE), FakeSink(), transport)

    body = client.post("/users/u_1/models/fraud/predict", json={"instances": [[1.0, 2.0]]}).json()

    assert body["predictions"] == [1]
    assert transport.calls[0]["url"] == "http://localhost:49312/predict"
    assert transport.calls[0]["json"] == {"instances": [[1.0, 2.0]]}


def test_predict_404s_distinctly_when_the_model_name_is_unknown():
    client = _override(FakeStore(None, None), FakeSink(), FakeTransport())

    response = client.post("/users/u_1/models/nope/predict", json={"instances": [[1.0]]})

    assert response.status_code == 404
    assert "no production version" in response.json()["detail"].lower()


def test_predict_404s_distinctly_when_the_version_is_promoted_but_not_deployed():
    client = _override(FakeStore(VERSION, None), FakeSink(), FakeTransport())

    response = client.post("/users/u_1/models/fraud/predict", json={"instances": [[1.0]]})

    assert response.status_code == 404
    # A single undifferentiated 404 would conflate "wrong name" with "deploy failed",
    # which are opposite problems with opposite fixes.
    assert "not deployed" in response.json()["detail"].lower()


def test_a_successful_prediction_is_recorded_as_telemetry():
    sink = FakeSink()
    client = _override(FakeStore(VERSION, LIVE), sink, FakeTransport())

    client.post("/users/u_1/models/fraud/predict", json={"instances": [[1.0, 2.0]]})

    event = sink.events[0]
    assert event["model_version_id"] == "mv_1"
    assert event["status"] == "ok"
    assert event["latency_ms"] > 0
    # The whole reason api-fication unblocks drift: Verity finally sees the inputs.
    assert event["inputs"] == {"instances": [[1.0, 2.0]]}
    assert event["prediction"] == {"predictions": [1], "probabilities": None}


def test_a_failing_prediction_is_recorded_as_an_error_event():
    sink = FakeSink()
    transport = FakeTransport(error=ConnectionError("container is gone"))
    client = _override(FakeStore(VERSION, LIVE), sink, transport)

    response = client.post("/users/u_1/models/fraud/predict", json={"instances": [[1.0]]})

    assert response.status_code == 502
    assert sink.events[0]["status"] == "error"
    assert sink.events[0]["error_type"] == "ConnectionError"


def test_a_broken_telemetry_sink_cannot_break_a_working_prediction():
    client = _override(FakeStore(VERSION, LIVE), FakeSink(explode=True), FakeTransport())

    # Falcon's governing rule, applied to the proxy: monitoring must never be why
    # inference fails.
    response = client.post("/users/u_1/models/fraud/predict", json={"instances": [[1.0]]})

    assert response.status_code == 200
    assert response.json()["predictions"] == [1]
```

```python
# appended to server/tests/test_proxy.py
from serving.sink import TelemetrySink


class RecordingStore:
    def __init__(self):
        self.batches = []

    def save_telemetry_events(self, *, events):
        self.batches.append(events)
        return len(events)


def test_the_sink_drops_rather_than_blocking_when_its_queue_is_full():
    sink = TelemetrySink(metadata_store=RecordingStore(), maxsize=2, flush_interval=3600)

    for _ in range(5):
        sink.record({"model_version_id": "mv_1"})

    # Non-blocking by construction: an overwhelmed monitoring path degrades itself,
    # never the request path it is watching.
    assert sink.dropped == 3


def test_the_sink_writes_queued_events_on_flush():
    store = RecordingStore()
    sink = TelemetrySink(metadata_store=store, maxsize=10, flush_interval=3600)
    sink.record({"model_version_id": "mv_1"})
    sink.record({"model_version_id": "mv_2"})

    sink.flush()

    assert len(store.batches[0]) == 2


def test_a_store_that_raises_on_flush_does_not_propagate():
    class BrokenStore:
        def save_telemetry_events(self, *, events):
            raise RuntimeError("database down")

    sink = TelemetrySink(metadata_store=BrokenStore(), maxsize=10, flush_interval=3600)
    sink.record({"model_version_id": "mv_1"})

    sink.flush()  # must not raise
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd server; uv run pytest tests/test_proxy.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_telemetry_sink' from 'main'`

- [ ] **Step 3: Implement the sink**

```python
# server/serving/sink.py
"""Server-side telemetry buffer for the inference proxy.

Mirrors the SDK reporter's contract deliberately: a request path must never wait on a
monitoring write. Recording synchronously would put a database round trip in front of
every prediction, which is the exact cost the SDK already refuses to pay.
"""

import queue
import threading


class TelemetrySink:
    def __init__(self, metadata_store, maxsize=10_000, flush_interval=5.0):
        self.metadata_store = metadata_store
        self.queue = queue.Queue(maxsize=maxsize)
        self.flush_interval = flush_interval
        self.dropped = 0
        self._dropped_lock = threading.Lock()
        self._stopping = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def record(self, event):
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            with self._dropped_lock:
                self.dropped += 1

    def flush(self):
        events = []
        while True:
            try:
                events.append(self.queue.get_nowait())
            except queue.Empty:
                break
        if not events:
            return 0
        try:
            return self.metadata_store.save_telemetry_events(events=events)
        except Exception:  # noqa: BLE001 - a failed telemetry write is not an incident
            return 0

    def stop(self):
        self._stopping.set()
        if self._thread is not None:
            self._thread.join(timeout=self.flush_interval + 1)
            self._thread = None
        self.flush()

    def _loop(self):
        while not self._stopping.wait(self.flush_interval):
            self.flush()
```

- [ ] **Step 4: Add the store lookup**

In `server/storage/models/supabase.py`:

```python
    def find_production_version_by_name(self, *, user_id, name):
        model = self.find_model(user_id=user_id, name=name)
        if model is None:
            return None
        return self.find_production_version(model_id=model["id"])
```

- [ ] **Step 5: Add the route and its dependencies**

In `server/main.py`:

```python
@lru_cache
def get_telemetry_sink():
    sink = TelemetrySink(metadata_store=get_metadata_store())
    sink.start()
    return sink


@lru_cache
def get_predict_transport():
    import httpx

    return httpx.Client(timeout=30.0)


@app.post("/users/{user_id}/models/{name}/predict")
async def predict(
    user_id: str,
    name: str,
    body: dict,
    metadata_store=Depends(get_metadata_store),
    sink=Depends(get_telemetry_sink),
    transport=Depends(get_predict_transport),
):
    # user_id is in the path because model names are unique per user, not globally
    # (Schemas.md: UNIQUE (user_id, name)), and no auth exists yet to supply it. At V1.5
    # the API key identifies the org and this collapses to /models/{name}/predict.
    version = metadata_store.find_production_version_by_name(user_id=user_id, name=name)
    if version is None:
        raise HTTPException(404, f"no production version of {name!r} for user {user_id!r}")

    deployment = metadata_store.find_live_deployment(model_version_id=version["id"])
    if deployment is None:
        # Distinct from the 404 above on purpose: "promoted but not deployed" and
        # "no such model" are opposite problems with opposite fixes.
        raise HTTPException(404, f"{name!r} is promoted but not deployed — check its deployment row")

    started = time.perf_counter()
    try:
        response = transport.post(f"{deployment['endpoint_url']}/predict", json=body, timeout=30.0)
        prediction = response.json()
    except Exception as exc:  # noqa: BLE001
        _record(sink, version["id"], started, body, None, exc)
        raise HTTPException(502, f"model container did not answer: {type(exc).__name__}")

    _record(sink, version["id"], started, body, prediction, None)
    return prediction


def _record(sink, model_version_id, started, inputs, prediction, exc):
    """Telemetry can never be the reason a prediction fails — Falcon's rule, applied here."""
    try:
        sink.record(
            {
                "model_version_id": model_version_id,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "status": "ok" if exc is None else "error",
                "latency_ms": (time.perf_counter() - started) * 1000,
                "inputs": inputs,
                "prediction": prediction,
                "error_type": type(exc).__name__ if exc is not None else None,
            }
        )
    except Exception:  # noqa: BLE001
        pass
```

Add `import time`, `from fastapi import HTTPException`, and `from serving.sink import TelemetrySink` to the imports.

- [ ] **Step 6: Run to verify it passes**

Run: `cd server; uv run pytest tests/test_proxy.py -v`
Expected: 9 passed

- [ ] **Step 7: Run the full server suite**

Run: `cd server; uv run pytest -q`
Expected: 181 + 9 passed, 1 skipped

---

### Task 11: Documentation and live verification

**Files:**
- Modify: `README.md`, `docs/Schemas.md`, `docs/architecture.md`, `docs/progression.md`
- Modify: `client/src/lib/verity.ts`, `client/src/app/page.tsx` (surface the deployment URL)

- [ ] **Step 1: Start Docker Desktop and confirm the daemon is reachable**

Run: `docker version --format "{{.Server.Version}}"`
Expected: a version string, not a pipe error.

- [ ] **Step 2: Run the previously-skipped integration test**

Run: `cd server; uv run pytest tests/test_runtime_docker.py -v -m docker`
Expected: 1 passed — a real image builds, starts, and answers `/health`.

- [ ] **Step 3: Live end-to-end**

```powershell
cd server; uv run uvicorn main:app --port 8000     # one shell
verity --demo --user-id u_1 --name served-demo     # another
```

Confirm in the response: `status: production`, a non-null `deployment` with `status: live` and a `host_port`, and `manifest.io_schema` populated.

- [ ] **Step 4: Call the served model**

```powershell
curl -X POST http://localhost:8000/users/u_1/models/served-demo/predict `
  -H "Content-Type: application/json" `
  -d '{\"instances\": [[0.0, 1.0]]}'
```

Expected: `{"predictions": [...], "probabilities": [[...]]}`.

- [ ] **Step 5: Confirm the telemetry the proxy wrote**

`GET http://localhost:8000/models/{mv_id}/telemetry` — request count reflects the calls just made, and confirm in Supabase that `inputs` and `prediction` are **non-null for the first time in the project's history**.

- [ ] **Step 6: Confirm archival teardown**

Upload a second version under the same name. Confirm the first version's container is stopped (`docker ps` no longer lists it), its `deployment` row reads `stopped`, and the new one is `live`.

- [ ] **Step 7: Confirm the non-fatal contract**

Stop Docker Desktop, upload a third version, and confirm the response still reports `status: production` with `deployment: null`, and that a `failed` deployment row records the daemon error. **This is the single most important verification in the plan** — it proves a serving failure cannot corrupt a promotion.

- [ ] **Step 8: Update the docs**

- `docs/Schemas.md` — flip `manifest.io_schema`, `environment`, `serving_pattern` and the whole `deployment` table from ⬜ to ✅, naming the migrations.
- `docs/architecture.md` — new section on serving between Falcon and the frontend; add the proxy route to §13's repo map and §1's route list; extend §11's trace with the deploy step and a proxied prediction.
- `README.md` — move api-fication from "designed, not built" to built, in Current status.
- `docs/progression.md` — entry 9: what shipped, what the live run proved, and every gap that remains.

- [ ] **Step 9: Full suite, both projects**

Run: `cd server; uv run pytest -q` then `cd verity; uv run pytest -q`
Expected: 190 server (1 skipped only if Docker is stopped again), 44 SDK.

---

## Self-Review

**Spec coverage:** every section of the spec maps to a task — environment capture (1), introspection (2), schema (3–4), app template (5), build context (6), runtime (7), deploy + archival teardown (8), orchestrator wiring (9), proxy + non-blocking telemetry (10), docs + verification (11). The spec's three named failure modes (build failure, never-healthy, runtime unavailable) are each covered by a test in Tasks 8 and 9.

**Type consistency:** `io_schema` keys (`n_features`, `feature_names`, `classes`, `has_predict_proba`) are identical in Tasks 2, 5, 6, 8, 9. `environment` keys (`python_version`, `packages`) are identical in Tasks 1, 6, 8, 9. `deploy()`'s signature in Task 8 matches its call site in Task 9. Store method names in Task 4 match their uses in Tasks 8 and 10.

**Known gap carried from the spec, not a plan defect:** `deploy` is skipped when `io_schema` is null (Task 9), so a model whose surface cannot be introspected is promoted but never served. That is the correct behaviour — serving a model whose input contract is unknown would mean guessing it — and the null `io_schema` on the manifest records exactly why.
