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