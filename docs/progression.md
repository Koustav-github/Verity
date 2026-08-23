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

9. api-fication is in — Verity serves the models it promotes. A version that reaches
   `production` is now built into its own container image, started, health-checked, and put
   behind `POST /users/{user_id}/models/{name}/predict`. This is the first entry where "the
   deployment side ends" is literally true rather than aspirational.
   - **One image per version, built from the training environment.** The SDK captures its own
     `scikit-learn`/`numpy`/`scipy`/`cloudpickle`/Python versions at `assemble()` time and ships
     them; the image is built from those pins. A pickle is only reliably loadable against the
     versions that wrote it, so a single shared serving image was never an option — and the
     capture has to happen client-side, because introspecting on the server would faithfully
     report the wrong machine. A free consequence: the artifact is copied into the image, so the
     serving container needs **zero credentials**, the same principle the eval sandbox already
     had, reached from a different direction.
   - **The request schema is measured, not guessed.** `execution.sandbox.introspect()` reads
     `n_features_in_`, `feature_names_in_`, `classes_` and `predict_proba` off the fitted
     estimator inside the same scrubbed subprocess `predict()` already ran in, at ingest, beside
     Hawkeye. Structure is a fact about the object; only semantics need an LLM. `feature_names`
     being None is a real answer — it makes the served API positional rather than named, instead
     of inventing column names.
   - **`manifest.io_schema` is populated for the first time.** It had been specced in Schemas.md
     since the first draft and never created.
   - **`telemetry_event.inputs` and `.prediction` are non-null for the first time in this
     project's history.** They were created with Falcon and left empty because a customer-hosted
     model has no reason to ship payloads back. Verity serving is what makes them writable, and
     therefore what unblocks drift detection — that is why serving came before the drift metrics
     rather than after.
   - **Deploy is non-fatal, like Falcon.** It runs after the version is already `production`, so
     a build failure writes a `failed` deployment row and returns `deployment: null` rather than
     500ing a promotion that genuinely succeeded. Verified live by pointing the server at an
     unreachable Docker daemon: `DockerRuntime` raised, `deploy()` recorded the reason in the
     real database, and the orchestrator swallowed it — all three layers behaved.
   - Verified live end-to-end: a real 585 MB image built and started, the proxy returned real
     predictions, a second upload under the same name promoted and **tore down the container it
     replaced** (old one `Exited (0)`, proxy re-routed with no intervention), and telemetry rows
     landed carrying inputs and predictions.
   - **Two bugs found by running it for real, not by tests.** The SDK's 60s HTTP timeout could
     not survive a cold container build (~3 min), so the client reported `ReadTimeout` for a
     request the server went on to complete successfully — raised to 600s and made configurable.
     And `uv sync` without `--extra dev` silently pruned pytest from the server venv, so
     `uv run pytest` fell back to the *global* interpreter; the suite kept passing against the
     wrong packages, and a pandas test that should have skipped "passed" by accident. Fixed with
     `uv sync --extra dev`, after which the full suite runs on the venv it claims to.
   - **One honest loose end:** `manifest.serving_pattern` was created and is never written.
     Stamping it would mean updating an append-only evidence row after the fact, and the
     `deployment` row already records how a version is served. It stays empty rather than being
     written dishonestly; a later migration drops it if nothing claims it.
   Still not automated past here: drift and data-quality metrics (now unblocked), alerting (V7),
   the `agent_run` audit trail, comparative promotion gating, and anything beyond a single
   local replica — deployments live and die with the machine running Verity.

10. Falcon detects and notifies now — what entry 9 still listed as "alerting (V7)" moved
    forward into V1, brought closer by api-fication itself: once the proxy measures real
    production latency (entry 9) and sees real production inputs, comparing live traffic
    against itself rather than against a cold-sandbox figure stopped requiring anything V3's
    analytics store was supposed to unlock first.
    - **Two checks, zero LLM calls, reusing machinery that already existed rather than
      reimplementing it.** `agents/brain4/falcon/detect.py`'s `detect_systemic_anomaly` compares
      a version's trailing 15 minutes of traffic (`error_rate`, then `latency_p95_ms`) against
      the 15 minutes before that — never against `eval_reference`, which is a cold-sandbox
      estimate that always looks better than real traffic and would just manufacture false
      alarms. `detect_quality_anomaly` re-runs Nat's own `score()`/`apply_thresholds()` against
      accumulated delayed labels, against the exact `metric_set` and thresholds frozen at
      promotion time — not a second scoring implementation, the same one. Below
      `WINDOW_MIN_EVENTS=20` events in either window, or `MIN_LABELS=30` accumulated labels,
      both are a deliberate no-op: a handful of events producing a scary-looking number is
      noise, not signal.
    - **No scheduler, no poller — both checks run inline, triggered by the events that already
      move data.** `check_systemic` fires from `TelemetrySink.flush()` (once per distinct
      `model_version_id` in the batch, after the write succeeds) and from `POST /telemetry`,
      covering both a Verity-served model and a customer-hosted one reporting through the SDK.
      `check_quality` fires from the new `POST /predictions/{prediction_id}/outcomes` once its
      batch of labels lands. Every call site wraps the check in its own `try/except` on top of
      the check's *own* internal one — belt and suspenders around the one rule that must never
      break: a detection bug can never turn a successful write into a failed response.
    - **`prediction_id`, minted before the row that would hold it exists.** The proxy's
      `predict()` generates `pred_<uuid4>` before calling the container, not read back from a
      database insert — `TelemetrySink` only queues the write, so the bigint `telemetry_event.id`
      doesn't exist yet when the response goes out, and a customer reporting a delayed outcome
      needs something to correlate against immediately. New `label_event` table, upserted on
      `(telemetry_event_id, instance_index)` so a corrected label overwrites rather than
      double-counts.
    - **The row is the truth; email is best-effort on top of it.** `record_and_notify` writes a
      new `alert_event` row unconditionally, first, then attempts an email via AWS SES (same
      account api-fication's S3 usage already lives in) only if the model has a registered
      `alert_email` — a raising or failing send is swallowed and never un-writes the alert.
      `emailed_at` staying null is the only record delivery didn't happen; nothing retries it.
    - `alert_email` threads all the way from `verity.assemble(model, ..., alert_email=...)`
      through `upload()` → `/ingest` → `orchestrator.build_artifact()` →
      `agents/brain3/fury/registry.py`'s `register()` → `create_model()`. Deliberately one-way:
      the existing-model branch of `register()` never touches a model's `alert_email` on a later
      version's upload, so re-uploading an already-registered model can't silently change who
      gets notified.
    - **Verified live, not just against the unit suite — with one constraint worked around
      rather than fought.** `check_systemic` compares two windows exactly 15 minutes apart by
      `occurred_at`, which is set server-side and isn't backdateable through any API; genuinely
      exercising it with two real, temporally-separated windows would cost ~30 real minutes of
      waiting for one verification step, which wasn't worth burning. What *was* run for real:
      started the server, used `verity --demo` to get a promoted, Docker-deployed model, sent 35
      clean predictions in one burst (one of 36 attempts hit the unrelated flake noted below) —
      `GET /models/{id}/alerts` stayed empty, confirming
      `check_systemic` runs without error and correctly stays silent with no aged baseline window
      yet (not a false negative; there was nothing 15-30 minutes old to compare against). Then,
      against the same model, reported 32 deliberately wrong outcomes
      (`actual = 1 - predicted`) through the new outcomes route — three real `quality` alerts
      fired once `MIN_LABELS` was crossed, detail `{"metric": "accuracy", "op": ">=", "value":
      0.75, "actual": 0.0}`, thresholds read back verbatim from the promoting `eval_run`. A
      second version was
      assembled with `alert_email="ops@example.com"` set and put through the same wrong-outcome
      burst specifically to exercise the SES-failure path (no `SES_SENDER`/SES-authorized
      credentials configured in this environment — only S3's, which don't authorize
      `ses:SendEmail`): two more alerts fired, `emailed_at` stayed null on every alert across
      both runs, and every triggering request — `/predict`, and all 63
      `/predictions/.../outcomes` calls — returned 200. The systemic check's own comparison
      arithmetic is what `test_falcon_detect.py` and `test_falcon_monitor.py` prove instead, with
      synthetic timestamps exactly 15 and 30 minutes apart — that is the actual proof of
      correctness for that half, not a claim of a live soak test that wasn't run.
    - **Found live, not a regression in this feature:** two of 171 live requests logged during
      this verification hit a
      transient `httpx`/Supabase `RemoteProtocolError: Server disconnected` inside
      `find_model`/`find_production_version` — pre-existing code this task didn't touch, both
      simple retries, named here for the record rather than silently absorbed.
    - **Accepted risks, named in the design spec, not solved here:** one global
      `RELATIVE_INCREASE_THRESHOLD` for every model regardless of how bursty its traffic
      naturally is; a model whose customer never reports a single outcome is invisible to
      `check_quality` forever, indistinguishable from "the model is fine"; each window only ever
      compares against its immediate predecessor, so a slow decline spread across many windows
      never trips any single comparison (needs a longer historical baseline — the analytics store
      V3 already earmarks for telemetry volume, not invented here); best-effort email only, no
      retry queue for a failed SES send.
    - **Security finding surfaced during implementation, deliberately not fixed:**
      `POST /predictions/{prediction_id}/outcomes` has no ownership check — a `prediction_id` is
      a 128-bit `uuid4` not otherwise exposed at rest, but anyone who obtains one can report an
      outcome against it with no verification they received that prediction. Same class of gap
      `/ingest`, `/telemetry`, and `/predict` already carry by the standing, already-reasoned
      decision that no route has auth until V1.5 — securing one while that stands would be
      theatre. Called out specifically here because this route feeds directly into the alerting
      signal: a leaked `prediction_id` lets someone inject a fabricated label that suppresses a
      real alert or manufactures a false one, a sharper consequence than a bad `/telemetry` event
      merely skewing a summary stat.
    Still not automated past here: input drift detection (a separate mechanism — needs a
    distributional distance metric that doesn't exist yet), autonomous retraining (a human
    decides, always), the `agent_run` audit trail, comparative promotion gating, and anything
    beyond a single local replica.
