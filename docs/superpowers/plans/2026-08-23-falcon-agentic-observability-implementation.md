# Falcon Agentic Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Falcon watches production traffic and delayed customer-reported outcomes for two
signals — systemic degradation (latency/error rate, self-compared) and quality degradation
(re-scored against the same thresholds that gated promotion) — and on either, writes an
in-app alert row and sends an email. A human decides whether to retrain; the re-upload
already flows through Fury's existing promotion logic with no new engineering there.

**Architecture:** Two pure detection functions in `agents/brain4/falcon/detect.py` reuse
Nat's own metric/threshold machinery. Two orchestrating functions in
`agents/brain4/falcon/monitor.py` pull data via the metadata store and call them,
non-fatally, from two existing call sites (`TelemetrySink.flush()` and a new outcome-
reporting endpoint). Notification writes a row first, emails best-effort second.

**Tech Stack:** Python 3.12, FastAPI, pydantic v2, boto3 (SES), Alembic, Supabase (Postgres),
pytest.

**Spec:** `docs/superpowers/specs/2026-08-23-falcon-agentic-observability-design.md`

## Global Constraints

- **Do not commit and do not push.** Standing instruction from the user. Every task ends
  by running the suite, not by committing. Leave all work in the working tree.
- **No `unittest.mock` anywhere.** Hand-written fakes only, matching every existing test
  file in `server/tests/` and `verity/tests/`.
- Every collaborator is injected with a lazy real default: `param=None` → `param or
  _default_param` → deferred import inside the default. Follow this exactly for
  `notify_fn`, `email_fn`, `now_fn` wherever they appear below.
- Dispatch on a dict, never an `if` chain, where the codebase already does (Nat's
  `_METRIC_FNS`, `_OPS`) — this plan reuses those, it does not add new ones.
- Falcon makes **zero LLM calls**. Both new checks are arithmetic over data that already
  exists.
- Both new checks are **non-fatal**: a failure must never break the telemetry write or the
  outcome-reporting request that triggered it. Match the exact shape of
  `orchestrator._configure_monitoring` and `orchestrator._deploy` — write what you can,
  swallow the rest, return `None`.
- `RELATIVE_INCREASE_THRESHOLD = 0.5`, `WINDOW_MINUTES = 15`, `WINDOW_MIN_EVENTS = 20`,
  `MIN_LABELS = 30` — exact values from the spec, not tunable per task.
- Full-sentence test names describing behaviour, e.g.
  `test_a_fifty_percent_latency_jump_trips_the_systemic_check`.
- Run server tests from `server/` with `uv run pytest`; SDK tests from `verity/` with
  `uv run pytest`. **Before running any server suite, confirm `uv run python -c "import
  supabase, boto3"` succeeds** — a bare `uv sync` without `--extra dev` has silently
  pruned pytest from this venv before (see `docs/progression.md` entry 9), which makes
  `uv run pytest` fall back to the wrong interpreter and pass against missing packages.
  If it fails, run `uv sync --extra dev` first.

---

### Task 1: Detection functions — the two pure checks

**Files:**
- Create: `agents/brain4/falcon/detect.py`
- Create: `server/tests/test_falcon_detect.py`

**Interfaces:**
- Consumes: `agents.brain2.nat.score.score`, `agents.brain2.nat.score.apply_thresholds`,
  `agents.brain2.nat.score.RESOURCE_PREFIX` (all already exist, unchanged).
- Produces:
  - `detect_systemic_anomaly(*, recent_summary, baseline_summary) -> dict | None`
  - `detect_quality_anomaly(*, metric_set, thresholds, y_true, y_pred, y_proba=None) -> dict | None`
  - Constants `WINDOW_MINUTES`, `WINDOW_MIN_EVENTS`, `RELATIVE_INCREASE_THRESHOLD`,
    `MIN_LABELS`

`recent_summary`/`baseline_summary` are whatever `server/telemetry.py:summarize()` returns
— a dict with `request_count`, `error_rate`, `latency_p50_ms`, `latency_p95_ms`,
`latency_p99_ms`, `truncated`, `eval_reference`.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_falcon_detect.py
import pytest

from agents.brain4.falcon.detect import (
    MIN_LABELS,
    RELATIVE_INCREASE_THRESHOLD,
    WINDOW_MIN_EVENTS,
    detect_quality_anomaly,
    detect_systemic_anomaly,
)


def _summary(request_count=50, error_rate=0.01, latency_p95_ms=20.0):
    return {
        "request_count": request_count,
        "error_rate": error_rate,
        "latency_p50_ms": latency_p95_ms / 2,
        "latency_p95_ms": latency_p95_ms,
        "latency_p99_ms": latency_p95_ms * 1.1,
        "truncated": False,
        "eval_reference": None,
    }


def test_a_fifty_percent_error_rate_jump_trips_the_systemic_check():
    baseline = _summary(error_rate=0.02)
    recent = _summary(error_rate=0.02 * (1 + RELATIVE_INCREASE_THRESHOLD) + 0.001)

    anomaly = detect_systemic_anomaly(recent_summary=recent, baseline_summary=baseline)

    assert anomaly["metric"] == "error_rate"
    assert anomaly["baseline"] == 0.02
    assert anomaly["relative_increase"] > RELATIVE_INCREASE_THRESHOLD


def test_a_fifty_percent_latency_jump_trips_the_systemic_check():
    baseline = _summary(latency_p95_ms=20.0)
    recent = _summary(latency_p95_ms=20.0 * (1 + RELATIVE_INCREASE_THRESHOLD) + 1.0)

    anomaly = detect_systemic_anomaly(recent_summary=recent, baseline_summary=baseline)

    assert anomaly["metric"] == "latency_p95_ms"


def test_error_rate_is_checked_before_latency_when_both_have_jumped():
    baseline = _summary(error_rate=0.02, latency_p95_ms=20.0)
    recent = _summary(error_rate=0.05, latency_p95_ms=40.0)

    anomaly = detect_systemic_anomaly(recent_summary=recent, baseline_summary=baseline)

    assert anomaly["metric"] == "error_rate"


def test_normal_variance_does_not_trip_the_check():
    baseline = _summary(error_rate=0.02, latency_p95_ms=20.0)
    recent = _summary(error_rate=0.021, latency_p95_ms=20.5)

    assert detect_systemic_anomaly(recent_summary=recent, baseline_summary=baseline) is None


def test_a_window_below_the_minimum_event_count_is_never_flagged_even_if_the_numbers_look_bad():
    baseline = _summary(request_count=WINDOW_MIN_EVENTS - 1, error_rate=0.01)
    recent = _summary(request_count=WINDOW_MIN_EVENTS - 1, error_rate=0.9)

    # A handful of requests producing a scary-looking rate is noise, not signal.
    assert detect_systemic_anomaly(recent_summary=recent, baseline_summary=baseline) is None


def test_a_baseline_error_rate_of_zero_does_not_divide_by_zero():
    baseline = _summary(error_rate=0.0)
    recent = _summary(error_rate=0.1)

    # Any nonzero rate against a perfect baseline is worth flagging, not a ZeroDivisionError.
    anomaly = detect_systemic_anomaly(recent_summary=recent, baseline_summary=baseline)

    assert anomaly["metric"] == "error_rate"


def _threshold(metric, op, value):
    return {"metric": metric, "op": op, "value": value}


def test_a_real_threshold_failure_is_caught():
    thresholds = [_threshold("accuracy", ">=", 0.9)]

    anomaly = detect_quality_anomaly(
        metric_set=["accuracy"],
        thresholds=thresholds,
        y_true=[1, 0, 1, 0, 1, 0, 1, 0, 1, 0],
        y_pred=[1, 0, 1, 0, 1, 0, 1, 1, 0, 1],  # 3 wrong of 10 -> 0.7 accuracy
    )

    assert anomaly is not None
    assert anomaly["metric"] == "accuracy"


def test_a_clean_pass_returns_none():
    thresholds = [_threshold("accuracy", ">=", 0.5)]

    anomaly = detect_quality_anomaly(
        metric_set=["accuracy"],
        thresholds=thresholds,
        y_true=[1, 0, 1, 0],
        y_pred=[1, 0, 1, 0],
    )

    assert anomaly is None


def test_a_resource_threshold_in_the_mix_is_filtered_out_rather_than_causing_a_false_failure():
    # A resource.* threshold has no corresponding key in a quality-only scores dict.
    # Nat's own rule ("a threshold on a skipped metric is a failure, not a silent pass")
    # would otherwise report a quality alert caused by a systemic threshold that was
    # never given data to evaluate.
    thresholds = [
        _threshold("accuracy", ">=", 0.5),
        _threshold("resource.latency_p95_ms", "<=", 100.0),
    ]

    anomaly = detect_quality_anomaly(
        metric_set=["accuracy"],
        thresholds=thresholds,
        y_true=[1, 0, 1, 0],
        y_pred=[1, 0, 1, 0],
    )

    assert anomaly is None


def test_a_proba_only_metric_is_scored_when_y_proba_is_supplied():
    thresholds = [_threshold("roc_auc", ">=", 0.99)]

    anomaly = detect_quality_anomaly(
        metric_set=["roc_auc"],
        thresholds=thresholds,
        y_true=[0, 0, 1, 1],
        y_pred=[0, 0, 1, 1],
        y_proba=[[0.9, 0.1], [0.8, 0.2], [0.4, 0.6], [0.3, 0.7]],
    )

    assert anomaly is not None  # this proba ordering scores well below 0.99
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd server; uv run pytest tests/test_falcon_detect.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.brain4.falcon.detect'`

- [ ] **Step 3: Implement `detect.py`**

```python
# agents/brain4/falcon/detect.py
"""Falcon's two anomaly checks. Zero LLM calls — both are arithmetic over data that
already exists, reusing Nat's own metric and threshold machinery rather than
reimplementing gating a second time.
"""

from agents.brain2.nat.score import RESOURCE_PREFIX, apply_thresholds, score

WINDOW_MINUTES = 15             # recent = [now - WINDOW_MINUTES, now)
                                 # baseline = [now - 2*WINDOW_MINUTES, now - WINDOW_MINUTES)
WINDOW_MIN_EVENTS = 20          # below this, normal variance looks like an anomaly
RELATIVE_INCREASE_THRESHOLD = 0.5   # 50% worse than the trailing window trips an alert
MIN_LABELS = 30                 # below this, a handful of delayed labels is noise, not signal

# Checked in this order: an error-rate jump is reported over a latency jump when both
# are present, because a request that errors is a worse outcome than one that's merely
# slow, and one alert is more actionable than two firing on the same underlying cause.
_SYSTEMIC_METRICS = ("error_rate", "latency_p95_ms")


def _relative_increase(recent, baseline):
    if baseline <= 0:
        # Any nonzero recent value against a perfect baseline is worth flagging outright,
        # not a ZeroDivisionError. Treated as "infinitely worse".
        return float("inf") if recent > 0 else 0.0
    return (recent - baseline) / baseline


def detect_systemic_anomaly(*, recent_summary, baseline_summary):
    """Compare two summarize() outputs for the same model_version_id.

    Never compares against eval_reference — that's a cold-sandbox estimate and always
    looks better than real traffic, so it would just manufacture false alarms. This
    compares a version against its own recent history instead.
    """
    if (
        recent_summary["request_count"] < WINDOW_MIN_EVENTS
        or baseline_summary["request_count"] < WINDOW_MIN_EVENTS
    ):
        return None

    for metric in _SYSTEMIC_METRICS:
        recent_value = recent_summary[metric]
        baseline_value = baseline_summary[metric]
        increase = _relative_increase(recent_value, baseline_value)
        if increase > RELATIVE_INCREASE_THRESHOLD:
            return {
                "metric": metric,
                "recent": recent_value,
                "baseline": baseline_value,
                "relative_increase": increase,
            }
    return None


def detect_quality_anomaly(*, metric_set, thresholds, y_true, y_pred, y_proba=None):
    """Recompute Nat's own metrics against accumulated labels and re-run the exact
    thresholds that gated promotion.

    Filters `thresholds` to non-resource ones first: a resource.* threshold has no
    corresponding key in this quality-only scores dict, and Nat's rule that a threshold
    on a skipped metric is a failure (not a silent pass) would otherwise report a
    quality alert caused by a systemic threshold that was never given data to evaluate.
    """
    outputs = {"y_true": y_true, "y_pred": y_pred, "y_proba": y_proba}
    scores, _skipped = score(section="ML", metric_set=metric_set, outputs=outputs)

    quality_thresholds = [
        t for t in thresholds if not t["metric"].startswith(RESOURCE_PREFIX)
    ]
    verdict, failed_on = apply_thresholds(scores=scores, thresholds=quality_thresholds)
    if verdict == "pass":
        return None
    return failed_on[0]
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd server; uv run pytest tests/test_falcon_detect.py -v`
Expected: 10 passed

- [ ] **Step 5: Run the full server suite to confirm no regressions**

Run: `cd server; uv run pytest -q`
Expected: 207 existing + 10 new = 217 passed

---

### Task 2: Schema — `label_event`, `alert_event`, and three new columns

**Files:**
- Create: `server/migrations/versions/<rev1>_add_prediction_id_to_telemetry_event.py`
- Create: `server/migrations/versions/<rev2>_create_label_event_table.py`
- Create: `server/migrations/versions/<rev3>_create_alert_event_table.py`
- Create: `server/migrations/versions/<rev4>_add_alert_config_columns.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `telemetry_event.prediction_id` (text, nullable, indexed), table `label_event`,
  table `alert_event`, `monitoring_config.alert_thresholds` (jsonb, nullable),
  `model.alert_email` (text, nullable).

- [ ] **Step 1: Confirm the current head**

Run: `cd server; uv run alembic heads`
Expected: `c3b8e15d47af (head)`. Chain the first new revision off this.

- [ ] **Step 2: `prediction_id` on `telemetry_event`**

Generate with `uv run alembic revision -m "add prediction_id to telemetry_event"`, then
replace the body:

```python
"""add prediction_id to telemetry_event

The proxy mints its own correlation key at request time, because TelemetrySink queues
writes and the database-assigned bigint id does not exist yet when the response is
returned. Nullable: SDK-reported events from a customer-hosted model have no
correlation key for delayed labels, and that is an honest absence, not a bug.

Revision ID: <rev1>
Revises: c3b8e15d47af
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "<rev1>"
down_revision: Union[str, Sequence[str], None] = "c3b8e15d47af"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("telemetry_event", sa.Column("prediction_id", sa.Text(), nullable=True))
    op.create_index(
        "ix_telemetry_event_prediction_id", "telemetry_event", ["prediction_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_telemetry_event_prediction_id", table_name="telemetry_event")
    op.drop_column("telemetry_event", "prediction_id")
```

- [ ] **Step 3: `label_event` table**

Generate with `uv run alembic revision -m "create label event table"`, chain off `<rev1>`:

```python
"""create label_event table

A delayed outcome the customer reports against a prediction_id, keyed to one instance
inside that prediction's batch. Kept as its own table rather than a column on
telemetry_event so a label can arrive fully asynchronously — days or weeks later —
without touching an append-only row.

Unique on (telemetry_event_id, instance_index): reporting the same instance twice is a
correction, upserted rather than accumulated as a duplicate that would double-count in
the next quality check.

Revision ID: <rev2>
Revises: <rev1>
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "<rev2>"
down_revision: Union[str, Sequence[str], None] = "<rev1>"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "label_event",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "telemetry_event_id",
            sa.BigInteger(),
            sa.ForeignKey("telemetry_event.id"),
            nullable=False,
        ),
        sa.Column("instance_index", sa.Integer(), nullable=False),
        sa.Column("actual", sa.JSON(), nullable=False),
        sa.Column(
            "reported_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_unique_constraint(
        "uq_label_event_telemetry_event_instance",
        "label_event",
        ["telemetry_event_id", "instance_index"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_label_event_telemetry_event_instance", "label_event", type_="unique"
    )
    op.drop_table("label_event")
```

- [ ] **Step 4: `alert_event` table**

Generate with `uv run alembic revision -m "create alert event table"`, chain off `<rev2>`:

```python
"""create alert_event table

Falcon's notification, in-app half. Written before any email is attempted — this row is
the source of truth for whether an alert fired; email is best-effort delivery on top of
it, and emailed_at staying null is the only record that delivery didn't happen.

Revision ID: <rev3>
Revises: <rev2>
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "<rev3>"
down_revision: Union[str, Sequence[str], None] = "<rev2>"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alert_event",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "model_version_id",
            sa.Text(),
            sa.ForeignKey("model_version.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),  # "systemic" | "quality"
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("emailed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_alert_event_model_version_id", "alert_event", ["model_version_id"])


def downgrade() -> None:
    op.drop_index("ix_alert_event_model_version_id", table_name="alert_event")
    op.drop_table("alert_event")
```

- [ ] **Step 5: `alert_thresholds` and `alert_email` columns**

Generate with `uv run alembic revision -m "add alert config columns"`, chain off `<rev3>`:

```python
"""add alert config columns

alert_thresholds freezes the exact metric_set and thresholds a version was promoted
under, the same "as applied" philosophy as eval_run.thresholds: a later change to the
detection defaults must not retroactively change what an already-promoted version is
being watched against. alert_email is where a human receives a notification; nullable
because a model whose owner never registered one simply gets the in-app row alone.

Revision ID: <rev4>
Revises: <rev3>
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "<rev4>"
down_revision: Union[str, Sequence[str], None] = "<rev3>"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "monitoring_config", sa.Column("alert_thresholds", sa.JSON(), nullable=True)
    )
    op.add_column("model", sa.Column("alert_email", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("model", "alert_email")
    op.drop_column("monitoring_config", "alert_thresholds")
```

- [ ] **Step 6: Apply and verify**

Run: `cd server; uv run alembic upgrade head`
Then: `uv run alembic current`
Expected: current matches `<rev4>`. Confirm in Supabase that `label_event` and
`alert_event` exist, and that `telemetry_event`, `monitoring_config`, and `model` each
gained their new column.

---

### Task 3: Storage methods

**Files:**
- Modify: `server/storage/models/supabase.py`
- Modify: `server/tests/test_supabase.py`

**Interfaces:**
- Consumes: Task 2's schema.
- Produces on `SupabaseMetadataStore`:
  - `create_model(*, user_id, name, model_class, task_type, alert_email=None)` — extended
  - `save_monitoring_config(*, model_version_id, eval_run_id, config)` — extended to
    persist `alert_thresholds` when present
  - `find_telemetry_event_by_prediction_id(*, prediction_id) -> dict | None`
  - `save_label_event(*, telemetry_event_id, instance_index, actual) -> str` (upsert)
  - `find_labeled_outcomes(*, model_version_id, limit=1000) -> list[dict]`
  - `find_model_by_version(*, model_version_id) -> dict | None`
  - `save_alert_event(*, model_version_id, kind, metric, detail) -> str`
  - `update_alert_event(*, alert_event_id, emailed_at) -> None`
  - `find_alert_events(*, model_version_id) -> list[dict]`

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_supabase.py`, using the `FakeSupabaseClient`/`FakeTable`
already defined there:

```python
def test_create_model_persists_the_alert_email_when_given():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    store.create_model(
        user_id="u_1", name="fraud", model_class="sklearn",
        task_type="classification", alert_email="ops@example.com",
    )

    assert fake_client.calls[0][1]["alert_email"] == "ops@example.com"


def test_create_model_omits_alert_email_when_not_given():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    store.create_model(user_id="u_1", name="fraud", model_class="sklearn", task_type="classification")

    # Explicit None, not silently dropped: a fresh model legitimately has no configured
    # recipient, and the column should say so rather than never having been written.
    assert fake_client.calls[0][1]["alert_email"] is None


def test_save_monitoring_config_persists_alert_thresholds_when_present():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    store.save_monitoring_config(
        model_version_id="mv_1", eval_run_id="evr_1",
        config={"metrics": ["request_count"], "eval_reference": {},
               "alert_thresholds": {"error_rate_relative_increase": 0.5}},
    )

    inserted = fake_client.calls[0][1]
    assert inserted["alert_thresholds"] == {"error_rate_relative_increase": 0.5}


def test_save_monitoring_config_omits_alert_thresholds_when_absent():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    store.save_monitoring_config(
        model_version_id="mv_1", eval_run_id="evr_1",
        config={"metrics": ["request_count"], "eval_reference": {}},
    )

    assert "alert_thresholds" not in fake_client.calls[0][1]


def test_find_telemetry_event_by_prediction_id_returns_none_when_unknown():
    store = SupabaseMetadataStore(client=FakeSupabaseClient())

    assert store.find_telemetry_event_by_prediction_id(prediction_id="pred_nope") is None


def test_find_telemetry_event_by_prediction_id_finds_the_row():
    fake_client = FakeSupabaseClient(
        rows={"telemetry_event": [{"id": 1, "prediction_id": "pred_abc", "model_version_id": "mv_1"}]}
    )
    store = SupabaseMetadataStore(client=fake_client)

    event = store.find_telemetry_event_by_prediction_id(prediction_id="pred_abc")

    assert event["id"] == 1
    assert event["model_version_id"] == "mv_1"


def test_save_label_event_inserts_with_a_prefixed_id():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    label_id = store.save_label_event(telemetry_event_id=1, instance_index=0, actual={"y": 1})

    assert label_id.startswith("lbl_")
    assert fake_client.calls[0][0] == "label_event"


def test_find_labeled_outcomes_joins_labels_to_their_recorded_predictions():
    fake_client = FakeSupabaseClient(
        rows={
            "telemetry_event": [
                {
                    "id": 1, "model_version_id": "mv_1",
                    "prediction": {"predictions": [1, 0], "probabilities": [[0.1, 0.9], [0.8, 0.2]]},
                }
            ],
            "label_event": [
                {"telemetry_event_id": 1, "instance_index": 0, "actual": {"y": 1}},
                {"telemetry_event_id": 1, "instance_index": 1, "actual": {"y": 0}},
            ],
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    outcomes = store.find_labeled_outcomes(model_version_id="mv_1")

    assert len(outcomes) == 2
    assert outcomes[0] == {"y_true": 1, "y_pred": 1, "y_proba": [0.1, 0.9]}
    assert outcomes[1] == {"y_true": 0, "y_pred": 0, "y_proba": [0.8, 0.2]}


def test_find_model_by_version_returns_the_owning_models_alert_email():
    fake_client = FakeSupabaseClient(
        rows={
            "model_version": [{"id": "mv_1", "model_id": "mdl_1"}],
            "model": [{"id": "mdl_1", "alert_email": "ops@example.com"}],
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    model = store.find_model_by_version(model_version_id="mv_1")

    assert model["alert_email"] == "ops@example.com"


def test_save_alert_event_inserts_with_a_prefixed_id():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    alert_id = store.save_alert_event(
        model_version_id="mv_1", kind="systemic", metric="error_rate", detail={"recent": 0.3},
    )

    assert alert_id.startswith("alrt_")
    inserted = fake_client.calls[0][1]
    assert inserted["kind"] == "systemic"
    assert inserted["detail"] == {"recent": 0.3}


def test_update_alert_event_writes_emailed_at():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    store.update_alert_event(alert_event_id="alrt_1", emailed_at="2026-08-23T00:00:00+00:00")

    assert fake_client.calls[0] == (
        "alert_event", "update", {"emailed_at": "2026-08-23T00:00:00+00:00"}, [("id", "alrt_1")],
    )


def test_find_alert_events_returns_rows_for_the_version_newest_first():
    fake_client = FakeSupabaseClient(
        rows={
            "alert_event": [
                {"id": "alrt_1", "model_version_id": "mv_1", "created_at": "2026-08-01T00:00:00Z"},
                {"id": "alrt_2", "model_version_id": "mv_1", "created_at": "2026-08-02T00:00:00Z"},
                {"id": "alrt_3", "model_version_id": "mv_2", "created_at": "2026-08-03T00:00:00Z"},
            ]
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    alerts = store.find_alert_events(model_version_id="mv_1")

    assert [a["id"] for a in alerts] == ["alrt_2", "alrt_1"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd server; uv run pytest tests/test_supabase.py -v -k "alert or label or by_version"`
Expected: FAIL — `AttributeError: 'SupabaseMetadataStore' object has no attribute
'find_telemetry_event_by_prediction_id'`

> If `FakeSupabaseClient`/`FakeTable` cannot yet serve a query pattern used above (e.g. a
> select filtered by more than the columns it currently supports, or a table with no rows
> preset), extend the fake in the same file rather than reaching for `unittest.mock`. It
> already supports `.select()`, `.eq()`, `.order()`, `.limit()`, `.insert()`, `.update()`
> — the join in `find_labeled_outcomes` and `find_model_by_version` is done in Python,
> by calling the fake twice (once per table), not by asking the fake to join.

- [ ] **Step 3: Implement the store methods**

Modify `create_model`:

```python
    def create_model(self, *, user_id, name, model_class, task_type, alert_email=None):
        model_id = f"mdl_{uuid.uuid4().hex}"
        self.client.table("model").insert(
            {
                "id": model_id,
                "user_id": user_id,
                "name": name,
                "model_class": model_class,
                "task_type": task_type,
                "alert_email": alert_email,
            }
        ).execute()
        return model_id
```

Modify `save_monitoring_config`:

```python
    def save_monitoring_config(self, *, model_version_id, eval_run_id, config):
        config_id = f"mcfg_{uuid.uuid4().hex}"
        row = {
            "id": config_id,
            "model_version_id": model_version_id,
            "eval_run_id": eval_run_id,
            "metrics": config["metrics"],
            "eval_reference": config["eval_reference"],
        }
        if "alert_thresholds" in config:
            row["alert_thresholds"] = config["alert_thresholds"]
        self.client.table("monitoring_config").insert(row).execute()
        return config_id
```

Add the new methods:

```python
    def find_telemetry_event_by_prediction_id(self, *, prediction_id):
        result = (
            self.client.table("telemetry_event")
            .select("*")
            .eq("prediction_id", prediction_id)
            .execute()
        )
        return result.data[0] if result.data else None

    def save_label_event(self, *, telemetry_event_id, instance_index, actual):
        label_id = f"lbl_{uuid.uuid4().hex}"
        # Upsert on (telemetry_event_id, instance_index): a corrected label overwrites
        # the earlier one instead of accumulating a duplicate that would double-count
        # in the next quality check.
        self.client.table("label_event").upsert(
            {
                "id": label_id,
                "telemetry_event_id": telemetry_event_id,
                "instance_index": instance_index,
                "actual": actual,
            },
            on_conflict="telemetry_event_id,instance_index",
        ).execute()
        return label_id

    def find_labeled_outcomes(self, *, model_version_id, limit=1000):
        events = (
            self.client.table("telemetry_event")
            .select("*")
            .eq("model_version_id", model_version_id)
            .execute()
        ).data or []
        events_by_id = {event["id"]: event for event in events}

        labels = (
            self.client.table("label_event")
            .select("*")
            .limit(limit)
            .execute()
        ).data or []

        outcomes = []
        for label in labels:
            event = events_by_id.get(label["telemetry_event_id"])
            if event is None:
                continue
            index = label["instance_index"]
            prediction = event.get("prediction") or {}
            probabilities = prediction.get("probabilities")
            outcomes.append(
                {
                    # Every writer of label_event.actual (the outcomes route in Task 7)
                    # wraps the value as {"y": ...}; no other shape is ever produced.
                    "y_true": label["actual"]["y"],
                    "y_pred": prediction["predictions"][index],
                    "y_proba": probabilities[index] if probabilities is not None else None,
                }
            )
        return outcomes

    def find_model_by_version(self, *, model_version_id):
        version = (
            self.client.table("model_version")
            .select("*")
            .eq("id", model_version_id)
            .execute()
        )
        if not version.data:
            return None
        model = (
            self.client.table("model")
            .select("*")
            .eq("id", version.data[0]["model_id"])
            .execute()
        )
        return model.data[0] if model.data else None

    def save_alert_event(self, *, model_version_id, kind, metric, detail):
        alert_id = f"alrt_{uuid.uuid4().hex}"
        self.client.table("alert_event").insert(
            {
                "id": alert_id,
                "model_version_id": model_version_id,
                "kind": kind,
                "metric": metric,
                "detail": detail,
            }
        ).execute()
        return alert_id

    def update_alert_event(self, *, alert_event_id, emailed_at):
        self.client.table("alert_event").update({"emailed_at": emailed_at}).eq(
            "id", alert_event_id
        ).execute()

    def find_alert_events(self, *, model_version_id):
        result = (
            self.client.table("alert_event")
            .select("*")
            .eq("model_version_id", model_version_id)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []
```

> `find_labeled_outcomes` pulling every telemetry_event for a version before filtering to
> labeled ones is a real limitation, named here rather than hidden: it does not scale past
> `label_event` reaching real volume. Acceptable at V1's traffic, and the same relational-
> at-V1-analytics-at-V3 tradeoff `server/telemetry.py` already documents for
> `TELEMETRY_READ_LIMIT`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd server; uv run pytest tests/test_supabase.py -v`
Expected: all pass

- [ ] **Step 5: Run the full server suite**

Run: `cd server; uv run pytest -q`
Expected: 217 + 13 = 230 passed

---

### Task 4: `agents/brain4/falcon/monitor.py` — orchestration and alert thresholds

**Files:**
- Modify: `agents/brain4/falcon/monitor.py`
- Modify: `server/tests/test_falcon_monitor.py`

**Interfaces:**
- Consumes: `agents.brain4.falcon.detect.detect_systemic_anomaly`,
  `detect_quality_anomaly`, and their constants (Task 1); the new store methods (Task 3).
- Produces:
  - `build_alert_thresholds(*, eval_run) -> dict`
  - `configure(...)` — extended to include `alert_thresholds` in its returned config
  - `check_systemic(*, model_version_id, metadata_store, now_fn=None, notify_fn=None) -> dict | None`
  - `check_quality(*, model_version_id, metadata_store, notify_fn=None) -> dict | None`

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_falcon_monitor.py`. Extend the existing `FakeMetadataStore`
in that file rather than writing a second one:

```python
class FakeMetadataStore:
    def __init__(self):
        self.saved = []
        self.telemetry_windows = []   # each `since` value the check asked for, in call order
        self.events = []              # the full window's events, spanning both halves
        self.monitoring_configs = {}
        self.labeled_outcomes = {}

    def save_monitoring_config(self, *, model_version_id, eval_run_id, config):
        self.saved.append(
            {"model_version_id": model_version_id, "eval_run_id": eval_run_id, "config": config}
        )
        return "mcfg_1"

    def find_monitoring_config(self, *, model_version_id):
        return self.monitoring_configs.get(model_version_id)

    def find_labeled_outcomes(self, *, model_version_id, limit=1000):
        return self.labeled_outcomes.get(model_version_id, [])

    def find_telemetry_events(self, *, model_version_id, since, limit=10_000):
        self.telemetry_windows.append(since)
        return self.events
```

`find_telemetry_events` has no upper time bound in its real signature (`since` only), so
`check_systemic` cannot call it twice with two different `since` values and expect
disjoint windows back — the "baseline" call would also include every "recent" event. It
calls the store **once**, covering the full `2*WINDOW_MINUTES` span, and splits the
returned rows in Python using `occurred_at` against the recent/baseline boundary. The
fake mirrors that: it returns one flat `self.events` list regardless of `since`, exactly
as a real `since=baseline_since` query would (that bound is far enough back to include
both windows).

```python
from datetime import datetime, timedelta, timezone

FIXED_NOW = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)


def _events_in_window(start_minutes_ago, end_minutes_ago, count, status="ok"):
    """`count` events evenly spaced between `end_minutes_ago` and `start_minutes_ago`
    minutes before FIXED_NOW -- a slice `check_systemic` will carve out of one combined
    result once it splits by `occurred_at` against its recent/baseline boundary."""
    step = (start_minutes_ago - end_minutes_ago) / count
    return [
        {
            "status": status,
            "latency_ms": 10.0,
            "occurred_at": (FIXED_NOW - timedelta(minutes=end_minutes_ago + i * step)).isoformat(),
        }
        for i in range(count)
    ]


def test_build_alert_thresholds_carries_the_eval_runs_own_thresholds_forward():
    eval_run = {
        "metric_set": {"resolved": ["accuracy", "f1"], "skipped": []},
        "thresholds": [{"metric": "accuracy", "op": ">=", "value": 0.9}],
    }

    thresholds = build_alert_thresholds(eval_run=eval_run)

    assert thresholds["quality_metric_set"] == ["accuracy", "f1"]
    assert thresholds["quality_thresholds"] == eval_run["thresholds"]
    assert thresholds["error_rate_relative_increase"] == RELATIVE_INCREASE_THRESHOLD
    assert thresholds["latency_p95_relative_increase"] == RELATIVE_INCREASE_THRESHOLD


def test_configure_writes_alert_thresholds_alongside_the_eval_reference():
    store = FakeMetadataStore()

    config = configure(
        model_version_id="mv_1", eval_run_id="evr_1",
        eval_run={
            "verdict": "pass", "scores": {},
            "metric_set": {"resolved": ["accuracy"], "skipped": []},
            "thresholds": [{"metric": "accuracy", "op": ">=", "value": 0.9}],
        },
        metadata_store=store,
    )

    assert config["alert_thresholds"]["quality_thresholds"] == [
        {"metric": "accuracy", "op": ">=", "value": 0.9}
    ]
    assert store.saved[0]["config"]["alert_thresholds"] == config["alert_thresholds"]


def test_check_systemic_notifies_on_a_real_jump():
    store = FakeMetadataStore()
    store.events = [
        *_events_in_window(30, 15, WINDOW_MIN_EVENTS),                # baseline: clean
        *_events_in_window(15, 0, WINDOW_MIN_EVENTS, status="error"), # recent: all errors
    ]
    notified = []

    check_systemic(
        model_version_id="mv_1", metadata_store=store,
        now_fn=lambda: FIXED_NOW, notify_fn=lambda **kwargs: notified.append(kwargs),
    )

    assert notified
    assert notified[0]["kind"] == "systemic"
    assert notified[0]["model_version_id"] == "mv_1"


def test_check_systemic_does_not_notify_when_nothing_is_wrong():
    store = FakeMetadataStore()
    store.events = [
        *_events_in_window(30, 15, WINDOW_MIN_EVENTS),
        *_events_in_window(15, 0, WINDOW_MIN_EVENTS),
    ]
    notified = []

    check_systemic(
        model_version_id="mv_1", metadata_store=store,
        now_fn=lambda: FIXED_NOW, notify_fn=lambda **kwargs: notified.append(kwargs),
    )

    assert notified == []


def test_check_systemic_swallows_a_raising_notify_fn():
    store = FakeMetadataStore()
    store.events = [
        *_events_in_window(30, 15, WINDOW_MIN_EVENTS),
        *_events_in_window(15, 0, WINDOW_MIN_EVENTS, status="error"),
    ]

    def boom(**kwargs):
        raise RuntimeError("email provider down")

    # Must not raise: a broken notification path cannot break telemetry recording.
    check_systemic(
        model_version_id="mv_1", metadata_store=store,
        now_fn=lambda: FIXED_NOW, notify_fn=boom,
    )


def test_check_quality_is_a_noop_below_the_minimum_label_count():
    store = FakeMetadataStore()
    store.monitoring_configs["mv_1"] = {
        "alert_thresholds": {
            "quality_metric_set": ["accuracy"],
            "quality_thresholds": [{"metric": "accuracy", "op": ">=", "value": 0.99}],
        }
    }
    store.labeled_outcomes["mv_1"] = [
        {"y_true": 1, "y_pred": 0, "y_proba": None} for _ in range(MIN_LABELS - 1)
    ]
    notified = []

    check_quality(
        model_version_id="mv_1", metadata_store=store,
        notify_fn=lambda **kwargs: notified.append(kwargs),
    )

    assert notified == []


def test_check_quality_notifies_on_a_real_threshold_failure():
    store = FakeMetadataStore()
    store.monitoring_configs["mv_1"] = {
        "alert_thresholds": {
            "quality_metric_set": ["accuracy"],
            "quality_thresholds": [{"metric": "accuracy", "op": ">=", "value": 0.99}],
        }
    }
    store.labeled_outcomes["mv_1"] = [
        {"y_true": 1, "y_pred": 0, "y_proba": None} for _ in range(MIN_LABELS)
    ]
    notified = []

    check_quality(
        model_version_id="mv_1", metadata_store=store,
        notify_fn=lambda **kwargs: notified.append(kwargs),
    )

    assert notified
    assert notified[0]["kind"] == "quality"


def test_check_quality_is_a_noop_when_the_version_has_no_monitoring_config():
    store = FakeMetadataStore()
    notified = []

    # A version predating this feature, or one whose config write failed, must not crash
    # the outcome-reporting endpoint that calls this.
    check_quality(
        model_version_id="mv_never_configured", metadata_store=store,
        notify_fn=lambda **kwargs: notified.append(kwargs),
    )

    assert notified == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd server; uv run pytest tests/test_falcon_monitor.py -v -k "alert_thresholds or check_"`
Expected: FAIL — `ImportError: cannot import name 'build_alert_thresholds'`

- [ ] **Step 3: Implement the additions to `monitor.py`**

```python
from agents.brain2.nat.score import RESOURCE_PREFIX
from agents.brain4.falcon.detect import (
    MIN_LABELS,
    RELATIVE_INCREASE_THRESHOLD,
    WINDOW_MIN_EVENTS,
    WINDOW_MINUTES,
    detect_quality_anomaly,
    detect_systemic_anomaly,
)

# ... existing METRICS, build_eval_reference ...


def build_alert_thresholds(*, eval_run):
    """Freeze the exact metric_set and thresholds that gated this promotion, the same
    "as applied" philosophy as eval_run.thresholds: a later change to the detection
    defaults must not retroactively change what an already-promoted version is watched
    against."""
    return {
        "error_rate_relative_increase": RELATIVE_INCREASE_THRESHOLD,
        "latency_p95_relative_increase": RELATIVE_INCREASE_THRESHOLD,
        "quality_metric_set": eval_run.get("metric_set", {}).get("resolved", []),
        "quality_thresholds": eval_run.get("thresholds", []),
    }


def configure(*, model_version_id, eval_run_id, eval_run, metadata_store):
    config = {
        "metrics": METRICS,
        "eval_reference": build_eval_reference(
            eval_run_id=eval_run_id, scores=eval_run.get("scores", {})
        ),
        "alert_thresholds": build_alert_thresholds(eval_run=eval_run),
    }
    config_id = metadata_store.save_monitoring_config(
        model_version_id=model_version_id, eval_run_id=eval_run_id, config=config
    )
    return {"id": config_id, **config}


def check_systemic(*, model_version_id, metadata_store, now_fn=None, notify_fn=None):
    """Compare the two adjacent WINDOW_MINUTES windows for this version and notify on an
    anomaly. Non-fatal: called from a background flush and a request path, neither of
    which may fail because a detection bug did."""
    now_fn = now_fn or _default_now
    notify_fn = notify_fn or _default_notify

    from telemetry import summarize

    now = now_fn()
    recent_since = now - _minutes(WINDOW_MINUTES)
    baseline_since = now - _minutes(2 * WINDOW_MINUTES)

    try:
        # find_telemetry_events has no upper bound (`since` only) — calling it twice
        # with two different `since` values would make the "baseline" window also
        # include every "recent" event. One call over the full span, split here.
        events = metadata_store.find_telemetry_events(
            model_version_id=model_version_id, since=_iso(baseline_since)
        )
        # Boundary is > / <=, not >= / <, deliberately: the >= variant was found during
        # Task 4's implementation to route an event landing exactly on recent_since into
        # the recent window, shrinking the baseline window by one and intermittently
        # failing WINDOW_MIN_EVENTS. > / <= makes the boundary tick the baseline's last
        # point rather than the recent window's first, with no gap or double-count.
        recent_events = [e for e in events if _occurred_at(e) > recent_since]
        baseline_events = [e for e in events if _occurred_at(e) <= recent_since]
        anomaly = detect_systemic_anomaly(
            recent_summary=summarize(events=recent_events),
            baseline_summary=summarize(events=baseline_events),
        )
        if anomaly is None:
            return None
        notify_fn(
            model_version_id=model_version_id, kind="systemic",
            metric=anomaly["metric"], detail=anomaly, metadata_store=metadata_store,
        )
        return anomaly
    except Exception:  # noqa: BLE001
        return None


def check_quality(*, model_version_id, metadata_store, notify_fn=None):
    """Below MIN_LABELS, a no-op. Otherwise re-score against the exact thresholds that
    gated this version's promotion and notify on a failure. Non-fatal for the same
    reason as check_systemic."""
    notify_fn = notify_fn or _default_notify

    try:
        config = metadata_store.find_monitoring_config(model_version_id=model_version_id)
        if not config or not config.get("alert_thresholds"):
            return None

        outcomes = metadata_store.find_labeled_outcomes(model_version_id=model_version_id)
        if len(outcomes) < MIN_LABELS:
            return None

        thresholds = config["alert_thresholds"]
        anomaly = detect_quality_anomaly(
            metric_set=thresholds["quality_metric_set"],
            thresholds=thresholds["quality_thresholds"],
            y_true=[o["y_true"] for o in outcomes],
            y_pred=[o["y_pred"] for o in outcomes],
            y_proba=[o["y_proba"] for o in outcomes] if any(o["y_proba"] for o in outcomes) else None,
        )
        if anomaly is None:
            return None
        notify_fn(
            model_version_id=model_version_id, kind="quality",
            metric=anomaly["metric"], detail=anomaly, metadata_store=metadata_store,
        )
        return anomaly
    except Exception:  # noqa: BLE001
        return None


def _minutes(n):
    from datetime import timedelta

    return timedelta(minutes=n)


def _iso(dt):
    return dt.isoformat()


def _occurred_at(event):
    """Parse a telemetry_event's occurred_at back into a comparable datetime.

    Handles a trailing "Z" (some ISO producers use it, Python's own isoformat() output
    from _iso() above doesn't) so a real Postgres-returned timestamp and a test fixture
    built with _iso() compare correctly either way.
    """
    from datetime import datetime

    value = event["occurred_at"]
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def _default_now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _default_notify(**kwargs):
    from agents.brain4.falcon.notify import record_and_notify

    return record_and_notify(**kwargs)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd server; uv run pytest tests/test_falcon_monitor.py -v`
Expected: all pass (existing + 8 new)

- [ ] **Step 5: Run the full server suite**

Run: `cd server; uv run pytest -q`
Expected: 230 + 8 = 238 passed

---

### Task 5: Notification — `alert_event` row plus best-effort SES email

**Files:**
- Create: `agents/brain4/falcon/email.py`
- Create: `agents/brain4/falcon/notify.py`
- Create: `server/tests/test_falcon_notify.py`
- Modify: `server/pyproject.toml`

**Interfaces:**
- Consumes: `find_model_by_version`, `save_alert_event`, `update_alert_event` (Task 3).
- Produces: `record_and_notify(*, model_version_id, kind, metric, detail, metadata_store,
  email_fn=None) -> str`, `send_alert_email(*, to, subject, body, client=None) -> None`.

- [ ] **Step 1: Add the SES dependency**

`boto3` is already a transitive dependency via S3, but add it explicitly to
`server/pyproject.toml`'s `dependencies` if it is not already a direct one — check with
`grep boto3 server/pyproject.toml` first; it is already there for `S3BlobStore`, so no
change needed. Confirm: `cd server; uv run python -c "import boto3; boto3.client('ses',
region_name='us-east-1')"` succeeds without a network call (client construction alone
doesn't need credentials to succeed).

- [ ] **Step 2: Write the failing tests**

```python
# server/tests/test_falcon_notify.py
from agents.brain4.falcon.notify import record_and_notify


class FakeMetadataStore:
    def __init__(self, model_with_email=None):
        self.saved_alerts = []
        self.updated_alerts = []
        self._model = model_with_email

    def save_alert_event(self, *, model_version_id, kind, metric, detail):
        self.saved_alerts.append(
            {"model_version_id": model_version_id, "kind": kind, "metric": metric, "detail": detail}
        )
        return "alrt_1"

    def update_alert_event(self, *, alert_event_id, emailed_at):
        self.updated_alerts.append({"alert_event_id": alert_event_id, "emailed_at": emailed_at})

    def find_model_by_version(self, *, model_version_id):
        return self._model


def test_record_and_notify_writes_the_alert_row_first():
    store = FakeMetadataStore(model_with_email={"alert_email": None})

    alert_id = record_and_notify(
        model_version_id="mv_1", kind="systemic", metric="error_rate",
        detail={"recent": 0.3}, metadata_store=store,
        email_fn=lambda **kwargs: None,
    )

    assert alert_id == "alrt_1"
    assert store.saved_alerts[0]["kind"] == "systemic"


def test_record_and_notify_skips_email_when_no_address_is_configured():
    store = FakeMetadataStore(model_with_email={"alert_email": None})
    sent = []

    record_and_notify(
        model_version_id="mv_1", kind="systemic", metric="error_rate",
        detail={}, metadata_store=store, email_fn=lambda **kwargs: sent.append(kwargs),
    )

    assert sent == []
    assert store.updated_alerts == []


def test_record_and_notify_sends_email_when_an_address_is_configured():
    store = FakeMetadataStore(model_with_email={"alert_email": "ops@example.com"})
    sent = []

    record_and_notify(
        model_version_id="mv_1", kind="quality", metric="accuracy",
        detail={"actual": 0.5}, metadata_store=store,
        email_fn=lambda **kwargs: sent.append(kwargs),
    )

    assert sent[0]["to"] == "ops@example.com"
    assert "accuracy" in sent[0]["subject"]
    assert store.updated_alerts[0]["alert_event_id"] == "alrt_1"


def test_a_raising_email_fn_does_not_prevent_the_alert_from_being_recorded():
    store = FakeMetadataStore(model_with_email={"alert_email": "ops@example.com"})

    def boom(**kwargs):
        raise RuntimeError("SES is down")

    # Must not raise: the alert row already landed, which is the source of truth.
    alert_id = record_and_notify(
        model_version_id="mv_1", kind="systemic", metric="latency_p95_ms",
        detail={}, metadata_store=store, email_fn=boom,
    )

    assert alert_id == "alrt_1"
    assert store.updated_alerts == []  # never confirmed sent


def test_record_and_notify_survives_no_model_found_for_the_version():
    store = FakeMetadataStore(model_with_email=None)
    sent = []

    # A version whose model lookup fails for any reason still gets its in-app row.
    alert_id = record_and_notify(
        model_version_id="mv_1", kind="systemic", metric="error_rate",
        detail={}, metadata_store=store, email_fn=lambda **kwargs: sent.append(kwargs),
    )

    assert alert_id == "alrt_1"
    assert sent == []
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd server; uv run pytest tests/test_falcon_notify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.brain4.falcon.notify'`

- [ ] **Step 4: Implement `email.py`**

```python
# agents/brain4/falcon/email.py
"""Best-effort alert delivery via AWS SES — the same account api-fication's S3 usage
lives in, so no new vendor. Failure here must never be the reason an alert goes
unrecorded; see notify.py for how that's enforced.
"""

import os


def send_alert_email(*, to, subject, body, client=None):
    client = client or _real_client()
    client.send_email(
        Source=os.environ.get("SES_SENDER", "alerts@verity.dev"),
        Destination={"ToAddresses": [to]},
        Message={
            "Subject": {"Data": subject},
            "Body": {"Text": {"Data": body}},
        },
    )


def _real_client():
    import boto3

    return boto3.client("ses", region_name=os.environ.get("SES_REGION", "us-east-1"))
```

- [ ] **Step 5: Implement `notify.py`**

```python
# agents/brain4/falcon/notify.py
"""Falcon's notification: an in-app row first, an email best-effort second.

The row is the source of truth for whether an alert fired at all. Email delivery can
fail — a dead SES endpoint, an unconfigured account — without that failure erasing the
alert; it just arrives without an email a human happened to see.
"""


def record_and_notify(*, model_version_id, kind, metric, detail, metadata_store, email_fn=None):
    email_fn = email_fn or _default_email_fn

    alert_id = metadata_store.save_alert_event(
        model_version_id=model_version_id, kind=kind, metric=metric, detail=detail
    )

    model = _find_model(metadata_store, model_version_id)
    to = (model or {}).get("alert_email")
    if not to:
        return alert_id

    try:
        email_fn(
            to=to,
            subject=f"Verity alert: {kind} — {metric}",
            body=f"model_version_id={model_version_id}\nkind={kind}\nmetric={metric}\ndetail={detail}",
        )
    except Exception:  # noqa: BLE001 - the row already landed; email is best-effort
        return alert_id

    metadata_store.update_alert_event(alert_event_id=alert_id, emailed_at=_now_iso())
    return alert_id


def _find_model(metadata_store, model_version_id):
    try:
        return metadata_store.find_model_by_version(model_version_id=model_version_id)
    except Exception:  # noqa: BLE001
        return None


def _now_iso():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _default_email_fn(**kwargs):
    from agents.brain4.falcon.email import send_alert_email

    return send_alert_email(**kwargs)
```

- [ ] **Step 6: Run to verify it passes**

Run: `cd server; uv run pytest tests/test_falcon_notify.py -v`
Expected: 5 passed

- [ ] **Step 7: Run the full server suite**

Run: `cd server; uv run pytest -q`
Expected: 238 + 5 = 243 passed

---

### Task 6: Wire `check_systemic` into `TelemetrySink.flush()` and the `/telemetry` route

**Files:**
- Modify: `server/serving/sink.py`
- Modify: `server/main.py`
- Modify: `server/tests/test_proxy.py`
- Modify: `server/tests/test_main.py`

**Interfaces:**
- Consumes: `agents.brain4.falcon.monitor.check_systemic` (Task 4).
- Produces: `TelemetrySink.__init__` gains `detect_fn=None`; `/telemetry` calls the same
  check after a successful write.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_proxy.py`:

```python
def test_flush_calls_detect_fn_once_per_distinct_model_version_in_the_batch():
    store = RecordingStore()
    calls = []
    sink = TelemetrySink(
        metadata_store=store, maxsize=10, flush_interval=3600,
        detect_fn=lambda model_version_id: calls.append(model_version_id),
    )
    sink.record({"model_version_id": "mv_1"})
    sink.record({"model_version_id": "mv_1"})
    sink.record({"model_version_id": "mv_2"})

    sink.flush()

    assert sorted(calls) == ["mv_1", "mv_2"]


def test_a_raising_detect_fn_does_not_stop_flush_from_returning_its_count():
    store = RecordingStore()

    def boom(model_version_id):
        raise RuntimeError("detection is broken")

    sink = TelemetrySink(metadata_store=store, maxsize=10, flush_interval=3600, detect_fn=boom)
    sink.record({"model_version_id": "mv_1"})

    assert sink.flush() == 1  # the save succeeded; detection failing must not undo that


def test_flush_with_no_events_never_calls_detect_fn():
    calls = []
    sink = TelemetrySink(
        metadata_store=RecordingStore(), maxsize=10, flush_interval=3600,
        detect_fn=lambda model_version_id: calls.append(model_version_id),
    )

    sink.flush()

    assert calls == []
```

Append to `server/tests/test_main.py`:

```python
def test_ingest_telemetry_calls_check_systemic_for_each_distinct_model_version():
    import main

    calls = []
    original = main.get_metadata_store

    class NoopStore:
        def save_telemetry_events(self, *, events):
            return len(events)

    app.dependency_overrides[main.get_metadata_store] = lambda: NoopStore()
    app.dependency_overrides[main.get_systemic_check] = lambda: (lambda mv: calls.append(mv))
    try:
        TestClient(app).post(
            "/telemetry",
            json={
                "events": [
                    {"model_version_id": "mv_1", "occurred_at": "2026-08-23T00:00:00Z", "status": "ok"},
                    {"model_version_id": "mv_2", "occurred_at": "2026-08-23T00:00:00Z", "status": "ok"},
                ]
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert sorted(calls) == ["mv_1", "mv_2"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd server; uv run pytest tests/test_proxy.py tests/test_main.py -v -k "detect_fn or check_systemic"`
Expected: FAIL — `TypeError: TelemetrySink.__init__() got an unexpected keyword argument
'detect_fn'`

- [ ] **Step 3: Extend `TelemetrySink`**

```python
class TelemetrySink:
    def __init__(self, metadata_store, maxsize=10_000, flush_interval=5.0, detect_fn=None):
        self.metadata_store = metadata_store
        self.queue = queue.Queue(maxsize=maxsize)
        self.flush_interval = flush_interval
        # Bound *after* self.metadata_store exists, not a @staticmethod: the default
        # must reuse the store already injected into this sink, not construct a second
        # real one. A version of this that built its own SupabaseMetadataStore() would
        # both duplicate the connection and silently ignore a fake store injected for
        # tests, since detect_fn would call the real thing regardless.
        self.detect_fn = detect_fn or self._default_detect_fn
        self.dropped = 0
        self._dropped_lock = threading.Lock()
        self._stopping = threading.Event()
        self._thread = None

    def _default_detect_fn(self, model_version_id):
        from agents.brain4.falcon.monitor import check_systemic

        check_systemic(model_version_id=model_version_id, metadata_store=self.metadata_store)
```

Modify `flush()`:

```python
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
            written = self.metadata_store.save_telemetry_events(events=events)
        except Exception:  # noqa: BLE001 - a failed telemetry write is not an incident
            return 0

        # Detection runs after the write succeeds, once per distinct version in this
        # batch — never per event, and never ahead of the data it needs existing.
        for model_version_id in {e["model_version_id"] for e in events}:
            try:
                self.detect_fn(model_version_id)
            except Exception:  # noqa: BLE001 - detection failing must not undo a write
                pass
        return written
```

> `_default_detect_fn` is a bound method, not a `@staticmethod`, specifically so it can
> read `self.metadata_store` — the same store this sink was constructed with, which is
> already whatever `get_telemetry_sink()` in `main.py` passed in. This still follows the
> lazy-real-default idiom (`detect_fn=None` → `detect_fn or self._default_detect_fn` →
> the deferred `import` inside); it just resolves its collaborator from `self` instead of
> constructing a fresh one, because a fresh one would silently bypass whatever store a
> test injected.

- [ ] **Step 4: Wire `/telemetry` in `main.py`**

Add a dependency and use it:

```python
@lru_cache
def get_systemic_check():
    from agents.brain4.falcon.monitor import check_systemic

    def run(model_version_id):
        check_systemic(model_version_id=model_version_id, metadata_store=get_metadata_store())

    return run
```

```python
@app.post("/telemetry")
async def ingest_telemetry(
    batch: TelemetryBatch,
    metadata_store=Depends(get_metadata_store),
    systemic_check=Depends(get_systemic_check),
):
    written = metadata_store.save_telemetry_events(
        events=[event.model_dump(mode="json") for event in batch.events]
    )
    for model_version_id in {event.model_version_id for event in batch.events}:
        try:
            systemic_check(model_version_id)
        except Exception:  # noqa: BLE001 - detection failing must not fail ingestion
            pass
    return {"written": written}
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd server; uv run pytest tests/test_proxy.py tests/test_main.py -v`
Expected: all pass

- [ ] **Step 6: Run the full server suite**

Run: `cd server; uv run pytest -q`
Expected: 243 + 4 = 247 passed

---

### Task 7: `prediction_id` in the proxy response, and the outcome-reporting endpoint

**Files:**
- Modify: `server/main.py`
- Modify: `server/tests/test_proxy.py`

**Interfaces:**
- Consumes: `find_telemetry_event_by_prediction_id`, `save_label_event`,
  `agents.brain4.falcon.monitor.check_quality` (Tasks 3–4).
- Produces: `predict()` returns `{..., "prediction_id": "pred_..."}`;
  `POST /predictions/{prediction_id}/outcomes`.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_proxy.py`:

```python
def test_predict_returns_a_prediction_id():
    client = _client(FakeStore(VERSION, LIVE), FakeSink(), FakeTransport())

    body = client.post(
        "/users/u_1/models/fraud/predict", json={"instances": [[1.0, 2.0]]}
    ).json()

    assert body["prediction_id"].startswith("pred_")


def test_the_prediction_id_is_recorded_on_the_telemetry_event():
    sink = FakeSink()
    client = _client(FakeStore(VERSION, LIVE), sink, FakeTransport())

    body = client.post(
        "/users/u_1/models/fraud/predict", json={"instances": [[1.0]]}
    ).json()

    assert sink.events[0]["prediction_id"] == body["prediction_id"]


class FakeOutcomeStore:
    def __init__(self, event=None):
        self.event = event
        self.saved_labels = []
        self.checked = []

    def find_telemetry_event_by_prediction_id(self, *, prediction_id):
        return self.event

    def save_label_event(self, *, telemetry_event_id, instance_index, actual):
        self.saved_labels.append(
            {"telemetry_event_id": telemetry_event_id, "instance_index": instance_index, "actual": actual}
        )
        return "lbl_1"


def test_outcomes_404s_for_an_unknown_prediction_id():
    client = _client(FakeOutcomeStore(event=None), FakeSink(), FakeTransport())

    response = client.post(
        "/predictions/pred_nope/outcomes", json={"outcomes": [{"index": 0, "actual": 1}]}
    )

    assert response.status_code == 404


def test_outcomes_422s_for_an_index_outside_the_recorded_predictions():
    event = {"id": 1, "model_version_id": "mv_1", "prediction": {"predictions": [1]}}
    client = _client(FakeOutcomeStore(event=event), FakeSink(), FakeTransport())

    response = client.post(
        "/predictions/pred_abc/outcomes", json={"outcomes": [{"index": 5, "actual": 1}]}
    )

    assert response.status_code == 422


def test_a_valid_outcome_is_saved_and_triggers_the_quality_check():
    event = {"id": 1, "model_version_id": "mv_1", "prediction": {"predictions": [1]}}
    store = FakeOutcomeStore(event=event)
    app.dependency_overrides[get_metadata_store] = lambda: store
    app.dependency_overrides[get_quality_check] = lambda: (
        lambda model_version_id: store.checked.append(model_version_id)
    )
    try:
        response = TestClient(app).post(
            "/predictions/pred_abc/outcomes", json={"outcomes": [{"index": 0, "actual": 1}]}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert store.saved_labels[0]["telemetry_event_id"] == 1
    assert store.checked == ["mv_1"]
```

Add the import at the top of `server/tests/test_proxy.py`:

```python
from main import app, get_metadata_store, get_predict_transport, get_quality_check, get_telemetry_sink
```

(replacing the existing narrower import line)

- [ ] **Step 2: Run to verify it fails**

Run: `cd server; uv run pytest tests/test_proxy.py -v -k "prediction_id or outcomes"`
Expected: FAIL — `ImportError: cannot import name 'get_quality_check' from 'main'`

- [ ] **Step 3: Add `prediction_id` generation to `predict()`**

```python
@lru_cache
def get_quality_check():
    from agents.brain4.falcon.monitor import check_quality

    def run(model_version_id):
        check_quality(model_version_id=model_version_id, metadata_store=get_metadata_store())

    return run
```

Modify `predict()` — generate the id before the container call, thread it through
`_record`, and return it:

```python
@app.post("/users/{user_id}/models/{name}/predict")
async def predict(
    user_id: str,
    name: str,
    body: dict,
    metadata_store=Depends(get_metadata_store),
    sink=Depends(get_telemetry_sink),
    transport=Depends(get_predict_transport),
):
    version = metadata_store.find_production_version_by_name(user_id=user_id, name=name)
    if version is None:
        raise HTTPException(404, f"no production version of {name!r} for user {user_id!r}")

    deployment = metadata_store.find_live_deployment(model_version_id=version["id"])
    if deployment is None:
        raise HTTPException(
            404,
            f"{name!r} is promoted but not deployed — check its deployment row for the reason",
        )

    # Minted here, not read back from the DB insert: TelemetrySink queues writes, so the
    # database-assigned bigint id does not exist yet when this response is returned. A
    # customer reporting a delayed outcome needs something to correlate against right now.
    prediction_id = f"pred_{uuid.uuid4().hex}"

    started = time.perf_counter()
    try:
        response = transport.post(
            f"{deployment['endpoint_url']}/predict", json=body, timeout=30.0
        )
        prediction = response.json()
    except Exception as exc:  # noqa: BLE001 - the container is out of our process
        _record(sink, version["id"], started, body, None, exc, prediction_id)
        raise HTTPException(502, f"model container did not answer: {type(exc).__name__}")

    _record(sink, version["id"], started, body, prediction, None, prediction_id)
    return {**prediction, "prediction_id": prediction_id}


def _record(sink, model_version_id, started, inputs, prediction, exc, prediction_id):
    try:
        sink.record(
            {
                "model_version_id": model_version_id,
                "prediction_id": prediction_id,
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

Add `import uuid` to the top of `server/main.py`.

- [ ] **Step 4: Add the outcomes route**

```python
class Outcome(BaseModel):
    index: int
    actual: object


class OutcomeBatch(BaseModel):
    outcomes: list[Outcome] = Field(default=..., max_length=1000)


@app.post("/predictions/{prediction_id}/outcomes")
async def report_outcomes(
    prediction_id: str,
    batch: OutcomeBatch,
    metadata_store=Depends(get_metadata_store),
    quality_check=Depends(get_quality_check),
):
    event = metadata_store.find_telemetry_event_by_prediction_id(prediction_id=prediction_id)
    if event is None:
        raise HTTPException(404, f"no prediction found for {prediction_id!r}")

    predictions = (event.get("prediction") or {}).get("predictions") or []
    for outcome in batch.outcomes:
        if outcome.index < 0 or outcome.index >= len(predictions):
            raise HTTPException(
                422,
                f"index {outcome.index} is outside this prediction's batch of {len(predictions)}",
            )

    for outcome in batch.outcomes:
        metadata_store.save_label_event(
            telemetry_event_id=event["id"], instance_index=outcome.index,
            actual={"y": outcome.actual},
        )

    try:
        quality_check(event["model_version_id"])
    except Exception:  # noqa: BLE001 - detection failing must not fail label reporting
        pass

    return {"recorded": len(batch.outcomes)}
```

> Validation runs as a full pass over `batch.outcomes` *before* any `save_label_event`
> call, so a batch containing one bad index writes nothing rather than partially
> recording.

- [ ] **Step 5: Run to verify it passes**

Run: `cd server; uv run pytest tests/test_proxy.py -v`
Expected: all pass (existing + 6 new)

- [ ] **Step 6: Run the full server suite**

Run: `cd server; uv run pytest -q`
Expected: 247 + 6 = 253 passed

---

### Task 8: SDK — `alert_email` at `assemble()` time

**Files:**
- Modify: `verity/src/verity/client.py`
- Modify: `verity/src/verity/transport.py`
- Modify: `verity/tests/test_client.py`
- Modify: `verity/tests/test_transport.py`
- Modify: `server/main.py`
- Modify: `server/orchestrator.py`
- Modify: `agents/brain3/fury/registry.py`
- Modify: `server/tests/test_main.py`
- Modify: `server/tests/test_orchestrator.py`
- Modify: `server/tests/test_fury_registry.py`

**Interfaces:**
- Consumes: `create_model(..., alert_email=None)` (Task 3).
- Produces: `verity.assemble(..., alert_email=None)`; threaded through `upload()`,
  `/ingest`, `build_artifact()`, `register()`, unchanged for every existing caller that
  doesn't pass it.

- [ ] **Step 1: Write the failing SDK tests**

Append to `verity/tests/test_transport.py`:

```python
def test_upload_sends_the_alert_email_as_a_form_field_when_given():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"status": "pending"})

    client = httpx.Client(transport=httpx.MockTransport(handler))

    upload(
        payload=b"bytes", sha256="abc", user_id="u_1", name="m", args={},
        endpoint="http://verity-server.test", client=client, alert_email="ops@example.com",
    )

    assert b"ops@example.com" in captured["request"].content


def test_upload_omits_alert_email_when_not_given():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"status": "pending"})

    client = httpx.Client(transport=httpx.MockTransport(handler))

    upload(
        payload=b"bytes", sha256="abc", user_id="u_1", name="m", args={},
        endpoint="http://verity-server.test", client=client,
    )

    assert b"alert_email" not in captured["request"].content
```

Append to `verity/tests/test_client.py` (matching whatever fake-transport-capture
convention that file already uses — inspect it first; if it patches `upload` directly,
follow that pattern rather than introducing a new one):

```python
def test_assemble_forwards_alert_email_to_upload(monkeypatch):
    import verity.client as client_module

    captured = {}
    monkeypatch.setattr(
        client_module, "upload", lambda **kwargs: captured.update(kwargs) or {"status": "pending"}
    )

    client_module.assemble(
        FakeModel(), user_id="u_1", name="m", alert_email="ops@example.com",
    )

    assert captured["alert_email"] == "ops@example.com"
```

> If `verity/tests/test_client.py` has no `FakeModel` already, check what the existing
> `assemble()` tests in that file use for a model object and reuse it rather than
> inventing a second fixture.

- [ ] **Step 2: Run to verify it fails**

Run: `cd verity; uv run pytest tests/test_transport.py tests/test_client.py -v -k alert_email`
Expected: FAIL — `TypeError: upload() got an unexpected keyword argument 'alert_email'`

- [ ] **Step 3: Wire `alert_email` through the SDK**

In `verity/src/verity/transport.py`:

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
        alert_email: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,

) -> dict:
    client = client or httpx.Client(timeout=timeout)
    files = {"artifact": ("artifact", payload, "application/octet-stream")}
    data = {"user_id": user_id, "name": name, "sha256":sha256, "args": json.dumps(args)}

    if fixture_payload is not None:
        files["fixture"] = ("fixture", fixture_payload, "application/octet-stream")
        data["fixture_descriptor"] = json.dumps(fixture_descriptor)

    if environment is not None:
        data["environment"] = json.dumps(environment)

    # Where a human is notified when Falcon detects something off. Optional, and only
    # meaningful on the upload that first creates the model — later versions of an
    # existing model don't need to repeat it.
    if alert_email is not None:
        data["alert_email"] = alert_email

    response = client.post(
        f"{endpoint}/ingest",
        files = files,
        data = data,
    )
    response.raise_for_status()
    return response.json()
```

In `verity/src/verity/client.py`:

```python
def assemble(
    model,
    user_id: str,
    name: str,
    endpoint: str = "http://localhost:8000",
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    X_test=None,
    y_test=None,
    fixture: tuple | None = None,
    alert_email: str | None = None,
    **args,
) -> dict:
    """..."""
    fixture_payload, fixture_descriptor = _resolve_fixture(X_test, y_test, fixture)
    payload, sha256 = serialize(model)
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
        alert_email=alert_email,
        timeout=timeout,
    )
```

- [ ] **Step 4: Run to verify the SDK tests pass**

Run: `cd verity; uv run pytest -q`
Expected: 45 existing + 3 new = 48 passed

- [ ] **Step 5: Thread it through the server side — write the failing tests first**

Append to `server/tests/test_main.py`:

```python
def test_ingest_forwards_the_alert_email_to_the_orchestrator():
    captured = {}

    def fake_build_artifact(**kwargs):
        captured.update(kwargs)
        return {"model_version_id": "mv_1"}

    app.dependency_overrides[get_build_artifact] = lambda: fake_build_artifact
    try:
        TestClient(app).post(
            "/ingest",
            files={"artifact": ("artifact", b"bytes")},
            data={"user_id": "u_1", "name": "m", "sha256": "abc", "alert_email": "ops@example.com"},
        )
    finally:
        app.dependency_overrides.clear()

    assert captured["alert_email"] == "ops@example.com"
```

Append to `server/tests/test_orchestrator.py`:

```python
def test_alert_email_is_forwarded_to_register_fn():
    captured = {}

    def recording_register(**kwargs):
        captured.update(kwargs)
        return {"model_id": "mdl_123", "status": "pending", "archived_model_version_id": None}

    _build(register_fn=recording_register, alert_email="ops@example.com")

    assert captured["alert_email"] == "ops@example.com"
```

Append to `server/tests/test_fury_registry.py` (matching that file's existing
`FakeMetadataStore`):

```python
def test_register_creates_a_new_model_with_the_given_alert_email():
    store = FakeMetadataStore()

    register(
        user_id="u_1", name="fraud", model_version_id="mv_1",
        manifest={"model_class": "sklearn", "task_type": "classification"},
        verdict=None, eval_run_id=None, metadata_store=store,
        alert_email="ops@example.com",
    )

    assert store.created_models[0]["alert_email"] == "ops@example.com"
```

> Check `test_fury_registry.py`'s existing `FakeMetadataStore.create_model` — it likely
> needs an `alert_email=None` parameter added to accept the new keyword, matching how
> real `create_model` now does.

- [ ] **Step 6: Run to verify it fails**

Run: `cd server; uv run pytest tests/test_main.py tests/test_orchestrator.py tests/test_fury_registry.py -v -k alert_email`
Expected: FAIL — `TypeError: build_artifact() got an unexpected keyword argument 'alert_email'`

- [ ] **Step 7: Wire `alert_email` through the server**

In `server/main.py`'s `/ingest`:

```python
@app.post("/ingest")
async def ingest(
    artifact: UploadFile,
    user_id: str = Form(...),
    name: str = Form(...),
    sha256: str = Form(...),
    args: str = Form("{}"),
    fixture: UploadFile | None = File(None),
    fixture_descriptor: str | None = Form(None),
    environment: str | None = Form(None),
    alert_email: str | None = Form(None),
    build_artifact_fn: Callable = Depends(get_build_artifact),
):
    payload = await artifact.read()
    fixture_payload = await fixture.read() if fixture is not None else None
    return build_artifact_fn(
        payload = payload,
        sha256 = sha256,
        user_id=user_id,
        name=name,
        args=json.loads(args),
        fixture_payload=fixture_payload,
        fixture_descriptor=json.loads(fixture_descriptor) if fixture_descriptor else None,
        environment=json.loads(environment) if environment else None,
        alert_email=alert_email,
    )
```

In `server/orchestrator.py`, add the parameter and thread it into `register_fn`:

```python
def build_artifact(
    *,
    payload,
    sha256,
    user_id,
    name,
    args,
    blob_store,
    metadata_store,
    identify_fn=None,
    evaluate_fn=None,
    find_existing_fn=None,
    register_fn=None,
    configure_fn=None,
    deploy_fn=None,
    introspect_fn=None,
    fixture_payload=None,
    fixture_descriptor=None,
    environment=None,
    alert_email=None,
):
```

and:

```python
    registration = register_fn(
        user_id=user_id,
        name=name,
        model_version_id=model_version_id,
        manifest=manifest,
        verdict=verdict,
        eval_run_id=eval_run_id,
        metadata_store=metadata_store,
        alert_email=alert_email,
    )
```

In `agents/brain3/fury/registry.py`:

```python
def register(*, user_id, name, model_version_id, manifest, verdict, eval_run_id, metadata_store, alert_email=None):
    model = metadata_store.find_model(user_id=user_id, name=name)
    if model is None:
        model_id = metadata_store.create_model(
            user_id=user_id,
            name=name,
            model_class=manifest.get("model_class"),
            task_type=manifest.get("task_type"),
            alert_email=alert_email,
        )
    else:
        model_id = model["id"]
    # ... unchanged from here
```

- [ ] **Step 8: Run to verify it passes**

Run: `cd server; uv run pytest tests/test_main.py tests/test_orchestrator.py tests/test_fury_registry.py -v`
Expected: all pass

- [ ] **Step 9: Run the full server suite**

Run: `cd server; uv run pytest -q`
Expected: 253 + 3 = 256 passed

---

### Task 9: `GET /models/{model_version_id}/alerts` — the in-app read

**Files:**
- Modify: `server/main.py`
- Modify: `server/tests/test_main.py`

**Interfaces:**
- Consumes: `find_alert_events` (Task 3).
- Produces: `GET /models/{model_version_id}/alerts` → `{"model_version_id", "alerts": [...]}`

- [ ] **Step 1: Write the failing test**

Append to `server/tests/test_main.py`:

```python
def test_get_alerts_returns_the_stored_rows_for_the_version():
    class FakeStore:
        def find_alert_events(self, *, model_version_id):
            return [{"id": "alrt_1", "model_version_id": model_version_id, "kind": "systemic"}]

    app.dependency_overrides[get_metadata_store] = lambda: FakeStore()
    try:
        response = TestClient(app).get("/models/mv_1/alerts")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["model_version_id"] == "mv_1"
    assert body["alerts"][0]["kind"] == "systemic"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd server; uv run pytest tests/test_main.py -v -k get_alerts`
Expected: FAIL — 404 Not Found, route doesn't exist yet

- [ ] **Step 3: Add the route**

```python
@app.get("/models/{model_version_id}/alerts")
async def read_alerts(
    model_version_id: str,
    metadata_store=Depends(get_metadata_store),
):
    alerts = metadata_store.find_alert_events(model_version_id=model_version_id)
    return {"model_version_id": model_version_id, "alerts": alerts}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd server; uv run pytest tests/test_main.py -v -k get_alerts`
Expected: 1 passed

- [ ] **Step 5: Run the full server suite**

Run: `cd server; uv run pytest -q`
Expected: 256 + 1 = 257 passed

---

### Task 10: Documentation and live verification

**Files:**
- Modify: `README.md`, `docs/Schemas.md`, `docs/architecture.md`, `docs/progression.md`

- [ ] **Step 1: Live end-to-end — the systemic path**

With the server running (`cd server; uv run uvicorn main:app --port 8000`) and a version
already promoted and deployed (reuse the one from api-fication's live run, or produce a
fresh one via `verity --demo`):

```powershell
# Send enough traffic to fill a baseline window, then a recent window that's clearly worse.
# Repeat WINDOW_MIN_EVENTS+ times each, e.g. via a small loop hitting:
curl -X POST http://127.0.0.1:8000/users/<user>/models/<name>/predict -d '{"instances": [[...]]}'
```

Confirm: `GET /models/{mv_id}/alerts` shows a `systemic` row after the second window lands
(within one `flush_interval`, i.e. ~5s after the batch that crosses the threshold).

- [ ] **Step 2: Live end-to-end — the quality path**

```powershell
# Capture a prediction_id from a /predict response, then:
curl -X POST http://127.0.0.1:8000/predictions/<prediction_id>/outcomes `
  -d '{"outcomes": [{"index": 0, "actual": 0}]}'
```

Repeat with `MIN_LABELS` deliberately-wrong outcomes against a version whose quality
threshold this can breach. Confirm a `quality` row appears in `GET /models/{mv_id}/alerts`.

- [ ] **Step 3: Confirm the non-fatal contract for notification**

With no `SES_SENDER`/AWS credentials configured for SES specifically (S3 credentials
alone won't authorize `ses:SendEmail`), trigger an alert and confirm: the `alert_event`
row still lands, `emailed_at` stays null, and the triggering request (`/telemetry` or
`/predictions/.../outcomes`) still returns 200.

- [ ] **Step 4: Update the docs**

- `docs/Schemas.md` — add `label_event` and `alert_event` table sections (✅, naming the
  migrations); add `alert_thresholds`/`alert_email` to `monitoring_config`/`model`.
- `docs/architecture.md` — extend §8 (Falcon) with the two checks, the `prediction_id`
  minting rationale, and the non-fatal wiring into `TelemetrySink.flush()` and the two
  new routes; add both new routes to §1's route list and §13's repo map.
- `README.md` — update Current status: Falcon now detects and notifies, not just
  configures and exposes.
- `docs/progression.md` — entry 10: what shipped, the live verification results, and the
  accepted risks from the spec (single global threshold, no label-absence detection, no
  email retry).

- [ ] **Step 5: Full suite, both projects**

Run: `cd server; uv run pytest -q` then `cd verity; uv run pytest -q`
Expected: 257 server passed, 48 SDK passed.

---

## Self-Review

**Spec coverage:** every settled decision and component in the spec maps to a task —
systemic + quality signals (Tasks 1, 4), delayed labels and `prediction_id` minting
(Task 7), inline triggering with no scheduler (Tasks 6–7), in-app + email notification
(Task 5), the `find_monitoring_config`-not-`find_eval_run` correction from the spec's
self-review (Task 4's `check_quality` reads `alert_thresholds` off `monitoring_config`
only), and the resource-threshold filtering correctness fix (Task 1's
`detect_quality_anomaly`).

**Type consistency:** `detect_systemic_anomaly`'s return shape (`metric`, `recent`,
`baseline`, `relative_increase`) matches what `check_systemic` passes into `notify_fn`'s
`detail` in Task 4. `find_labeled_outcomes`'s per-outcome shape (`y_true`, `y_pred`,
`y_proba`) matches exactly what `detect_quality_anomaly` consumes. `record_and_notify`'s
signature in Task 5 matches every call site in Task 4's `_default_notify`.

**Known gap carried from the spec, not a plan defect:** `find_labeled_outcomes` fetches
every `telemetry_event` for a version before filtering to labeled ones (Task 3) — flagged
inline as a real limitation that does not scale past V1 traffic, matching the same
tradeoff already accepted for `TELEMETRY_READ_LIMIT`.
