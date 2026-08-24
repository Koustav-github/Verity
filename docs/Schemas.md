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

**Every V1 table below is stated against the migrations that actually ran.** This file spent
most of its life describing intent, and drifted: columns were specced here and never created,
created and never specced, and one whole table was listed as V1 while no migration for it
exists. Tables where spec and reality agree say so in one line; where they disagree, each column
carries a marker, so the drift cannot come back silently.

| | Meaning |
|---|---|
| ✅ | in the database — a migration in `server/migrations/versions/` creates it |
| ⬜ | specced, not built. Nothing needs it yet; the version that needs it adds it |
| ❌ | specced, then **cut**. Kept visible with the reason, so it isn't re-proposed |

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
| `alert_email` | text | nullable; who Falcon emails on a detected anomaly. Set at `assemble()` time (`verity.assemble(..., alert_email=...)`); `agents/brain3/fury/registry.py`'s existing-model branch never touches it, so re-uploading a new version of an already-registered model cannot silently change who gets notified |

Every column here is built (`c8e51f4d9a06`; `alert_email` added by `4d3c31bd85bd`).

`model_class` is aspirational as written above: the shipped registry
(`agents/brain3/fury/registry.py`) copies this value straight from Hawkeye's manifest,
which is a framework class name like `LogisticRegression` (see
`agents/brain1/hawkeye/identify.py`), not one of the `ML` · `DL` · `RL` · `LLM_APP` ·
`RAG` · `AGENTIC` taxonomy values. Reconciling the stored value with the declared
taxonomy is open.

### `model_version`
One row per distinct artifact. **Version identity is the content hash** — re-registering an
unchanged artifact dedupes; any byte change is inherently a new version.

| Column | Type | Built? | Notes |
|---|---|---|---|
| `id` | text PK | ✅ | `mv_…` |
| `artifact_sha256` | text | ✅ | unique per model; the version identity |
| `artifact_uri` | text | ✅ | where it lives (not necessarily custody) |
| `user_id` | text | ✅ | tenancy key until `org_id` replaces it at V1.5 |
| `args` | jsonb | ✅ | whatever the caller passed alongside the artifact |
| `status` | text | ✅ | `pending` · `staging` · `staging_failed` · `production` · `archived` |
| `model_id` | text FK → `model` | ✅ | added later, by `d4a729c6e153` |
| `promoted_from` | text FK → `eval_run` | ✅ | the evidence that promoted it; null if never promoted |
| `created_at` | timestamptz | ✅ | |
| `artifact_bytes` | bigint | ⬜ | would drive the custody-vs-pointer decision. Nothing needs it until V3 hosts weights too large to keep |
| `manifest_id` | text FK → `manifest` | ❌ | **cut.** The relationship already exists as `manifest.model_version_id`; a second pointer in the opposite direction is two facts that can disagree |

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
| `io_schema` | jsonb | ✅ | the introspected input surface — `n_features`, `feature_names`, `classes`, `has_predict_proba`, `estimator_class` (`a7f19c4e02b3`) |
| `environment` | jsonb | ✅ | the training environment the SDK captured — Python version and pinned package versions (`a7f19c4e02b3`) |
| `serving_pattern` | text | ⚠️ | column created by `a7f19c4e02b3`, **never written**. See the note below |
| `platform` | text | ⬜ | `windows` · `macos` · `linux` · `docker` · `k8s` |
| `confidence` | jsonb | ⬜ | per-field confidence scores |
| `review_required` | jsonb | ⬜ | fields Hawkeye could not resolve, and what they block |
| `declared_overrides` | jsonb | ⬜ | human/lineage-supplied values filling those gaps |

The ⬜ rows are specced here and **not in the table** — `90fc6eeb5714_create_manifest_table.py`
created six columns, `a1c4e7b90d33` added `task_type`, and `a7f19c4e02b3` added the three
serving columns. Nothing has needed the rest yet.

`serving_pattern` is the one honest loose end: api-fication created the column intending to
stamp it `container` on deploy, and then didn't. Writing it would mean updating a `manifest`
row after the fact, and this table is append-only *because* it is evidence — so the value
would have to be back-filled by the very step it is meant to describe. The `deployment` row is
the authoritative record of how a version is served, and it already exists. The column stays
empty rather than being written dishonestly; if nothing ever claims it, a later migration
drops it.

`environment` is an addition to this spec rather than an original member of it, made for the
same reason `eval_run.fixture` was: without it, `io_schema` describes a model whose runtime
requirements are unrecorded, and a serving image could not be rebuilt reproducibly from stored
rows alone. It belongs on the manifest because it describes the artifact *as identified*, and
because a redeploy has to work without the original upload still being in memory.

api-fication is what needs `io_schema` and `serving_pattern`, and it is worth being precise
about where each field comes from, because they have different trust levels:

| Field | Source | Why there |
|---|---|---|
| `n_features`, `feature_names`, `classes`, `has_predict_proba` | `execution.sandbox.introspect()` — the same scrubbed subprocess `predict()` runs in | read off the fitted estimator; deterministic, no LLM. Loading an artifact is arbitrary code execution whether or not anything is predicted afterwards, so it happens behind the same credential allowlist |
| training environment (`scikit-learn`, `numpy`, `python`, `cloudpickle` versions) | **`verity.environment.capture()`, client-side at `assemble()`** | must be client-side — introspecting on the server would report the *server's* versions, which is exactly the wrong answer for a pickle written elsewhere |

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
| `fixture` | jsonb | the typed fixture descriptor — `kind`, `uri`, `sha256`, `spec` |
| `error` | jsonb | why a run ended `error`; null otherwise |
| `started_at` / `finished_at` / `created_at` | timestamptz | |

Every column here is built. `fixture` and `error` are deliberate extensions over this file's
original list, both documented in `b2d5f8c14e77_create_eval_run_table.py`: `error` because
`verdict` can be `error` and the reason doesn't belong in `failed_on` (which means *which
metrics missed*), and `fixture` because `test_set_ref` is a bare pointer that can't say what
kind of fixture it points at — the field the mechanism registry dispatches on.

`thresholds` is stored **as applied**, not referenced — a later threshold change must never
retroactively alter what a past gate decided.

### `agent_run`
Audit trail across all four agents. The thing that makes agentic behaviour debuggable.

**⬜ No migration creates this table.** It has been listed under V1 since the first draft and
was never built — the pipeline runs, and nothing records *that it ran*. Today a promotion is
reconstructible from `eval_run` and `promoted_from`, which is the load-bearing part; what is
missing is the per-agent trace of inputs, outputs, and tool calls. Listed here as V1 scope that
V1 did not deliver, rather than quietly moved to a later version.

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
| `prediction_id` | text | nullable, indexed (`ix_telemetry_event_prediction_id`). The proxy's own correlation key, minted at request time (`pred_…`, `server/main.py`'s `predict()`) and threaded onto the queued telemetry row before it's ever written — the database-assigned bigint `id` doesn't exist yet when the response goes out, and a customer reporting a delayed outcome needs something to correlate against immediately. Null for SDK-reported events (`POST /telemetry`) from a customer-hosted model, which has no such key to report; that's an honest absence, not a bug |

Every column here is built (`e91a3d7c5b28`; `prediction_id` added by `48814b26b964`).

`inputs` and `prediction` are created but **not written at V1**. They exist to support drift
detection, which is V7; the V1 metric set — request count, latency percentiles, error rate —
needs neither. Leaving them null removes the entire sampling-policy question at V1 (no rate to
configure, no config for the SDK to fetch before it can start) and costs nothing V1 promises.
V7 adds a writer rather than a migration.

api-fication changed this: once Verity serves the model itself, `inputs` and `prediction` are
non-null on every proxied request (§9 of `architecture.md`), and `prediction_id` exists
specifically so that written `prediction` can later be joined against a delayed label — see
`label_event` below.

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
| `alert_thresholds` | jsonb | nullable; the exact `quality_metric_set` / `quality_thresholds` (copied verbatim from the promoting `eval_run`) plus `error_rate_relative_increase` / `latency_p95_relative_increase` this version is watched against — frozen at `configure()` time, the same "as applied" philosophy as `eval_run.thresholds`, so a later change to Falcon's detection defaults never retroactively changes what an already-promoted version is being alerted on |

Every column here is built (`e91a3d7c5b28`; `alert_thresholds` added by `4d3c31bd85bd`).

`eval_reference` is a **feasibility reference, not a production baseline**. Its numbers are
lifted from the `eval_run` that promoted the version, which measured them in a single-process,
single-client, cold sandbox — production latency under real concurrency will be materially
higher. The `basis` marker exists so a reader can tell what kind of number it is looking at.

This was originally "nothing in V1 compares against it" — no longer true. Falcon's
agentic-observability feature (`agents/brain4/falcon/detect.py`, `monitor.py`) compares two
adjacent live-traffic windows against each other, never against `eval_reference` directly (see
`architecture.md` §8.7) — the honesty constraint against a cold-sandbox baseline still holds,
it's just satisfied by comparing recent traffic to its own immediately preceding traffic instead
of to a feasibility figure that was always going to look better than production.

### `deployment`
Where a promoted version actually runs. Created by `c3b8e15d47af`; listed under V6 in earlier
drafts of this file and pulled forward because V1 now serves. Every column below is built.

| Column | Type | Notes |
|---|---|---|
| `id` | text PK | `dep_…` |
| `model_version_id` | text FK → `model_version` | |
| `image_tag` | text | the built image; identifies the exact environment serving this version |
| `container_id` | text | runtime handle; null while `building` or if the build failed |
| `host_port` | integer | ephemeral, assigned by the runtime and read back — not from a registry. Nullable: meaningful for `DockerRuntime`'s local port, always null for `FargateRuntime`, which has no equivalent concept (`endpoint_url` alone identifies where the task is reachable) |
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

### `label_event`
A delayed outcome the customer reports against a `prediction_id`, keyed to one instance inside
that prediction's batch. Created by `f25b10b1e9a6`. Every column below is built.

| Column | Type | Notes |
|---|---|---|
| `id` | text PK | `lbl_…` |
| `telemetry_event_id` | bigint FK → `telemetry_event` | |
| `instance_index` | integer | which row inside that prediction's batch this label answers |
| `actual` | jsonb | always `{"y": <value>}` — every writer (`POST /predictions/{id}/outcomes`) wraps it the same way, so no reader has to handle a second shape |
| `reported_at` | timestamptz | server default `now()` |

`UNIQUE (telemetry_event_id, instance_index)`, enforced by `save_label_event`'s `upsert(...,
on_conflict="telemetry_event_id,instance_index")` — reporting the same instance twice is treated
as a correction, overwriting the earlier value rather than accumulating a duplicate that would
double-count in the next `check_quality` run.

Kept as its own table rather than a column on `telemetry_event` specifically so a label can
arrive fully asynchronously — days or weeks after the prediction it answers — without touching
an append-only row. `find_labeled_outcomes` (`server/storage/models/supabase.py`) is the read
side: it joins this table back to `telemetry_event.prediction` in Python (the fake client used in
tests has no join support, and neither call needed one) to produce `{y_true, y_pred, y_proba}`
triples, fetching every `telemetry_event` for the version before filtering to labeled ones — a
real limitation named rather than hidden, the same relational-at-V1 tradeoff
`TELEMETRY_READ_LIMIT` already accepts, and one that does not scale past V1 traffic.

### `alert_event`
Falcon's notification, in-app half. Created by `ec18816eb5c9`. Every column below is built.

| Column | Type | Notes |
|---|---|---|
| `id` | text PK | `alrt_…` |
| `model_version_id` | text FK → `model_version` | indexed (`ix_alert_event_model_version_id`) |
| `kind` | text | `systemic` \| `quality` |
| `metric` | text | which metric tripped — `error_rate`, `latency_p95_ms`, or a quality metric name |
| `detail` | jsonb | the anomaly dict `detect_systemic_anomaly`/`detect_quality_anomaly` returned — `{metric, recent, baseline, relative_increase}` for a systemic alert, `{metric, op, value, actual}` for a quality one |
| `created_at` | timestamptz | server default `now()` |
| `emailed_at` | timestamptz | nullable; set only after a confirmed SES send |

Written before any email is attempted — this row is the source of truth for whether an alert
fired at all; email is best-effort delivery on top of it, and `emailed_at` staying null is the
only record that delivery didn't happen (no `alert_email` configured, or a failed send — both
look identical from this column alone; `GET /models/{id}/alerts` is the only place that
distinction could be surfaced, and today it isn't). No retry queue for a failed send — named as
an accepted gap in the design spec, not solved here.

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
| V7 (Alerting) | `alert_rule` · `recommendation` · `experiment` | Per-model configurable thresholds (today's are one global constant), retraining candidates, shadow/A-B experiments. `alert_event` itself moved to V1 with Falcon's detection feature |
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
