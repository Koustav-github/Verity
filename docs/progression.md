the record is being maintained after the reboot

1. verity sdk is working, serializes models and push it to the ingestion pipeline on server. The ingestion pipeline hands over the model to orchestrator to build artifacts. Nothing automated
2. Serializes and uploads model at staging and gets artifacts and stores model withOUT any extension. straight what is returned from the llm returns. and the orchestration is set up till this
3. Nat (agent 2, evaluation) is in. The Hawkeye -> Nat handoff is automatic now: one
   `/ingest` call identifies the model AND evaluates it, and `model_version.status`
   moves off `pending` on its own -> `staging` on a pass, `staging_failed` otherwise.
   Ships with the SDK, so `assemble(model, X_test=..., y_test=...)` is the whole loop.
   - The eval mechanism is chosen from the fixture's `kind`, not hardcoded. Only
     `labeled_holdout` is registered; images/RAG/rollout are a module + a registry line
     later, not a rewrite (agents/brain2/nat/registry.py).
   - `predict()` runs in a subprocess with every credential withheld
     (server/execution/sandbox.py). There is a test that proves the child cannot read
     SUPABASE_KEY.
   - Systemic metrics (latency percentiles, throughput, memory, CPU, GPU) are measured
     by that harness on every run and gate exactly like quality metrics. Verified: a
     model scoring accuracy 1.0 still fails on a latency threshold.
   - Caveat, deliberately: those numbers are single-process, single-client, cold
     sandbox. Feasibility, not production p99. Real load is Falcon's.

4. shifted the workflow to aws service. There were several challenges with seaweedfs, first i had to get a virtual machine, for hosting seaweedfs distributed storage or else there was no possible way. got the free aws account with $100 free credits, so spun up a s3 bucket, we could have also done a ec2 instance, but utilizing better infra since available.

5. Fury (agent 3, registry) is in. The Hawkeye -> Nat -> Fury handoff is automatic now —
   one `/ingest` call identifies, evaluates, AND registers. This changes what entry 3 said:
   a passing verdict no longer stops at `staging` — it goes straight to `production`, since
   Fury is what actually decides promotion now, not the eval step itself. `staging` is
   effectively retired as a resting state; `staging_failed` and `pending` are unaffected.
   - `name` is now required on every upload (`assemble(model, name=..., ...)`), and it's
     the only thing that decides "is this a new model or a new version of one I already
     know." Uploads grouped under the same name share one `model` row and one lineage.
   - Exact byte-for-byte re-uploads under the same name are a true no-op — checked before
     anything else runs, so a repeat costs nothing (no Hawkeye call, no Nat call, no
     sandbox execution). Verified live: re-uploading an identical model returned the
     existing record with `deduplicated: true`, no new row.
   - Promotion is fully automatic on a passing verdict, with no comparison against the
     incumbent — a new version that clears its own thresholds replaces whatever's
     currently `production`, and the one it replaces moves to `archived`. Both keep their
     own `promoted_from` pointer to the eval_run that justified them, so the archived
     version's evidence isn't lost. Verified live end-to-end against real S3/Supabase/Groq:
     first version of a new model -> production; a second version -> production, first ->
     archived; the same second version re-uploaded -> deduped, no new row.
   - Accepted, undecided risk, not solved here: a version that barely clears its own bar
     can replace an incumbent that was scoring far better, since nothing compares them.
     Mitigated only by visibility (both eval_runs stay on record), not prevented.
   - Fixed along the way, not code: a real repo-hygiene bug where `verity/tests` was
     gitignored, so every prior commit touching SDK tests had silently no-op'd across this
     whole project — the suite existed on disk but was never actually in git history until
     this was caught and fixed.
   Still not automated past here: api-fication (nothing actually serves a `production`
   model yet), Falcon, the `agent_run` audit trail, comparative promotion gating.

6. `client/` is no longer an untouched scaffold — added an MVP intake form (single page,
   `client/src/app/page.tsx`) that calls `/ingest` directly from the browser and renders
   the real response: manifest, per-threshold pass/fail, a verdict stamp. Ships a bundled
   demo model+fixture (`client/public/demo/`) so the whole Hawkeye->Nat->Fury loop is one
   click, no Python needed to try it.
   - The server had no CORS config at all — added it, scoped to localhost:3000, since a
     browser can't call a cross-origin API without it.
   - Client-side SHA-256 (Web Crypto) computes the same digest the server now verifies
     (the fix-wave's finding #3) — the frontend can't get away with a stale/wrong hash
     any more than the CLI can.
   - Fixed a second gitignore-too-broad bug, same category as `verity/tests`: the root
     `.gitignore`'s bare `demo/` pattern was also silently matching `client/public/demo/`
     — scoped it to `/demo/` (root only).
   - Verified against the real backend, not just a build check: a Node script replicating
     the exact browser flow confirmed all three response shapes the UI renders — pass to
     production, an archived-replacement, and a deduplicated repeat.
   Still frontend-only for identify+evaluate+register; there's no view of a model once
   registered (no GET endpoint exists server-side yet), so this only ever shows the result
   of the upload just made, not a history/dashboard.

7. Falcon (agent 4, observability) is in — the V1 loop's four agents are all built. A
   promotion to `production` now switches monitoring on by itself, and the SDK reports live
   traffic back from wherever the customer serves the model.
   - Falcon needs no LLM. Everything it does is deterministic: when Fury promotes a version,
     `promoted_from` already points at the eval_run that justified it, and that row already
     holds measured latency percentiles, throughput, memory and quality scores. The baseline
     is lifted from evidence that already exists rather than guessed.
   - **The honesty constraint that shaped the whole design:** those eval numbers come from a
     single-process, single-client, cold sandbox. Production latency under real concurrency
     will be materially higher. So the config records them tagged `"basis":
     "sandbox_feasibility"` for context and for V7's rule engine — and V1 compares nothing
     and alerts on nothing. A baseline that's wrong by construction would just train people
     to ignore it. New `monitoring_config` table (Schemas.md never specced one, only the
     README diagram named it).
   - `verity.monitor(model, model_version_id=...)` wraps the customer's model; `.predict()`
     behaves identically and telemetry ships from a background thread. The governing rule is
     that telemetry can never be why inference fails or slows: enqueue is non-blocking and
     drops on a full queue, the HTTP call never touches the predict path, and if the model
     raises, the caller gets that exact exception back — recorded, then re-raised unchanged.
   - `GET /models/{id}/telemetry` and a panel in the intake UI make it visible, so this isn't
     write-only. `inputs`/`prediction` columns exist but stay null at V1 — they're for V7
     drift detection, and leaving them empty removes the whole sampling question.
   - Found and fixed during review, not shipped: a background security review caught that
     `status` was an unconstrained string (anything != "ok" counts as an error, so a client
     sending "OK" would silently inflate the error rate) and that the batch size was
     unbounded; and the SDK reporter leaked a thread + atexit reference per instance, could
     stall shutdown for minutes against a dead endpoint, and had a racy drop counter.
   - Declined deliberately: `/telemetry` has no auth. Real, but systemic — the whole app has
     none by design until V1.5, and `/ingest` is equally open while accepting arbitrary
     pickles. Securing one endpoint while that stands would be theatre.
   Still not automated past here: api-fication (nothing serves a `production` model yet —
   Verity will, once built; until then the customer serves it and the SDK reports back),
   alerting and drift (V7), the `agent_run` audit trail, comparative promotion gating.
8. Housekeeping, one real outage fix, and the api-fication decision. Nothing new shipped in the
   pipeline — this entry exists so the next reader isn't confused by three things that changed
   underneath them.
   - **Groq retired the entire Llama line.** `llama-3.3-70b-versatile` started returning 404
     `model_not_found`, which broke both LLM agents at once. `GET /openai/v1/models` on the live
     key showed what was actually reachable; `agents/provider.py` now defaults to
     `openai/gpt-oss-120b`. Two tests had pinned the old model as a bare string literal and
     failed for the wrong reason — they now assert against `DEFAULT_MODEL` imported from
     `agents.provider`, so they pin *"the provider default is used"* rather than which model
     that happens to be this month. The bug was in the test, not the code: pinning a vendor's
     model name is pinning something you don't control.
   - **The repo still contained an entirely different, abandoned project.** The root
     `pyproject.toml` declared `verity-eval` ("local-first RAG evaluation") with torch,
     transformers, sentence-transformers and pinecone as dependencies, plus a matching
     `requirements.txt`, `uv.lock`, a 1.1 GB `.venv`, and a `verity_eval.egg-info/`. Its
     `[tool.setuptools.packages.find] include = ["verity*"]` claimed the SDK's package name from
     the wrong directory. All deleted. The real packages are `verity/pyproject.toml` (SDK) and
     `server/pyproject.toml` (server). `Architechture.md` and `agent.md` went with it —
     pre-reboot documents whose content the README now covers, and whose content had started to
     actively contradict the current design.
   - **Docs moved into `docs/`**, leaving only `README.md` at the root: `architecture.md` (was
     `verity-architecture-and-workflow.md`), `Schemas.md`, `Metrics.md`, `progression.md`, and
     `reference/mlflow.md`. `Schemas.md` and `Metrics.md` deliberately kept their filenames —
     roughly fifteen code comments and both committed design specs refer to them by bare name in
     prose, and moving a file leaves that prose true where renaming it would make fifteen
     comments lie.
   - **Two documentation lies found while doing it, both worth more than the tidying.**
     `Schemas.md` documented six `manifest` columns — `io_schema`, `serving_pattern`, `platform`,
     `confidence`, `review_required`, `declared_overrides` — that no migration ever created, and
     omitted four that exist. The table now marks which is which. And `architecture.md` had no
     section on Falcon at all: it was written before Falcon existed and still claimed three
     agents, one route, and 102/21 tests. Both are the same failure mode — a document that
     described intent and was never re-read against reality.
   - **api-fication is now designed rather than open.** Settled: one container image per promoted
     version, built from the training environment the SDK captures at `assemble()` time (so a
     pickle is never loaded against a different sklearn than it was written with); request schema
     generated from the model's own introspected surface; deploy fires automatically on
     promotion, non-fatal like Falcon; Verity proxies `/models/{name}/predict` to the live
     container. Local Docker first, behind a runtime interface so a cloud runner is a new class
     rather than a rewrite. Scope is narrowed on purpose to tabular/classical ML — sklearn,
     XGBoost, LightGBM — finished end to end before a second model class starts.
   - Correction on the record, because an earlier entry implied otherwise: the plan was for some
     time that Verity would *never* serve inference, and that the customer would always host the
     model with the SDK reporting back. That was my inference from an unanswered question, not a
     decision anyone made, and it had already been written into the Falcon spec as settled fact.
     Verity serves; self-hosting plus SDK telemetry stays as the secondary path.
