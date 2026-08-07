# Fury (Model Registry) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the pipeline a third agent, Fury, that groups uploads into named models,
dedupes identical re-uploads, and automatically promotes a passing evaluation straight to
`production` — archiving whatever it replaces.

**Architecture:** Fury mirrors Hawkeye and Nat exactly: a pure-function module with every
collaborator injected, no LLM call (everything it does is deterministic bookkeeping), and
`orchestrator.py` sequences it after Nat the same way it already sequences Nat after
Hawkeye. Two entry points — `find_existing` (dedup, checked before anything else runs) and
`register` (identity linking + conditional promotion, checked after Nat's verdict is known,
or after Hawkeye if no fixture was supplied at all).

**Tech Stack:** Same as the rest of `server/` — FastAPI, Supabase (via `supabase-py`),
Alembic, pytest with hand-written fakes. No new dependencies.

## Global Constraints

- TDD throughout: write the failing test, watch it fail for the stated reason, write the
  minimal code to pass, watch it pass, commit. No exceptions.
- No `unittest.mock` — every fake is a hand-written class, matching every existing test
  file in this codebase.
- Full-sentence test names (`test_a_new_model_is_created_on_first_upload_under_a_name`,
  not `test_new_model`).
- Every collaborator is injected with a lazy real default (`identify_fn=None` →
  `_default_identify()`), matching the existing pattern in `orchestrator.py` exactly.
- **Behavioral change, called out explicitly so it isn't a silent surprise later:** the
  `staging` status, reached today when Nat's verdict is `pass`
  ([orchestrator.py:5](../../../server/orchestrator.py#L5)), becomes unreachable once Fury
  is wired in. A passing verdict now goes straight to `production`. `staging_failed`
  (fail/error) and `pending` (no fixture) are unaffected. This is a direct, approved
  consequence of the spec's Flow section — Task 8 rewrites the one existing test that
  currently asserts `staging`.
- Spec reference: [docs/superpowers/specs/2026-08-05-fury-registry-design.md](../specs/2026-08-05-fury-registry-design.md)
  — read it if any task here seems to lack context on *why*, not just *what*.

---

### Task 1: Migrations — `model` table and `model_version` columns

**Files:**
- Create: `server/migrations/versions/c8e51f4d9a06_create_model_table.py`
- Create: `server/migrations/versions/d4a729c6e153_add_model_id_and_promoted_from_to_model_version.py`

**Interfaces:**
- Produces: a `model` table (`id`, `user_id`, `name`, `model_class`, `task_type`,
  `created_at`, unique on `(user_id, name)`) and two new nullable columns on
  `model_version`: `model_id` (FK → `model.id`) and `promoted_from` (FK → `eval_run.id`).
  Every later task in this plan depends on both columns existing.

No pytest tests for migrations — matches the existing convention (`a1c4e7b90d33` and
`b2d5f8c14e77` have none either). Verified operationally in Task 11 by running
`alembic upgrade head` against the real Supabase project.

- [ ] **Step 1: Write the `model` table migration**

```python
"""create model table

The logical model, grouping versions across uploads. Fury's identity layer — every
model_version links here via model_id (added in the next migration) so "which versions
belong to the same model" has a real answer instead of being inferred.

Revision ID: c8e51f4d9a06
Revises: b2d5f8c14e77
Create Date: 2026-08-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8e51f4d9a06'
down_revision: Union[str, Sequence[str], None] = 'b2d5f8c14e77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "model",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("model_class", sa.Text(), nullable=True),
        sa.Column("task_type", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_unique_constraint("uq_model_user_id_name", "model", ["user_id", "name"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_model_user_id_name", "model", type_="unique")
    op.drop_table("model")
```

Save to `server/migrations/versions/c8e51f4d9a06_create_model_table.py`.

- [ ] **Step 2: Write the `model_version` column migration**

```python
"""add model_id and promoted_from to model_version

Both are part of Schemas.md's original model_version spec but neither was ever actually
migrated — only artifact_sha256/artifact_uri/user_id/args/status/created_at exist in the
real table today. This adds the two columns Fury needs: model_id links a version to its
logical model (Fury's identity layer); promoted_from records which eval_run justified a
production promotion, so "why is this version live" is always answerable from the row
itself, not from memory of how the pipeline behaved that day.

Revision ID: d4a729c6e153
Revises: c8e51f4d9a06
Create Date: 2026-08-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4a729c6e153'
down_revision: Union[str, Sequence[str], None] = 'c8e51f4d9a06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "model_version",
        sa.Column("model_id", sa.Text(), sa.ForeignKey("model.id"), nullable=True),
    )
    op.add_column(
        "model_version",
        sa.Column("promoted_from", sa.Text(), sa.ForeignKey("eval_run.id"), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("model_version", "promoted_from")
    op.drop_column("model_version", "model_id")
```

Save to `server/migrations/versions/d4a729c6e153_add_model_id_and_promoted_from_to_model_version.py`.

- [ ] **Step 3: Commit**

```bash
git add server/migrations/versions/c8e51f4d9a06_create_model_table.py server/migrations/versions/d4a729c6e153_add_model_id_and_promoted_from_to_model_version.py
git commit -m "Add model table and model_version.model_id/promoted_from migrations"
```

(Do not run `alembic upgrade head` yet — Task 11 applies every migration once the code
that depends on them exists, matching how Nat's migrations were verified.)

---

### Task 2: `SupabaseMetadataStore` — read methods and fake `select` support

**Files:**
- Modify: `server/storage/models/supabase.py` (append after line 53)
- Modify: `server/tests/test_supabase.py`

**Interfaces:**
- Consumes: nothing new — same `self.client` (a `supabase-py` client or the test fake)
  already used by every existing method on this class.
- Produces:
  - `find_model(*, user_id, name) -> dict | None`
  - `find_model_version_by_hash(*, model_id, sha256) -> dict | None`
  - `find_production_version(*, model_id) -> dict | None`

  Task 4 (Fury's `find_existing`) calls `find_model` and `find_model_version_by_hash`
  directly. Task 5/6 (`register`) call all three.

The existing `FakeTable`/`FakeSupabaseClient` in `test_supabase.py` only support
`insert`/`update` — there is no `select`. This task extends them. The extension is shared
test infrastructure for every read method added in this task, so it is written once, in
the first RED step, rather than per-method.

- [ ] **Step 1: Extend the test fakes with `select` support, and write the first failing test**

Replace the top of `server/tests/test_supabase.py` (everything before
`def test_save_model_version_inserts_a_row_and_returns_its_id():`) with:

```python
from storage.models.supabase import SupabaseMetadataStore


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeTable:
    def __init__(self, name, calls, rows=None):
        self.name = name
        self.calls = calls
        self.rows = rows if rows is not None else []
        self._payload = None
        self._verb = None
        self._filters = []
        self._select_cols = None

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

    def execute(self):
        if self._verb == "select":
            self.calls.append((self.name, "select", self._select_cols, list(self._filters)))
            matches = [
                row for row in self.rows
                if all(row.get(col) == val for col, val in self._filters)
            ]
            return FakeResponse(matches)
        if self._verb == "update":
            self.calls.append((self.name, "update", self._payload, list(self._filters)))
            return FakeResponse([])
        self.calls.append((self.name, self._payload))
        return FakeResponse([])


class FakeSupabaseClient:
    def __init__(self, rows=None):
        self.calls = []
        self.rows = rows or {}

    def table(self, name):
        return FakeTable(name, self.calls, rows=self.rows.get(name, []))
```

This changes the recorded shape of an `update` call from a bare filter tuple to a list of
filter tuples (so a future compound filter can be expressed the same way a select's is).
One existing assertion depends on the old shape — fix it now, in this same step, so the
suite doesn't go red for an unrelated reason:

Find in `test_supabase.py`:
```python
    assert fake_client.calls == [
        ("model_version", "update", {"status": "staging"}, ("id", "mv_abc"))
    ]
```
Replace with:
```python
    assert fake_client.calls == [
        ("model_version", "update", {"status": "staging"}, [("id", "mv_abc")])
    ]
```

Now append the first new test, at the end of the file:

```python


def test_find_model_returns_the_row_matching_user_and_name():
    fake_client = FakeSupabaseClient(
        rows={
            "model": [
                {"id": "mdl_1", "user_id": "u_1", "name": "fraud-classifier"},
                {"id": "mdl_2", "user_id": "u_1", "name": "churn-model"},
            ]
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    model = store.find_model(user_id="u_1", name="fraud-classifier")

    assert model == {"id": "mdl_1", "user_id": "u_1", "name": "fraud-classifier"}
    assert fake_client.calls == [
        ("model", "select", "*", [("user_id", "u_1"), ("name", "fraud-classifier")])
    ]


def test_find_model_returns_none_when_no_row_matches():
    fake_client = FakeSupabaseClient(rows={"model": []})
    store = SupabaseMetadataStore(client=fake_client)

    assert store.find_model(user_id="u_1", name="does-not-exist") is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd server && uv run pytest tests/test_supabase.py -v
```
Expected: the two new tests `FAIL` with `AttributeError: 'SupabaseMetadataStore' object has
no attribute 'find_model'`. The filter-shape fix from Step 1 should make the pre-existing
`test_update_model_version_status_targets_exactly_one_row` pass, not fail — if it fails,
the replacement in Step 1 was not applied correctly.

- [ ] **Step 3: Implement `find_model`**

Append to `server/storage/models/supabase.py`:

```python

    def find_model(self, *, user_id, name):
        result = (
            self.client.table("model")
            .select("*")
            .eq("user_id", user_id)
            .eq("name", name)
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
git commit -m "Add SupabaseMetadataStore.find_model with fake select support"
```

- [ ] **Step 6: Write the failing tests for the remaining two read methods**

Append to `server/tests/test_supabase.py`:

```python


def test_find_model_version_by_hash_returns_the_matching_version():
    fake_client = FakeSupabaseClient(
        rows={
            "model_version": [
                {"id": "mv_1", "model_id": "mdl_1", "artifact_sha256": "abc123"},
                {"id": "mv_2", "model_id": "mdl_1", "artifact_sha256": "def456"},
            ]
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    version = store.find_model_version_by_hash(model_id="mdl_1", sha256="abc123")

    assert version == {"id": "mv_1", "model_id": "mdl_1", "artifact_sha256": "abc123"}
    assert fake_client.calls == [
        (
            "model_version",
            "select",
            "*",
            [("model_id", "mdl_1"), ("artifact_sha256", "abc123")],
        )
    ]


def test_find_model_version_by_hash_returns_none_when_no_version_matches():
    fake_client = FakeSupabaseClient(rows={"model_version": []})
    store = SupabaseMetadataStore(client=fake_client)

    assert store.find_model_version_by_hash(model_id="mdl_1", sha256="nope") is None


def test_find_production_version_returns_the_current_live_version():
    fake_client = FakeSupabaseClient(
        rows={
            "model_version": [
                {"id": "mv_1", "model_id": "mdl_1", "status": "archived"},
                {"id": "mv_2", "model_id": "mdl_1", "status": "production"},
            ]
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    version = store.find_production_version(model_id="mdl_1")

    assert version == {"id": "mv_2", "model_id": "mdl_1", "status": "production"}
    assert fake_client.calls == [
        ("model_version", "select", "*", [("model_id", "mdl_1"), ("status", "production")])
    ]


def test_find_production_version_returns_none_when_nothing_is_live():
    fake_client = FakeSupabaseClient(
        rows={"model_version": [{"id": "mv_1", "model_id": "mdl_1", "status": "pending"}]}
    )
    store = SupabaseMetadataStore(client=fake_client)

    assert store.find_production_version(model_id="mdl_1") is None
```

- [ ] **Step 7: Run tests to verify they fail**

```bash
cd server && uv run pytest tests/test_supabase.py -v
```
Expected: the four new tests `FAIL` with `AttributeError`.

- [ ] **Step 8: Implement the remaining two read methods**

Append to `server/storage/models/supabase.py`:

```python

    def find_model_version_by_hash(self, *, model_id, sha256):
        result = (
            self.client.table("model_version")
            .select("*")
            .eq("model_id", model_id)
            .eq("artifact_sha256", sha256)
            .execute()
        )
        return result.data[0] if result.data else None

    def find_production_version(self, *, model_id):
        result = (
            self.client.table("model_version")
            .select("*")
            .eq("model_id", model_id)
            .eq("status", "production")
            .execute()
        )
        return result.data[0] if result.data else None
```

- [ ] **Step 9: Run tests to verify they pass**

```bash
cd server && uv run pytest tests/test_supabase.py -v
```
Expected: all `PASS`.

- [ ] **Step 10: Commit**

```bash
git add server/storage/models/supabase.py server/tests/test_supabase.py
git commit -m "Add find_model_version_by_hash and find_production_version"
```

---

### Task 3: `SupabaseMetadataStore` — write methods

**Files:**
- Modify: `server/storage/models/supabase.py` (append after Task 2's additions)
- Modify: `server/tests/test_supabase.py`

**Interfaces:**
- Consumes: `FakeSupabaseClient`/`FakeTable` from Task 2 (already support `insert` and the
  list-shaped `update` filters).
- Produces:
  - `create_model(*, user_id, name, model_class, task_type) -> str` (a new `mdl_...` id)
  - `link_model_version(*, model_version_id, model_id) -> None`
  - `promote_model_version(*, model_version_id, eval_run_id) -> None`
  - `archive_model_version(*, model_version_id) -> None`

  All four are called directly by `register` in Tasks 5 and 6.

- [ ] **Step 1: Write the failing test for `create_model`**

Append to `server/tests/test_supabase.py`:

```python


def test_create_model_inserts_a_row_and_returns_its_id():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    model_id = store.create_model(
        user_id="u_1", name="fraud-classifier", model_class="LogisticRegression",
        task_type="classification",
    )

    assert model_id.startswith("mdl_")
    assert fake_client.calls == [
        (
            "model",
            {
                "id": model_id,
                "user_id": "u_1",
                "name": "fraud-classifier",
                "model_class": "LogisticRegression",
                "task_type": "classification",
            },
        )
    ]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd server && uv run pytest tests/test_supabase.py::test_create_model_inserts_a_row_and_returns_its_id -v
```
Expected: `FAIL` with `AttributeError: 'SupabaseMetadataStore' object has no attribute 'create_model'`.

- [ ] **Step 3: Implement `create_model`**

Append to `server/storage/models/supabase.py`:

```python

    def create_model(self, *, user_id, name, model_class, task_type):
        model_id = f"mdl_{uuid.uuid4().hex}"
        self.client.table("model").insert(
            {
                "id": model_id,
                "user_id": user_id,
                "name": name,
                "model_class": model_class,
                "task_type": task_type,
            }
        ).execute()
        return model_id
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd server && uv run pytest tests/test_supabase.py::test_create_model_inserts_a_row_and_returns_its_id -v
```
Expected: `PASS`.

- [ ] **Step 5: Write the failing tests for the three remaining write methods**

Append to `server/tests/test_supabase.py`:

```python


def test_link_model_version_sets_the_model_id_on_one_row():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    store.link_model_version(model_version_id="mv_abc", model_id="mdl_1")

    assert fake_client.calls == [
        ("model_version", "update", {"model_id": "mdl_1"}, [("id", "mv_abc")])
    ]


def test_promote_model_version_sets_status_and_promoted_from():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    store.promote_model_version(model_version_id="mv_abc", eval_run_id="evr_1")

    assert fake_client.calls == [
        (
            "model_version",
            "update",
            {"status": "production", "promoted_from": "evr_1"},
            [("id", "mv_abc")],
        )
    ]


def test_archive_model_version_sets_status_to_archived():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    store.archive_model_version(model_version_id="mv_abc")

    assert fake_client.calls == [
        ("model_version", "update", {"status": "archived"}, [("id", "mv_abc")])
    ]
```

- [ ] **Step 6: Run tests to verify they fail**

```bash
cd server && uv run pytest tests/test_supabase.py -v
```
Expected: the three new tests `FAIL` with `AttributeError`.

- [ ] **Step 7: Implement the three remaining write methods**

Append to `server/storage/models/supabase.py`:

```python

    def link_model_version(self, *, model_version_id, model_id):
        self.client.table("model_version").update({"model_id": model_id}).eq(
            "id", model_version_id
        ).execute()

    def promote_model_version(self, *, model_version_id, eval_run_id):
        self.client.table("model_version").update(
            {"status": "production", "promoted_from": eval_run_id}
        ).eq("id", model_version_id).execute()

    def archive_model_version(self, *, model_version_id):
        self.client.table("model_version").update({"status": "archived"}).eq(
            "id", model_version_id
        ).execute()
```

- [ ] **Step 8: Run the full file to verify everything passes**

```bash
cd server && uv run pytest tests/test_supabase.py -v
```
Expected: all `PASS` — 14 tests total (the original 4, plus 6 from Task 2, plus 4 new
here).

- [ ] **Step 9: Commit**

```bash
git add server/storage/models/supabase.py server/tests/test_supabase.py
git commit -m "Add create_model, link_model_version, promote_model_version, archive_model_version"
```

---

### Task 4: Fury — `find_existing` (dedup check)

**Files:**
- Create: `agents/brain3/fury/registry.py`
- Create: `server/tests/test_fury_registry.py`

**Interfaces:**
- Consumes: `metadata_store.find_model(user_id, name)`,
  `metadata_store.find_model_version_by_hash(model_id, sha256)` (Task 2).
- Produces: `find_existing(*, user_id, sha256, name, metadata_store) -> dict | None`.
  Task 7 (`orchestrator.py`) calls this before anything else runs.

No `__init__.py` — matches `agents/brain1/hawkeye/` and `agents/brain2/nat/`, which are
implicit namespace packages.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_fury_registry.py`:

```python
from agents.brain3.fury.registry import find_existing


class FakeMetadataStore:
    def __init__(self, models=None, versions=None):
        self.models = models or {}
        self.versions = versions or {}

    def find_model(self, *, user_id, name):
        return self.models.get((user_id, name))

    def find_model_version_by_hash(self, *, model_id, sha256):
        return self.versions.get((model_id, sha256))


def test_find_existing_returns_none_when_no_model_exists_under_that_name():
    store = FakeMetadataStore()

    assert find_existing(
        user_id="u_1", sha256="abc123", name="fraud-classifier", metadata_store=store
    ) is None


def test_find_existing_returns_none_when_the_model_exists_but_the_hash_does_not_match():
    store = FakeMetadataStore(models={("u_1", "fraud-classifier"): {"id": "mdl_1"}})

    assert find_existing(
        user_id="u_1", sha256="abc123", name="fraud-classifier", metadata_store=store
    ) is None


def test_find_existing_returns_the_version_when_both_the_name_and_hash_match():
    store = FakeMetadataStore(
        models={("u_1", "fraud-classifier"): {"id": "mdl_1"}},
        versions={("mdl_1", "abc123"): {"id": "mv_1", "status": "production"}},
    )

    result = find_existing(
        user_id="u_1", sha256="abc123", name="fraud-classifier", metadata_store=store
    )

    assert result == {"id": "mv_1", "status": "production"}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd server && uv run pytest tests/test_fury_registry.py -v
```
Expected: `FAIL` with `ModuleNotFoundError: No module named 'agents.brain3'`.

- [ ] **Step 3: Implement `find_existing`**

Create `agents/brain3/fury/registry.py`:

```python
def find_existing(*, user_id, sha256, name, metadata_store):
    """A byte-for-byte re-upload under the same name is a true no-op.

    Checked before anything else runs — before the S3 write, before Hawkeye, before Nat
    — so an exact repeat costs nothing beyond this lookup. A hash match under a
    *different* name is deliberately not a hit: identical bytes registered under a new
    name is a legitimate new registration, not an accidental duplicate.
    """
    model = metadata_store.find_model(user_id=user_id, name=name)
    if model is None:
        return None
    return metadata_store.find_model_version_by_hash(model_id=model["id"], sha256=sha256)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd server && uv run pytest tests/test_fury_registry.py -v
```
Expected: all 3 `PASS`.

- [ ] **Step 5: Commit**

```bash
git add agents/brain3/fury/registry.py server/tests/test_fury_registry.py
git commit -m "Add Fury.find_existing for content-hash dedup"
```

---

### Task 5: Fury — `register`, identity linking and non-promoting paths

**Files:**
- Modify: `agents/brain3/fury/registry.py`
- Modify: `server/tests/test_fury_registry.py`

**Interfaces:**
- Consumes: `metadata_store.find_model`, `.create_model`, `.link_model_version` (identity);
  `.update_model_version_status` (already exists, from the Nat work — reused here for the
  fail/error case rather than adding a redundant method).
- Produces: `register(*, user_id, name, model_version_id, manifest, verdict, eval_run_id, metadata_store) -> dict`,
  returning `{"model_id": str, "status": str, "archived_model_version_id": str | None}`.
  This task covers every path except the promotion path (Task 6 adds that on top).

This task's fake `FakeMetadataStore` needs `update_model_version_status` in addition to
what Task 4's fake had — extend it in place rather than duplicating the class.

- [ ] **Step 1: Write the failing tests for identity linking and the no-eval / fail paths**

Replace the `FakeMetadataStore` class in `server/tests/test_fury_registry.py` with an
extended version, and add the new tests. The full new top of the file:

```python
from agents.brain3.fury.registry import find_existing, register


class FakeMetadataStore:
    def __init__(self, models=None, versions=None):
        self.models = models or {}
        self.versions = versions or {}
        self.created_models = []
        self.linked = []
        self.status_updates = []
        self.promotions = []
        self.archived = []
        self._next_model_id = 1

    def find_model(self, *, user_id, name):
        return self.models.get((user_id, name))

    def find_model_version_by_hash(self, *, model_id, sha256):
        return self.versions.get((model_id, sha256))

    def create_model(self, *, user_id, name, model_class, task_type):
        model_id = f"mdl_{self._next_model_id}"
        self._next_model_id += 1
        self.created_models.append(
            {"id": model_id, "user_id": user_id, "name": name,
             "model_class": model_class, "task_type": task_type}
        )
        self.models[(user_id, name)] = {"id": model_id}
        return model_id

    def link_model_version(self, *, model_version_id, model_id):
        self.linked.append((model_version_id, model_id))

    def update_model_version_status(self, *, model_version_id, status):
        self.status_updates.append((model_version_id, status))

    def find_production_version(self, *, model_id):
        return None

    def promote_model_version(self, *, model_version_id, eval_run_id):
        self.promotions.append((model_version_id, eval_run_id))

    def archive_model_version(self, *, model_version_id):
        self.archived.append(model_version_id)
```

(Keep the three existing `find_existing` tests below this — they are unaffected, since
`find_model`/`find_model_version_by_hash` behave identically.)

Append the new tests at the end of the file:

```python


MANIFEST = {"framework": "sklearn", "model_class": "LogisticRegression", "task_type": "classification"}


def test_register_creates_a_model_on_the_first_upload_under_a_new_name():
    store = FakeMetadataStore()

    result = register(
        user_id="u_1", name="fraud-classifier", model_version_id="mv_1",
        manifest=MANIFEST, verdict=None, eval_run_id=None, metadata_store=store,
    )

    assert store.created_models == [
        {
            "id": result["model_id"], "user_id": "u_1", "name": "fraud-classifier",
            "model_class": "LogisticRegression", "task_type": "classification",
        }
    ]


def test_register_reuses_the_existing_model_on_a_second_upload_under_the_same_name():
    store = FakeMetadataStore(models={("u_1", "fraud-classifier"): {"id": "mdl_existing"}})

    result = register(
        user_id="u_1", name="fraud-classifier", model_version_id="mv_2",
        manifest=MANIFEST, verdict=None, eval_run_id=None, metadata_store=store,
    )

    assert store.created_models == []
    assert result["model_id"] == "mdl_existing"


def test_register_links_the_model_version_regardless_of_verdict():
    store = FakeMetadataStore()

    result = register(
        user_id="u_1", name="fraud-classifier", model_version_id="mv_1",
        manifest=MANIFEST, verdict=None, eval_run_id=None, metadata_store=store,
    )

    assert store.linked == [("mv_1", result["model_id"])]


def test_register_with_no_verdict_reports_pending_and_writes_no_status():
    store = FakeMetadataStore()

    result = register(
        user_id="u_1", name="fraud-classifier", model_version_id="mv_1",
        manifest=MANIFEST, verdict=None, eval_run_id=None, metadata_store=store,
    )

    assert result["status"] == "pending"
    assert result["archived_model_version_id"] is None
    assert store.status_updates == []


def test_register_with_a_failing_verdict_writes_staging_failed():
    store = FakeMetadataStore()

    result = register(
        user_id="u_1", name="fraud-classifier", model_version_id="mv_1",
        manifest=MANIFEST, verdict="fail", eval_run_id="evr_1", metadata_store=store,
    )

    assert result["status"] == "staging_failed"
    assert store.status_updates == [("mv_1", "staging_failed")]
    assert store.promotions == []


def test_register_with_an_error_verdict_also_writes_staging_failed():
    store = FakeMetadataStore()

    result = register(
        user_id="u_1", name="fraud-classifier", model_version_id="mv_1",
        manifest=MANIFEST, verdict="error", eval_run_id="evr_1", metadata_store=store,
    )

    assert result["status"] == "staging_failed"
    assert store.status_updates == [("mv_1", "staging_failed")]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd server && uv run pytest tests/test_fury_registry.py -v
```
Expected: the six new tests `FAIL` — `find_existing`'s three tests should still `PASS`
(the fake extension didn't change their behavior); the `register` tests fail with
`ImportError: cannot import name 'register'`.

- [ ] **Step 3: Implement `register` for these paths (promotion path stubbed for now)**

Append to `agents/brain3/fury/registry.py`:

```python


def register(*, user_id, name, model_version_id, manifest, verdict, eval_run_id, metadata_store):
    """Link this version to its model's identity, and promote it if it earned that.

    Identity linking happens unconditionally — a pending or failed version is still
    part of a model's version history and needs to be findable as such. Promotion only
    happens on a passing verdict (added in the next task).
    """
    model = metadata_store.find_model(user_id=user_id, name=name)
    if model is None:
        model_id = metadata_store.create_model(
            user_id=user_id,
            name=name,
            model_class=manifest.get("model_class"),
            task_type=manifest.get("task_type"),
        )
    else:
        model_id = model["id"]

    metadata_store.link_model_version(model_version_id=model_version_id, model_id=model_id)

    if verdict != "pass":
        status = "pending"
        if verdict is not None:
            status = "staging_failed"
            metadata_store.update_model_version_status(
                model_version_id=model_version_id, status=status
            )
        return {"model_id": model_id, "status": status, "archived_model_version_id": None}

    raise NotImplementedError("promotion path added in the next task")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd server && uv run pytest tests/test_fury_registry.py -v
```
Expected: all 9 `PASS` (3 from Task 4, 6 new).

- [ ] **Step 5: Commit**

```bash
git add agents/brain3/fury/registry.py server/tests/test_fury_registry.py
git commit -m "Add Fury.register identity linking and non-promoting verdict paths"
```

---

### Task 6: Fury — `register`, promotion and archival path

**Files:**
- Modify: `agents/brain3/fury/registry.py`
- Modify: `server/tests/test_fury_registry.py`

**Interfaces:**
- Consumes: `metadata_store.find_production_version`, `.archive_model_version`,
  `.promote_model_version` — all already on `FakeMetadataStore` from Task 5, and all
  already implemented for real in Task 2/3.
- Produces: completes `register`'s contract from Task 5 — the `raise NotImplementedError`
  placeholder is replaced.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_fury_registry.py`:

```python


def test_register_with_a_passing_verdict_promotes_the_version_to_production():
    store = FakeMetadataStore()

    result = register(
        user_id="u_1", name="fraud-classifier", model_version_id="mv_1",
        manifest=MANIFEST, verdict="pass", eval_run_id="evr_1", metadata_store=store,
    )

    assert result["status"] == "production"
    assert store.promotions == [("mv_1", "evr_1")]


def test_register_with_a_passing_verdict_and_no_incumbent_archives_nothing():
    store = FakeMetadataStore()

    result = register(
        user_id="u_1", name="fraud-classifier", model_version_id="mv_1",
        manifest=MANIFEST, verdict="pass", eval_run_id="evr_1", metadata_store=store,
    )

    assert store.archived == []
    assert result["archived_model_version_id"] is None


def test_register_with_a_passing_verdict_archives_the_current_production_version():
    class StoreWithIncumbent(FakeMetadataStore):
        def find_production_version(self, *, model_id):
            return {"id": "mv_old"}

    store = StoreWithIncumbent(models={("u_1", "fraud-classifier"): {"id": "mdl_1"}})

    result = register(
        user_id="u_1", name="fraud-classifier", model_version_id="mv_new",
        manifest=MANIFEST, verdict="pass", eval_run_id="evr_1", metadata_store=store,
    )

    assert store.archived == ["mv_old"]
    assert result["archived_model_version_id"] == "mv_old"
    assert store.promotions == [("mv_new", "evr_1")]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd server && uv run pytest tests/test_fury_registry.py -v
```
Expected: the three new tests `FAIL` with `NotImplementedError: promotion path added in
the next task`.

- [ ] **Step 3: Implement the promotion path**

In `agents/brain3/fury/registry.py`, replace:

```python
    raise NotImplementedError("promotion path added in the next task")
```

with:

```python
    incumbent = metadata_store.find_production_version(model_id=model_id)
    archived_id = None
    if incumbent is not None:
        metadata_store.archive_model_version(model_version_id=incumbent["id"])
        archived_id = incumbent["id"]

    metadata_store.promote_model_version(model_version_id=model_version_id, eval_run_id=eval_run_id)
    return {"model_id": model_id, "status": "production", "archived_model_version_id": archived_id}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd server && uv run pytest tests/test_fury_registry.py -v
```
Expected: all 12 `PASS`.

- [ ] **Step 5: Commit**

```bash
git add agents/brain3/fury/registry.py server/tests/test_fury_registry.py
git commit -m "Add Fury.register promotion and archival of the previous production version"
```

---

### Task 7: `orchestrator.py` — dedup short-circuit and `name` threading

**Files:**
- Modify: `server/orchestrator.py`
- Modify: `server/tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `find_existing(*, user_id, sha256, name, metadata_store)` (Task 4).
- Produces: `build_artifact` gains a required `name` keyword-only parameter and a new
  `find_existing_fn=None` parameter. On a dedup hit, `build_artifact` returns immediately
  without touching `blob_store`, `identify_fn`, or `evaluate_fn`.

This task does not yet wire in `register_fn` — that is Task 8, so `_STATUS_FOR_VERDICT`
and the existing promotion-free status logic stay as they are for now. Every existing test
in `test_orchestrator.py` needs `name=` added to its `build_artifact` call and a
`find_existing_fn` that returns `None` (the "nothing found" case) so the new dedup check
doesn't change existing behavior. This task rewrites the whole test file for that reason —
touching one call site at a time here would leave the file in an inconsistent state
between steps.

- [ ] **Step 1: Rewrite the test file with `name` and `find_existing_fn` added everywhere, plus the new dedup tests**

Replace the entire contents of `server/tests/test_orchestrator.py`:

```python
import cloudpickle

from orchestrator import build_artifact


class FakeBlobStore:
    def __init__(self):
        self.blobs = {}

    def put(self, sha256: str, payload: bytes) -> str:
        self.blobs[sha256] = payload
        return f"s3://artifacts/{sha256}"


class FakeMetadataStore:
    def __init__(self):
        self.model_versions = {}
        self.manifests = {}
        self.eval_runs = {}
        self.status_updates = []

    def save_model_version(self, *, sha256, artifact_uri, user_id, args, status):
        self.model_versions[sha256] = {
            "artifact_uri": artifact_uri,
            "user_id": user_id,
            "args": args,
            "status": status,
        }
        return "mv_123"

    def save_manifest(self, *, model_version_id, manifest):
        self.manifests[model_version_id] = manifest
        return "mf_456"

    def save_eval_run(self, *, model_version_id, eval_run):
        self.eval_runs[model_version_id] = eval_run
        return "evr_789"

    def update_model_version_status(self, *, model_version_id, status):
        self.status_updates.append((model_version_id, status))


def fake_identify(model):
    return {"framework": "sklearn", "model_class": "FakeModel"}


def passing_eval(**kwargs):
    return {"verdict": "pass", "scores": {"accuracy": 1.0}, "seen": sorted(kwargs)}


def no_existing(**kwargs):
    return None


def fake_register(**kwargs):
    verdict = kwargs["verdict"]
    if verdict == "pass":
        status = "production"
    elif verdict is not None:
        status = "staging_failed"
    else:
        status = "pending"
    return {"model_id": "mdl_123", "status": status, "archived_model_version_id": None}


def test_build_artifact_stores_bytes_metadata_and_manifest_then_returns_a_record():
    blob_store = FakeBlobStore()
    metadata_store = FakeMetadataStore()
    identified_models = []

    def recording_identify(model):
        identified_models.append(model)
        return {"framework": "sklearn", "model_class": "FakeModel"}

    payload = cloudpickle.dumps({"kind": "fake-model"})

    result = build_artifact(
        payload=payload,
        sha256="abc123",
        user_id="u_1",
        name="fake-model",
        args={"framework_hint": "sklearn"},
        blob_store=blob_store,
        metadata_store=metadata_store,
        identify_fn=recording_identify,
        find_existing_fn=no_existing,
        register_fn=fake_register,
    )

    assert blob_store.blobs["abc123"] == payload
    assert metadata_store.model_versions["abc123"]["artifact_uri"] == "s3://artifacts/abc123"
    assert metadata_store.model_versions["abc123"]["status"] == "pending"
    assert identified_models == [{"kind": "fake-model"}]
    assert metadata_store.manifests["mv_123"] == {"framework": "sklearn", "model_class": "FakeModel"}
    assert result == {
        "model_version_id": "mv_123",
        "artifact_uri": "s3://artifacts/abc123",
        "status": "pending",
        "manifest": {"framework": "sklearn", "model_class": "FakeModel"},
        "eval_run": None,
    }


def test_a_fixture_supplied_at_ingest_is_stored_and_evaluated_in_the_same_pass():
    blob_store = FakeBlobStore()
    metadata_store = FakeMetadataStore()
    fixture_payload = cloudpickle.dumps({"X": [[0.0]], "y": [0]})

    result = build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256="abc123",
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=blob_store,
        metadata_store=metadata_store,
        identify_fn=fake_identify,
        find_existing_fn=no_existing,
        register_fn=fake_register,
        fixture_payload=fixture_payload,
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=passing_eval,
    )

    assert blob_store.blobs["def456"] == fixture_payload
    assert metadata_store.eval_runs["mv_123"]["verdict"] == "pass"
    assert result["eval_run"]["id"] == "evr_789"
    assert result["eval_run"]["verdict"] == "pass"


def test_the_fixture_descriptor_records_where_the_test_set_actually_landed():
    blob_store = FakeBlobStore()
    seen = {}

    def recording_eval(**kwargs):
        seen.update(kwargs)
        return {"verdict": "pass"}

    build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256="abc123",
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=blob_store,
        metadata_store=FakeMetadataStore(),
        identify_fn=fake_identify,
        find_existing_fn=no_existing,
        register_fn=fake_register,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=recording_eval,
    )

    assert seen["fixture"]["uri"] == "s3://artifacts/def456"
    assert seen["data"] == {"X": [[0.0]], "y": [0]}


def test_without_a_fixture_nothing_is_evaluated_and_the_version_stays_pending():
    metadata_store = FakeMetadataStore()

    def must_not_run(**_):
        raise AssertionError("evaluate_fn must not be called without a fixture")

    result = build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256="abc123",
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=FakeBlobStore(),
        metadata_store=metadata_store,
        identify_fn=fake_identify,
        find_existing_fn=no_existing,
        register_fn=fake_register,
        evaluate_fn=must_not_run,
    )

    assert result["eval_run"] is None
    assert result["status"] == "pending"
    assert metadata_store.eval_runs == {}


def test_an_exact_repeat_of_name_and_hash_short_circuits_before_anything_else_runs():
    existing_record = {"id": "mv_existing", "artifact_uri": "s3://artifacts/abc123", "status": "production"}

    def found_existing(**kwargs):
        return existing_record

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("nothing downstream should run on a dedup hit")

    result = build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256="abc123",
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=type("ExplodingBlobStore", (), {"put": must_not_run})(),
        metadata_store=type("ExplodingMetadataStore", (), {})(),
        identify_fn=must_not_run,
        find_existing_fn=found_existing,
        register_fn=must_not_run,
    )

    assert result == {
        "model_version_id": "mv_existing",
        "artifact_uri": "s3://artifacts/abc123",
        "status": "production",
        "manifest": None,
        "eval_run": None,
        "deduplicated": True,
    }


def test_the_dedup_check_is_given_the_users_id_hash_and_name():
    seen = {}

    def recording_find_existing(**kwargs):
        seen.update(kwargs)
        return None

    build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256="abc123",
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=FakeBlobStore(),
        metadata_store=FakeMetadataStore(),
        identify_fn=fake_identify,
        find_existing_fn=recording_find_existing,
        register_fn=fake_register,
    )

    assert seen["user_id"] == "u_1"
    assert seen["sha256"] == "abc123"
    assert seen["name"] == "fake-model"
```

Note what was deliberately dropped from the old file: the two tests asserting `status ==
"staging"` / `"staging_failed"` on eval outcomes, and the one asserting an errored eval
holds the version at `staging_failed`. Those move to Task 8, once `register_fn` is
actually wired into the status decision instead of being a passthrough fake. For the same
reason, the first test's expected result has no `model_id` key — this task's code change
only touches lines 1–24 (the dedup check) and leaves the function's tail, including its
final `return` statement, untouched; `register_fn` is accepted as a parameter here but not
yet called, so nothing populates `model_id` until Task 8 rewires that return statement too.
`register_fn=fake_register` is still passed in these tests for forward compatibility with
Task 8, even though it's unused at this stage.

- [ ] **Step 2: Run tests to verify the new ones fail for the right reason**

```bash
cd server && uv run pytest tests/test_orchestrator.py -v
```
Expected: `test_an_exact_repeat_of_name_and_hash_short_circuits_before_anything_else_runs`
and `test_the_dedup_check_is_given_the_users_id_hash_and_name` `FAIL` with
`TypeError: build_artifact() got an unexpected keyword argument 'find_existing_fn'` (or
similar — `name` is also unexpected). The other four tests should also fail with the same
`TypeError`, since `name=` isn't accepted yet either.

- [ ] **Step 3: Implement the dedup short-circuit and `name` parameter**

Replace `server/orchestrator.py` lines 1–24 (from the top through
`artifact_uri = blob_store.put(sha256, payload)`) with:

```python
import cloudpickle

# A verdict that isn't a clean pass leaves the version held, never promoted. Fury
# decides production; this only decides whether the version earned a look.
_STATUS_FOR_VERDICT = {"pass": "staging"}


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
    fixture_payload=None,
    fixture_descriptor=None,
):
    identify_fn = identify_fn or _default_identify
    evaluate_fn = evaluate_fn or _default_evaluate
    find_existing_fn = find_existing_fn or _default_find_existing
    register_fn = register_fn or _default_register

    existing = find_existing_fn(
        user_id=user_id, sha256=sha256, name=name, metadata_store=metadata_store
    )
    if existing is not None:
        return {
            "model_version_id": existing["id"],
            "artifact_uri": existing["artifact_uri"],
            "status": existing["status"],
            "manifest": None,
            "eval_run": None,
            "deduplicated": True,
        }

    artifact_uri = blob_store.put(sha256, payload)
```

Leave everything from the `# Deserializing here means...` comment through the end of
`build_artifact`'s current body (down to and including the final `return {...}` and the
`_evaluate`/`_default_identify`/`_default_evaluate` functions) untouched for this step —
Task 8 rewrites the tail of the function and adds `_default_register`. Do not add
`_default_find_existing` yet either; add it now alongside the other two `_default_*`
functions at the bottom of the file, right after `_default_evaluate`:

```python


def _default_find_existing(**kwargs):
    from agents.brain3.fury.registry import find_existing

    return find_existing(**kwargs)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd server && uv run pytest tests/test_orchestrator.py -v
```
Expected: all 6 `PASS`.

- [ ] **Step 5: Commit**

```bash
git add server/orchestrator.py server/tests/test_orchestrator.py
git commit -m "Wire Fury dedup check into build_artifact; require name"
```

---

### Task 8: `orchestrator.py` — wire `register_fn`, remove `_STATUS_FOR_VERDICT`

**Files:**
- Modify: `server/orchestrator.py`
- Modify: `server/tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `register(*, user_id, name, model_version_id, manifest, verdict, eval_run_id, metadata_store)`
  (Tasks 5–6).
- Produces: `build_artifact`'s final status now comes from `register_fn`'s return value —
  `_STATUS_FOR_VERDICT` and the module docstring comment describing it are removed
  entirely, since Fury now owns every post-manifest status decision.

- [ ] **Step 1: Write the failing tests for promotion, failure, and error behavior**

Append to `server/tests/test_orchestrator.py`:

```python


def test_a_passing_verdict_promotes_the_version_to_production():
    metadata_store = FakeMetadataStore()

    result = build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256="abc123",
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=FakeBlobStore(),
        metadata_store=metadata_store,
        identify_fn=fake_identify,
        find_existing_fn=no_existing,
        register_fn=fake_register,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=passing_eval,
    )

    assert result["status"] == "production"


def test_a_failing_verdict_holds_the_version_at_staging_failed():
    metadata_store = FakeMetadataStore()

    result = build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256="abc123",
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=FakeBlobStore(),
        metadata_store=metadata_store,
        identify_fn=fake_identify,
        find_existing_fn=no_existing,
        register_fn=fake_register,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=lambda **_: {"verdict": "fail"},
    )

    assert result["status"] == "staging_failed"


def test_an_eval_that_errored_also_holds_the_version_at_staging_failed():
    metadata_store = FakeMetadataStore()

    result = build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256="abc123",
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=FakeBlobStore(),
        metadata_store=metadata_store,
        identify_fn=fake_identify,
        find_existing_fn=no_existing,
        register_fn=fake_register,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=lambda **_: {"verdict": "error", "error": {"message": "boom"}},
    )

    assert result["status"] == "staging_failed"


def test_register_is_called_even_when_no_fixture_was_supplied():
    seen = {}

    def recording_register(**kwargs):
        seen.update(kwargs)
        return {"model_id": "mdl_1", "status": "pending", "archived_model_version_id": None}

    build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256="abc123",
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=FakeBlobStore(),
        metadata_store=FakeMetadataStore(),
        identify_fn=fake_identify,
        find_existing_fn=no_existing,
        register_fn=recording_register,
    )

    assert seen["verdict"] is None
    assert seen["eval_run_id"] is None
    assert seen["name"] == "fake-model"


def test_register_receives_the_eval_run_id_and_verdict_when_an_eval_ran():
    seen = {}

    def recording_register(**kwargs):
        seen.update(kwargs)
        return {"model_id": "mdl_1", "status": "production", "archived_model_version_id": None}

    build_artifact(
        payload=cloudpickle.dumps({"kind": "fake-model"}),
        sha256="abc123",
        user_id="u_1",
        name="fake-model",
        args={},
        blob_store=FakeBlobStore(),
        metadata_store=FakeMetadataStore(),
        identify_fn=fake_identify,
        find_existing_fn=no_existing,
        register_fn=recording_register,
        fixture_payload=cloudpickle.dumps({"X": [[0.0]], "y": [0]}),
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
        evaluate_fn=passing_eval,
    )

    assert seen["verdict"] == "pass"
    assert seen["eval_run_id"] == "evr_789"
```

This task's Step 3 makes `build_artifact`'s return dict unconditionally include
`"model_id": registration["model_id"]`. Task 7's version of
`test_build_artifact_stores_bytes_metadata_and_manifest_then_returns_a_record` deliberately
had `model_id` *removed* from its expected result (Task 7's own code change didn't produce
it yet) — now that this task's code does produce it, that same test needs it back, or its
exact-dict assertion will fail once Step 3 lands. Restore the line now, in this same step:

Find in `server/tests/test_orchestrator.py`:
```python
    assert result == {
        "model_version_id": "mv_123",
        "artifact_uri": "s3://artifacts/abc123",
        "status": "pending",
        "manifest": {"framework": "sklearn", "model_class": "FakeModel"},
        "eval_run": None,
    }
```
Replace with:
```python
    assert result == {
        "model_version_id": "mv_123",
        "artifact_uri": "s3://artifacts/abc123",
        "status": "pending",
        "manifest": {"framework": "sklearn", "model_class": "FakeModel"},
        "eval_run": None,
        "model_id": "mdl_123",
    }
```
(`"mdl_123"` is what `fake_register`, already used by this test, always returns as
`model_id` — see the module-level fake defined in Task 7.)

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd server && uv run pytest tests/test_orchestrator.py -v
```
Expected: only **three** of the five new tests actually fail here, not five — this is a
real quirk of the old code worth understanding, not a sign something's wrong.
`test_a_passing_verdict_promotes_the_version_to_production` fails (`"staging"` from the old
`_STATUS_FOR_VERDICT` logic, not `"production"`), and the two `register_fn`-recording tests
fail (a `KeyError` — `register_fn` isn't called at all under the old code, so `seen` stays
empty). But `test_a_failing_verdict_holds_the_version_at_staging_failed` and
`test_an_eval_that_errored_also_holds_the_version_at_staging_failed` **coincidentally
already pass**: `_STATUS_FOR_VERDICT.get("fail"/"error", "staging_failed")` falls through
to the same `"staging_failed"` default the new code also produces for those verdicts. Both
tests remain fully valid — after Step 3 they pass for the new, correct reason
(`register_fn`'s return value) rather than the old one, and that mechanism is what the rest
of this task's test suite exists to prove. Don't try to force a 5-failure RED here; three
is the real, reproducible count.

- [ ] **Step 3: Wire `register_fn` into `build_artifact` and remove `_STATUS_FOR_VERDICT`**

In `server/orchestrator.py`, find this block (the tail of `build_artifact`, currently
reading, with line numbers from the file as it stood after Task 7):

```python
    model_version_id = metadata_store.save_model_version(
        sha256=sha256,
        artifact_uri=artifact_uri,
        user_id=user_id,
        args=args,
        status="pending",
    )
    metadata_store.save_manifest(model_version_id=model_version_id, manifest=manifest)

    status = "pending"
    eval_record = None
    if fixture_payload is not None:
        eval_run = _evaluate(
            evaluate_fn=evaluate_fn,
            manifest=manifest,
            payload=payload,
            fixture_payload=fixture_payload,
            fixture_descriptor=fixture_descriptor,
            blob_store=blob_store,
        )
        eval_run_id = metadata_store.save_eval_run(
            model_version_id=model_version_id, eval_run=eval_run
        )
        status = _STATUS_FOR_VERDICT.get(eval_run["verdict"], "staging_failed")
        metadata_store.update_model_version_status(
            model_version_id=model_version_id, status=status
        )
        eval_record = {"id": eval_run_id, **eval_run}

    return {
        "model_version_id": model_version_id,
        "artifact_uri": artifact_uri,
        "status": status,
        "manifest": manifest,
        "eval_run": eval_record,
    }
```

Replace it with:

```python
    model_version_id = metadata_store.save_model_version(
        sha256=sha256,
        artifact_uri=artifact_uri,
        user_id=user_id,
        args=args,
        status="pending",
    )
    metadata_store.save_manifest(model_version_id=model_version_id, manifest=manifest)

    eval_record = None
    verdict = None
    eval_run_id = None
    if fixture_payload is not None:
        eval_run = _evaluate(
            evaluate_fn=evaluate_fn,
            manifest=manifest,
            payload=payload,
            fixture_payload=fixture_payload,
            fixture_descriptor=fixture_descriptor,
            blob_store=blob_store,
        )
        eval_run_id = metadata_store.save_eval_run(
            model_version_id=model_version_id, eval_run=eval_run
        )
        verdict = eval_run["verdict"]
        eval_record = {"id": eval_run_id, **eval_run}

    registration = register_fn(
        user_id=user_id,
        name=name,
        model_version_id=model_version_id,
        manifest=manifest,
        verdict=verdict,
        eval_run_id=eval_run_id,
        metadata_store=metadata_store,
    )

    return {
        "model_version_id": model_version_id,
        "artifact_uri": artifact_uri,
        "status": registration["status"],
        "manifest": manifest,
        "eval_run": eval_record,
        "model_id": registration["model_id"],
    }
```

Also remove the now-dead module-level comment and constant at the top of the file (lines
3–5 as the file stood before this task):

```python
# A verdict that isn't a clean pass leaves the version held, never promoted. Fury
# decides production; this only decides whether the version earned a look.
_STATUS_FOR_VERDICT = {"pass": "staging"}
```

Finally, add `_default_register` at the bottom of the file, next to `_default_find_existing`:

```python


def _default_register(**kwargs):
    from agents.brain3.fury.registry import register

    return register(**kwargs)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd server && uv run pytest tests/test_orchestrator.py -v
```
Expected: all 11 `PASS`.

- [ ] **Step 5: Commit**

```bash
git add server/orchestrator.py server/tests/test_orchestrator.py
git commit -m "Wire Fury.register into build_artifact; passing evals now promote to production"
```

---

### Task 9: `main.py` — `name` form field

**Files:**
- Modify: `server/main.py:32-53`
- Modify: `server/tests/test_main.py`

**Interfaces:**
- Consumes: nothing new from other tasks — this only threads a new required form field
  through to `build_artifact_fn`, whichever function that resolves to.
- Produces: `/ingest` now requires `name` in the multipart form data; requests without it
  get FastAPI's standard 422 for a missing required field.

- [ ] **Step 1: Write the failing tests**

In `server/tests/test_main.py`, update the three existing `fake_build_artifact` closures
to accept and forward `name` — replace the whole file:

```python
from fastapi.testclient import TestClient

from main import app, get_build_artifact


def test_ingest_parses_the_upload_and_returns_build_artifact_result():
    captured = {}

    def fake_build_artifact(payload, sha256, user_id, name, args, **kwargs):
        captured["call"] = (payload, sha256, user_id, name, args)
        captured["kwargs"] = kwargs
        return {"model_version_id": "mv_123", "status": "pending"}

    app.dependency_overrides[get_build_artifact] = lambda: fake_build_artifact
    client = TestClient(app)

    response = client.post(
        "/ingest",
        files={"artifact": ("artifact", b"fake-bytes", "application/octet-stream")},
        data={
            "user_id": "u_1",
            "sha256": "abc123",
            "name": "fraud-classifier",
            "args": '{"framework_hint": "sklearn"}',
        },
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"model_version_id": "mv_123", "status": "pending"}
    assert captured["call"] == (
        b"fake-bytes", "abc123", "u_1", "fraud-classifier", {"framework_hint": "sklearn"}
    )


def test_ingest_requires_a_name():
    app.dependency_overrides[get_build_artifact] = lambda: (lambda **_: {})
    client = TestClient(app)

    response = client.post(
        "/ingest",
        files={"artifact": ("artifact", b"fake-bytes", "application/octet-stream")},
        data={"user_id": "u_1", "sha256": "abc123"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 422


def test_ingest_without_a_fixture_asks_for_no_evaluation():
    captured = {}

    def fake_build_artifact(payload, sha256, user_id, name, args, **kwargs):
        captured.update(kwargs)
        return {"model_version_id": "mv_123", "status": "pending"}

    app.dependency_overrides[get_build_artifact] = lambda: fake_build_artifact
    client = TestClient(app)

    client.post(
        "/ingest",
        files={"artifact": ("artifact", b"fake-bytes", "application/octet-stream")},
        data={"user_id": "u_1", "sha256": "abc123", "name": "fraud-classifier"},
    )

    app.dependency_overrides.clear()

    assert captured["fixture_payload"] is None
    assert captured["fixture_descriptor"] is None


def test_ingest_forwards_an_uploaded_fixture_and_its_descriptor():
    captured = {}

    def fake_build_artifact(payload, sha256, user_id, name, args, **kwargs):
        captured.update(kwargs)
        return {"model_version_id": "mv_123", "status": "staging"}

    app.dependency_overrides[get_build_artifact] = lambda: fake_build_artifact
    client = TestClient(app)

    response = client.post(
        "/ingest",
        files={
            "artifact": ("artifact", b"fake-bytes", "application/octet-stream"),
            "fixture": ("fixture", b"fixture-bytes", "application/octet-stream"),
        },
        data={
            "user_id": "u_1",
            "sha256": "abc123",
            "name": "fraud-classifier",
            "fixture_descriptor": '{"kind": "labeled_holdout", "sha256": "def456"}',
        },
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert captured["fixture_payload"] == b"fixture-bytes"
    assert captured["fixture_descriptor"] == {
        "kind": "labeled_holdout",
        "sha256": "def456",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd server && uv run pytest tests/test_main.py -v
```
Expected: `test_ingest_requires_a_name` `FAILS` (currently returns 200, since `name` isn't
a field yet). The other three fail with a `TypeError` inside the fake, since `main.py`
isn't sending `name` as a positional/keyword argument yet — FastAPI will actually 422 on
these too today only if `name` were required server-side, which it isn't yet; expect the
assertion on `captured["call"]` to fail instead, or a `TypeError` if the fake's stricter
signature rejects the call. Either failure mode confirms the test exercises real behavior.

- [ ] **Step 3: Add the `name` form field**

In `server/main.py`, replace the `ingest` function (lines 32–53):

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
    build_artifact_fn: Callable = Depends(get_build_artifact),
):
    payload = await artifact.read()
    # No fixture means no eval: the version is stored and identified, and stays
    # `pending` until something gives Nat data to judge it against.
    fixture_payload = await fixture.read() if fixture is not None else None
    return build_artifact_fn(
        payload = payload,
        sha256 = sha256,
        user_id=user_id,
        name=name,
        args=json.loads(args),
        fixture_payload=fixture_payload,
        fixture_descriptor=json.loads(fixture_descriptor) if fixture_descriptor else None,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd server && uv run pytest tests/test_main.py -v
```
Expected: all 4 `PASS`.

- [ ] **Step 5: Run the full server suite to catch any other breakage**

```bash
cd server && uv run pytest -q
```
Expected: all pass. If `test_main_wiring.py` fails, it means it constructs a call into
`build_artifact` directly without `name` — re-check its content; as of the last read in
this plan's preparation it only asserts on `get_build_artifact()`'s wiring (which stores/
functions it builds), not on calling `build_artifact` itself, so it should be unaffected.

- [ ] **Step 6: Commit**

```bash
git add server/main.py server/tests/test_main.py
git commit -m "Require name on /ingest and thread it to build_artifact"
```

---

### Task 10: SDK — `name` threading through `client.py`, `transport.py`, `cli.py`

**Files:**
- Modify: `verity/src/verity/client.py`
- Modify: `verity/src/verity/transport.py`
- Modify: `verity/src/verity/cli.py`
- Modify: `verity/tests/test_client.py`
- Modify: `verity/tests/test_transport.py`
- Modify: `verity/tests/test_cli.py`

**Interfaces:**
- Produces: `verity.assemble(model, user_id, name, ...)` — `name` becomes a required
  positional-or-keyword parameter, matching decision 1 in the spec. `transport.upload(...)`
  gains a required `name` parameter and sends it as a form field. The CLI gains a required
  `--name` flag.

- [ ] **Step 1: Write the failing tests for `transport.upload`**

Replace the entire contents of `verity/tests/test_transport.py`:

```python
import httpx

from verity.transport import upload


def test_upload_posts_artifact_bytes_and_metadata_to_ingest_endpoint():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"model_version_id": "mv_123", "status": "pending"})

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = upload(
        payload=b"fake-artifact-bytes",
        sha256="abc123",
        user_id="u_1",
        name="fraud-classifier",
        args={"framework_hint": "sklearn"},
        endpoint="http://verity-server.test",
        client=client,
    )

    request = captured["request"]
    assert request.url == "http://verity-server.test/ingest"
    assert request.method == "POST"
    assert b"fake-artifact-bytes" in request.content
    assert b"abc123" in request.content
    assert b"u_1" in request.content
    assert b"fraud-classifier" in request.content
    assert result == {"model_version_id": "mv_123", "status": "pending"}


def test_upload_sends_the_fixture_as_a_second_file_with_its_descriptor():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"status": "staging"})

    client = httpx.Client(transport=httpx.MockTransport(handler))

    upload(
        payload=b"fake-artifact-bytes",
        sha256="abc123",
        user_id="u_1",
        name="fraud-classifier",
        args={},
        endpoint="http://verity-server.test",
        client=client,
        fixture_payload=b"fake-fixture-bytes",
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
    )

    content = captured["request"].content
    assert b"fake-fixture-bytes" in content
    assert b'name="fixture"' in content
    assert b"labeled_holdout" in content


def test_upload_omits_the_fixture_parts_entirely_when_there_is_no_fixture():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"status": "pending"})

    client = httpx.Client(transport=httpx.MockTransport(handler))

    upload(
        payload=b"fake-artifact-bytes",
        sha256="abc123",
        user_id="u_1",
        name="fraud-classifier",
        args={},
        endpoint="http://verity-server.test",
        client=client,
    )

    content = captured["request"].content
    assert b'name="fixture"' not in content
    assert b"fixture_descriptor" not in content
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd verity && uv run pytest tests/test_transport.py -v
```
Expected: `FAIL` — `TypeError: upload() got an unexpected keyword argument 'name'`.

- [ ] **Step 3: Add `name` to `transport.upload`**

Replace `verity/src/verity/transport.py`:

```python
import json
import httpx

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

) -> dict:
    client = client or httpx.Client(timeout=60.0)
    files = {"artifact": ("artifact", payload, "application/octet-stream")}
    data = {"user_id": user_id, "name": name, "sha256":sha256, "args": json.dumps(args)}

    # Sent only when there is something to evaluate — the server reads their absence
    # as "identify this version but don't judge it yet".
    if fixture_payload is not None:
        files["fixture"] = ("fixture", fixture_payload, "application/octet-stream")
        data["fixture_descriptor"] = json.dumps(fixture_descriptor)

    response = client.post(
        f"{endpoint}/ingest",
        files = files,
        data = data,

    )
    response.raise_for_status()
    return response.json()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd verity && uv run pytest tests/test_transport.py -v
```
Expected: all `PASS`.

- [ ] **Step 5: Commit**

```bash
git add verity/src/verity/transport.py verity/tests/test_transport.py
git commit -m "Require name in verity.transport.upload"
```

- [ ] **Step 6: Write the failing tests for `client.assemble`**

Replace the entire contents of `verity/tests/test_client.py`:

```python
import httpx

from verity.client import assemble


def test_assemble_serializes_the_model_and_uploads_it():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"model_version_id": "mv_123", "status": "pending"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    model = {"kind": "fake-model", "weights": [1, 2, 3]}

    result = assemble(
        model,
        user_id="u_1",
        name="fraud-classifier",
        endpoint="http://verity-server.test",
        client=client,
    )

    assert result == {"model_version_id": "mv_123", "status": "pending"}
    assert captured["request"].url == "http://verity-server.test/ingest"
    assert b"u_1" in captured["request"].content
    assert b"fraud-classifier" in captured["request"].content


def _mock_client(captured):
    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"status": "staging"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_holdout_passed_to_assemble_travels_with_the_model():
    captured = {}

    assemble(
        {"kind": "fake-model"},
        user_id="u_1",
        name="fraud-classifier",
        endpoint="http://verity-server.test",
        client=_mock_client(captured),
        X_test=[[0.0], [1.0]],
        y_test=[0, 1],
    )

    content = captured["request"].content
    assert b'name="fixture"' in content
    assert b"labeled_holdout" in content


def test_a_prebuilt_fixture_can_be_passed_directly_for_kinds_without_a_shortcut():
    captured = {}
    fixture = (b"prebuilt-bytes", {"kind": "labeled_holdout", "sha256": "def456"})

    assemble(
        {"kind": "fake-model"},
        user_id="u_1",
        name="fraud-classifier",
        endpoint="http://verity-server.test",
        client=_mock_client(captured),
        fixture=fixture,
    )

    assert b"prebuilt-bytes" in captured["request"].content


def test_supplying_only_half_of_a_holdout_is_rejected_before_anything_is_uploaded():
    captured = {}

    try:
        assemble(
            {"kind": "fake-model"},
            user_id="u_1",
            name="fraud-classifier",
            endpoint="http://verity-server.test",
            client=_mock_client(captured),
            X_test=[[0.0], [1.0]],
        )
    except ValueError as exc:
        assert "y_test" in str(exc)
    else:
        raise AssertionError("expected a ValueError when y_test is missing")

    assert "request" not in captured


def test_extra_keyword_arguments_are_still_forwarded_as_args():
    captured = {}

    assemble(
        {"kind": "fake-model"},
        user_id="u_1",
        name="fraud-classifier",
        endpoint="http://verity-server.test",
        client=_mock_client(captured),
        framework_hint="sklearn",
    )

    assert b"framework_hint" in captured["request"].content
```

- [ ] **Step 7: Run tests to verify they fail**

```bash
cd verity && uv run pytest tests/test_client.py -v
```
Expected: `FAIL` — `TypeError: assemble() got an unexpected keyword argument 'name'`.

- [ ] **Step 8: Add `name` to `client.assemble`**

Replace `verity/src/verity/client.py`:

```python
import httpx

from verity.fixture import labeled_holdout
from verity.serialize import serialize
from verity.transport import upload


def assemble(
    model,
    user_id: str,
    name: str,
    endpoint: str = "http://localhost:8000",
    client: httpx.Client | None = None,
    X_test=None,
    y_test=None,
    fixture: tuple | None = None,
    **args,
) -> dict:
    """Upload a trained model for identification, evaluation, and monitoring.

    `name` identifies this model across uploads — re-uploading under the same name
    registers a new version of the same model; a new name starts a new one.

    Pass X_test/y_test to have the model evaluated and gated in the same call. Pass
    `fixture` instead — a (payload, descriptor) pair from verity.fixture — for kinds
    that have no keyword shortcut. With neither, the model is identified and stored
    but left unevaluated.
    """
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
    )


def _resolve_fixture(X_test, y_test, fixture):
    if fixture is not None:
        return fixture
    if X_test is None and y_test is None:
        return None, None
    # Rejected here rather than server-side: a half-specified holdout is a mistake in
    # the caller's script, and finding out after the upload helps nobody.
    if X_test is None or y_test is None:
        raise ValueError("X_test and y_test must be given together, or neither")
    return labeled_holdout(X_test, y_test)
```

- [ ] **Step 9: Run tests to verify they pass**

```bash
cd verity && uv run pytest tests/test_client.py -v
```
Expected: all `PASS`.

- [ ] **Step 10: Commit**

```bash
git add verity/src/verity/client.py verity/tests/test_client.py
git commit -m "Require name in verity.assemble"
```

- [ ] **Step 11: Write the failing tests for the CLI**

In `verity/tests/test_cli.py`, every `fake_assemble` closure needs a `name` parameter, and
every `main([...])` call needs `--name <value>` added to its argv list. Replace the whole
file:

```python
from verity.cli import main


def test_demo_flag_trains_a_model_and_calls_assemble_with_it():
    calls = []

    def fake_assemble(model, user_id, name, endpoint, **kwargs):
        calls.append({"model": model, "user_id": user_id, "name": name, "endpoint": endpoint, **kwargs})
        return {"model_version_id": "mv_test", "status": "pending"}

    main(
        ["--demo", "--user-id", "cli-test-user", "--name", "demo-model", "--endpoint", "http://example.test"],
        assemble_fn=fake_assemble,
    )

    assert len(calls) == 1
    assert calls[0]["user_id"] == "cli-test-user"
    assert calls[0]["name"] == "demo-model"
    assert calls[0]["endpoint"] == "http://example.test"
    assert hasattr(calls[0]["model"], "predict")


def test_the_demo_ships_a_holdout_so_it_exercises_the_whole_loop():
    calls = []

    def fake_assemble(model, user_id, name, endpoint, **kwargs):
        calls.append(kwargs)
        return {"status": "staging"}

    main(["--demo", "--user-id", "cli-test-user", "--name", "demo-model"], assemble_fn=fake_assemble)

    assert len(calls[0]["X_test"]) == len(calls[0]["y_test"])
    assert len(calls[0]["y_test"]) > 0


def test_model_path_loads_a_cloudpickled_file_and_calls_assemble_with_it(tmp_path):
    import cloudpickle

    model_path = tmp_path / "model.pkl"
    model_path.write_bytes(cloudpickle.dumps({"kind": "fake-model"}))

    calls = []

    def fake_assemble(model, user_id, name, endpoint, **kwargs):
        calls.append(model)
        return {"model_version_id": "mv_test", "status": "pending"}

    main(
        [str(model_path), "--user-id", "cli-test-user", "--name", "my-model"],
        assemble_fn=fake_assemble,
    )

    assert calls == [{"kind": "fake-model"}]


def test_a_test_set_file_is_loaded_and_passed_as_the_holdout(tmp_path):
    import cloudpickle

    model_path = tmp_path / "model.pkl"
    model_path.write_bytes(cloudpickle.dumps({"kind": "fake-model"}))
    test_set_path = tmp_path / "holdout.pkl"
    test_set_path.write_bytes(cloudpickle.dumps(([[0.0], [1.0]], [0, 1])))

    calls = []

    def fake_assemble(model, user_id, name, endpoint, **kwargs):
        calls.append(kwargs)
        return {"status": "staging"}

    main(
        [str(model_path), "--user-id", "u_1", "--name", "my-model", "--test-set", str(test_set_path)],
        assemble_fn=fake_assemble,
    )

    assert calls[0]["X_test"] == [[0.0], [1.0]]
    assert calls[0]["y_test"] == [0, 1]


def test_a_model_without_a_test_set_is_uploaded_with_no_holdout(tmp_path):
    import cloudpickle

    model_path = tmp_path / "model.pkl"
    model_path.write_bytes(cloudpickle.dumps({"kind": "fake-model"}))

    calls = []

    def fake_assemble(model, user_id, name, endpoint, **kwargs):
        calls.append(kwargs)
        return {"status": "pending"}

    main([str(model_path), "--user-id", "u_1", "--name", "my-model"], assemble_fn=fake_assemble)

    assert calls[0]["X_test"] is None
    assert calls[0]["y_test"] is None


def test_name_is_required():
    import pytest

    with pytest.raises(SystemExit):
        main(["--demo", "--user-id", "cli-test-user"], assemble_fn=lambda **_: {})
```

- [ ] **Step 12: Run tests to verify they fail**

```bash
cd verity && uv run pytest tests/test_cli.py -v
```
Expected: `FAIL` — most tests error with `TypeError: main() got an unexpected keyword
argument` style failures from the fake, or `SystemExit` from argparse rejecting the
unrecognized `--name` flag; `test_name_is_required` fails because `--name` isn't required
yet, so `main` doesn't raise.

- [ ] **Step 13: Add `--name` to the CLI**

Replace `verity/src/verity/cli.py`:

```python
import argparse

import cloudpickle

from verity.client import assemble


def main(argv=None, assemble_fn=assemble):
    parser = argparse.ArgumentParser(prog="verity")
    parser.add_argument("model_path", nargs="?", help="path to a cloudpickled model file")
    parser.add_argument("--demo", action="store_true", help="use a tiny built-in demo model instead of a file")
    parser.add_argument("--test-set", help="path to a cloudpickled (X_test, y_test) tuple to evaluate against")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--name", required=True, help="identifies this model across versions")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)

    X_test = y_test = None

    if args.demo:
        from sklearn.linear_model import LogisticRegression

        X, y = [[0], [1], [2], [3]], [0, 0, 1, 1]
        model = LogisticRegression().fit(X, y)
        # The demo carries its own holdout so `--demo` walks the full loop —
        # identify, evaluate, gate — rather than stopping at identification.
        X_test, y_test = X, y
    elif args.model_path:
        with open(args.model_path, "rb") as f:
            model = cloudpickle.load(f)
    else:
        parser.error("either a model_path or --demo is required")

    if args.test_set:
        with open(args.test_set, "rb") as f:
            X_test, y_test = cloudpickle.load(f)

    result = assemble_fn(
        model,
        user_id=args.user_id,
        name=args.name,
        endpoint=args.endpoint,
        X_test=X_test,
        y_test=y_test,
    )
    print(result)


if __name__ == "__main__":
    main()
```

- [ ] **Step 14: Run tests to verify they pass**

```bash
cd verity && uv run pytest tests/test_cli.py -v
```
Expected: all 6 `PASS`.

- [ ] **Step 15: Run the full SDK suite**

```bash
cd verity && uv run pytest -q --ignore=tests/test_auth.py
```
Expected: all pass. (`test_auth.py` is pre-existing broken/unrelated — imports
`verity.auth`, which doesn't exist. Excluded, same as every prior session.)

- [ ] **Step 16: Commit**

```bash
git add verity/src/verity/cli.py verity/tests/test_cli.py
git commit -m "Require --name on the verity CLI"
```

---

### Task 11: Full verification — migrations, live end-to-end, `progression.md`

**Files:**
- No new source files.
- Modify: `progression.md`

**Interfaces:** None — this task only verifies everything from Tasks 1–10 together.

- [ ] **Step 1: Run the full server test suite**

```bash
cd server && uv run pytest -q
```
Expected: all pass — 94 tests total. Baseline before this plan was 67. Per-file deltas:
`test_supabase.py` 4 → 14 (+10, Tasks 2–3), `test_orchestrator.py` 7 → 11 (+4, Tasks 7–8:
−3 dropped / +2 added in Task 7, +5 added in Task 8), `test_main.py` 3 → 4 (+1, Task 9),
`test_fury_registry.py` 0 → 12 (+12, a new file, Tasks 4–6). Total delta +27.

- [ ] **Step 2: Run the full SDK test suite**

```bash
cd verity && uv run pytest -q --ignore=tests/test_auth.py
```
Expected: all pass.

- [ ] **Step 3: Apply the migrations to the real Supabase project**

```bash
cd server && uv run alembic upgrade head
```
Expected: both new revisions (`c8e51f4d9a06`, `d4a729c6e153`) apply cleanly. Confirm in
the Supabase table editor (or via `uv run python -c "..."` selecting from
`information_schema.columns`) that `model` exists and `model_version` now has `model_id`
and `promoted_from`.

- [ ] **Step 4: Live end-to-end smoke test — first version of a new model, passing eval**

Run the server:
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

result = assemble(
    model, user_id='fury-e2e', name='fury-e2e-classifier',
    X_test=[[0.5], [9.5]], y_test=[0, 1],
)
print(result)
assert result['status'] == 'production', result['status']
assert result.get('model_id', '').startswith('mdl_')
print('OK: first version promoted straight to production')
"
```
Expected: `status: "production"`, and the printed dict includes a `model_id` starting
`mdl_`.

- [ ] **Step 5: Live end-to-end smoke test — second version replaces the first**

```bash
cd verity && uv run python -c "
from sklearn.linear_model import LogisticRegression
from verity.client import assemble

X = [[0.0], [1.0], [2.0], [8.0], [9.0], [10.0]]
y = [0, 0, 0, 1, 1, 1]
model = LogisticRegression(C=0.3).fit(X, y)  # different hyperparameter -> different bytes

result = assemble(
    model, user_id='fury-e2e', name='fury-e2e-classifier',
    X_test=[[0.5], [9.5]], y_test=[0, 1],
)
print(result)
assert result['status'] == 'production'
print('OK: second version also promoted; check Supabase that the first is now archived')
"
```
Then confirm directly against Supabase that exactly one `model_version` row for this
`model_id` has `status = 'production'` and the first one now has `status = 'archived'`.

- [ ] **Step 6: Live end-to-end smoke test — exact repeat is a no-op**

Re-run the **exact same script from Step 5 unchanged** (same `C=0.3` model, same name).
Expected: `result["deduplicated"] is True` (add a `print(result.get("deduplicated"))` to
confirm), and no new row appears in `model_version` for this hash/name pair in Supabase.

- [ ] **Step 7: Update `progression.md`**

Append a new numbered entry describing what's now automated: Fury groups uploads by name,
dedupes exact repeats, and auto-promotes a passing eval straight to `production`,
archiving whatever it replaces — closing the gap `progression.md` entry 3 left open
("Still not automated past here: Fury, promotion to production, `promoted_from`").

- [ ] **Step 8: Commit**

```bash
git add progression.md
git commit -m "Record Fury (model registry) as implemented in progression.md"
```

---

## Explicitly out of scope (unchanged from the spec)

api-fication, Falcon, the `agent_run` audit trail, comparative promotion gating,
org-scoped uniqueness, and masked/proxied artifact URLs. None of these are touched by any
task above.
