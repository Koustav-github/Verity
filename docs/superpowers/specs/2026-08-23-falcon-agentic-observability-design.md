# Falcon, agentic observability — closing the loop

## Context

`docs/architecture.md` §8 records what Falcon does today, and it is entirely passive: it
lifts a reference out of the `eval_run` that justified a promotion, and exposes whatever
telemetry arrives against it as *context* — no comparison, no threshold, no alert. That
was deliberate at V1: the only baseline available was a cold-sandbox number, and alerting
against a number that's wrong by construction would train people to ignore alerts.

api-fication removed that excuse. Since [docs/progression.md](../../progression.md) entry 9,
Verity itself serves promoted versions and the proxy writes real production `inputs` and
`prediction` into `telemetry_event` — columns that existed since Falcon shipped and stayed
null until now. Falcon can finally watch something real.

This spec is what Falcon watches, what counts as "something off," and what happens when it
finds it. The answer, settled in conversation before this was written down: **a human, not
an autonomous retrain.** Falcon detects and notifies; a human decides whether to retrain and
re-upload. That re-upload is not new engineering — Fury's existing same-name promotion logic
already archives whatever it replaces. The loop closes on infrastructure that already exists;
the only new work is *deciding when to ring the bell*.

### Settled decisions

| Decision | Choice |
|---|---|
| Signal | Both systemic (latency/error rate) and quality (accuracy/F1/etc.), not drift — drift needs a metric that doesn't exist yet and is deliberately left for a separate spec |
| Quality's ground truth | Delayed labels reported by the customer against a `prediction_id`, not a proxy signal |
| Trigger | Inline, on every new signal — no scheduler. Nothing in Verity runs on a clock today, and this doesn't need to be the first thing that does |
| Channel | Both an in-app `alert_event` row and an email, via AWS SES — already the account api-fication's S3 usage lives in |
| Where Falcon compares systemic numbers | **Against itself** — a trailing window vs. the window before it, never against `eval_reference`. That number is a cold-sandbox estimate and always looks better than real traffic; comparing against it would manufacture false alarms, which is the exact failure mode Falcon was built to avoid |
| Scope | Verity-served predictions only. The SDK-monitored (customer-hosted) path has no `prediction_id` to correlate a delayed label against, and no `inputs`/`prediction` to score against even if it did |

## The wrinkle that shaped the design: telemetry writes are already asynchronous

The proxy's `TelemetrySink` (`server/serving/sink.py`) queues events and flushes on a
5-second timer specifically so a monitoring write can never add latency to a prediction.
That means the database-assigned `telemetry_event.id` (a bigint) does not exist yet at the
moment the proxy would return a response — it's only assigned when the row is actually
inserted, seconds later, off the request path entirely.

A customer reporting an outcome later needs something to report it *against*, and that
something has to be handed back in the `/predict` response, synchronously. So the proxy
mints its own correlation key — `prediction_id`, a `pred_` -prefixed text id generated at
request time, carried inside the queued event, and written alongside the bigint PK when the
row eventually lands. The public API never sees the bigint at all.

## Architecture

```
Verity-served prediction
   │
   ▼
proxy generates prediction_id = "pred_<hex>"
   │  response: {predictions: [...], probabilities: [...], prediction_id: "pred_..."}
   │
   ▼
TelemetrySink queues {..., prediction_id, model_version_id}
   │  every flush_interval:
   ├─ save_telemetry_events(events)               → rows land with prediction_id
   └─ for each distinct model_version_id in batch:
        check_systemic(model_version_id)          → NEW, non-fatal
             compare trailing window vs. the window before it
             anomalous? → record_and_notify(kind="systemic", ...)

customer, days/weeks later
   │
   ▼
POST /predictions/{prediction_id}/outcomes
   {"outcomes": [{"index": 0, "actual": 1}]}
   │
   ├─ find_telemetry_event_by_prediction_id → validates it exists, gets model_version_id
   ├─ save_label_event(...) for each outcome        → label_event rows
   └─ check_quality(model_version_id)                → NEW, non-fatal
        enough labels accumulated? (>= MIN_LABELS)
             │
             ▼
        find_labeled_outcomes → {y_true, y_pred, y_proba} arrays
        score()                    [Nat's own metric functions, reused]
        apply_thresholds()         [Nat's own gate, the one that promoted this version]
             failing? → record_and_notify(kind="quality", ...)

record_and_notify:
   ├─ save_alert_event(...)                         → alert_event row (the "in-app" half)
   └─ send_alert_email(...)   best-effort, AWS SES   → the "email" half

Human sees the alert → retrains → verity.assemble(model, name="fraud", ...)
   → already flows through the existing pipeline. Nothing new required here.
```

## Components

### 1. `agents/brain4/falcon/detect.py` — the two checks, deterministic, no LLM

Falcon stays what it has always been: zero LLM calls. Detection is arithmetic over data
that already exists.

```python
WINDOW_MINUTES = 15             # recent = [now - WINDOW_MINUTES, now)
                                 # baseline = [now - 2*WINDOW_MINUTES, now - WINDOW_MINUTES)
WINDOW_MIN_EVENTS = 20          # below this, normal variance looks like an anomaly
RELATIVE_INCREASE_THRESHOLD = 0.5   # 50% worse than the trailing window trips an alert
MIN_LABELS = 30                 # below this, a handful of delayed labels is noise, not signal

def detect_systemic_anomaly(*, recent_summary, baseline_summary) -> dict | None:
    """Compare two summarize() outputs for the same model_version_id.

    Returns {"metric", "recent", "baseline", "relative_increase"} for the first metric
    that crossed RELATIVE_INCREASE_THRESHOLD, checking error_rate then latency_p95_ms.
    None if both windows are too small or nothing crossed.
    """

def detect_quality_anomaly(*, metric_set, thresholds, y_true, y_pred, y_proba=None) -> dict | None:
    """Recompute Nat's own metrics against accumulated labels and re-run the exact
    thresholds that gated promotion.

    Filters `thresholds` to non-resource ones before calling apply_thresholds() — a
    resource.* threshold has no corresponding key in a quality-only `scores` dict, and
    Nat's own rule ("a threshold on a skipped metric is a failure, not a silent pass")
    would otherwise report a quality alert caused by a systemic threshold that was never
    given data to evaluate.

    Returns the first `failed_on` entry as a dict, or None on a clean pass.
    """
```

Both are pure — no store access, no I/O — exactly like `agents/brain2/nat/score.py`, which
they call directly: `detect_quality_anomaly` imports `score` and `apply_thresholds` from
there rather than re-implementing gating.

### 2. `agents/brain4/falcon/monitor.py` — orchestration, extended

Two new entry points beside the existing `configure()`, both **non-fatal by the same
contract** every Falcon function already follows: a failing check must never break the
request that triggered it.

```python
def check_systemic(*, model_version_id, metadata_store, now_fn=None, notify_fn=None) -> dict | None:
    """Pull the two adjacent WINDOW_MINUTES windows defined above via two
    find_telemetry_events() calls, summarize() each, and notify on an anomaly.
    Called after every flush, for every version present in that batch."""

def check_quality(*, model_version_id, metadata_store, notify_fn=None) -> dict | None:
    """Pull accumulated label_event rows for this version; below MIN_LABELS, no-op.
    Otherwise read `metadata_store.find_monitoring_config(model_version_id)` for the
    frozen `alert_thresholds` -- the exact metric_set and thresholds that gated this
    version's promotion, captured once at configure() time -- and notify on a failure.

    Deliberately does not re-fetch the eval_run itself: alert_thresholds already froze
    what's needed, and reading from two places that describe the same promotion would
    be two facts that can drift apart -- the same reasoning that cut
    model_version.manifest_id in docs/Schemas.md."""
```

`configure()` itself gains one responsibility: writing the alert thresholds alongside the
existing eval reference, so they travel with the same row and the same provenance.

```python
def build_alert_thresholds(*, eval_run) -> dict:
    return {
        "error_rate_relative_increase": RELATIVE_INCREASE_THRESHOLD,
        "latency_p95_relative_increase": RELATIVE_INCREASE_THRESHOLD,
        "quality_metric_set": eval_run.get("metric_set", {}).get("resolved", []),
        "quality_thresholds": eval_run.get("thresholds", []),
    }
```

Stored as data rather than hardcoded constants read at check-time, for the same reason
`eval_run.thresholds` is stored *as applied*: a later change to the defaults must not
retroactively change what an already-promoted version is being watched against.

### 3. `agents/brain4/falcon/notify.py` — the alert, and its delivery

```python
def record_and_notify(*, model_version_id, kind, metric, detail, metadata_store, email_fn=None) -> str:
    """Writes the alert_event row first — that row is the source of truth. Email is
    best-effort delivery on top of it, wrapped so an SES outage cannot make an alert
    silently vanish; it just arrives without an email a human happened to see."""
```

```python
# agents/brain4/falcon/email.py
def send_alert_email(*, to, subject, body, client=None) -> None
def _real_client():
    import boto3
    return boto3.client("ses", region_name=os.environ.get("SES_REGION", "us-east-1"))
```

Same lazy-real-default shape as every other collaborator in this codebase. `to` is read
from `model.alert_email`; a version whose model has none simply gets the in-app row and no
email — not an error, since email was always the second half of "in-app and email," not a
requirement for the first half to fire.

### 4. Correlating a delayed label — the new endpoint

```
POST /predictions/{prediction_id}/outcomes
Body: {"outcomes": [{"index": 0, "actual": 1}, {"index": 1, "actual": 0}]}
```

`index` addresses a position inside the original batched `instances` list — a single
`/predict` call can carry many instances, and a label has to say which one it's confirming.
Defaults to `[{"index": 0, ...}]` shape for the common single-instance case; the field is
still required so the intent is explicit rather than assumed.

404 if `prediction_id` doesn't resolve to a telemetry event; 422 if an `index` is outside
the range of that event's recorded `prediction["predictions"]`. Writes are an **upsert** on
`(telemetry_event_id, instance_index)` — a corrected label overwrites the earlier one rather
than accumulating duplicates that would double-count in the next quality check.

### 5. New reads on `SupabaseMetadataStore`

| Method | Returns |
|---|---|
| `find_telemetry_event_by_prediction_id(*, prediction_id)` | the event row, or `None` |
| `save_label_event(*, telemetry_event_id, instance_index, actual)` | `lbl_…`, upserted |
| `find_labeled_outcomes(*, model_version_id, limit=1000)` | `[{"y_true", "y_pred", "y_proba"}, …]`, joined from `label_event` → `telemetry_event`, extracting `instance_index` out of the stored `prediction`/`inputs` |
| `find_model_by_version(*, model_version_id)` | joins through `model_version.model_id`, for `alert_email` |
| `save_alert_event(*, model_version_id, kind, metric, detail)` | `alrt_…` |
| `update_alert_event(*, alert_event_id, emailed_at)` | records whether email delivery succeeded |

### 6. Schema

**`telemetry_event` gains `prediction_id`** (text, nullable, indexed) — populated only by
the proxy; SDK-reported events from a customer-hosted model leave it null, honestly
reflecting that they have no correlation key for delayed labels.

**New table `label_event`** — `id` (`lbl_`), `telemetry_event_id` (FK), `instance_index`
(integer), `actual` (jsonb — shape mirrors whatever `y_true` looks like for the task),
`reported_at`. Unique on `(telemetry_event_id, instance_index)`.

**New table `alert_event`** — `id` (`alrt_`), `model_version_id` (FK), `kind` (`systemic` |
`quality`), `metric` (text), `detail` (jsonb — the anomaly dict from `detect.py`,
`{"failed_on", "scores", "sample_size"}` for quality or `{"recent", "baseline",
"relative_increase"}` for systemic), `created_at`, `emailed_at` (nullable).

**`monitoring_config` gains `alert_thresholds`** (jsonb, nullable — old rows predate
detection and legitimately have none).

**`model` gains `alert_email`** (text, nullable). Threaded through as an optional
`verity.assemble(..., alert_email=...)` keyword, written once at `create_model()` time.
Changing it later is out of scope — no update path exists yet, matching how `name` is
likewise fixed at creation.

**New read, for the in-app half:** `GET /models/{model_version_id}/alerts` — lists
`alert_event` rows for a version, newest first. A human (or eventually a dashboard) has
somewhere to look even before an email arrives, or if none was configured.

## Error handling

Both checks inherit the non-fatal contract every agent boundary in this codebase already
uses: `_configure_monitoring` swallows exceptions because it runs after a promotion that
already succeeded, and `_deploy` does the same because it runs after the version is already
`production`. `check_systemic` and `check_quality` are no different in kind — they run after
a telemetry write or a label write that already succeeded, and a detection bug must not
turn a successful ingest into a failed request.

Email failure is one layer further removed: `record_and_notify` writes the `alert_event` row
*before* attempting email, so a dead SES endpoint costs a missing email, never a missing
alert.

## Testing

Hand-written fakes, zero `unittest.mock`, matching every existing suite in this repo.

| File | Covers |
|---|---|
| `test_falcon_detect.py` | `detect_systemic_anomaly`: a real jump crosses the threshold, normal variance doesn't, too few events in either window is a no-op regardless of the numbers. `detect_quality_anomaly`: a real threshold failure is caught, a resource.* threshold in the mix is filtered out rather than causing a false failure, a clean pass returns `None` |
| `test_falcon_monitor.py` (extended) | `check_systemic` pulls the right two windows and calls notify only on an anomaly; `check_quality` no-ops below `MIN_LABELS` and fires exactly at the threshold; `build_alert_thresholds` carries the eval_run's own thresholds forward unchanged |
| `test_falcon_notify.py` | `record_and_notify` writes the alert row even when the injected `email_fn` raises; `to=None` (no `alert_email` configured) skips email without erroring |
| `test_proxy.py` (extended) | the response includes a `prediction_id`; `TelemetrySink.flush()` calls the injected `detect_fn` once per distinct `model_version_id` in the flushed batch, and a raising `detect_fn` doesn't stop the flush from returning its count |
| `test_outcomes.py` | valid outcome → `label_event` row and a `check_quality` call; unknown `prediction_id` → 404; out-of-range `index` → 422; reporting the same `(prediction_id, index)` twice upserts rather than duplicating |
| `test_supabase.py` (extended) | every new store method against the existing hand-written fake client |

## Accepted risks, named not solved

- **A relative-window comparison can miss a slow, gradual decline.** Each window only ever
  compares against its immediate predecessor, so a model degrading a little every day never
  crosses `RELATIVE_INCREASE_THRESHOLD` in any single step. Catching that needs a longer
  historical baseline, which needs the analytics store V3 already earmarks for
  telemetry volume — not invented here.
- **Delayed-label quality tracking depends entirely on the customer actually reporting
  outcomes.** A model with no reported labels is invisible to `check_quality` forever, and
  that silence looks identical to "the model is fine." Nothing detects the absence of
  labels itself.
- **One default threshold set for every model.** `RELATIVE_INCREASE_THRESHOLD` is a single
  global constant, not tuned per task. A model whose traffic is naturally bursty may trip
  more false alarms than one with steady load.
- **Best-effort email only.** There is no retry queue for a failed SES send; `emailed_at`
  staying null is the only record that it didn't go out, and nothing surfaces that
  distinction except the `GET /alerts` read.

## Out of scope

Input drift detection (a separate spec — it needs a distributional distance metric that
doesn't exist yet, unlike the two signals here which reuse Nat's existing machinery
wholesale). Autonomous retraining — a human decides, always. A scheduler or job queue of
any kind. Per-model configurable thresholds beyond what `alert_thresholds` already stores.
Multiple alert recipients, Slack/webhook channels, or any notification channel besides
email — `docs/Schemas.md`'s MCP table already names `notification` as one of Falcon's
future connections; this spec is the first concrete instance of it, not the whole of it.
Retry/backoff for failed email delivery. Changing `alert_email` after model creation.
