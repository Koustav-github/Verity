# Schemas

Data model and MCP connection contract for the agentic pipeline. Tables are grouped by the
version that introduces them — see [`README.md`](../README.md#roadmap) for the version carve.

Types are written generically (`text`, `jsonb`, `timestamptz`, `bigint`). Engine choice is
deliberately unpinned.

---

## Conventions

- **Ids** — prefixed, sortable, opaque: `mdl_`, `mv_`, `evr_`, `mcpc_`. Prefixes make an id
  self-describing in logs and error messages.
- **Tenancy** — every table from V1.5 onward carries `org_id`. It is not optional and not
  nullable; a row without an owner is a data-leak waiting to happen.
- **Timestamps** — `created_at` / `updated_at` on everything, UTC.
- **Immutability** — `manifest`, `eval_run`, and `agent_run` are append-only. They are
  evidence: a promotion decision must always be re-derivable from the rows that caused it.
- **Deletes** — soft (`deleted_at`) everywhere except telemetry, which ages out by retention
  policy.

---

## V1 — The Loop

### `model`
The logical model, stable across versions.

| Column | Type | Notes |
|---|---|---|
| `id` | text PK | `mdl_…` |
| `user_id` | text | owns this model; `UNIQUE (user_id, name)` — a name is unique per user, not globally, until `org_id` replaces `user_id` as the tenancy boundary at V1.5 |
| `name` | text | unique per user today (see `user_id`'s note); becomes unique per org at V1.5 |
| `model_class` | text | `ML` · `DL` · `RL` · `LLM_APP` · `RAG` · `AGENTIC` |
| `task_type` | text | Atlas lookup key, e.g. `binary_classification` |
| `created_at` | timestamptz | |

`model_class` is aspirational as written above: the shipped registry
(`agents/brain3/fury/registry.py`) copies this value straight from Hawkeye's manifest,
which is a framework class name like `LogisticRegression` (see
`agents/brain1/hawkeye/identify.py`), not one of the `ML` · `DL` · `RL` · `LLM_APP` ·
`RAG` · `AGENTIC` taxonomy values. Reconciling the stored value with the declared
taxonomy is open.

### `model_version`
One row per distinct artifact. **Version identity is the content hash** — re-registering an
unchanged artifact dedupes; any byte change is inherently a new version.

| Column | Type | Notes |
|---|---|---|
| `id` | text PK | `mv_…` |
| `model_id` | text FK → `model` | |
| `artifact_sha256` | text | unique per model; the version identity |
| `artifact_uri` | text | where it lives (not necessarily custody) |
| `artifact_bytes` | bigint | drives custody vs. pointer decision |
| `status` | text | `pending` · `staging` · `staging_failed` · `production` · `archived` |
| `manifest_id` | text FK → `manifest` | |
| `promoted_from` | text FK → `eval_run` | the evidence that promoted it; null if never promoted |
| `created_at` | timestamptz | |

`promoted_from` is the important one: promotion is a *consequence of evidence*, and the row
records which evidence. A production version with a null `promoted_from` is an incident.

`staging` is unreachable, and **stays** unreachable. The shipped Fury pipeline evaluates a
version and, on a passing verdict, promotes it straight from `pending` to `production` with no
intermediate stop.

An earlier revision of this file predicted that api-fication would make `staging` real by
inserting a review/approval step between eval and promotion. That prediction was wrong: the
settled design deploys automatically on promotion, so there is no state in which a version has
passed but is waiting for a human. The value is left in the enum rather than dropped because a
V1.5 approval gate is a plausible thing to want once there is more than one developer — but
nothing writes it today, and nothing is planned to before then.

### `manifest`
Hawkeye's output. Append-only.

| Column | Type | Built? | Notes |
|---|---|---|---|
| `id` | text PK | ✅ | `mf_…` |
| `model_version_id` | text FK | ✅ | |
| `framework` | text | ✅ | `sklearn` · `xgboost` · `lightgbm` · `onnx` · … |
| `detected_via` | text | ✅ | the signal used, e.g. `onnx.producer_name` |
| `model_class` | text | ✅ | the estimator class name, e.g. `LogisticRegression` |
| `hyperparameters` | jsonb | ✅ | whatever was visible in the artifact |
| `task_type` | text | ✅ | coarse only — `classification` · `regression` · … |
| `created_at` | timestamptz | ✅ | |
| `io_schema` | jsonb | ⬜ | inputs/outputs, shapes, dtypes, roles |
| `serving_pattern` | text | ⬜ | `in_process` · `http_endpoint` · `container` |
| `platform` | text | ⬜ | `windows` · `macos` · `linux` · `docker` · `k8s` |
| `confidence` | jsonb | ⬜ | per-field confidence scores |
| `review_required` | jsonb | ⬜ | fields Hawkeye could not resolve, and what they block |
| `declared_overrides` | jsonb | ⬜ | human/lineage-supplied values filling those gaps |

The ⬜ rows are specced here and **not in the table** — `90fc6eeb5714_create_manifest_table.py`
created six columns, and `a1c4e7b90d33` added `task_type`. Nothing has needed the rest yet.

api-fication is what needs `io_schema` and `serving_pattern`, and it is worth being precise
about where each field comes from, because they have different trust levels:

| Field | Source | Why there |
|---|---|---|
| `n_features`, `feature_names`, `classes`, `has_predict_proba` | sandbox introspection of the artifact | read off the fitted estimator; deterministic, no LLM |
| training environment (`sklearn`, `numpy`, `python` versions) | **captured client-side by the SDK at `assemble()`** | must be client-side — introspecting on the server would report the *server's* versions, which is exactly the wrong answer for a pickle |
| `serving_pattern` | set to `container` when a version deploys | a record of what happened, not a prediction |

`review_required` and `declared_overrides` remain the honest core of the design, whenever they
land. Hawkeye can read an ONNX graph's *shape* but not its *semantics* — six anonymous float
inputs, with no way to know column three is `Sex` where `0 = male`. Anything it can't recover
should be flagged rather than guessed, and the override that fills it recorded separately so
detected and declared never blur together. This is also the ceiling on what a generated
`/predict` schema can promise: names and arity, not meaning.

### `eval_run`
Nat's output. Append-only.

| Column | Type | Notes |
|---|---|---|
| `id` | text PK | `evr_…` |
| `model_version_id` | text FK | |
| `mechanism` | text | `labeled_holdout` · `rollout` · `interrogation` |
| `metric_set` | jsonb | resolved from Atlas at run time |
| `scores` | jsonb | metric → value |
| `thresholds` | jsonb | metric → pass condition, as applied |
| `verdict` | text | `pass` · `fail` · `error` |
| `failed_on` | jsonb | which metrics missed, with example ids |
| `test_set_ref` | text | pointer to the exact test set used |
| `started_at` / `finished_at` | timestamptz | |

`thresholds` is stored **as applied**, not referenced — a later threshold change must never
retroactively alter what a past gate decided.

### `agent_run`
Audit trail across all four agents. The thing that makes agentic behavior debuggable.

| Column | Type | Notes |
|---|---|---|
| `id` | text PK | `agr_…` |
| `agent` | text | `hawkeye` · `nat` · `fury` · `falcon` |
| `model_version_id` | text FK | |
| `trigger` | text | `manual` · `sdk_checkin` · `ci` |
| `inputs` / `outputs` | jsonb | |
| `mcp_calls` | jsonb | ordered list of tool invocations made |
| `status` | text | `running` · `succeeded` · `failed` |
| `error` | jsonb | |

### `telemetry_event`
Falcon's ingestion target. Relational at V1; moves to the analytics store at V3 when volume
outgrows it.

| Column | Type | Notes |
|---|---|---|
| `id` | bigint PK | |
| `model_version_id` | text FK | |
| `occurred_at` | timestamptz | indexed |
| `latency_ms` | double | |
| `status` | text | `ok` · `error` · `timeout` |
| `inputs` / `prediction` | jsonb | sampled, not necessarily every request |
| `error_type` | text | |

`inputs` and `prediction` are created but **not written at V1**. They exist to support drift
detection, which is V7; the V1 metric set — request count, latency percentiles, error rate —
needs neither. Leaving them null removes the entire sampling-policy question at V1 (no rate to
configure, no config for the SDK to fetch before it can start) and costs nothing V1 promises.
V7 adds a writer rather than a migration.

### `monitoring_config`
Falcon's output — written when a version reaches `production`.

| Column | Type | Notes |
|---|---|---|
| `id` | text PK | `mcfg_…` |
| `model_version_id` | text FK → `model_version` | |
| `eval_run_id` | text FK → `eval_run` | provenance: which eval the reference came from |
| `metrics` | jsonb | metric names to collect; fixed at V1 |
| `eval_reference` | jsonb | eval-time measured values, carrying `"basis": "sandbox_feasibility"` |
| `created_at` | timestamptz | |

`eval_reference` is a **feasibility reference, not a production baseline**. Its numbers are
lifted from the `eval_run` that promoted the version, which measured them in a single-process,
single-client, cold sandbox — production latency under real concurrency will be materially
higher. Nothing in V1 compares against it; the `basis` marker exists so V7's rule engine can
tell what kind of number it is reading.

### `deployment`
Where a promoted version actually runs. **Designed, not yet built** — this is api-fication's
table, listed under V6 in earlier drafts of this file and pulled forward because V1 serves.

| Column | Type | Notes |
|---|---|---|
| `id` | text PK | `dep_…` |
| `model_version_id` | text FK → `model_version` | |
| `image_tag` | text | the built image; identifies the exact environment serving this version |
| `container_id` | text | runtime handle; null while `building` or if the build failed |
| `host_port` | integer | ephemeral, assigned by the runtime and read back — not from a registry |
| `endpoint_url` | text | where the proxy forwards to |
| `status` | text | `building` · `live` · `failed` · `stopped` |
| `error` | jsonb | why a `failed` deployment failed; null otherwise |
| `created_at` | timestamptz | |
| `stopped_at` | timestamptz | set when the version is archived and its container torn down |

Append-only in spirit, with two mutable fields: `status` and `stopped_at`. A version that is
promoted, replaced, and re-promoted gets a *new* row each time — the history of what served
when is worth keeping, and re-using a row would erase it.

`error` exists for the same reason `eval_run.error` does: a deploy can fail for reasons that
aren't a metric missing a threshold (image build failure, dependency resolution, a pickle that
loads in the training environment but not the built one), and those reasons belong on record
rather than in a log line. Deployment failure is deliberately **non-fatal** to the promotion
that triggered it — the version is already `production` by then, and a build failure must not
retroactively 500 a promotion that genuinely succeeded. It leaves a `failed` row instead.

---

## V1.5 — Multi-tenancy

### `organization`
`id` · `name` · `plan` · `created_at`

### `user`
`id` · `org_id` · `email` · `role` · `created_at`

### `api_key`
One org-scoped key, per the product definition.

| Column | Type | Notes |
|---|---|---|
| `id` | text PK | `key_…` |
| `org_id` | text FK | |
| `key_hash` | text | **hash only** — the raw key is shown once at issuance and never stored |
| `prefix` | text | first 8 chars, for identifying a key in the UI without revealing it |
| `last_used_at` | timestamptz | |
| `revoked_at` | timestamptz | |

### `quota`
`org_id` · `monitored_models_limit` (2 on free tier) · `monitored_models_used` ·
`requests_per_minute`

### `audit_log`
`id` · `org_id` · `actor` · `action` · `target` · `metadata` · `occurred_at`

---

## V2 — MCP Fabric

### `mcp_connection`

| Column | Type | Notes |
|---|---|---|
| `id` | text PK | `mcpc_…` |
| `org_id` | text FK | |
| `name` | text | human label |
| `server_type` | text | `filesystem` · `mlflow` · `object_store` · `compute` · … |
| `transport` | jsonb | see contract below |
| `auth_mode` | text | `none` · `bearer` · `oauth` · `mtls` |
| `secret_ref` | text | pointer into the secrets broker — **never the credential itself** |
| `tools_allowed` | jsonb | explicit allow-list; empty means nothing is callable |
| `scopes` | jsonb | e.g. `["registry:read", "registry:write"]` |
| `status` | text | `active` · `degraded` · `revoked` |
| `last_health_at` | timestamptz | |

### `mcp_capability`
Discovered per connection, cached, re-validated on health check.

`id` · `connection_id` · `kind` (`tool`/`resource`/`prompt`) · `name` · `input_schema` ·
`discovered_at`

### `mcp_call_log`
Every tool invocation an agent makes. Append-only.

`id` · `connection_id` · `agent_run_id` · `tool_name` · `arguments` · `result_status` ·
`duration_ms` · `called_at`

This table is what makes the system auditable. "Why did Fury promote this?" has to be
answerable by replaying rows, not by re-running an agent and hoping it decides the same way.

---

## MCP connection contract

How the agentic layer reaches every external system. Registration payload:

```json
{
  "name": "MLflow Registry (prod)",
  "server_type": "mlflow",
  "transport": {
    "kind": "http",
    "url": "https://mlflow.internal:5000/mcp"
  },
  "auth": {
    "mode": "bearer",
    "secret_ref": "sec_01JQ8F..."
  },
  "tools_allowed": [
    "search_registered_models",
    "get_model_version",
    "create_model_version",
    "transition_model_version_stage"
  ],
  "scopes": ["registry:read", "registry:write"]
}
```

`transport.kind` is one of:

| Kind | Shape | Used for |
|---|---|---|
| `stdio` | `{ "command": …, "args": [...], "env": {...} }` | local servers the agent spawns |
| `http` | `{ "url": … }` | remote servers |
| `sse` | `{ "url": … }` | remote servers with streaming |

Discovery response, cached into `mcp_capability`:

```json
{
  "connection_id": "mcpc_01JQ8G...",
  "protocol_version": "2025-06-18",
  "capabilities": { "tools": true, "resources": true, "prompts": false },
  "tools": [
    {
      "name": "create_model_version",
      "input_schema": { "type": "object", "required": ["name", "source"] }
    }
  ],
  "discovered_at": "2026-07-31T09:14:22Z"
}
```

### Enforcement rules

1. **Allow-list is authoritative.** A tool the server advertises but the connection does not
   list is not callable — capability discovery informs, it does not authorize.
2. **Credentials stay in the broker.** Agents receive a `secret_ref` and the client pool
   resolves it at call time. No credential ever enters an agent's context or a prompt.
3. **Every call is logged** to `mcp_call_log` before the result is used.
4. **Scope-to-agent binding** — an agent may only use connections carrying the scopes its
   stage requires (matrix below). Falcon cannot write to the registry; Fury cannot deploy.

### Agent → MCP usage

| Agent | Server types | Scopes | Purpose |
|---|---|---|---|
| Hawkeye | `filesystem` · `object_store` · `mlflow` | `artifact:read` · `registry:read` | Read the artifact and any training lineage |
| Nat | `python-exec` · `compute` · `vector_store` · `llm_provider` | `exec:run` · `artifact:read` | Run the eval; score against Atlas metrics |
| Fury | `mlflow` · `object_store` | `registry:write` · `artifact:write` | Register version, record lineage, gate promotion |
| Falcon | `observability` · `notification` | `telemetry:write` · `alert:write` | Configure monitoring, wire alert channels |

---

## V3+ — Sketches

Detailed once the version is designed; listed here so the shape is visible.

| Version | Tables | Purpose |
|---|---|---|
| V3 (DL) | `artifact_pointer` · `resource_sample` | External custody for large weights; GPU/memory telemetry |
| V4 (LLM/RAG) | `prompt_version` · `index_snapshot` · `eval_example` · `trace_span` | Version identity becomes prompt + index, not weights |
| V5 (RL/Agentic) | `environment` · `episode` · `trajectory` · `tool_invocation` | Rollout-based eval; a policy is meaningless without a pinned environment |
| V6 (Platform) | `agent_heartbeat` · `platform_target` | Which *target* a deployment runs on, and whether its agent is alive. `deployment` itself moved to V1 with api-fication |
| V7 (Alerting) | `alert_rule` · `alert_event` · `recommendation` · `experiment` | Thresholds, firing history, retraining candidates |
| V8 (Enterprise) | `role` · `permission` · `sso_config` · `agent_decision_log` | RBAC and a defensible record of every agent decision |

---

## Open questions

- **Where declared feature semantics live.** `manifest.declared_overrides` holds them today,
  but if they originate from a training run they arguably belong to the run, not the
  manifest. Resolving this decides whether Hawkeye's happy path is "ingest an MLflow run"
  (semantics come free) or "ingest a file" (semantics must be declared).
- **Test-set custody.** `eval_run.test_set_ref` points somewhere; whether Verity stores test
  sets or only references customer-held ones is a data-residency question that lands well
  before V8.
- **Telemetry retention and sampling rate** — unspecified until V3 gives it a real store.
- **Whether `manifest.declared_overrides` can reach the generated serving schema.** api-fication
  derives `/predict`'s request shape from introspected names and arity. If a human declares that
  column three is `Sex` with `0 = male`, that semantic belongs in the generated API's validation
  — but nothing currently carries a declaration from the manifest into the built image, and the
  two would have to stay in sync across redeploys.
