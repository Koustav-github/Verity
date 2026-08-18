# Falcon — Observability — Design

## Context

`progression.md` entry 5 records where the loop stands: Hawkeye identifies a model, Nat
evaluates it, Fury registers and promotes it. A passing verdict now reaches `production`
automatically. But `production` is still only a status on a row — nothing observes the
version once it is live, and `telemetry_event` (specced in `Schemas.md` since the start)
has never been created.

This spec covers **Falcon**, the fourth and final agent of the V1 loop
(`artifact → Hawkeye → Nat → Fury → registered version → Falcon → monitoring config →
telemetry`, per [README.md](../../../README.md)). Its job per the vision doc: *"configures
monitoring for the now-live model: which metrics to collect, what the healthy baseline
looks like, where thresholds sit... Monitoring switches on with the deployment instead of
being a follow-up task that never gets done."*

**Falcon does not depend on api-fication.** Verity never serves inference. The README's V1
scope for Falcon is "in-process SDK": the customer serves the model in their own
environment, and the Verity SDK instruments that serving and reports back. This is the
direct expression of the product's "your model deploys wherever you want — any cloud, any
vendor, on-prem — and an agent reports back" thesis. Falcon is therefore buildable now,
and completes the V1 loop without Verity ever standing up an inference endpoint.

---

## Decisions

Five load-bearing decisions, recorded with their reasoning because several will look
arbitrary later without it.

**1. Falcon is a deterministic config generator, not an LLM agent.**
When Fury promotes a version it sets `promoted_from`, pointing at the `eval_run` that
justified the promotion. That row already contains measured values: `resource.latency_p50_ms`,
`p95`, `p99`, `resource.throughput_rps`, `resource.peak_memory_mb`, `resource.cpu_time_s`,
and every quality score. Falcon needs to guess nothing and call no LLM — it lifts a
reference from evidence that already exists. This also avoids reintroducing the run-to-run
threshold instability already observed with Nat, on a config that would live far longer
than a single eval.

**2. The eval-time reference is recorded, not enforced — nothing compares or alerts at V1.**
This is the most important honesty constraint in the design. The eval-time resource numbers
are single-process, single-client, cold-sandbox *feasibility* figures — `score.py:46-49`
says so in as many words. Production latency under real concurrency will be materially
higher. A config that treated the eval baseline as a production threshold would false-alarm
constantly and immediately train its users to ignore it. So `monitoring_config` stores the
reference **for context and for V7's rule engine to consume**, and V1 fires nothing.
Alerting is explicitly V7 in the roadmap; this decision keeps V1 from shipping a baseline
that is wrong by construction.

**3. The SDK instruments by wrapping, not by explicit logging calls.**
`verity.monitor(model, model_version_id=...)` returns a proxy whose `predict` is timed and
whose every other attribute delegates to the wrapped model. One line, no changes to the
customer's serving code — the "zero manual wiring" claim applied to observability. An
explicit `log_prediction()` primitive would cover odd serving setups a proxy cannot wrap,
but it is precisely the manual wiring the product exists to remove, so it is not built
here.

**4. `inputs` and `prediction` are not captured at V1.**
`Schemas.md` specs both columns as "sampled, not necessarily every request." They exist to
support drift detection, which is V7. The V1 metric set — request count, latency
percentiles, error rate — needs none of them. Leaving them null at V1 removes the entire
sampling-policy question (no sample rate to configure, no config for the SDK to fetch
before it can start), and costs nothing that V1 promises. The columns are still created, so
V7 adds a writer rather than a migration.

**5. Business metrics are out of scope.**
Falcon V1 ships systemic telemetry only, matching the README's stated scope exactly.
Business value (revenue saved, conversion lift, churn avoided) requires ground-truth
outcomes that only exist in the customer's own systems and often arrive weeks after the
prediction. The telemetry captured here records the *prediction* and never the *outcome*,
so it structurally cannot compute business value. Doing it properly means an
outcome-ingestion endpoint joined to stored predictions by request id — a separate feature
deserving its own spec, not a rider on this one.

---

## Schema changes

### New table: `telemetry_event`

Specced in `Schemas.md` since V1, never created. Built here exactly as specced.

| Column | Type | Notes |
|---|---|---|
| `id` | bigint PK | autoincrement — the only non-prefixed id in the schema, per spec |
| `model_version_id` | text FK → `model_version` | |
| `occurred_at` | timestamptz | indexed — every read filters on a time window |
| `latency_ms` | double | |
| `status` | text | `ok` · `error` · `timeout` |
| `inputs` | jsonb | nullable; not written at V1 (decision 4) |
| `prediction` | jsonb | nullable; not written at V1 (decision 4) |
| `error_type` | text | nullable; the exception class name when `status = "error"` |

Indexed on `(model_version_id, occurred_at)` — the read path's only access pattern.

### New table: `monitoring_config`

Falcon's output. **Not in `Schemas.md` today** — the README's pipeline diagram names a
"monitoring config" but no table was ever specced for it. Added here, and `Schemas.md`
updated to match, the same way Fury's deviations were recorded rather than left to diverge
silently.

| Column | Type | Notes |
|---|---|---|
| `id` | text PK | `mcfg_…` |
| `model_version_id` | text FK → `model_version` | |
| `eval_run_id` | text FK → `eval_run` | provenance: which eval the reference came from |
| `metrics` | jsonb | the metric names to collect — fixed set at V1 |
| `eval_reference` | jsonb | eval-time measured values, for context (decision 2) |
| `created_at` | timestamptz | |

`metrics` at V1 is exactly:
```json
["request_count", "latency_p50_ms", "latency_p95_ms", "latency_p99_ms", "error_rate"]
```

`eval_reference` carries an explicit `"basis": "sandbox_feasibility"` marker so a future
reader — or V7's rule engine — cannot mistake it for a production baseline:
```json
{
  "basis": "sandbox_feasibility",
  "eval_run_id": "evr_…",
  "latency_p50_ms": 0.165, "latency_p95_ms": 0.234, "latency_p99_ms": 0.243,
  "throughput_rps": 379.8, "peak_memory_mb": 111.4,
  "quality": {"accuracy": 1.0, "f1": 1.0}
}
```
Resource keys are copied with the `resource.` prefix stripped; `quality` holds every
non-`resource.` score from the eval run verbatim.

---

## Flow

### At promotion (inside `/ingest`)

```
... Fury register() → status "production"
      ↓
Falcon configure(model_version_id, eval_run_id, eval_run)
      ├─ lift resource.* and quality scores from the eval_run
      ├─ build {metrics, eval_reference}
      └─ save_monitoring_config(...) → mcfg_…
      ↓
/ingest response gains "monitoring_config": {...} (or null)
```

Falcon runs **only** when the registration status is `production`. A `pending`,
`staging_failed`, or deduplicated version gets no config — there is nothing live to watch.

### At serving time (in the customer's process)

```
monitored = verity.monitor(model, model_version_id="mv_…", endpoint=…)
monitored.predict(X)
   ├─ time the call, capture status/error_type
   ├─ enqueue a telemetry event (non-blocking; drops if the queue is full)
   └─ return the model's real result, or re-raise its real exception unchanged
            ↓ background daemon thread
      POST /telemetry  {events: [...]}  → telemetry_event rows
```

### At read time

```
GET /models/{model_version_id}/telemetry?hours=24
   → {model_version_id, hours, request_count, events_read,
      latency_p50_ms, latency_p95_ms, latency_p99_ms,
      error_rate, eval_reference}
```

`hours` is a float query param defaulting to **24** (a demo run and a next-day look both
land inside it). `events_read` reports how many rows the summary actually consumed, so a
window truncated by the read limit is visible rather than silently wrong.

`eval_reference` is returned alongside the observed values so the two can be shown
side by side — clearly labeled as feasibility-vs-production, never as pass/fail.

---

## Components

Mirrors the existing agent pattern exactly — pure functions, injected collaborators, lazy
real defaults. No new architectural shape.

- **`agents/brain4/falcon/monitor.py`** — one entry point:
  ```python
  configure(*, model_version_id, eval_run_id, eval_run, metadata_store) -> dict
  ```
  Pure computation plus one insert. No LLM, no network beyond the store.

- **`server/storage/models/supabase.py`** gains four methods in the established
  single-purpose-wrapper style:
  - `save_monitoring_config(*, model_version_id, eval_run_id, config) -> str`
  - `find_monitoring_config(*, model_version_id) -> dict | None`
  - `save_telemetry_events(*, events) -> int` (batch insert; returns rows written)
  - `find_telemetry_events(*, model_version_id, since, limit=10_000) -> list[dict]`

- **`server/main.py`** gains two routes, keeping the existing flat `@app.post` style:
  - `POST /telemetry` — batch ingestion
  - `GET /models/{model_version_id}/telemetry` — the summary

- **`server/telemetry.py`** — `summarize(events, eval_reference)`, a pure function computing
  count / percentiles / error rate from a list of events. Separated from the route so it is
  testable without HTTP, matching how `score.py` is separate from `evaluate.py`.

- **`verity/src/verity/monitor.py`** — the SDK proxy and its background queue:
  ```python
  monitor(model, *, model_version_id, endpoint="http://localhost:8000",
          transport=None) -> MonitoredModel
  ```
  Concrete defaults, so nothing is left to invent at implementation time: queue
  `maxsize=10_000`, batch size `100`, flush interval `5.0` seconds. `transport` is the
  injection seam for tests, matching the `client=None` pattern used throughout the SDK.

- **`client/`** — a telemetry panel on the existing intake page.

---

## Error handling

Three different policies, each deliberate:

**Falcon in the ingest path is non-fatal.** This is a deliberate asymmetry from Fury. Fury
raising is correct — if Fury fails, the promotion did not happen, and a 500 is the truth.
Falcon runs *after* a version is already `production`; a config-generation failure must not
500 a request whose promotion genuinely succeeded. So the call is wrapped: on failure the
response returns the successful promotion with `monitoring_config: null`, and monitoring can
be configured later. The failure is not silent — it surfaces in the response as a null
config rather than a fabricated one.

**The SDK never breaks the customer's inference.** This is the governing constraint of the
whole SDK piece, and it is enforced structurally rather than by care:
- the HTTP call never happens on the `predict` path — only on a background daemon thread;
- enqueue is non-blocking; a full queue drops the event and increments a drop counter
  rather than blocking the caller;
- the timing/recording wrapper catches its own exceptions and swallows them;
- **if the model itself raises, the original exception propagates unchanged** — Falcon
  records `status="error"` and the exception class name, then re-raises. Telemetry must
  never be the reason a customer's serving fails, and must never mask their real error.

`flush()` is exposed for explicit draining, and an `atexit` hook drains on exit so a
short-lived process does not lose its tail.

**Ingestion validates and rejects.** A malformed batch returns 4xx; it cannot corrupt
stored rows or affect any other request.

---

## Testing

Same conventions as the rest of the codebase: hand-written fakes, no `unittest.mock`,
full-sentence test names, TDD throughout.

The one genuinely new testing problem is the SDK's background thread. It is tested with a
fake transport plus an explicit `flush()` — never by sleeping — so the suite stays
deterministic and fast. The non-negotiable behaviours get their own tests:
- a model that raises still raises the *same* exception to the caller, with telemetry recorded;
- a transport that is down does not raise into `predict()`;
- a full queue drops rather than blocks.

`summarize()` is a pure function over a list of dicts, so percentile and error-rate maths
are tested directly with known inputs and hand-computed expected values.

---

## Scale boundary, stated

The read path fetches events and computes percentiles in Python (numpy is already a
dependency). This does not scale, and that is consistent with `Schemas.md`'s own note that
`telemetry_event` is "relational at V1; moves to the analytics store at V3 when volume
outgrows it." To keep the failure mode bounded rather than unbounded, `find_telemetry_events`
takes an explicit `limit` and the summary reports how many events it actually read, so a
truncated window is visible in the response instead of silently wrong.

---

## Explicitly out of scope

- **Alerting and notification channels** — V7. This spec produces the config V7's rule
  engine will consume; it fires nothing itself.
- **Drift detection** — V7, and the reason `inputs`/`prediction` exist as columns but stay
  null here (decision 4).
- **Business metrics** — decision 5. Needs outcome ingestion; its own spec.
- **The analytics store** — V3, per the scale boundary above.
- **`agent_run` audit trail** — still a cross-cutting gap across all four agents, still
  deferred, still deserving its own pass rather than being smuggled in here.
- **api-fication** — Verity does not serve inference. The customer serves; the SDK reports.
