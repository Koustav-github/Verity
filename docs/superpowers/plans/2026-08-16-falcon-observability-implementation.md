# Falcon (Observability) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the V1 loop's fourth agent — when Fury promotes a version to
`production`, Falcon writes a monitoring config; an SDK wrapper reports live telemetry from
the customer's own serving process; a read endpoint and UI panel make it visible.

**Architecture:** Falcon mirrors Fury — a deterministic pure function with injected
collaborators, no LLM. It lifts an eval-time reference from the `eval_run` that promoted the
version (already linked via `promoted_from`). The SDK wraps the customer's model in a proxy
that times `predict()` and ships batched events from a background thread; a new
`POST /telemetry` ingests them and `GET /models/{id}/telemetry` summarizes them.

**Tech Stack:** Same as the rest of `server/` and `verity/` — FastAPI, Supabase
(`supabase-py`), Alembic, numpy, httpx, pytest with hand-written fakes. Frontend is the
existing Next.js 16 app. No new dependencies.

**Spec:** [docs/superpowers/specs/2026-08-16-falcon-observability-design.md](../specs/2026-08-16-falcon-observability-design.md)

## Global Constraints

- TDD throughout: write the failing test, run it, confirm it fails for the stated reason,
  write minimal code, confirm it passes, commit. No exceptions.
- **Reports must include real, complete, unabbreviated pytest output** for every RED and
  GREEN run, copied character-for-character from actual terminal execution. This project has
  caught a fabricated transcript before via independent re-execution; assume yours will be
  re-run and compared, including the exact version banner
  (`platform win32 -- Python 3.12.5, pytest-9.1.1, pluggy-1.6.0`, `plugins: anyio-4.14.2`).
- No `unittest.mock` anywhere — hand-written fakes only, matching every existing test file.
- Full-sentence test names (`test_a_model_that_raises_still_raises_the_same_exception`).
- Every collaborator injected with a lazy real default, matching `orchestrator.py`'s
  existing `_default_identify` / `_default_evaluate` / `_default_register` pattern.
- Run server tests from `server/`: `cd server && uv run pytest`. Run SDK tests from
  `verity/`: `cd verity && uv run pytest tests/` — **no `--ignore` flag is needed or
  permitted**; the suite is fully green.
- **`inputs` and `prediction` columns are created but never written at V1** (spec decision
  4). Do not add sampling logic.
- **Falcon fires no alerts and compares nothing** (spec decision 2). The eval reference is
  recorded for context and for V7's rule engine only.
- Current Alembic head is `708d94baed01`. The new migration chains off it.

---

### Task 1: Migration — `telemetry_event` and `monitoring_config`

**Files:**
- Create: `server/migrations/versions/e91a3d7c5b28_create_telemetry_event_and_monitoring_config.py`

**Interfaces:**
- Produces: two tables. `telemetry_event` (`id` bigint autoincrement PK,
  `model_version_id` FK, `occurred_at`, `latency_ms`, `status`, `inputs`, `prediction`,
  `error_type`) and `monitoring_config` (`id` text PK `mcfg_…`, `model_version_id` FK,
  `eval_run_id` FK → `eval_run`, `metrics` JSON, `eval_reference` JSON, `created_at`).
  Every later task depends on both.

No pytest tests — matches this codebase's migration convention (none of the seven existing
migrations have tests). Applied against the real database in Task 12.

- [ ] **Step 1: Write the migration**

Create `server/migrations/versions/e91a3d7c5b28_create_telemetry_event_and_monitoring_config.py`:

```python
"""create telemetry_event and monitoring_config

Falcon's two tables, introduced together.

telemetry_event was specced in Schemas.md from V1 and never created. inputs/prediction are
created here but deliberately not written at V1 — they exist for V7 drift detection, so
that work lands as a writer rather than as another migration.

monitoring_config is new: the README's pipeline diagram names a "monitoring config" as
Falcon's output but no table was ever specced for it. Added here and recorded in Schemas.md,
the same way Fury's deviations were documented rather than left to diverge silently.

Revision ID: e91a3d7c5b28
Revises: 708d94baed01
Create Date: 2026-08-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e91a3d7c5b28'
down_revision: Union[str, Sequence[str], None] = '708d94baed01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "telemetry_event",
        # The only non-prefixed id in the schema — Schemas.md specs bigint here because
        # this table is append-only and high-volume, unlike the mv_/evr_/mdl_ tables.
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "model_version_id",
            sa.Text(),
            sa.ForeignKey("model_version.id"),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=True),
        sa.Column("prediction", sa.JSON(), nullable=True),
        sa.Column("error_type", sa.Text(), nullable=True),
    )
    # The read path's only access pattern: one version, one time window.
    op.create_index(
        "ix_telemetry_event_version_time",
        "telemetry_event",
        ["model_version_id", "occurred_at"],
    )

    op.create_table(
        "monitoring_config",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "model_version_id",
            sa.Text(),
            sa.ForeignKey("model_version.id"),
            nullable=False,
        ),
        sa.Column("eval_run_id", sa.Text(), sa.ForeignKey("eval_run.id"), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("eval_reference", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_monitoring_config_model_version_id",
        "monitoring_config",
        ["model_version_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_monitoring_config_model_version_id", table_name="monitoring_config")
    op.drop_table("monitoring_config")
    op.drop_index("ix_telemetry_event_version_time", table_name="telemetry_event")
    # Lossy and unrecoverable: this destroys all collected production telemetry.
    op.drop_table("telemetry_event")
```

- [ ] **Step 2: Verify the revision chain is linear**

```bash
cd server && uv run alembic history
```
Expected: `708d94baed01 -> e91a3d7c5b28 (head), create telemetry_event and monitoring_config`
appears at the top, and there is exactly one head. Do **not** run `alembic upgrade head` —
Task 12 applies migrations once the code depending on them exists.

- [ ] **Step 3: Commit**

```bash
git add server/migrations/versions/e91a3d7c5b28_create_telemetry_event_and_monitoring_config.py
git commit -m "Add telemetry_event and monitoring_config migrations"
```

---

### Task 2: Store — `monitoring_config` read and write

**Files:**
- Modify: `server/storage/models/supabase.py` (append after `archive_model_version`)
- Modify: `server/tests/test_supabase.py` (append at end)

**Interfaces:**
- Consumes: the existing `FakeTable`/`FakeSupabaseClient`/`FakeResponse` fakes in
  `test_supabase.py`, which already support `select`, `insert`, `update`, and `eq`.
- Produces:
  - `save_monitoring_config(*, model_version_id, eval_run_id, config) -> str` (an `mcfg_…` id)
  - `find_monitoring_config(*, model_version_id) -> dict | None`

  Task 4 calls `save_monitoring_config`; Task 8 calls `find_monitoring_config`.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_supabase.py`:

```python


MONITORING_CONFIG = {
    "metrics": ["request_count", "latency_p50_ms", "latency_p95_ms", "latency_p99_ms", "error_rate"],
    "eval_reference": {
        "basis": "sandbox_feasibility",
        "eval_run_id": "evr_1",
        "latency_p95_ms": 0.234,
        "quality": {"accuracy": 1.0},
    },
}


def test_save_monitoring_config_inserts_a_row_and_returns_its_id():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    config_id = store.save_monitoring_config(
        model_version_id="mv_abc", eval_run_id="evr_1", config=MONITORING_CONFIG
    )

    assert config_id.startswith("mcfg_")
    assert fake_client.calls == [
        (
            "monitoring_config",
            {
                "id": config_id,
                "model_version_id": "mv_abc",
                "eval_run_id": "evr_1",
                "metrics": MONITORING_CONFIG["metrics"],
                "eval_reference": MONITORING_CONFIG["eval_reference"],
            },
        )
    ]


def test_find_monitoring_config_returns_the_row_for_that_version():
    fake_client = FakeSupabaseClient(
        rows={
            "monitoring_config": [
                {"id": "mcfg_1", "model_version_id": "mv_abc", "metrics": ["error_rate"]},
                {"id": "mcfg_2", "model_version_id": "mv_other", "metrics": []},
            ]
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    config = store.find_monitoring_config(model_version_id="mv_abc")

    assert config["id"] == "mcfg_1"
    assert fake_client.calls == [
        ("monitoring_config", "select", "*", [("model_version_id", "mv_abc")])
    ]


def test_find_monitoring_config_returns_none_when_the_version_is_not_monitored():
    fake_client = FakeSupabaseClient(rows={"monitoring_config": []})
    store = SupabaseMetadataStore(client=fake_client)

    assert store.find_monitoring_config(model_version_id="mv_nope") is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd server && uv run pytest tests/test_supabase.py -v
```
Expected: the three new tests `FAIL` with
`AttributeError: 'SupabaseMetadataStore' object has no attribute 'save_monitoring_config'`
(and the same for `find_monitoring_config`).

- [ ] **Step 3: Implement both methods**

Append to `server/storage/models/supabase.py`:

```python

    def save_monitoring_config(self, *, model_version_id, eval_run_id, config):
        config_id = f"mcfg_{uuid.uuid4().hex}"
        self.client.table("monitoring_config").insert(
            {
                "id": config_id,
                "model_version_id": model_version_id,
                "eval_run_id": eval_run_id,
                "metrics": config["metrics"],
                "eval_reference": config["eval_reference"],
            }
        ).execute()
        return config_id

    def find_monitoring_config(self, *, model_version_id):
        result = (
            self.client.table("monitoring_config")
            .select("*")
            .eq("model_version_id", model_version_id)
            .execute()
        )
        return result.data[0] if result.data else None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd server && uv run pytest tests/test_supabase.py -v
```
Expected: all `PASS`.

- [ ] **Step 5: Commit**

```bash
git add server/storage/models/supabase.py server/tests/test_supabase.py
git commit -m "Add monitoring_config store methods"
```

---

### Task 3: Store — telemetry write and read, plus fake query support

**Files:**
- Modify: `server/storage/models/supabase.py` (append after Task 2's additions)
- Modify: `server/tests/test_supabase.py` (extend `FakeTable`, append tests)

**Interfaces:**
- Consumes: `FakeTable` from `test_supabase.py`, which this task extends with `gte`,
  `order`, and `limit` — PostgREST query methods the existing fake does not support.
- Produces:
  - `save_telemetry_events(*, events) -> int` (rows written; `0` for an empty list)
  - `find_telemetry_events(*, model_version_id, since, limit=10_000) -> list[dict]`

  Task 7 calls `save_telemetry_events`; Task 8 calls `find_telemetry_events`.

- [ ] **Step 1: Extend `FakeTable` with `gte`, `order`, and `limit`**

In `server/tests/test_supabase.py`, replace the `FakeTable` class in its entirety with:

```python
class FakeTable:
    def __init__(self, name, calls, rows=None):
        self.name = name
        self.calls = calls
        self.rows = rows if rows is not None else []
        self._payload = None
        self._verb = None
        self._filters = []
        self._select_cols = None
        self._gte = None
        self._order = None
        self._limit = None

    def select(self, columns="*"):
        self._verb = "select"
        self._select_cols = columns
        return self

    def insert(self, payload):
        self._verb = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._verb = "update"
        self._payload = payload
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def gte(self, column, value):
        self._gte = (column, value)
        return self

    def order(self, column, desc=False):
        self._order = (column, desc)
        return self

    def limit(self, count):
        self._limit = count
        return self

    def _matching_rows(self):
        matches = [
            row for row in self.rows
            if all(row.get(col) == val for col, val in self._filters)
        ]
        if self._gte is not None:
            column, value = self._gte
            matches = [row for row in matches if row.get(column) >= value]
        if self._order is not None:
            column, desc = self._order
            matches = sorted(matches, key=lambda row: row.get(column), reverse=desc)
        if self._limit is not None:
            matches = matches[: self._limit]
        return matches

    def execute(self):
        if self._verb == "select":
            self.calls.append((self.name, "select", self._select_cols, list(self._filters)))
            return FakeResponse(self._matching_rows())
        if self._verb == "update":
            self.calls.append((self.name, "update", self._payload, list(self._filters)))
            # Real PostgREST returns the affected row(s) here (representation mode) —
            # an update matching zero rows comes back with an empty data array, which is
            # exactly the "silently did nothing" case the four registry mutations must
            # detect and raise on rather than reporting a false success.
            return FakeResponse(self._matching_rows())
        self.calls.append((self.name, self._payload))
        return FakeResponse([])
```

- [ ] **Step 2: Write the failing tests**

Append to `server/tests/test_supabase.py`:

```python


def test_save_telemetry_events_inserts_the_whole_batch_at_once():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)
    events = [
        {"model_version_id": "mv_1", "occurred_at": "2026-08-16T10:00:00+00:00",
         "latency_ms": 1.5, "status": "ok", "error_type": None},
        {"model_version_id": "mv_1", "occurred_at": "2026-08-16T10:00:01+00:00",
         "latency_ms": 2.5, "status": "error", "error_type": "ValueError"},
    ]

    written = store.save_telemetry_events(events=events)

    assert written == 2
    assert fake_client.calls == [("telemetry_event", events)]


def test_save_telemetry_events_does_nothing_for_an_empty_batch():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    assert store.save_telemetry_events(events=[]) == 0
    assert fake_client.calls == []


def test_find_telemetry_events_returns_only_this_version_within_the_window():
    fake_client = FakeSupabaseClient(
        rows={
            "telemetry_event": [
                {"id": 1, "model_version_id": "mv_1", "occurred_at": "2026-08-16T10:00:00+00:00"},
                {"id": 2, "model_version_id": "mv_1", "occurred_at": "2026-08-15T10:00:00+00:00"},
                {"id": 3, "model_version_id": "mv_other", "occurred_at": "2026-08-16T10:00:00+00:00"},
            ]
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    events = store.find_telemetry_events(
        model_version_id="mv_1", since="2026-08-16T00:00:00+00:00"
    )

    assert [e["id"] for e in events] == [1]


def test_find_telemetry_events_honours_the_limit():
    rows = [
        {"id": n, "model_version_id": "mv_1", "occurred_at": f"2026-08-16T10:00:0{n}+00:00"}
        for n in range(5)
    ]
    fake_client = FakeSupabaseClient(rows={"telemetry_event": rows})
    store = SupabaseMetadataStore(client=fake_client)

    events = store.find_telemetry_events(
        model_version_id="mv_1", since="2026-08-16T00:00:00+00:00", limit=2
    )

    assert len(events) == 2
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd server && uv run pytest tests/test_supabase.py -v
```
Expected: the four new tests `FAIL` with `AttributeError` for
`save_telemetry_events` / `find_telemetry_events`. The `FakeTable` replacement in Step 1
must not break any pre-existing test — if one fails, the replacement was applied
incorrectly.

- [ ] **Step 4: Implement both methods**

Append to `server/storage/models/supabase.py`:

```python

    def save_telemetry_events(self, *, events):
        """Batch insert. The SDK batches on its side, so this never sees one row at a time.

        Unlike the registry mutations, a zero-row result is not checked for: this is an
        insert, not an update — PostgREST raises on a failed insert (e.g. an FK violation
        for an unknown model_version_id) rather than silently affecting nothing.
        """
        if not events:
            return 0
        self.client.table("telemetry_event").insert(events).execute()
        return len(events)

    def find_telemetry_events(self, *, model_version_id, since, limit=10_000):
        result = (
            self.client.table("telemetry_event")
            .select("*")
            .eq("model_version_id", model_version_id)
            .gte("occurred_at", since)
            .order("occurred_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
```

- [ ] **Step 5: Run the whole file to verify everything passes**

```bash
cd server && uv run pytest tests/test_supabase.py -v
```
Expected: all `PASS` — 21 tests (14 pre-existing + 3 from Task 2 + 4 here).

- [ ] **Step 6: Commit**

```bash
git add server/storage/models/supabase.py server/tests/test_supabase.py
git commit -m "Add telemetry store methods and fake gte/order/limit support"
```

---

### Task 4: Falcon — `configure()`

**Files:**
- Create: `agents/brain4/falcon/monitor.py`
- Create: `server/tests/test_falcon_monitor.py`

**Interfaces:**
- Consumes: `metadata_store.save_monitoring_config(*, model_version_id, eval_run_id, config)`
  (Task 2); `RESOURCE_PREFIX` (the string `"resource."`) from `agents/brain2/nat/score.py`.
- Produces:
  - `METRICS` — the fixed V1 metric list
  - `build_eval_reference(*, eval_run_id, scores) -> dict`
  - `configure(*, model_version_id, eval_run_id, eval_run, metadata_store) -> dict`
    returning `{"id": "mcfg_…", "metrics": [...], "eval_reference": {...}}`

  Task 5 calls `configure`.

No `__init__.py` — this repo uses implicit namespace packages throughout (neither
`agents/brain1/hawkeye/`, `agents/brain2/nat/`, nor `agents/brain3/fury/` has one).

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_falcon_monitor.py`:

```python
from agents.brain4.falcon.monitor import METRICS, build_eval_reference, configure


class FakeMetadataStore:
    def __init__(self):
        self.saved = []

    def save_monitoring_config(self, *, model_version_id, eval_run_id, config):
        self.saved.append(
            {"model_version_id": model_version_id, "eval_run_id": eval_run_id, "config": config}
        )
        return "mcfg_1"


EVAL_RUN = {
    "verdict": "pass",
    "scores": {
        "accuracy": 1.0,
        "f1": 0.98,
        "resource.latency_p50_ms": 0.165,
        "resource.latency_p95_ms": 0.234,
        "resource.peak_memory_mb": 111.4,
        "resource.gpu_memory_mb": None,
    },
}


def test_the_reference_splits_resource_metrics_from_quality_scores():
    reference = build_eval_reference(eval_run_id="evr_1", scores=EVAL_RUN["scores"])

    assert reference["latency_p50_ms"] == 0.165
    assert reference["latency_p95_ms"] == 0.234
    assert reference["peak_memory_mb"] == 111.4
    assert reference["gpu_memory_mb"] is None
    assert reference["quality"] == {"accuracy": 1.0, "f1": 0.98}


def test_the_reference_is_labelled_as_sandbox_feasibility_not_a_production_baseline():
    reference = build_eval_reference(eval_run_id="evr_1", scores=EVAL_RUN["scores"])

    assert reference["basis"] == "sandbox_feasibility"
    assert reference["eval_run_id"] == "evr_1"


def test_configure_saves_a_config_carrying_the_fixed_v1_metric_set():
    store = FakeMetadataStore()

    config = configure(
        model_version_id="mv_1",
        eval_run_id="evr_1",
        eval_run=EVAL_RUN,
        metadata_store=store,
    )

    assert config["id"] == "mcfg_1"
    assert config["metrics"] == METRICS
    assert "request_count" in METRICS and "error_rate" in METRICS
    assert store.saved[0]["model_version_id"] == "mv_1"
    assert store.saved[0]["eval_run_id"] == "evr_1"


def test_configure_survives_an_eval_run_that_recorded_no_scores():
    store = FakeMetadataStore()

    config = configure(
        model_version_id="mv_1",
        eval_run_id="evr_1",
        eval_run={"verdict": "pass"},
        metadata_store=store,
    )

    assert config["eval_reference"]["quality"] == {}
    assert config["metrics"] == METRICS
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd server && uv run pytest tests/test_falcon_monitor.py -v
```
Expected: `FAIL` with `ModuleNotFoundError: No module named 'agents.brain4'`.

- [ ] **Step 3: Implement `configure`**

Create `agents/brain4/falcon/monitor.py`:

```python
from agents.brain2.nat.score import RESOURCE_PREFIX

# The V1 metric set, fixed. Falcon does not choose these per model — the README's V1 scope
# for Falcon is exactly "request count, latency percentiles, error rate", and unlike Nat's
# quality metrics there is nothing task-dependent about them: every served model has
# requests, latency, and errors.
METRICS = [
    "request_count",
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "error_rate",
]


def build_eval_reference(*, eval_run_id, scores):
    """Split an eval_run's flat score map into resource values and quality values.

    The `basis` marker is not decoration. These numbers come from a single-process,
    single-client, cold sandbox — they are a feasibility reference, NOT a production
    baseline, and production latency under real concurrency will be materially higher.
    Nothing in V1 compares against them; they are recorded for context and for V7's rule
    engine, which must be able to tell what kind of number it is reading.
    """
    reference = {"basis": "sandbox_feasibility", "eval_run_id": eval_run_id, "quality": {}}
    for key, value in (scores or {}).items():
        if key.startswith(RESOURCE_PREFIX):
            reference[key[len(RESOURCE_PREFIX):]] = value
        else:
            reference["quality"][key] = value
    return reference


def configure(*, model_version_id, eval_run_id, eval_run, metadata_store):
    """Switch monitoring on for a version that just reached production.

    Deterministic, like Fury: the reference is lifted from evidence that already exists
    (the eval_run that promoted this version), so there is nothing to guess and no LLM
    call to make.
    """
    config = {
        "metrics": METRICS,
        "eval_reference": build_eval_reference(
            eval_run_id=eval_run_id, scores=eval_run.get("scores", {})
        ),
    }
    config_id = metadata_store.save_monitoring_config(
        model_version_id=model_version_id, eval_run_id=eval_run_id, config=config
    )
    return {"id": config_id, **config}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd server && uv run pytest tests/test_falcon_monitor.py -v
```
Expected: all 4 `PASS`.

- [ ] **Step 5: Commit**

```bash
git add agents/brain4/falcon/monitor.py server/tests/test_falcon_monitor.py
git commit -m "Add Falcon.configure — deterministic monitoring config from the eval run"
```

---

### Task 5: Orchestrator — wire Falcon after a successful promotion

**Files:**
- Modify: `server/orchestrator.py`
- Modify: `server/tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `configure(*, model_version_id, eval_run_id, eval_run, metadata_store)` (Task 4).
- Produces: `build_artifact` gains a `configure_fn=None` parameter and its response gains a
  `"monitoring_config"` key (the config dict, or `None`).

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_orchestrator.py`:

```python


def fake_configure(**kwargs):
    return {"id": "mcfg_1", "metrics": ["error_rate"], "eval_reference": {"basis": "sandbox_feasibility"}}


def test_a_promoted_version_gets_monitoring_configured():
    seen = {}

    def recording_configure(**kwargs):
        seen.update(kwargs)
        return fake_configure()

    result = build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256=FAKE_MODEL_SHA256,
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=FakeBlobStore(),
        metadata_store=FakeMetadataStore(),
        identify_fn=fake_identify,
        find_existing_fn=no_existing,
        register_fn=fake_register,
        configure_fn=recording_configure,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=passing_eval,
    )

    assert result["monitoring_config"]["id"] == "mcfg_1"
    assert seen["model_version_id"] == "mv_123"
    assert seen["eval_run_id"] == "evr_789"


def test_a_version_that_was_not_promoted_gets_no_monitoring_config():
    def must_not_run(**_):
        raise AssertionError("configure_fn must not run for a non-production version")

    result = build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256=FAKE_MODEL_SHA256,
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=FakeBlobStore(),
        metadata_store=FakeMetadataStore(),
        identify_fn=fake_identify,
        find_existing_fn=no_existing,
        register_fn=fake_register,
        configure_fn=must_not_run,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=lambda **_: {"verdict": "fail"},
    )

    assert result["monitoring_config"] is None
    assert result["status"] == "staging_failed"


def test_a_falcon_failure_does_not_lose_a_promotion_that_already_succeeded():
    def exploding_configure(**_):
        raise RuntimeError("supabase is down")

    result = build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256=FAKE_MODEL_SHA256,
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=FakeBlobStore(),
        metadata_store=FakeMetadataStore(),
        identify_fn=fake_identify,
        find_existing_fn=no_existing,
        register_fn=fake_register,
        configure_fn=exploding_configure,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=passing_eval,
    )

    assert result["status"] == "production"
    assert result["monitoring_config"] is None


def test_an_upload_with_no_fixture_is_never_monitored():
    def must_not_run(**_):
        raise AssertionError("configure_fn must not run without an eval")

    result = build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256=FAKE_MODEL_SHA256,
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=FakeBlobStore(),
        metadata_store=FakeMetadataStore(),
        identify_fn=fake_identify,
        find_existing_fn=no_existing,
        register_fn=fake_register,
        configure_fn=must_not_run,
    )

    assert result["monitoring_config"] is None
    assert result["status"] == "pending"
```

**Also required, or six pre-existing tests break.** Every existing test calls
`build_artifact` without `configure_fn`, so it falls through to `_default_configure` → the
real `configure()` → `metadata_store.save_monitoring_config(...)`. Six existing tests reach
a `production` status and would hit that path, failing with `AttributeError` because the
module-level `FakeMetadataStore` has no such method.

Do **not** fix this by adding `configure_fn=` to individual tests — enumerating them is
fragile and easy to get wrong. Instead give the shared fake the method, so the real (pure,
network-free) Falcon works against it:

In `server/tests/test_orchestrator.py`, add this method to the module-level
`FakeMetadataStore` class:

```python
    def save_monitoring_config(self, *, model_version_id, eval_run_id, config):
        self.monitoring_configs[model_version_id] = config
        return "mcfg_fake"
```

and initialise its store in that class's `__init__`:

```python
        self.monitoring_configs = {}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd server && uv run pytest tests/test_orchestrator.py -v
```
Expected: the four new tests `FAIL` with
`TypeError: build_artifact() got an unexpected keyword argument 'configure_fn'`.

- [ ] **Step 3: Wire Falcon into `build_artifact`**

In `server/orchestrator.py`, add `configure_fn=None` to the signature (after `register_fn=None`):

```python
    find_existing_fn=None,
    register_fn=None,
    configure_fn=None,
    fixture_payload=None,
    fixture_descriptor=None,
```

Add its default alongside the others:

```python
    register_fn = register_fn or _default_register
    configure_fn = configure_fn or _default_configure
```

Then replace the final `return {...}` block with:

```python
    monitoring_config = None
    if registration["status"] == "production":
        monitoring_config = _configure_monitoring(
            configure_fn=configure_fn,
            model_version_id=model_version_id,
            eval_run_id=eval_run_id,
            eval_run=eval_record,
            metadata_store=metadata_store,
        )

    return {
        "model_version_id": model_version_id,
        "artifact_uri": artifact_uri,
        "status": registration["status"],
        "manifest": manifest,
        "eval_run": eval_record,
        "model_id": registration["model_id"],
        "deduplicated": False,
        "archived_model_version_id": registration["archived_model_version_id"],
        "monitoring_config": monitoring_config,
    }
```

Add this helper next to `_evaluate`:

```python
def _configure_monitoring(
    *, configure_fn, model_version_id, eval_run_id, eval_run, metadata_store
):
    """Switch monitoring on, but never at the cost of the promotion that just succeeded.

    Deliberate asymmetry from Fury: Fury raising is correct, because if Fury fails the
    promotion did not happen and a 500 is the truth. Falcon runs AFTER the version is
    already `production` — a config failure here must not 500 a request whose promotion
    genuinely succeeded. The caller gets a null config, which is visible rather than
    fabricated, and monitoring can be configured later.
    """
    try:
        return configure_fn(
            model_version_id=model_version_id,
            eval_run_id=eval_run_id,
            eval_run=eval_run,
            metadata_store=metadata_store,
        )
    except Exception:
        return None
```

And add the lazy default at the bottom, next to `_default_register`:

```python


def _default_configure(**kwargs):
    from agents.brain4.falcon.monitor import configure

    return configure(**kwargs)
```

Finally, the dedup short-circuit's return dict (near the top of `build_artifact`) must gain
the new key so both response shapes stay consistent — add `"monitoring_config": None` to it:

```python
            return {
                "model_version_id": existing["id"],
                "artifact_uri": existing["artifact_uri"],
                "status": existing["status"],
                "manifest": None,
                "eval_run": None,
                "deduplicated": True,
                "model_id": existing["model_id"],
                "monitoring_config": None,
            }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd server && uv run pytest tests/test_orchestrator.py -v
```
Expected: all `PASS`. If
`test_build_artifact_stores_bytes_metadata_and_manifest_then_returns_a_record` or the dedup
test fails on an exact-dict comparison, add `"monitoring_config": None` to that test's
expected dict — the response shape genuinely changed and the assertion must follow.

- [ ] **Step 5: Commit**

```bash
git add server/orchestrator.py server/tests/test_orchestrator.py
git commit -m "Wire Falcon into build_artifact; a promotion now switches monitoring on"
```

---

### Task 6: `summarize()` — the pure summary function

**Files:**
- Create: `server/telemetry.py`
- Create: `server/tests/test_telemetry_summary.py`

**Interfaces:**
- Consumes: nothing — pure function over a list of dicts. `numpy` is already a server
  dependency.
- Produces: `summarize(*, events, eval_reference=None, limit=None) -> dict` with keys
  `request_count`, `error_rate`, `latency_p50_ms`, `latency_p95_ms`, `latency_p99_ms`,
  `truncated`, `eval_reference`.

  Task 8 calls this.

Kept in its own module, separate from the route, for the same reason `score.py` is separate
from `evaluate.py`: the maths is testable without HTTP.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_telemetry_summary.py`:

```python
import pytest

from telemetry import summarize


def event(latency_ms, status="ok"):
    return {"latency_ms": latency_ms, "status": status}


def test_an_empty_window_reports_zeroes_rather_than_dividing_by_zero():
    summary = summarize(events=[])

    assert summary["request_count"] == 0
    assert summary["error_rate"] == 0.0
    assert summary["latency_p50_ms"] is None
    assert summary["latency_p95_ms"] is None
    assert summary["latency_p99_ms"] is None


def test_percentiles_are_computed_over_the_observed_latencies():
    events = [event(float(n)) for n in range(1, 101)]

    summary = summarize(events=events)

    assert summary["request_count"] == 100
    assert summary["latency_p50_ms"] == pytest.approx(50.5)
    assert summary["latency_p95_ms"] == pytest.approx(95.05)
    assert summary["latency_p99_ms"] == pytest.approx(99.01)


def test_error_rate_counts_every_non_ok_status():
    events = [event(1.0), event(1.0, "error"), event(1.0, "timeout"), event(1.0)]

    summary = summarize(events=events)

    assert summary["error_rate"] == pytest.approx(0.5)


def test_an_errored_event_with_no_latency_still_counts_as_a_request():
    events = [event(2.0), {"latency_ms": None, "status": "error"}]

    summary = summarize(events=events)

    assert summary["request_count"] == 2
    assert summary["error_rate"] == pytest.approx(0.5)
    assert summary["latency_p50_ms"] == pytest.approx(2.0)


def test_hitting_the_read_limit_is_reported_rather_than_silently_truncating():
    events = [event(1.0) for _ in range(10)]

    assert summarize(events=events, limit=10)["truncated"] is True
    assert summarize(events=events, limit=100)["truncated"] is False
    assert summarize(events=events)["truncated"] is False


def test_the_eval_reference_is_passed_through_untouched_for_side_by_side_display():
    reference = {"basis": "sandbox_feasibility", "latency_p95_ms": 0.234}

    summary = summarize(events=[event(50.0)], eval_reference=reference)

    assert summary["eval_reference"] == reference
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd server && uv run pytest tests/test_telemetry_summary.py -v
```
Expected: `FAIL` with `ModuleNotFoundError: No module named 'telemetry'`.

- [ ] **Step 3: Implement `summarize`**

Create `server/telemetry.py`:

```python
import numpy as np


def summarize(*, events, eval_reference=None, limit=None):
    """Turn raw telemetry rows into the V1 metric set.

    Deliberately compares nothing: `eval_reference` is passed straight through for
    side-by-side display, never checked against the observed values. The reference is a
    sandbox feasibility figure and the observed values are production under real load —
    comparing them would produce false alarms, and alerting is V7 regardless.
    """
    count = len(events)
    latencies = [e["latency_ms"] for e in events if e.get("latency_ms") is not None]
    errors = sum(1 for e in events if e.get("status") != "ok")

    summary = {
        "request_count": count,
        "error_rate": (errors / count) if count else 0.0,
        "latency_p50_ms": None,
        "latency_p95_ms": None,
        "latency_p99_ms": None,
        # The read path caps how many rows it fetches (relational storage at V1; the
        # analytics store is V3). Say so rather than reporting a silently partial window.
        "truncated": limit is not None and count >= limit,
        "eval_reference": eval_reference,
    }

    if latencies:
        values = np.asarray(latencies)
        summary["latency_p50_ms"] = float(np.percentile(values, 50))
        summary["latency_p95_ms"] = float(np.percentile(values, 95))
        summary["latency_p99_ms"] = float(np.percentile(values, 99))

    return summary
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd server && uv run pytest tests/test_telemetry_summary.py -v
```
Expected: all 6 `PASS`.

- [ ] **Step 5: Commit**

```bash
git add server/telemetry.py server/tests/test_telemetry_summary.py
git commit -m "Add telemetry summarize() — count, percentiles, error rate"
```

---

### Task 7: `POST /telemetry` — ingestion

**Files:**
- Modify: `server/main.py`
- Modify: `server/tests/test_main.py`

**Interfaces:**
- Consumes: `metadata_store.save_telemetry_events(*, events) -> int` (Task 3).
- Produces: `POST /telemetry` accepting `{"events": [...]}`, returning `{"written": N}`;
  and a new `get_metadata_store()` FastAPI dependency that Task 8 also uses.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_main.py`:

```python


def test_telemetry_ingestion_stores_the_whole_batch():
    captured = {}

    class FakeStore:
        def save_telemetry_events(self, *, events):
            captured["events"] = events
            return len(events)

    app.dependency_overrides[get_metadata_store] = lambda: FakeStore()
    client = TestClient(app)

    response = client.post(
        "/telemetry",
        json={
            "events": [
                {"model_version_id": "mv_1", "occurred_at": "2026-08-16T10:00:00+00:00",
                 "latency_ms": 1.5, "status": "ok"},
                {"model_version_id": "mv_1", "occurred_at": "2026-08-16T10:00:01+00:00",
                 "latency_ms": 9.0, "status": "error", "error_type": "ValueError"},
            ]
        },
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"written": 2}
    assert captured["events"][1]["error_type"] == "ValueError"


def test_telemetry_ingestion_rejects_an_event_missing_a_required_field():
    app.dependency_overrides[get_metadata_store] = lambda: None
    client = TestClient(app)

    response = client.post("/telemetry", json={"events": [{"latency_ms": 1.0}]})

    app.dependency_overrides.clear()

    assert response.status_code == 422


def test_telemetry_ingestion_accepts_an_empty_batch():
    class FakeStore:
        def save_telemetry_events(self, *, events):
            return 0

    app.dependency_overrides[get_metadata_store] = lambda: FakeStore()
    client = TestClient(app)

    response = client.post("/telemetry", json={"events": []})

    app.dependency_overrides.clear()

    assert response.json() == {"written": 0}
```

Also update the import at the top of the file:

```python
from main import app, get_build_artifact, get_metadata_store
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd server && uv run pytest tests/test_main.py -v
```
Expected: `FAIL` at import with
`ImportError: cannot import name 'get_metadata_store' from 'main'`.

- [ ] **Step 3: Add the dependency, the models, and the route**

In `server/main.py`, extend the imports:

```python
from pydantic import BaseModel, ConfigDict
```

Add the shared store dependency next to `get_build_artifact`:

```python
@lru_cache
def get_metadata_store():
    return SupabaseMetadataStore()
```

Add the request models above the routes:

```python
class TelemetryEvent(BaseModel):
    # `model_version_id` collides with pydantic v2's protected `model_` namespace, which
    # would emit a warning and can shadow BaseModel internals. Opting out of the
    # protection is correct here: the field name is fixed by the Schemas.md column name.
    model_config = ConfigDict(protected_namespaces=())

    model_version_id: str
    occurred_at: str
    status: str
    latency_ms: float | None = None
    error_type: str | None = None


class TelemetryBatch(BaseModel):
    events: list[TelemetryEvent]
```

Add the route:

```python
@app.post("/telemetry")
async def ingest_telemetry(
    batch: TelemetryBatch,
    metadata_store=Depends(get_metadata_store),
):
    written = metadata_store.save_telemetry_events(
        events=[event.model_dump() for event in batch.events]
    )
    return {"written": written}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd server && uv run pytest tests/test_main.py -v
```
Expected: all `PASS`.

- [ ] **Step 5: Commit**

```bash
git add server/main.py server/tests/test_main.py
git commit -m "Add POST /telemetry ingestion endpoint"
```

---

### Task 8: `GET /models/{id}/telemetry` — the read path

**Files:**
- Modify: `server/main.py`
- Modify: `server/tests/test_main.py`

**Interfaces:**
- Consumes: `summarize(*, events, eval_reference, limit)` (Task 6);
  `metadata_store.find_telemetry_events(*, model_version_id, since, limit)` and
  `find_monitoring_config(*, model_version_id)` (Tasks 2–3); `get_metadata_store` (Task 7).
- Produces: `GET /models/{model_version_id}/telemetry?hours=24` returning the summary plus
  `model_version_id` and `hours`.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_main.py`:

```python


def test_reading_telemetry_summarises_the_window_alongside_the_eval_reference():
    captured = {}

    class FakeStore:
        def find_telemetry_events(self, *, model_version_id, since, limit):
            captured["model_version_id"] = model_version_id
            captured["since"] = since
            captured["limit"] = limit
            return [
                {"latency_ms": 10.0, "status": "ok"},
                {"latency_ms": 20.0, "status": "error"},
            ]

        def find_monitoring_config(self, *, model_version_id):
            return {"eval_reference": {"basis": "sandbox_feasibility", "latency_p95_ms": 0.2}}

    app.dependency_overrides[get_metadata_store] = lambda: FakeStore()
    client = TestClient(app)

    response = client.get("/models/mv_1/telemetry")

    app.dependency_overrides.clear()

    body = response.json()
    assert response.status_code == 200
    assert body["model_version_id"] == "mv_1"
    assert body["request_count"] == 2
    assert body["error_rate"] == 0.5
    assert body["eval_reference"]["basis"] == "sandbox_feasibility"
    assert captured["model_version_id"] == "mv_1"


def test_reading_telemetry_defaults_to_a_24_hour_window_and_accepts_an_override():
    seen = {}

    class FakeStore:
        def find_telemetry_events(self, *, model_version_id, since, limit):
            seen["since"] = since
            return []

        def find_monitoring_config(self, *, model_version_id):
            return None

    app.dependency_overrides[get_metadata_store] = lambda: FakeStore()
    client = TestClient(app)

    default_response = client.get("/models/mv_1/telemetry")
    assert default_response.json()["hours"] == 24.0

    override_response = client.get("/models/mv_1/telemetry?hours=1")
    assert override_response.json()["hours"] == 1.0

    app.dependency_overrides.clear()


def test_reading_telemetry_for_an_unmonitored_version_still_returns_a_summary():
    class FakeStore:
        def find_telemetry_events(self, *, model_version_id, since, limit):
            return []

        def find_monitoring_config(self, *, model_version_id):
            return None

    app.dependency_overrides[get_metadata_store] = lambda: FakeStore()
    client = TestClient(app)

    response = client.get("/models/mv_unknown/telemetry")

    app.dependency_overrides.clear()

    body = response.json()
    assert body["request_count"] == 0
    assert body["eval_reference"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd server && uv run pytest tests/test_main.py -v
```
Expected: the three new tests `FAIL` with `404` (the route does not exist), so the
`response.json()` assertions fail on the 404 body.

- [ ] **Step 3: Add the route**

In `server/main.py`, extend the imports:

```python
from datetime import datetime, timedelta, timezone

from telemetry import summarize
```

Add a module-level constant next to the CORS block:

```python
# Relational storage at V1 (Schemas.md moves telemetry to the analytics store at V3), so
# the read path caps what it pulls. `truncated` in the response says when this bit.
TELEMETRY_READ_LIMIT = 10_000
```

Add the route:

```python
@app.get("/models/{model_version_id}/telemetry")
async def read_telemetry(
    model_version_id: str,
    hours: float = 24.0,
    metadata_store=Depends(get_metadata_store),
):
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    events = metadata_store.find_telemetry_events(
        model_version_id=model_version_id, since=since, limit=TELEMETRY_READ_LIMIT
    )
    config = metadata_store.find_monitoring_config(model_version_id=model_version_id)
    summary = summarize(
        events=events,
        eval_reference=(config or {}).get("eval_reference"),
        limit=TELEMETRY_READ_LIMIT,
    )
    return {"model_version_id": model_version_id, "hours": hours, **summary}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd server && uv run pytest tests/test_main.py -v
```
Expected: all `PASS`.

- [ ] **Step 5: Run the full server suite**

```bash
cd server && uv run pytest -q
```
Expected: **129** tests passing — 102 baseline, plus 3 (Task 2) + 4 (Task 3) + 4 (Task 4) +
4 (Task 5) + 6 (Task 6) + 3 (Task 7) + 3 (here) = +27. Report the actual number you observe;
if it differs, say so rather than adjusting the expectation silently.

- [ ] **Step 6: Commit**

```bash
git add server/main.py server/tests/test_main.py
git commit -m "Add GET /models/{id}/telemetry read endpoint"
```

---

### Task 9: SDK — the monitored-model proxy

**Files:**
- Create: `verity/src/verity/monitor.py`
- Create: `verity/tests/test_monitor_proxy.py`

**Interfaces:**
- Consumes: nothing from earlier tasks — this is client-side.
- Produces: `MonitoredModel(model, *, reporter)`. The reporter contract is one method:
  `record(*, latency_ms, status, error_type)`. Task 10 implements the real reporter and
  the `monitor()` entry point.

- [ ] **Step 1: Write the failing tests**

Create `verity/tests/test_monitor_proxy.py`:

```python
import pytest

from verity.monitor import MonitoredModel


class FakeReporter:
    def __init__(self):
        self.records = []

    def record(self, *, latency_ms, status, error_type):
        self.records.append(
            {"latency_ms": latency_ms, "status": status, "error_type": error_type}
        )


class FakeModel:
    coef_ = [1, 2, 3]

    def predict(self, X):
        return [0 for _ in X]

    def predict_proba(self, X):
        return [[0.5, 0.5] for _ in X]


def test_predict_returns_the_models_real_result_unchanged():
    monitored = MonitoredModel(FakeModel(), reporter=FakeReporter())

    assert monitored.predict([[1.0], [2.0]]) == [0, 0]


def test_a_successful_predict_is_recorded_as_ok_with_a_real_latency():
    reporter = FakeReporter()
    monitored = MonitoredModel(FakeModel(), reporter=reporter)

    monitored.predict([[1.0]])

    assert len(reporter.records) == 1
    assert reporter.records[0]["status"] == "ok"
    assert reporter.records[0]["error_type"] is None
    assert reporter.records[0]["latency_ms"] >= 0


def test_a_model_that_raises_still_raises_the_same_exception_to_the_caller():
    class Exploding:
        def predict(self, X):
            raise ValueError("feature order mismatch")

    reporter = FakeReporter()
    monitored = MonitoredModel(Exploding(), reporter=reporter)

    with pytest.raises(ValueError) as excinfo:
        monitored.predict([[1.0]])

    assert "feature order mismatch" in str(excinfo.value)
    assert reporter.records[0]["status"] == "error"
    assert reporter.records[0]["error_type"] == "ValueError"


def test_a_broken_reporter_never_breaks_the_customers_inference():
    class BrokenReporter:
        def record(self, **_):
            raise RuntimeError("telemetry backend exploded")

    monitored = MonitoredModel(FakeModel(), reporter=BrokenReporter())

    assert monitored.predict([[1.0]]) == [0]


def test_every_other_attribute_delegates_to_the_wrapped_model():
    monitored = MonitoredModel(FakeModel(), reporter=FakeReporter())

    assert monitored.coef_ == [1, 2, 3]


def test_predict_proba_is_monitored_when_the_model_has_it():
    reporter = FakeReporter()
    monitored = MonitoredModel(FakeModel(), reporter=reporter)

    assert monitored.predict_proba([[1.0]]) == [[0.5, 0.5]]
    assert reporter.records[0]["status"] == "ok"


def test_predict_proba_raises_attribute_error_when_the_model_lacks_it():
    class LabelsOnly:
        def predict(self, X):
            return [0]

    monitored = MonitoredModel(LabelsOnly(), reporter=FakeReporter())

    with pytest.raises(AttributeError):
        monitored.predict_proba([[1.0]])
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd verity && uv run pytest tests/test_monitor_proxy.py -v
```
Expected: `FAIL` with `ModuleNotFoundError: No module named 'verity.monitor'`.

- [ ] **Step 3: Implement the proxy**

Create `verity/src/verity/monitor.py`:

```python
import time


class MonitoredModel:
    """A transparent proxy that times predict() and reports it, changing nothing else.

    The governing rule: telemetry must never be why the customer's inference fails or
    slows. Recording is best-effort and swallows its own exceptions; the model's own
    exception always propagates unchanged.
    """

    def __init__(self, model, *, reporter):
        # Set via __dict__ so __getattr__ delegation never sees these two names.
        object.__setattr__(self, "_model", model)
        object.__setattr__(self, "_reporter", reporter)

    def __getattr__(self, item):
        # Only reached when normal lookup fails, so predict/predict_proba below win.
        return getattr(self._model, item)

    def predict(self, X, *args, **kwargs):
        return self._call("predict", X, *args, **kwargs)

    def predict_proba(self, X, *args, **kwargs):
        if not hasattr(self._model, "predict_proba"):
            raise AttributeError(
                f"{type(self._model).__name__!r} object has no attribute 'predict_proba'"
            )
        return self._call("predict_proba", X, *args, **kwargs)

    def _call(self, method_name, X, *args, **kwargs):
        started = time.perf_counter()
        try:
            result = getattr(self._model, method_name)(X, *args, **kwargs)
        except BaseException as exc:
            self._record(
                latency_ms=(time.perf_counter() - started) * 1000,
                status="error",
                error_type=type(exc).__name__,
            )
            raise
        self._record(
            latency_ms=(time.perf_counter() - started) * 1000,
            status="ok",
            error_type=None,
        )
        return result

    def _record(self, **kwargs):
        try:
            self._reporter.record(**kwargs)
        except Exception:
            # A telemetry failure is never allowed to surface into the caller's
            # inference path — that is the whole contract of this wrapper.
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd verity && uv run pytest tests/test_monitor_proxy.py -v
```
Expected: all 7 `PASS`.

- [ ] **Step 5: Commit**

```bash
git add verity/src/verity/monitor.py verity/tests/test_monitor_proxy.py
git commit -m "Add MonitoredModel proxy — times predict, never breaks inference"
```

---

### Task 10: SDK — the batching reporter and `monitor()`

**Files:**
- Modify: `verity/src/verity/monitor.py`
- Create: `verity/tests/test_monitor_reporter.py`

**Interfaces:**
- Consumes: `MonitoredModel` (Task 9); `POST /telemetry` accepting `{"events": [...]}` (Task 7).
- Produces:
  - `TelemetryReporter(*, model_version_id, endpoint, transport=None, maxsize=10_000, batch_size=100, flush_interval=5.0)`
    with `.record(...)`, `.flush()`, and a `.dropped` counter
  - `monitor(model, *, model_version_id, endpoint="http://localhost:8000", transport=None, flush_interval=5.0) -> MonitoredModel`

- [ ] **Step 1: Write the failing tests**

Create `verity/tests/test_monitor_reporter.py`:

```python
from verity.monitor import TelemetryReporter, monitor


class FakeTransport:
    def __init__(self):
        self.batches = []

    def send(self, events):
        self.batches.append(events)


class ExplodingTransport:
    def send(self, events):
        raise ConnectionError("server unreachable")


def a_reporter(transport, **kwargs):
    # A long flush interval keeps the background thread out of the way so tests drive
    # flushing explicitly and stay deterministic — never sleep in a test.
    return TelemetryReporter(
        model_version_id="mv_1",
        endpoint="http://verity.test",
        transport=transport,
        flush_interval=3600,
        **kwargs,
    )


def test_recorded_events_are_sent_on_flush_with_the_version_and_a_timestamp():
    transport = FakeTransport()
    reporter = a_reporter(transport)

    reporter.record(latency_ms=1.5, status="ok", error_type=None)
    reporter.flush()

    assert len(transport.batches) == 1
    event = transport.batches[0][0]
    assert event["model_version_id"] == "mv_1"
    assert event["latency_ms"] == 1.5
    assert event["status"] == "ok"
    assert event["occurred_at"].endswith("+00:00")


def test_nothing_is_sent_when_there_is_nothing_recorded():
    transport = FakeTransport()
    reporter = a_reporter(transport)

    reporter.flush()

    assert transport.batches == []


def test_events_are_sent_in_batches_rather_than_one_request_each():
    transport = FakeTransport()
    reporter = a_reporter(transport, batch_size=2)

    for _ in range(5):
        reporter.record(latency_ms=1.0, status="ok", error_type=None)
    reporter.flush()

    assert [len(batch) for batch in transport.batches] == [2, 2, 1]


def test_a_full_queue_drops_events_instead_of_blocking_the_caller():
    transport = FakeTransport()
    reporter = a_reporter(transport, maxsize=2)

    for _ in range(5):
        reporter.record(latency_ms=1.0, status="ok", error_type=None)

    assert reporter.dropped == 3
    reporter.flush()
    assert sum(len(batch) for batch in transport.batches) == 2


def test_a_transport_that_is_down_never_raises_into_the_caller():
    reporter = a_reporter(ExplodingTransport())

    reporter.record(latency_ms=1.0, status="ok", error_type=None)
    reporter.flush()  # must not raise


def test_monitor_returns_a_proxy_whose_predictions_reach_the_transport():
    class FakeModel:
        def predict(self, X):
            return [1]

    transport = FakeTransport()
    monitored = monitor(
        FakeModel(),
        model_version_id="mv_9",
        transport=transport,
        flush_interval=3600,
    )

    assert monitored.predict([[0.0]]) == [1]
    monitored.flush()

    assert transport.batches[0][0]["model_version_id"] == "mv_9"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd verity && uv run pytest tests/test_monitor_reporter.py -v
```
Expected: `FAIL` with
`ImportError: cannot import name 'TelemetryReporter' from 'verity.monitor'`.

- [ ] **Step 3: Implement the reporter and `monitor()`**

Add to the top of `verity/src/verity/monitor.py`:

```python
import atexit
import queue
import threading
import time
from datetime import datetime, timezone
```

Append to `verity/src/verity/monitor.py`:

```python


class _HttpTransport:
    def __init__(self, endpoint, client=None):
        self._endpoint = endpoint
        self._client = client

    def send(self, events):
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=10.0)
        self._client.post(f"{self._endpoint}/telemetry", json={"events": events})


class TelemetryReporter:
    """Buffers telemetry and ships it in batches from a background thread.

    Nothing here is allowed to block or break the caller: enqueue is non-blocking and
    drops on overflow, sending happens off the predict path, and transport failures are
    swallowed. Losing telemetry is always preferable to degrading the customer's serving.
    """

    def __init__(
        self,
        *,
        model_version_id,
        endpoint,
        transport=None,
        maxsize=10_000,
        batch_size=100,
        flush_interval=5.0,
    ):
        self._model_version_id = model_version_id
        self._transport = transport or _HttpTransport(endpoint)
        self._queue = queue.Queue(maxsize=maxsize)
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self.dropped = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        atexit.register(self.flush)

    def record(self, *, latency_ms, status, error_type):
        try:
            self._queue.put_nowait(
                {
                    "model_version_id": self._model_version_id,
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                    "latency_ms": latency_ms,
                    "status": status,
                    "error_type": error_type,
                }
            )
        except queue.Full:
            # Dropping is the correct failure mode: blocking here would add the
            # telemetry backlog to the customer's inference latency.
            self.dropped += 1

    def flush(self):
        while True:
            batch = self._next_batch()
            if not batch:
                return
            self._send(batch)

    def _next_batch(self):
        batch = []
        while len(batch) < self._batch_size:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def _send(self, batch):
        try:
            self._transport.send(batch)
        except Exception:
            pass

    def _loop(self):
        while not self._stop.wait(self._flush_interval):
            self.flush()


def monitor(
    model,
    *,
    model_version_id,
    endpoint="http://localhost:8000",
    transport=None,
    flush_interval=5.0,
):
    """Wrap a model so its predictions are reported to Verity.

    `model_version_id` is the id returned by assemble(). Upload and serving usually happen
    in different processes, so it is passed explicitly rather than remembered.
    """
    reporter = TelemetryReporter(
        model_version_id=model_version_id,
        endpoint=endpoint,
        transport=transport,
        flush_interval=flush_interval,
    )
    return MonitoredModel(model, reporter=reporter)
```

Finally, add a `flush()` passthrough to `MonitoredModel` so `monitored.flush()` works — add
this method to the class:

```python
    def flush(self):
        """Drain buffered telemetry now. Called automatically at process exit."""
        self._reporter.flush()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd verity && uv run pytest tests/test_monitor_reporter.py -v
```
Expected: all 6 `PASS`.

- [ ] **Step 5: Run the full SDK suite**

```bash
cd verity && uv run pytest tests/ -q
```
Expected: all pass — 34 tests (21 baseline + 7 Task 9 + 6 here). No `--ignore` flag.

- [ ] **Step 6: Commit**

```bash
git add verity/src/verity/monitor.py verity/tests/test_monitor_reporter.py
git commit -m "Add TelemetryReporter and monitor() — batched, non-blocking, fire-and-forget"
```

---

### Task 11: Frontend — telemetry panel

**Files:**
- Modify: `client/src/lib/verity.ts`
- Create: `client/src/components/telemetry-panel.tsx`
- Modify: `client/src/components/evidence-report.tsx`

**Interfaces:**
- Consumes: `GET /models/{id}/telemetry` (Task 8).
- Produces: a `<TelemetryPanel modelVersionId={...} />` component rendered inside the
  existing evidence report.

- [ ] **Step 1: Add the API types and fetch function**

Append to `client/src/lib/verity.ts`:

```typescript
export type TelemetrySummary = {
  model_version_id: string;
  hours: number;
  request_count: number;
  error_rate: number;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  latency_p99_ms: number | null;
  truncated: boolean;
  eval_reference: Record<string, unknown> | null;
};

export async function fetchTelemetry(
  modelVersionId: string,
): Promise<TelemetrySummary> {
  const response = await fetch(
    `${API_BASE}/models/${encodeURIComponent(modelVersionId)}/telemetry`,
  );
  if (!response.ok) {
    throw new IngestError(`Couldn't read telemetry (${response.status}).`);
  }
  return response.json();
}
```

Also add `monitoring_config` to the existing `IngestResult` type:

```typescript
  monitoring_config?: { id: string; metrics: string[] } | null;
```

- [ ] **Step 2: Create the panel component**

Create `client/src/components/telemetry-panel.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { fetchTelemetry, type TelemetrySummary } from "@/lib/verity";

function ms(value: number | null) {
  return value == null ? "—" : `${value.toFixed(2)} ms`;
}

export function TelemetryPanel({ modelVersionId }: { modelVersionId: string }) {
  const [summary, setSummary] = useState<TelemetrySummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      setSummary(await fetchTelemetry(modelVersionId));
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelVersionId]);

  if (error) {
    return <p className="mt-6 font-mono text-xs text-fail">{error}</p>;
  }
  if (!summary) {
    return <p className="mt-6 font-mono text-xs text-ink-soft">Reading telemetry…</p>;
  }

  return (
    <div className="mt-6 border-t border-rule pt-4 font-mono text-sm">
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-xs uppercase tracking-[0.2em] text-brass">
          Live traffic — last {summary.hours}h
        </h3>
        <button
          type="button"
          onClick={load}
          className="border border-ink px-2 py-1 text-[10px] uppercase tracking-[0.2em] hover:bg-ink hover:text-paper"
        >
          Refresh
        </button>
      </div>

      {summary.request_count === 0 ? (
        <p className="text-xs text-ink-soft">
          No requests recorded yet. Wrap the model with{" "}
          <code>verity.monitor(model, model_version_id=&quot;{modelVersionId}&quot;)</code> in
          your serving process and call predict.
        </p>
      ) : (
        <>
          <div className="flex items-baseline gap-2 py-1">
            <span className="shrink-0 text-ink-soft">requests</span>
            <span className="grow border-b border-dotted border-rule translate-y-[-3px]" />
            <span className="shrink-0 font-medium">{summary.request_count}</span>
          </div>
          <div className="flex items-baseline gap-2 py-1">
            <span className="shrink-0 text-ink-soft">error rate</span>
            <span className="grow border-b border-dotted border-rule translate-y-[-3px]" />
            <span className="shrink-0 font-medium">
              {(summary.error_rate * 100).toFixed(1)}%
            </span>
          </div>
          <div className="flex items-baseline gap-2 py-1">
            <span className="shrink-0 text-ink-soft">latency p50 / p95 / p99</span>
            <span className="grow border-b border-dotted border-rule translate-y-[-3px]" />
            <span className="shrink-0 font-medium">
              {ms(summary.latency_p50_ms)} / {ms(summary.latency_p95_ms)} /{" "}
              {ms(summary.latency_p99_ms)}
            </span>
          </div>
          {summary.truncated && (
            <p className="mt-2 text-xs text-ink-soft">
              Window truncated at the read limit — showing the most recent events only.
            </p>
          )}
        </>
      )}

      {summary.eval_reference != null && (
        <p className="mt-3 text-xs text-ink-soft">
          Eval-time reference (sandbox feasibility, not a production baseline — nothing is
          compared or alerted on):{" "}
          {ms((summary.eval_reference as Record<string, number>).latency_p95_ms ?? null)} p95.
        </p>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Render it from the evidence report**

In `client/src/components/evidence-report.tsx`, add the import at the top:

```tsx
import { TelemetryPanel } from "./telemetry-panel";
```

Then, immediately before the final closing `</section>` tag, add:

```tsx
      {result.monitoring_config && (
        <TelemetryPanel modelVersionId={result.model_version_id} />
      )}
```

- [ ] **Step 4: Verify the build compiles with no type errors**

```bash
cd client && npx next build
```
Expected: `✓ Compiled successfully` and `Finished TypeScript` with no errors.

- [ ] **Step 5: Commit**

```bash
git add client/src/lib/verity.ts client/src/components/telemetry-panel.tsx client/src/components/evidence-report.tsx
git commit -m "Add telemetry panel to the intake UI"
```

---

### Task 12: Full verification — migrations, live end-to-end, docs

**Files:**
- Modify: `Schemas.md`
- Modify: `progression.md`
- Modify: `README.md`

**Interfaces:** None — verifies Tasks 1–11 together.

- [ ] **Step 1: Run both suites**

```bash
cd server && uv run pytest -q
cd ../verity && uv run pytest tests/ -q
```
Expected: both fully green, no `--ignore` flags. Report the exact counts you observe.

- [ ] **Step 2: Apply the migration to the real database**

```bash
cd server && uv run alembic upgrade head
```
Expected: `708d94baed01 -> e91a3d7c5b28, create telemetry_event and monitoring_config`
applies cleanly. Then confirm both tables exist:

```bash
cd server && uv run python -c "
import os
from dotenv import load_dotenv
load_dotenv('.env')
from supabase import create_client
c = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY'])
print('telemetry_event:', c.table('telemetry_event').select('id').limit(1).execute().data)
print('monitoring_config:', c.table('monitoring_config').select('id').limit(1).execute().data)
"
```
Expected: both print `[]` (empty, but reachable — a missing table raises instead).

- [ ] **Step 3: Live end-to-end — promote a model and confirm monitoring switches on**

Start the server:
```bash
cd server && uv run uvicorn main:app --port 8000
```
In another shell:
```bash
cd verity && uv run python -c "
from sklearn.linear_model import LogisticRegression
from verity.client import assemble

X = [[0.0], [1.0], [2.0], [8.0], [9.0], [10.0]]
y = [0, 0, 0, 1, 1, 1]
model = LogisticRegression().fit(X, y)

result = assemble(model, user_id='falcon-e2e', name='falcon-e2e-classifier',
                  X_test=[[0.5], [9.5]], y_test=[0, 1])
print('status:', result['status'])
print('monitoring_config:', result['monitoring_config'])
assert result['status'] == 'production'
assert result['monitoring_config']['id'].startswith('mcfg_')
assert result['monitoring_config']['eval_reference']['basis'] == 'sandbox_feasibility'
print('OK: promotion switched monitoring on')
"
```
Expected: a `mcfg_` id and an `eval_reference` carrying real latency values lifted from the
eval run. **Record the printed `model_version_id`** — the next step needs it.

- [ ] **Step 4: Live end-to-end — report telemetry and read it back**

Using the `model_version_id` from Step 3 (substitute it for `MV_ID`):
```bash
cd verity && uv run python -c "
from sklearn.linear_model import LogisticRegression
from verity.monitor import monitor

model = LogisticRegression().fit([[0.0], [1.0], [2.0], [8.0]], [0, 0, 0, 1])
monitored = monitor(model, model_version_id='MV_ID', endpoint='http://127.0.0.1:8000')

for _ in range(20):
    monitored.predict([[1.0]])
try:
    monitored.predict('not-an-array')   # provokes a real error event
except Exception as exc:
    print('expected error propagated to caller:', type(exc).__name__)

monitored.flush()
print('flushed')
"
```
Then read it back:
```bash
curl -s "http://127.0.0.1:8000/models/MV_ID/telemetry" | python -m json.tool
```
Expected: `request_count` of 21, a non-zero `error_rate` (one of the 21 failed), real
latency percentiles, and the `eval_reference` from Step 3 alongside them.

- [ ] **Step 5: Confirm the UI panel renders**

```bash
cd client && npm run dev
```
Open `http://localhost:3000`, submit the bundled demo model, and confirm the evidence report
now shows a "Live traffic" panel. It will read zero requests for a freshly-uploaded model —
that is correct, and the panel's empty state explains how to start reporting.

- [ ] **Step 6: Update `Schemas.md`**

Immediately after the existing `telemetry_event` table, add this note:

```markdown
`inputs` and `prediction` are created but not written at V1. They exist to support drift
detection, which is V7; the V1 metric set (request count, latency percentiles, error rate)
needs neither. Leaving them null removes the entire sampling-policy question at V1 and
costs nothing V1 promises — V7 adds a writer rather than a migration.
```

Then add this new section directly below it:

```markdown
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

`eval_reference` is a **feasibility reference, not a production baseline**. The numbers are
lifted from the `eval_run` that promoted the version, which measured them in a
single-process, single-client, cold sandbox — production latency under real concurrency will
be materially higher. Nothing in V1 compares against it; the `basis` marker exists so V7's
rule engine can tell what kind of number it is reading.
```

- [ ] **Step 7: Update `progression.md` and `README.md`**

Append a `progression.md` entry 7 describing Falcon: promotion now switches monitoring on,
the SDK wrapper reports live telemetry, and the read path/UI make it visible — plus the two
honesty caveats (the eval reference is a feasibility figure and nothing compares against it;
alerting remains V7). In `README.md`'s **Current status**, replace the sentence saying Falcon
does not exist with an accurate description, and note that api-fication remains unbuilt (the
customer serves; Verity does not).

- [ ] **Step 8: Commit**

```bash
git add Schemas.md progression.md README.md
git commit -m "Document Falcon in Schemas.md, progression.md, and README"
```

---

## Explicitly out of scope (unchanged from the spec)

Alerting and notification channels (V7), drift detection (V7 — the reason `inputs`/`prediction`
stay null here), business metrics (needs outcome ingestion; its own spec), the analytics store
(V3), the `agent_run` audit trail (still a cross-cutting gap across all four agents), and
api-fication (Verity does not serve inference — the customer serves, the SDK reports).
