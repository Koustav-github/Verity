# api-fication — serving a promoted version

## Context

`docs/progression.md` entries 5–7 record where the loop stands: one `/ingest` call identifies a
model, evaluates it, registers it, promotes it to `production` on a passing verdict, and
switches monitoring on. Then it stops. **Nothing serves the model.** `production` today means a
row with a status, not a process answering requests.

That gap costs more than one missing feature. It is why `telemetry_event.inputs` and
`.prediction` are permanently null — the SDK reports latency and status from the customer's
process and has no reason to ship payloads back — and therefore why drift detection is not
merely unbuilt but *unbuildable*: Verity has never seen a production input. It is also why
Falcon's reference numbers carry `"basis": "sandbox_feasibility"` and compare against nothing;
the only latency Verity has ever measured came from a cold single-client sandbox.

This spec closes that gap for one model class.

### Settled decisions

| Decision | Choice |
|---|---|
| Model class | **Tabular / classical ML only** — scikit-learn, XGBoost, LightGBM. Finished end to end before a second class starts |
| Serving shape | **One container image per promoted model version** |
| Where containers run | **Local Docker**, behind a `ContainerRuntime` interface so a cloud runner is a new class, not a rewrite |
| Trigger | **Automatic on promotion**, in the same request, non-fatal on failure — the same contract Falcon already has |
| Public surface | Verity **proxies** `/predict`; the container's port is never exposed |
| `staging` | Stays unreachable. Auto-deploy means no state exists where a version has passed and waits for a human |

## Approaches considered

**A — One generic image, artifact fetched at runtime.** Build `verity-serve:base` once, run it
per version with an `ARTIFACT_URI` env var. Deploys in seconds. Rejected on two counts: one
image's scikit-learn would have to load every customer's pickle, and version skew is *the*
failure mode for pickled estimators; and it puts S3 credentials inside the container running
customer code, contradicting the credential allowlist in `server/execution/sandbox.py`.

**B — Per-version image built from a static template. Chosen.** The image *is* the training
environment, so skew is solved by construction rather than hoped away. The artifact is copied
into the build context, so the container holds its own model and needs **zero credentials**.
Cost is build time, which Docker's layer cache absorbs: a second model on the same dependency
set reuses the `pip install` layer.

**C — Image keyed by a dependency-set hash, artifact fetched at runtime.** Fewer builds than B,
still skew-correct. Rejected: it re-introduces credentials in the container and adds an
image-cache index, while Docker's layer cache already delivers most of the saving under B.

## Architecture

```
verity.assemble(model, name=..., X_test=..., y_test=...)
   └ environment.capture()   ← the training env, read client-side
        │  multipart: artifact + fixture + fixture_descriptor + environment
        ▼
POST /ingest → build_artifact()
   ├─ identify_fn(model)                    [Hawkeye, LLM]  → framework, task_type
   ├─ introspect_fn(payload)                [sandbox]       → io_schema
   ├─ save_model_version / save_manifest(… io_schema, environment)
   ├─ evaluate_fn(...)                      [Nat]           → eval_run
   ├─ register_fn(...)                      [Fury]          → status, archived_model_version_id
   ├─ if status == production:
   │     ├─ configure_fn(...)               [Falcon]        → monitoring_config
   │     └─ deploy_fn(...)                                  → deployment       ← NEW
   └─ return {..., deployment: {...}}

POST /users/{user_id}/models/{name}/predict                                    ← NEW
   ├─ find live deployment for the production version of (user_id, name)
   ├─ forward to http://localhost:{host_port}/predict
   ├─ enqueue telemetry_event (non-blocking)
   └─ return the container's response
```

## Components

Each is a separate module with one job, collaborators injected, following the
`param=None → param or _default_param → deferred import` idiom used throughout the codebase.

### 1. `verity/src/verity/environment.py` — capture, client-side

```python
def capture() -> dict:
    """{"python_version": "3.12", "packages": {"scikit-learn": "1.7.2", ...}}"""
```

Walks a fixed allowlist — `scikit-learn`, `numpy`, `scipy`, `pandas`, `xgboost`, `lightgbm`,
`cloudpickle` — recording the version of each that is importable and skipping the rest.

**This must be client-side and nowhere else.** Introspecting on the server would report the
*server's* versions, which is exactly the wrong answer for a pickle written somewhere else.
`cloudpickle` is in the list for the same reason as the frameworks: the container is what
unpickles the artifact, and cloudpickle's format is not guaranteed stable across majors.

Travels as a new multipart form field `environment` (JSON), alongside the existing
`fixture_descriptor`.

### 2. `server/execution/` — introspection in the sandbox

`runner.py` gains a `mode` key in its job dict, defaulting to `"predict"` so existing callers
are unaffected. `mode="introspect"` loads the artifact and returns its surface:

```python
{"estimator_class": str, "n_features": int | None, "feature_names": list[str] | None,
 "classes": list | None, "has_predict_proba": bool}
```

`sandbox.py` gains `introspect(*, model_payload, timeout=30)` beside `execute()`, reusing the
same scrubbed-env subprocess plumbing rather than duplicating it.

This runs **at ingest, not at deploy** — right after Hawkeye, merged into the manifest as
`io_schema`. Structure is measured; only semantics need a language model. That split is the
codebase's own rule (`docs/architecture.md` §14, idiom 3 — *never guess what can be measured instead*),
and it makes `manifest.io_schema` real rather than reserved. Cost is one extra subprocess per
ingest, on a path that already spawns one.

### 3. `server/serving/app_template.py` — a real file, not codegen

A normal, unit-testable FastAPI app checked into the repo. It reads `contract.json` and
`model.pkl` from its own directory at import:

| Route | Behaviour |
|---|---|
| `GET /health` | `{"status": "ok"}` |
| `POST /predict` | `{"instances": [...]}` → `{"predictions": [...], "probabilities": [[...]] \| null}` |

An instance is an **object keyed by feature name** when `feature_names` is known (scikit-learn
sets `feature_names_in_` whenever the estimator was fit on a DataFrame), and a **positional
array** otherwise, validated on length. This is the demo service's hardcoded `FEATURE_ORDER`
guard in `demo/serve/app.py`, derived instead of typed.

Loading happens at import, exactly as the demo does. A pickle that cannot load in the built
environment means the container never reports healthy — the deploy fails loudly instead of
failing on a customer's first request.

Nothing is string-templated except `requirements.txt` and the `FROM python:{version}-slim` line.

### 4. `server/serving/build.py` — the build context

```python
def render_context(*, dest, payload, io_schema, environment) -> None
```

Writes a temp directory containing `Dockerfile`, `requirements.txt`, `app.py` (copied from the
template), `contract.json`, `model.pkl`. Pure filesystem work, no Docker, so it is testable
without a daemon.

`requirements.txt` is the captured environment pinned exactly (`scikit-learn==1.7.2`), plus
Verity's own serving deps pinned by Verity (`fastapi`, `uvicorn`, `pydantic`). Dependencies are
copied and installed **before** the app and model, so a second model on the same dependency set
reuses the cached `pip install` layer — this ordering is what makes warm builds seconds instead
of minutes.

### 5. `server/serving/runtime.py` — the swappable seam

```python
class ContainerRuntime(Protocol):
    def build(self, *, context_dir: str, tag: str) -> None: ...
    def run(self, *, tag: str) -> dict:  ...   # {"container_id", "host_port"}
    def stop(self, *, container_id: str) -> None: ...
```

`DockerRuntime` is the only implementation, over the `docker` Python SDK. Ports are **ephemeral**
— `ports={"8000/tcp": None}`, read back after start — so there is no port registry to keep
consistent with reality.

Everything above this file talks to the protocol. An ECS or Fargate runtime later is a new
class and one wiring change.

### 6. `server/serving/deploy.py` — orchestration

```python
def deploy(*, model_version_id, payload, io_schema, environment, metadata_store,
           runtime=None, render_fn=None, wait_healthy_fn=None) -> dict
```

1. Insert a `deployment` row with status `building`.
2. Render the context; build the image tagged `verity-model:{model_version_id}`.
3. Run it; poll `GET /health` until ok or a 60s timeout.
4. Update the row to `live` with `container_id`, `host_port`, `endpoint_url`.

Any failure updates the row to `failed` with a structured `error` and raises — the orchestrator
catches it. Timeouts: 300s for build, 60s for health.

**Archival teardown.** Fury already returns `archived_model_version_id`. When it is non-null,
deploy looks up that version's live deployment, stops the container, and marks the row
`stopped`. "The live model is the latest one" becomes true at the process level, not just in a
status column.

### 7. `server/main.py` — the proxy

```
POST /users/{user_id}/models/{name}/predict
```

`user_id` is in the path because there is no auth yet and `model.name` is unique *per user*, not
globally (`UNIQUE (user_id, name)` in `docs/Schemas.md`). A bare `/models/{name}/predict` would
be genuinely ambiguous. At V1.5 the API key identifies the org and this collapses to
`/models/{name}/predict` — the clunky segment is temporary and load-bearing, not decoration.

The proxy resolves `(user_id, name)` → production version → live deployment, forwards the body,
and returns the container's response. 404 when no live deployment exists, with a message that
distinguishes *no such model*, *no production version*, and *promoted but not deployed* — three
different problems that a single 404 would conflate.

**Telemetry from the proxy is non-blocking.** It records into a bounded in-process queue drained
by a background task, mirroring the SDK reporter's contract in `docs/architecture.md` §8.4: a
full queue drops and counts, the write never happens inline, and a telemetry failure can never
turn a successful prediction into an error. Recording it synchronously would add database
latency to every prediction and violate the rule the SDK already follows.

This is where `inputs` and `prediction` finally get written.

### 8. Schema changes

**New table `deployment`** — specced in `docs/Schemas.md`, created here.

**`manifest` gains three columns**:

| Column | Type | Why |
|---|---|---|
| `io_schema` | jsonb | the introspected surface — already specced and marked ⬜; this makes it real |
| `serving_pattern` | text | set to `container` on deploy — likewise already specced |
| `environment` | jsonb | the SDK's captured training environment. **An extension over the original spec**, recorded in `docs/Schemas.md` for the same reason `eval_run.fixture` was: without it, `io_schema` describes a model whose runtime requirements are unrecorded, and the image cannot be built reproducibly from stored rows alone |

It goes on `manifest` rather than `model_version` because it describes *the artifact as
identified*, which is what the manifest is for, and because a redeploy must be able to rebuild
the identical image from rows alone without the original upload still being in memory.

**`model_version` gains nothing.**

## Error handling

Deploy failure is **non-fatal by design**, inheriting Falcon's contract and for the identical
reason: deploy runs *after* the version is already `production`, so raising would report a
promotion as failed when it genuinely succeeded. The orchestrator wraps `deploy_fn` in the same
`try/except` shape as `_configure_monitoring`, the response carries `deployment: null`, and a
`failed` row records why.

The failures worth naming separately in `deployment.error`, because they need different fixes:

| Failure | Cause | Signal |
|---|---|---|
| image build failed | dependency resolution, no network, no daemon | build step raises |
| container never healthy | the pickle does not load in the built environment | health timeout |
| runtime unavailable | Docker not running on the host | `DockerRuntime` construction raises |

## Testing

Existing conventions: hand-written fakes, zero `unittest.mock`, full-sentence test names.

| File | Covers |
|---|---|
| `verity/tests/test_environment.py` | captures importable framework versions and the Python version; skips absent ones without raising |
| `server/tests/test_introspect.py` | real subprocess, real estimator — `feature_names` present when fit on a DataFrame, `None` when fit on an array; `has_predict_proba` correct both ways |
| `server/tests/test_serving_app.py` | the template app driven directly via `TestClient` against a tmp `contract.json` + pickle: named instances, positional instances, wrong arity → 422, unknown feature name → 422, `probabilities` null when the estimator has no `predict_proba` |
| `server/tests/test_build_context.py` | rendered `requirements.txt` pins exactly what was captured; the Dockerfile installs dependencies before copying the model (the layer-cache property, asserted rather than assumed) |
| `server/tests/test_deploy.py` | fake `ContainerRuntime`: success writes `building` then `live`; a raising build writes `failed` with the error; a health timeout writes `failed`; archival stops the outgoing container |
| `server/tests/test_proxy.py` | fake runtime + fake transport: routes to the right port, the three distinct 404s, telemetry recorded on success *and* on model error, and a raising telemetry sink does not fail the prediction |
| `server/tests/test_supabase.py` | extended — `save_deployment`, `update_deployment_status`, `find_live_deployment` |
| `server/tests/test_orchestrator.py` | extended — deploy fires only at `production`; a raising `deploy_fn` yields `deployment: null` and does not fail the request |

One test needs a real daemon: `server/tests/test_runtime_docker.py` builds and runs an actual
image. It is marked `@pytest.mark.docker` and skipped when no daemon is reachable, so the
default suite stays offline and fast — the same treatment `test_sandbox.py` justifies for its
own real-subprocess test.

## Accepted risks, named not solved

- **A cold first build makes `/ingest` a 2–4 minute request.** Warm builds are seconds. Accepted
  rather than going async, because every stage of this pipeline is synchronous and the job queue
  is a V3 decision that should be made once for all four agents, not smuggled in here. This is
  the strongest argument yet for that queue.
- **No auth on `/predict`**, consistent with the standing V1.5 ruling: the whole app has none,
  and `/ingest` is equally open while accepting arbitrary pickles. Securing one route while that
  stands would be theatre.
- **Single replica, no autoscaling, no restart policy.** A container that dies stays dead until
  the next promotion.
- **Deployments are bound to the host running Verity.** Local Docker means they do not survive a
  machine going away. That is what the runtime interface exists to fix later.
- **Every request's inputs are written.** The sampling-rate and PII questions
  `docs/Schemas.md` defers are still deferred; at V1 volumes, writing all of them is the simpler
  correct thing, and the column was always specced as sampled *or* complete.
- **The generated schema promises names and arity, not meaning.** `manifest.declared_overrides`
  — where "column three is `Sex`, `0 = male`" would live — is still unbuilt, so nothing carries a
  human-declared semantic into the served API. Recorded as an open question in `docs/Schemas.md`.

## Out of scope

Drift and data-quality metrics — the next spec, and the reason this one comes first. ECR,
Fargate, or any cloud runner. Autoscaling, multi-replica, GPU. Batch or async inference. Model
warm pools. Any non-tabular framework. Auth. Rebasing Falcon's `eval_reference` onto real
production latency — now possible, but a separate change once there is production latency to
rebase onto.
