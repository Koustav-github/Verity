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
   Still not automated past here: Fury, promotion to production, `promoted_from`.