# MLflow — Full Architecture & Workflow

> Source analysed: `e:\Projects\AI\ML\mlflow`, branch `master`, version `3.15.2.dev0`,
> HEAD `e6a3c7130`. All file references are relative to the repo root.

---

## 1. The one-paragraph version

MLflow is a **four-layer system**. User code calls a *fluent/client API*
(`mlflow.log_metric(...)`). That API resolves a **tracking URI** into a concrete
**store implementation** via a scheme-based registry. The store either writes
locally (files / SQLAlchemy) or serialises the call into a **protobuf REST
request** to a tracking server. The server unpacks the protobuf, runs the same
store code server-side, and persists to a database + an **artifact repository**
(S3/GCS/Azure/local/…), which is itself a second scheme-based registry. Every
subsystem — tracking, model registry, artifacts, deployments, tracing, flavors —
is built on this same *"registry → abstract store → concrete backend"* shape.

```
┌──────────────────────────────────────────────────────────────────┐
│  User code    mlflow.log_metric() · @mlflow.trace · log_model()   │
├──────────────────────────────────────────────────────────────────┤
│  Fluent API           mlflow/tracking/fluent.py  (global state)   │
│  Client API           mlflow/tracking/client.py  (explicit ids)   │
├──────────────────────────────────────────────────────────────────┤
│  Registries      TrackingStoreRegistry · ModelRegistryStoreRegistry
│                  ArtifactRepositoryRegistry · flavor/plugin entrypoints
├──────────────────────────────────────────────────────────────────┤
│  AbstractStore   FileStore │ SqlAlchemyStore │ RestStore │ UC/Databricks
├──────────────────────────────────────────────────────────────────┤
│  Backends        ./mlruns │ SQLite/MySQL/PG/MSSQL │ HTTP server    │
│  Artifacts       local │ s3 │ gs │ wasbs │ abfss │ dbfs │ hdfs │ …  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. URI resolution — how MLflow decides *where* data goes

This is the single most important mechanism in the codebase. Everything else
hangs off it.

### 2.1 Tracking URI

`mlflow/tracking/_tracking_service/utils.py`

Resolution order in [`get_tracking_uri()`](mlflow/tracking/_tracking_service/utils.py#L147):

1. The process-global `_tracking_uri` (set by `mlflow.set_tracking_uri()`).
2. The `MLFLOW_TRACKING_URI` environment variable.
3. A computed default from `_get_default_tracking_uri()`:
   - if `./mlruns` already contains experiment data (a directory named with digits
     containing a `meta.yaml`), fall back to the **legacy file store** for backward
     compatibility;
   - otherwise `sqlite:///mlflow.db` (`DEFAULT_TRACKING_URI`, defined in
     `mlflow/store/tracking/__init__.py`).

Note the side effect in [`set_tracking_uri()`](mlflow/tracking/_tracking_service/utils.py#L73):
it also writes the env var (so subprocesses inherit it) **and** calls
`mlflow.tracing.provider.reset()`, because the tracer provider caches the trace
export destination derived from the tracking URI.

### 2.2 Scheme → store builder

`TrackingStoreRegistry` maps a URI *scheme* to a factory:

| Scheme | Builder | Concrete store |
|---|---|---|
| `""`, `file` | `_get_file_store` | `FileStore` |
| `sqlite`/`mysql`/`postgresql`/`mssql` | `_get_sqlalchemy_store` | `SqlAlchemyStore`, or `WorkspaceAwareSqlAlchemyStore` when `MLFLOW_ENABLE_WORKSPACES` is on |
| `http`, `https` | `_get_rest_store` | `RestStore` |
| `databricks` | `_get_databricks_rest_store` | `DatabricksTracingRestStore` |
| `databricks-uc`, `uc` | `_get_databricks_uc_rest_store` | *raises* — UC is a **registry** URI, not a tracking URI |
| anything else | `register_entrypoints()` | third-party plugins via the `mlflow.tracking_store` entry-point group |

The registry caches built stores with `@lru_cache(maxsize=100)` on
`_get_store_with_resolved_uri`, keyed on the **resolved** URI (never `None`,
because the meaning of `None` changes with the environment).

### 2.3 Artifact URI

An entirely parallel mechanism lives in
`mlflow/store/artifact/artifact_repository_registry.py`. Registered schemes:
`""`/`file`, `s3`, `r2`, `b2`, `gs`, `wasbs`, `abfss`, `ftp`, `sftp`, `dbfs`,
`hdfs`/`viewfs`, `http(s)`, `mlflow-artifacts`, plus two *indirection* schemes:

- **`runs:/<run_id>/<path>`** → `RunsArtifactRepository` — asks the tracking store
  for the run's `artifact_uri`, then delegates to the real repository.
- **`models:/<name>/<version|alias>`** → `ModelsArtifactRepository` — asks the
  model registry for the version's `source`, then delegates.

This is why `mlflow.pyfunc.load_model("models:/fraud@champion")` works against
S3, DBFS or a local path with no code change at the call site.

### 2.4 Model registry URI

`mlflow/tracking/_model_registry/utils.py` has its own registry, and it is
**deliberately separate from tracking**: you commonly run tracking against a
local server while the registry points at `databricks-uc`.

---

## 3. Experiment & run lifecycle

### 3.1 `start_run` → `end_run`

`mlflow/tracking/fluent.py`

```python
with mlflow.start_run(run_name="trial-7") as run:   # ActiveRun (subclass of Run)
    mlflow.log_param("lr", 0.01)
    mlflow.log_metric("loss", 0.3, step=1)
```

Mechanics:

1. `start_run()` resolves the experiment id: explicit arg → `MLFLOW_EXPERIMENT_ID`
   / `MLFLOW_EXPERIMENT_NAME` env → active experiment set by
   `mlflow.set_experiment()` → default experiment (`0`).
2. It pushes an `ActiveRun` onto a **thread-local run stack**, which is what makes
   nested runs (`nested=True`) work; the parent run id is recorded as the
   `mlflow.parentRunId` tag.
3. Context providers (`mlflow/tracking/context/`) auto-attach tags: git commit,
   source file, user, notebook path, Databricks job/cluster ids.
4. `ActiveRun.__exit__` calls `end_run()` with status `FINISHED`, or `FAILED` if an
   exception propagated. `_safe_end_run` is also wired to `atexit`.

`ActiveRun` is defined at `mlflow/tracking/fluent.py:374`.

### 3.2 Synchronous vs. asynchronous logging

Every `log_metric`/`log_param`/`set_tag` accepts `synchronous=None|True|False`.
When asynchronous, the call goes to `AbstractStore._async_logging_queue`
(`mlflow/utils/async_logging/async_logging_queue.py`):

```
log_metric(async) → AsyncLoggingQueue.log_batch_async()
                      └─ RunBatch pushed to a bounded-ish Queue
                         └─ "MLflowAsyncLoggingLoop" consumer thread
                            └─ _fetch_batch_from_queue()  ← coalesces batches
                               by run_id, ≤1000 items / ≤100 params / ≤100 tags
                               └─ worker ThreadPoolExecutor → store.log_batch()
```

`mlflow.flush_async_logging()` drains it; an `atexit` hook drains it on exit.
The caller gets a `RunOperations` handle wrapping the futures.

**Batching matters**: without it, a training loop logging per-step metrics would
issue one HTTP round-trip per metric.

### 3.3 Search

`mlflow/utils/search_utils.py` (~2900 lines) is a hand-rolled SQL-subset parser
built on `sqlparse`. It has two execution modes:

- **In-memory** (`SearchUtils.filter` / `.sort` / `.paginate`) — used by `FileStore`
  and by the auth plugin's post-filtering.
- **Translated to SQLAlchemy** — `SqlAlchemyStore` converts the parsed clauses into
  joins against `SqlLatestMetric`, `SqlParam`, `SqlTag` etc., with dialect-specific
  comparison functions for MSSQL/MySQL/SQLite/Postgres (`get_sql_comparison_func`).

Pagination is **offset-based**, encoded as base64 JSON `{"offset": N}`
(`create_page_token` / `parse_start_offset_from_page_token`).

Subclasses specialise the grammar: `SearchExperimentsUtils`,
`SearchTraceUtils`, `SearchLoggedModelsPaginationToken`, and model-registry
variants.

---

## 4. Storage backends

### 4.1 `FileStore` — `mlflow/store/tracking/file_store.py`

Layout on disk:

```
mlruns/
├── 0/                       # experiment id
│   ├── meta.yaml            # experiment metadata  ← the marker file
│   └── <run_uuid>/
│       ├── meta.yaml        # run metadata
│       ├── metrics/<key>    # append-only "timestamp value step" lines
│       ├── params/<key>     # single-line file
│       ├── tags/<key>
│       └── artifacts/
└── .trash/                  # soft-deleted experiments/runs
```

Metric history is literally an append-only text file — cheap writes, linear reads.
Deletion is a *move to `.trash/`*, which is what makes `restore_run` possible.
This backend is deprecated for new installs (hence the sqlite default) and does not
support the model registry's full feature set.

### 4.2 `SqlAlchemyStore` — `mlflow/store/tracking/sqlalchemy_store.py`

~10,300 lines, the production backend. Key tables (`mlflow/store/tracking/dbmodels/`):

`experiments`, `runs`, `params`, `metrics`, `latest_metrics`, `tags`,
`experiment_tags`, `datasets`, `inputs`, `input_tags`, `logged_models`,
`logged_model_params/metrics/tags`, `trace_info`, `trace_tags`,
`trace_request_metadata`, `assessments`, plus registry tables.

Notable design points:

- **`latest_metrics` is a denormalised table.** `metrics` holds the full history;
  `latest_metrics` holds one row per (run, key) so `search_runs` can filter on
  `metrics.loss < 0.5` with a single join instead of a window function.
- **Eager loading.** `_get_eager_run_query_options()` returns `selectinload`
  options so fetching N runs doesn't produce 5N queries.
- **Schema migrations** are Alembic revisions in `mlflow/store/db_migrations/versions/`.
  `mlflow db upgrade` runs them. The store refuses to start against an out-of-date
  schema.
- **Workspaces.** `WorkspaceAwareSqlAlchemyStore` +
  `mlflow/store/workspace_aware_mixin.py` add a `workspace_id` discriminator to
  every query when `MLFLOW_ENABLE_WORKSPACES=true`. The repo's `CLAUDE.md`
  explicitly forbids stripping this plumbing.

### 4.3 `RestStore` — `mlflow/store/tracking/rest_store.py`

Implements the same `AbstractStore` interface, but each method:

1. builds a **protobuf** request message (`mlflow/protos/service_pb2.py`),
2. `message_to_json` → HTTP call via `mlflow/utils/rest_utils.py`
   (`http_request`, with retry/backoff on 429/5xx and `MlflowHostCreds` for auth),
3. parses the JSON response back into the protobuf, then into an
   `mlflow.entities.*` object.

The `.proto` files in `mlflow/protos/` are therefore the actual API contract, shared
by the Python client, the Java client (`mlflow/java/`), the R client (`mlflow/R/`),
and the TypeScript frontend.

---

## 5. The tracking server

`mlflow server` boots a **FastAPI app that wraps the legacy Flask app**
(`mlflow/server/fastapi_app.py:201`, `create_fastapi_app`).

Composition order matters and is explicit in the source:

```
FastAPI(docs_url=None, redoc_url=None, openapi_url=None)   # docs intentionally off
 ├─ init_fastapi_security()          # security middleware, added BEFORE routes
 ├─ workspace_context_middleware     # binds workspace to a ContextVar per request
 ├─ gateway_timing_middleware
 ├─ include_router(otel_router)      # OTLP trace ingestion   ─┐
 ├─ include_router(job_api_router)                             │ native FastAPI
 ├─ include_router(gateway_router)   # /gateway/.../invocations│ routes take
 ├─ include_router(assistant_router) # localhost-only AI panel │ precedence over
 ├─ include_router(artifact_router)  # ASGI-streamed artifacts │ the catch-all
 ├─ include_router(mcp_server_router)                         ─┘
 └─ mount("/", WSGIMiddleware(flask_app))   # ~294 protobuf handlers + React UI
```

`mlflow/server/handlers.py` (~5,700 lines) holds the REST handlers. The canonical
shape of one:

```python
@catch_mlflow_exception
def _log_metric():
    request_message = _get_request_message(LogMetric())      # protobuf in
    _validate_param_keys_unique(...)                          # validation
    _get_tracking_store().log_metric(...)                     # same AbstractStore
    return _wrap_response(LogMetric.Response())               # protobuf out
```

So **the server runs the identical store code the local client would run.** A
`RestStore` client talking to a server backed by `SqlAlchemyStore` is just the
local path with a protobuf hop inserted.

### 5.1 Serving artifacts

With `--serve-artifacts`, the default artifact root becomes `mlflow-artifacts:/`
and clients use `MlflowArtifactsRepository`, which proxies uploads/downloads
through the server. `mlflow/server/artifact_router.py` handles this natively in
FastAPI so large files stream rather than buffer.

Every path arriving from a client is run through
[`validate_path_is_safe()`](mlflow/utils/uri.py#L481) — rejects `..` segments,
absolute paths, alternate separators, Windows drive letters, `#`, and
URL-decodes/escapes control characters first. There are ~20 call sites in
`handlers.py` alone. `validate_path_within_directory()` additionally resolves
symlinks for the local repository.

### 5.2 Auth

`mlflow/server/auth/` is an opt-in app-wrapper plugin: a `before_request` hook maps
each API route to a required permission (`READ`/`EDIT`/`MANAGE`/`NO_PERMISSIONS`),
checks it against a SQLite permission store, and for `search_*` endpoints
**post-filters the response** and back-fills pages so a filtered page still returns
`max_results` items (`_backfill_readable_mcp_results`).

---

## 6. Models: packaging, flavors, and pyfunc

### 6.1 The MLmodel contract

`mlflow.sklearn.log_model(model, name="m")` produces a directory:

```
m/
├── MLmodel               # the manifest
├── model.pkl             # flavor-specific payload
├── conda.yaml
├── python_env.yaml
├── requirements.txt
└── input_example.json    # optional
```

`MLmodel` is YAML written by `Model.save()` (`mlflow/models/model.py:774`):

```yaml
artifact_path: m
model_uuid: 8f3c...
run_id: abc123
utc_time_created: '2026-08-13 10:00:00'
signature:
  inputs:  '[{"name": "x", "type": "double"}]'
  outputs: '[{"type": "double"}]'
flavors:
  sklearn:                      # native flavor — full fidelity
    sklearn_version: 1.5.0
    pickled_model: model.pkl
    serialization_format: cloudpickle
  python_function:              # generic flavor — the deployment contract
    loader_module: mlflow.sklearn
    python_version: 3.10.12
    env: conda.yaml
```

**The two-flavor pattern is the core idea.** The native flavor round-trips the
original object (`mlflow.sklearn.load_model` gives you back a real
`sklearn.Pipeline`). The `python_function` flavor gives every model the same
`predict(data) -> data` interface, which is what scoring servers, `mlflow.evaluate`,
Spark UDFs, and Docker images target — so deployment tooling is written once.

There are ~40 built-in flavors (`mlflow/sklearn/`, `mlflow/pytorch/`,
`mlflow/transformers/`, `mlflow/langchain/`, `mlflow/openai/`, …), each exposing
the same `save_model` / `log_model` / `load_model` / `_load_pyfunc` quartet.

### 6.2 Signatures and schema enforcement

`mlflow/types/schema.py` + `mlflow/models/signature.py`. A signature can be passed
explicitly or **inferred** from an `input_example`. At predict time,
`mlflow/models/utils.py::_enforce_schema` validates and coerces: casts columns,
reorders by name, applies `_enforce_array`/`_enforce_object`/`_enforce_map` for
nested types, and raises on missing required inputs. Optional inputs are supported
via `required=False` in the schema.

### 6.3 Environment reproduction

At log time MLflow infers pip requirements by walking imported modules
(`mlflow/utils/requirements_utils.py`) and writes `requirements.txt`,
`conda.yaml`, `python_env.yaml`. At load/serve time
`mlflow/utils/environment.py` + `mlflow/pyfunc/backend.py` can rebuild that
environment with virtualenv, conda, or `uv` before running the model
(`env_manager=` on `mlflow models serve` / `predict`).

### 6.4 Serving

```
mlflow models serve -m models:/fraud@champion --env-manager uv
```

`mlflow/pyfunc/scoring_server/` exposes `/invocations` (and `/ping`,
`/health`, `/version`). It accepts `dataframe_split`, `dataframe_records`,
`instances`, `inputs`, and OpenAI-style chat payloads, runs `_enforce_schema`,
calls `predict`, and serialises the result. `mlflow models build-docker` bakes the
same server into an image (`mlflow/models/docker_utils.py`,
`mlflow/models/container/`).

---

## 7. Model Registry

Registry entities: **RegisteredModel** → many **ModelVersion** → optional
**aliases** and **tags**. Stages (`Staging`/`Production`) are the legacy mechanism;
**aliases** (`@champion`) plus tags are the current recommendation.

```
mlflow.register_model("runs:/<run_id>/model", "fraud")
  ├─ RunsArtifactRepository resolves runs:/ → real artifact URI
  ├─ create_registered_model (idempotent — tolerates already-exists)
  ├─ create_model_version(source=<resolved uri>, run_id=...)
  └─ poll until status == READY   (await_registration_for, default 300s)
```

Backends mirror tracking: `SqlAlchemyStore`, `FileStore`, `RestStore`,
`DatabricksWorkspaceModelRegistryStore`, and two Unity Catalog stores
(`mlflow/store/_unity_catalog/registry/`) which additionally handle temporary
scoped credential vending for the underlying cloud storage, and model lineage.

---

## 8. Tracing / GenAI observability

This is the newest and most intricate subsystem — MLflow builds on the
**OpenTelemetry SDK** but deliberately keeps its own tracer provider.

### 8.1 Provider isolation

`mlflow/tracing/provider.py` — `_TracerProviderWrapper` runs in one of two modes:

- **isolated** (default): MLflow owns a private `TracerProvider`, so it cannot break
  a host application that is already using OTel for something else;
- **global** (`MLFLOW_USE_DEFAULT_TRACER_PROVIDER=true`): MLflow attaches its span
  processors to the global OTel provider so MLflow and OTel spans land together.
  MLflow tracks its own `Once` flag rather than OTel's `_TRACER_PROVIDER_SET_ONCE`,
  so it can still install processors when auto-instrumentation got there first.

There's also an `_IsolatedRandomIdGenerator` (opt-in) that uses a private
`random.Random`, immune to user `random.seed()` calls that would otherwise produce
duplicate trace ids — re-seeded via `os.register_at_fork`.

### 8.2 Span pipeline

```
@mlflow.trace  /  mlflow.start_span()  /  autolog patches
        ↓  creates an OTel span, MLflow attributes attached
   SpanProcessor      mlflow/tracing/processor/{mlflow_v3,otel,inference_table,uc_table}.py
        │  on_start: register in InMemoryTraceManager, assign trace id
        │  on_end:   pop the trace when the root span ends
        ↓
   BatchSpanProcessor (optional, MLFLOW_USE_BATCH_SPAN_PROCESSOR)
        ↓
   SpanExporter       mlflow/tracing/export/mlflow_v3.py
        ↓
   AsyncTraceExportQueue     mlflow/tracing/export/async_export_queue.py
        │  bounded Queue + consumer thread + bounded worker pool
        ↓
   TracingClient → tracking store → trace_info / spans persisted
```

`InMemoryTraceManager` (`mlflow/tracing/trace_manager.py`) is the in-flight buffer:
spans accumulate under a trace id until the root span ends, at which point the
complete `Trace` is handed to the exporter. This is why a trace appears in the UI
as one object rather than N independent spans.

Destinations are pluggable (`mlflow/tracing/destination.py`): an MLflow experiment,
a Databricks inference table, a UC Delta table, or an OTLP collector
(`MLFLOW_TRACE_ENABLE_OTLP_DUAL_EXPORT` allows both at once).

`SpanBatcher` (`mlflow/tracing/export/span_batcher.py`) adds a second batching
layer for UC-table export: flush on `max_span_batch_size` **or** on
`max_interval_ms`, whichever comes first.

### 8.3 Flush semantics

Three coordinated layers, drained by
`flush_all_batch_processors()` (`mlflow/tracing/processor/base_mlflow.py:68`):

1. wait for in-flight `on_end` calls (a condition variable + pending counter),
2. `force_flush()` each `BatchSpanProcessor` (span queue → exporter),
3. `flush()` each exporter's `_async_queue` (DB-write queue → store).

Processors are held in a `WeakSet` so replaced tracer providers don't leak.

---

## 9. Autologging

`mlflow.autolog()` (`mlflow/tracking/fluent.py:3483`) does **not** import every ML
library. Instead:

1. For each supported flavor it registers a **post-import hook**
   (`mlflow/utils/import_hooks/`, a vendored `wrapt`-style `sys.meta_path` finder).
2. When the user's code eventually does `import sklearn`, the hook fires and calls
   `mlflow.sklearn.autolog()`.
3. That calls `safe_patch()` (`mlflow/utils/autologging_utils/safety.py:231`) on the
   library's methods (`Estimator.fit`, `Model.train`, …).

`safe_patch` has a precise error contract worth knowing:

- exceptions from the **original** function propagate to the caller unchanged;
- exceptions from **MLflow's patch logic** are caught and logged as warnings.

So autologging can never be the reason a user's training run fails. It also
handles run management (`manage_run=True` creates a run if none is active),
`silent=True`, and `disable=True`, with global state in
`AUTOLOGGING_INTEGRATIONS` guarded by `autologging_conf_lock`.

---

## 10. Evaluation

`mlflow.evaluate(model, data, targets=..., model_type="classifier")`
(`mlflow/models/evaluation/base.py`):

1. Resolve the model to a `pyfunc` (URI, callable, or a static predictions column).
2. Wrap the whole evaluation in
   `configure_autologging_for_evaluation()` — temporarily forces
   `log_models=False`, `silent=True`, `log_traces=True` for supported flavors so
   evaluation produces traces but not spurious logged models, then restores the
   previous configuration.
3. Run **evaluators** (`mlflow/models/evaluation/evaluators/`): default metrics per
   model type, SHAP explanations, plus any `extra_metrics` / `custom_artifacts`.
4. Log metrics/artifacts to the current run and return an `EvaluationResult`.

`mlflow.genai.evaluate()` (`mlflow/genai/`) is the LLM-focused sibling: it drives
**scorers** and **LLM judges** (`mlflow/genai/judges/`,
`mlflow/genai/scorers/`) over traces rather than tabular predictions, and supports
evaluation datasets, label schemas, and human review queues.

---

## 11. Projects & Deployments

**Projects** (`mlflow/projects/`) — an `MLproject` YAML declares entry points,
parameters and an environment. `mlflow run <uri>` fetches (local dir or git),
materialises the env (conda/virtualenv/docker/uv), and submits to a backend:
local subprocess, Databricks, or Kubernetes (`mlflow/projects/kubernetes.py`).

**Deployments** (`mlflow/deployments/`) — a plugin API (`create_deployment`,
`predict`, `update_deployment`, …) with built-in targets for SageMaker
(`mlflow/sagemaker/`), Azure ML, and the OpenAI-compatible **AI Gateway**
(`mlflow/gateway/`), which proxies to OpenAI/Anthropic/Bedrock/etc. behind a single
endpoint with rate limiting and usage tracing.

---

## 12. End-to-end trace of one call

`mlflow.log_metric("loss", 0.3)` against `http://localhost:5000`:

```
fluent.log_metric
 └ _get_or_start_run()                       thread-local run stack
   └ MlflowClient().log_metric(run_id, ...)
     └ TrackingServiceClient.store           → registry lookup on "http"
       └ RestStore.log_metric()
         └ LogMetric protobuf → message_to_json
           └ http_request() POST /api/2.0/mlflow/runs/log-metric   (retry/backoff)
─────────────────────────────── network ───────────────────────────────
             FastAPI → WSGIMiddleware → Flask → handlers._log_metric
               └ _get_request_message(LogMetric())      validate
                 └ _get_tracking_store()                → registry lookup on "postgresql"
                   └ SqlAlchemyStore.log_metric()
                     ├ INSERT INTO metrics
                     └ UPSERT latest_metrics
                       └ LogMetric.Response() → JSON
```

Swap the client tracking URI to `postgresql://…` and the middle three layers
disappear — the client calls `SqlAlchemyStore.log_metric()` directly. That
substitutability is the whole point of `AbstractStore`.

---

## 13. Extension points (all entry-point based)

| Entry-point group | Extends |
|---|---|
| `mlflow.tracking_store` | tracking backends |
| `mlflow.model_registry_store` | registry backends |
| `mlflow.artifact_repository` | artifact backends |
| `mlflow.deployments` | deployment targets |
| `mlflow.run_context_provider` | auto-tags on run creation |
| `mlflow.request_header_provider` | outbound HTTP headers |
| `mlflow.request_auth_provider` | auth schemes |
| `mlflow.app` | server app wrappers (this is how `basic-auth` is shipped) |
| `mlflow.dataset_constructor` | dataset sources |

Each registry calls `register_entrypoints()` at import time and warns (rather than
crashes) if a plugin fails to load.

---

## 14. Repo map — where to look for what

| Path | Contents |
|---|---|
| `mlflow/tracking/` | fluent + client API, context providers, store registries |
| `mlflow/store/tracking/` | `AbstractStore` and its 4 implementations |
| `mlflow/store/model_registry/` | registry stores |
| `mlflow/store/artifact/` | ~18 artifact repository implementations |
| `mlflow/store/db_migrations/` | Alembic revisions |
| `mlflow/server/` | FastAPI+Flask app, handlers, auth, GraphQL, React UI (`js/`) |
| `mlflow/models/` | `Model`/MLmodel, signatures, evaluation, docker |
| `mlflow/pyfunc/` | the universal inference interface + scoring server |
| `mlflow/tracing/` | OTel-based tracing: provider, processors, exporters |
| `mlflow/genai/` | GenAI evaluation, scorers, judges, prompt optimisation |
| `mlflow/protos/` | generated protobuf — the cross-language API contract |
| `mlflow/utils/` | search parser, rest utils, async logging, autolog safety, uri |
| `mlflow/<flavor>/` | ~40 framework integrations |
| `tests/` | mirrors the package layout |

---

## 15. Recurring design idioms

1. **Registry + abstract base + concrete backends.** Applied five times over.
2. **Protobuf as the cross-language contract.** Python/Java/R/TS all target `mlflow/protos/`.
3. **Same store code on both sides of the network.** REST is a transport, not a
   different implementation.
4. **Two-flavor models.** Native for fidelity, `python_function` for deployment.
5. **Lazy integration via post-import hooks.** Never import a heavy ML library the
   user hasn't imported.
6. **Autologging must never break user code.** Enforced structurally by `safe_patch`.
7. **Async-by-default write paths** with explicit `flush()` + `atexit` drains
   (metrics, artifacts, traces each have their own queue).
8. **Defence in depth on paths.** `validate_path_is_safe` at ~20 handler sites plus
   symlink resolution.
