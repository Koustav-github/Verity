# Fury — Model Registry — Design

## Context

`progression.md` entries 3–4 record where the loop currently stands: Hawkeye identifies a
model, Nat evaluates it and gates `model_version.status` to `staging` or `staging_failed`,
and artifacts persist to AWS S3. Nothing after that is automated — a passing eval doesn't
lead anywhere. `model_version.promoted_from` exists in the schema and has never been set.

This spec covers **Fury**, the registry agent — the third stage in the four-agent loop
(`artifact → Hawkeye → manifest → Nat → eval_report → Fury → registered version`, per
[README.md](../../../README.md)). Its job, per the vision doc: *"records the version, its
lineage, its eval scores, and the exact artifact that produced them, then gates promotion
on whether the scores clear their thresholds... Promotion is a consequence of evidence,
not a button someone remembers to press."*

Concretely, Fury answers two questions nothing in the pipeline currently answers:
**"is this a new model, or a new version of one I already know?"** and **"does a passing
eval mean this version should actually be the one serving traffic?"**

Out of scope for this spec, deliberately: api-fication (the serving layer — what actually
lets a `production` version be called), Falcon (observability), and the `agent_run` audit
trail (a cross-cutting gap across all four agents, not specific to Fury — worth its own
pass rather than being smuggled into this one).

---

## Decisions

Five load-bearing questions were resolved before writing this spec. Recording the *why*
here, not just the *what*, since these are the parts most likely to look arbitrary later
without the reasoning attached.

**1. Model identity is an explicit `name`, not inferred.**
The SDK now requires `name` at `assemble()` time. `Schemas.md`'s `model.name` was already
specced as "unique per org," implying an explicit key was always the intent — this just
makes it real. Inferring identity from `(user_id, manifest.model_class)` was rejected:
two unrelated models of the same sklearn class from the same user would wrongly collide
into one lineage. This is the standard pattern (MLflow, SageMaker both require an explicit
registered name) — no reason to invent something more clever.

**2. Promotion is fully automatic — no comparison against the incumbent.**
A passing verdict from Nat flows straight through to `production`. This matches the
README's own stated philosophy directly, quoted above. The alternative — requiring a new
version to *beat* the current production version's scores, not just clear its own
thresholds — is a real feature (a "champion/challenger" gate) but is explicitly deferred:
V1 is scoped as "the thinnest complete pass through all four agents," and comparative
gating is scope creep against that. The risk this accepts is real and named, not hidden:
a new version that barely clears its own bar can replace an incumbent that was scoring far
better. Mitigated only by visibility — see Error Handling / audit note below — not blocked.

**3. The previous `production` version becomes `archived`.**
One `production` slot per model, always unambiguous — this matters because api-fication
(next in the roadmap) needs a single, non-negotiable answer to "which artifact do I serve."
`archived` (not `Schemas.md`'s `retired`) is used because that's the term MLflow itself
uses for this exact state, and it's the vocabulary already in use in this conversation.
This is a deliberate deviation from `Schemas.md`'s current wording, recorded here rather
than silently diverging — the concept is identical, only the label differs.

**4. Same name, different bytes → full pipeline, no bypass.**
A re-upload under an existing name is a genuinely new artifact and goes through Hawkeye
and Nat like any other upload. It only takes over `production` if it passes. The one thing
that *is* skipped is re-registration bookkeeping for a version that already exists byte-
for-byte — see decision 5. Early in this discussion "same name" and "same bytes" were
briefly conflated; they are handled differently on purpose, and that distinction is the
core of what makes decisions 2 and 4 consistent with each other rather than contradictory.

**5. Identical bytes → true no-op — but only under the same name.**
Checked by `(user_id, sha256, name)`, not `(user_id, sha256)` alone, before anything else
runs — before the S3 write, before Hawkeye, before Nat. A full match returns the existing
record untouched. This is pure cost avoidance: two LLM calls and a sandboxed `predict()`
run, avoided on a hash match. `name` is deliberately part of the key: identical bytes
uploaded under a *different* name are a legitimate new registration, not an accidental
duplicate — a developer re-registering the same weights under a new lineage should get a
new `model` row, not silently be handed back the old one under the wrong name. The cost is
a redundant Hawkeye/Nat run in that specific case; the alternative (silently ignoring the
name the caller explicitly asked for) is worse.

---

## Schema changes

### New table: `model`

The logical model, grouping versions across uploads — this table does not exist today;
only `model_version` and `manifest` do.

| Column | Type | Notes |
|---|---|---|
| `id` | text PK | `mdl_...` |
| `name` | text | required at upload |
| `model_class` | text | copied from the *first* version's manifest at creation; not re-synced on later versions |
| `task_type` | text | same — set once, at creation |
| `user_id` | text | tenancy key; `Schemas.md` specs `org_id`, but no `organization` table exists yet (V1.5). `user_id` matches what `model_version` already uses today |
| `created_at` | timestamptz | |

`UNIQUE (user_id, name)` — two different users can both name something `"fraud-classifier"`
without colliding. Org-scoping later is a matter of swapping the uniqueness key, not
reshaping the table.

### `model_version` — one new column

`model_id` (FK → `model.id`, nullable — existing rows predate this table and have no
value to backfill). `promoted_from` (FK → `eval_run`) already exists and starts being set
for the first time by this work.

### Status values

`archived` is a new value in `model_version.status`. The column is untyped `text` with no
DB-level enum constraint (confirmed against the existing migrations), so this is additive
and carries no migration risk to rows already in `pending` / `staging` / `staging_failed`.

---

## Flow

Two distinct operations, happening at two different points in the pipeline. They end up
implemented as two entry points into the same module, but they are conceptually separate
and it matters to keep them that way:

**1. Dedup — first thing, before any other work.**
`orchestrator.py` checks `(user_id, sha256, name)` before the S3 write, before Hawkeye,
before Nat. A full match short-circuits the entire pipeline and returns the existing
record. Nothing downstream re-runs — not even Fury's own registration logic, since nothing
about the artifact or its intended identity changed. A hash match under a *different* name
is not a dedup hit — see decision 5.

**2. Identity + promotion — after Nat's eval, or after Hawkeye if no fixture was supplied.**
- Find-or-create the `model` row for `(user_id, name)`.
- Link this `model_version.model_id` to it. **This happens regardless of eval outcome** —
  a `pending` or `staging_failed` version is still part of that model's version history,
  and needs to be findable as such.
- *Only if the verdict is `pass`*: find whatever currently holds `production` for this
  `model_id`, set it to `archived`, set the new version to `production`, and set
  `promoted_from = eval_run_id`.

`name` becomes a required parameter on `verity.assemble(...)` from this point on. This is
a breaking SDK change, but it's the direct, unavoidable consequence of decision 1, not a
separate choice being smuggled in here.

---

## Components

Mirrors the existing Hawkeye/Nat pattern exactly — no new architectural shape introduced:

- **`agents/brain3/fury/registry.py`** — pure functions, every collaborator injected, no
  module-level state. Two entry points:
  - `find_existing(*, user_id, sha256, name, metadata_store)` — the dedup check
  - `register(*, user_id, name, model_version_id, manifest, verdict, eval_run_id, metadata_store)`
    — identity linking, and promotion when `verdict == "pass"`

- **`SupabaseMetadataStore`** gains roughly five small, single-purpose methods, each a thin
  wrapper over one `.table().op().execute()` call, matching every existing method on that
  class:
  - lookup by `(user_id, sha256, name)`
  - find-or-create `model` by `(user_id, name)`
  - find the current `production` version for a `model_id`
  - promote (`status → production`, set `promoted_from`)
  - archive (`status → archived`)

No new agent-level abstraction, no new orchestration pattern — `orchestrator.py` gains two
more calls into a third agent module, the same way it already calls into Hawkeye and Nat.

---

## Error handling

Fury does not need Nat's "never raises, records `verdict = error`" safety net. Nat's
eval carries genuine model-dependent uncertainty (a sandbox can crash, an LLM call can
fail); Fury's job is deterministic bookkeeping — hash lookups and status writes. If a
write fails, it raises normally and surfaces as a 500, consistent with how blob-store
failures already behave today. No new error-handling pattern is introduced.

The one accepted risk from decision 2 — an under-qualified version silently replacing a
strong incumbent — is mitigated by visibility, not prevention: the archived version's id
and its own scores are recorded alongside the new promotion, so a bad auto-promotion is
inspectable in the data even though nothing blocks it from happening. Building an actual
block (comparative gating) is explicitly deferred, not solved here.

---

## Testing

Same convention as the rest of the codebase: hand-written fakes, no `unittest.mock`,
full-sentence test names, TDD throughout. Fury is notably easier to test in isolation than
Hawkeye or Nat — it only ever consumes their *outputs* (a manifest, a verdict), never calls
either agent directly, so its tests need no LLM or sandbox fakes at all, only a fake
metadata store.

---

## Explicitly out of scope

- **api-fication** — Fury decides *which* version is authorized to serve; it does not
  stand up anything that serves it. That's the next roadmap step, not this one.
- **`agent_run` audit trail** — a gap across all four agents today, not Fury-specific.
  Worth its own pass across the whole pipeline rather than being added piecemeal here.
- **Comparative ("champion/challenger") promotion gating** — named as an accepted risk in
  decision 2, not built. A real feature, deferred past V1's "thinnest complete loop" scope.
- **Org-scoped uniqueness** — `(user_id, name)` today; becomes `(org_id, name)` at V1.5
  without reshaping the table, once `organization` exists.
- **Masked/proxied artifact URLs** — agreed as a good idea in an earlier discussion, but
  it's a read-path concern belonging to api-fication, not registry.
