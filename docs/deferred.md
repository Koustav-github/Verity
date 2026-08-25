# Deferred

Real, found-but-not-fixed issues, kept in one place so they don't get lost between
sessions. Each entry: what's wrong, why it wasn't fixed on the spot, and what it costs to
leave as-is. Move an entry out (delete it here, note it in `progression.md`) once it's
actually fixed.

---

## 1. `score()` lets one unmeasurable metric kill the whole computation

**Where:** `server/agents/brain2/nat/score.py` — the `score()` function's per-metric loop.

**What's wrong:** `score()` iterates `metric_set` and computes each one with
`scores[metric] = float(fn(outputs))`, with no per-metric error handling. If any single
metric's underlying sklearn function raises — the concrete case found: `roc_auc_score`
raises `ValueError` when `y_true` has only one class present — the exception propagates
out of `score()` entirely, discarding every metric in the set, not just the one that
couldn't be computed.

**Why it wasn't fixed on the spot:** found by accident while manually generating test
data to verify Falcon's frontend alerts panel (`alert_email`/`deployment` UI work,
2026-08-23) — reporting 32 delayed outcomes that all happened to land on the same class
made `detect_quality_anomaly` (which calls `score()`) raise, caught by `check_quality`'s
outer `try/except`, silently discarding that round's check. Real, but adjacent to the
task at hand, not part of it — fixing it properly means deciding a policy (skip the
metric that can't be computed and keep the rest, matching the existing "unrecognized
metric name is skipped, not fatal" rule right above this exact loop) and adding tests for
degenerate label distributions, which is its own small piece of work.

**Why this is newly reachable, not just a pre-existing footgun:** `score()` was written
for Nat's original eval path, where the fixture is something the *developer* constructed
(a labeled holdout they built) — a single-class holdout is an unusual mistake to make.
Falcon's quality check (`server/agents/brain4/falcon/monitor.py::check_quality`) calls the same
`score()` against **customer-reported delayed labels** accumulated over real production
traffic — far less controlled, and a run of same-answer labels (a quiet period, a
one-sided bug report, or literally the pathological case a bad-actor might construct
deliberately, see `docs/architecture.md` §8.10 on label injection) is a realistic way to
hit this for real.

**Cost of leaving it:** whenever reported labels for a version happen to collapse to one
class, Falcon's quality check silently produces *no* alert for that round — not because
nothing was wrong, but because the check itself failed at the `roc_auc` metric and never
got to evaluate `accuracy`, `f1`, or anything else in the set. `detection_errors`
(`server/agents/brain4/falcon/monitor.py`) does increment when this happens, so it's not
*invisible* — but nobody is currently watching that counter, so in practice it's
indistinguishable from "checked, found nothing wrong" to anyone not specifically looking.

**Shape of the eventual fix:** wrap each metric's computation individually inside
`score()`'s loop — on a raised exception, record it in `skipped` (the same list unknown-
metric-name and missing-`y_proba` cases already use, e.g. `{"metric": metric, "reason":
"raised: ValueError"}`) instead of letting it propagate, and continue scoring the rest of
`metric_set`. `apply_thresholds()` already treats a skipped metric as a failed threshold
by its own existing rule — so a genuinely-unmeasurable metric would still correctly show
up as a threshold failure, it just wouldn't take every other metric down with it.

---

## 2. `/models/{id}` addresses two different entity types depending on the route

**Where:** `server/main.py` — `GET /models/{model_version_id}/telemetry`, `GET
/models/{model_version_id}/alerts` (pre-existing, from Falcon) take a **version** id;
`GET /models/{model_id}/versions` (added by the model registry dashboard work) takes a
**model** id. All three share the `/models/{id}/...` prefix.

**What's wrong:** the two entity types are addressed identically at the URL level,
disambiguated only by the differing suffix (`/telemetry`, `/alerts` vs. `/versions`).
Not a live bug — nothing routes incorrectly — but a new contributor reading the route
table cold would have no way to tell `{model_version_id}` from `{model_id}` apart without
reading each handler's body.

**Why it wasn't fixed now:** the newer, better-named convention (`/model_versions/{id}`
for version-scoped resources) was used for every route added by this work. Renaming the
two older routes to match would be a breaking change to the frontend's existing
`telemetry-panel.tsx`/`traces-panel.tsx`/`alerts-panel.tsx` fetch calls, out of scope for
a read-only registry-browsing feature.

**Cost of leaving it:** purely a readability/discoverability cost for future contributors
reading `main.py`'s route table; no functional risk.

**Shape of the eventual fix:** rename the two older routes to
`/model_versions/{model_version_id}/telemetry` and
`/model_versions/{model_version_id}/alerts`, updating the three frontend fetch calls that
target them. Natural to bundle with V1.5's auth work, since that work already touches
every route's request shape.
