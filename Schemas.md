# Schemas

Data model and MCP connection contract for the agentic pipeline. Tables are grouped by the
version that introduces them — see [`README.md`](README.md#roadmap) for the version carve.

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
| `name` | text | unique per org |
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

`staging` is presently unreachable: the shipped Fury pipeline evaluates a version and, on a
passing verdict, promotes it straight from `pending` to `production` — there is no
intermediate stop. The `staging` state is pending api-fication (a review/approval step
between eval and promotion), per the design spec.

### `manifest`
Hawkeye's output. Append-only.

| Column | Type | Notes |
|---|---|---|
| `id` | text PK | `mf_…` |
| `model_version_id` | text FK | |
| `framework` | text | `sklearn` · `pytorch` · `onnx` · … |
| `detected_via` | text | the signal used, e.g. `onnx.producer_name` |
| `io_schema` | jsonb | inputs/outputs, shapes, dtypes, roles |
| `serving_pattern` | text | `in_process` · `http_endpoint` · `container` |
| `platform` | text | `windows` · `macos` · `linux` · `docker` · `k8s` |
| `confidence` | jsonb | per-field confidence scores |
| `review_required` | jsonb | fields Hawkeye could not resolve, and what they block |
| `declared_overrides` | jsonb | human/lineage-supplied values filling those gaps |

`review_required` and `declared_overrides` are the honest core of this table. Hawkeye can
read an ONNX graph's *shape* but not its *semantics* — six anonymous float inputs, with no
way to know column three is `Sex` where `0 = male`. Anything it can't recover gets flagged
rather than guessed, and the override that fills it is recorded separately so detected and
declared never blur together.

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
| V6 (Platform) | `deployment` · `agent_heartbeat` · `platform_target` | Where a version actually runs, and whether its agent is alive |
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
