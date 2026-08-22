# Verity — Full Architecture & Workflow

> Source analysed: `e:\Projects\Verity`, branch `main`. All file references are relative
> to the repo root. Server test suite: 207 passing. SDK test suite: 45 passing.
>
> This describes all four V1 agents and container serving, all as built.

---

## 1. The one-paragraph version

Verity is a **four-stage pipeline behind one HTTP endpoint**. A client SDK
(`verity.assemble(model, name=..., user_id=..., X_test=..., y_test=...)`) cloudpickles a
model, computes its own SHA-256, and POSTs multipart form data to `POST /ingest`. The
server re-verifies that digest against the actual bytes (never trusts the client's claim),
then runs four agents in sequence, each consuming only the previous one's output:
**Hawkeye** (an LLM call) infers the framework and task type from `repr(model)`; **Nat**
picks task-appropriate metrics from a fixed taxonomy, runs `predict()` in a
credential-scrubbed sandboxed subprocess, and scores the result against both quality and
systemic (latency/memory/CPU/GPU) thresholds; **Fury** groups the upload into a named
logical model, dedupes byte-identical repeats before any of the above runs, and — on a
passing verdict — promotes straight to `production`, archiving whatever it replaces; and
**Falcon**, which needs no LLM at all, switches monitoring on for the promoted version by
lifting its reference numbers straight out of the eval run that justified the promotion. Every
agent is a pure function with every collaborator injected and a lazy real default, so the
whole chain is testable with hand-written fakes and zero mocking.

A promotion additionally builds and starts a container for the version (§9), which Verity
then fronts: `POST /users/{id}/models/{name}/predict` proxies to it and records every request.
Two further routes sit outside the chain entirely — `POST /telemetry` accepts batched live
traffic from `verity.monitor()` when the customer hosts the model themselves, and
`GET /models/{id}/telemetry` summarises whichever source produced it.

```
┌──────────────────────────────────────────────────────────────────────┐
│  Client        verity.assemble()  │  client/ (Next.js intake form)   │
│                verity.monitor()   →  POST /telemetry                 │
├──────────────────────────────────────────────────────────────────────┤
│  Transport     multipart POST /ingest — artifact, sha256, name,      │
│                user_id, args, optional fixture + fixture_descriptor  │
├──────────────────────────────────────────────────────────────────────┤
│  Server        FastAPI  main.py  →  orchestrator.build_artifact()    │
├──────────────────────────────────────────────────────────────────────┤
│  Fury (dedup)   Hawkeye (LLM)   Nat (LLM + sandbox)   Fury   Falcon  │
│  find_existing → identify() → evaluate() → register() → configure()  │
├──────────────────────────────────────────────────────────────────────┤
│  Sandbox       execution/sandbox.py → subprocess, scrubbed env       │
├──────────────────────────────────────────────────────────────────────┤
│  Stores        S3BlobStore (artifacts)  │  SupabaseMetadataStore (rows)│
└──────────────────────────────────────────────────────────────────────┘
```

---

## 2. The core mechanism — fixture-kind dispatch and the agent handoff contract

This is the single idea everything else hangs off, the way URI-scheme resolution is for
MLflow. Verity has no equivalent registry-of-registries; instead, **every extension point
in the pipeline is a plain dict key dispatching to a module**, checked at the point closest
to where the ambiguity actually lives.

### 2.1 Fixture kind → eval mechanism

`agents/brain2/nat/registry.py:13-15`

```python
MECHANISMS = {
    labeled_holdout.KIND: labeled_holdout,
}
```

`for_fixture(fixture)` (`registry.py:18-25`) reads `fixture["kind"]` and raises
`UnsupportedFixture` on anything not registered. This is **not** LLM-chosen — it cannot be
hallucinated, because it's a dict lookup on a client-supplied string, resolved before any
LLM call happens. Widening Verity to a new model class is a new module
(`agents/brain2/nat/mechanisms/image_folder.py`, etc.) plus one line here — the comment at
`registry.py:9-12` names exactly which future version needs which kind
(`image_folder` → V3/DL, `corpus_index` → V4/RAG, `environment_ref` → V5/RL).

A mechanism module is a 3-item contract, in full at `mechanisms/labeled_holdout.py`:

| Name | Type | Meaning |
|---|---|---|
| `KIND` | str | matches a `fixture["kind"]` value |
| `ATLAS_SECTION` | str | which row of `Metrics.md`'s taxonomy applies |
| `profile(data)` | fn | deterministic facts about the fixture (`n_samples`, `n_classes`, …) |
| `run(*, model_payload, data, execute_fn)` | fn | produces raw outputs; computes zero metrics itself |

### 2.2 Injected collaborators with lazy real defaults

Every agent-calling boundary in the codebase follows the identical shape:
`param=None` → `param = param or _default_param` → `_default_param` does the heavy
(network/subprocess) import **inside the function body**, not at module load time. Four
instances, all in `server/orchestrator.py:22-25` and `:140-162`:

```python
identify_fn = identify_fn or _default_identify
evaluate_fn = evaluate_fn or _default_evaluate
find_existing_fn = find_existing_fn or _default_find_existing
register_fn = register_fn or _default_register
```

This is why every test in the codebase can inject a hand-written fake instead of hitting
Groq, S3, Supabase, or a subprocess — and why `_default_evaluate` can casually import
`execution.sandbox` (`orchestrator.py:146-150`) without that import cost landing on every
test collection.

### 2.3 Threshold ops as a dict, not a branch

`agents/brain2/nat/score.py:73-78` — `>=`/`<=`/`>`/`<` map to lambdas, keyed by the exact
string an LLM (Nat) is asked to emit. `apply_thresholds` (`score.py:81-93`) never branches
on operator identity; it looks one up and calls it. The same shape, `_METRIC_FNS`
(`score.py:15-41`), maps 14 concrete metric names to `(fn, needs_proba)` pairs, keyed by
Atlas section — so V4/V5 add a new top-level key to that dict, not a new `if` chain.

---

## 3. The `/ingest` lifecycle

`server/main.py:42-65` → `server/orchestrator.py:6-115`

### 3.1 What arrives

```
POST /ingest
  artifact           file, required   — cloudpickled model bytes
  user_id            form, required
  name               form, required   — the identity key (§5.1)
  sha256             form, required   — claimed digest, re-verified (§3.2)
  args               form, default "{}" — free-form passthrough dict
  fixture            file, optional   — cloudpickled {"X":.., "y":..}
  fixture_descriptor form, optional   — {"kind","sha256","spec"}
```

### 3.2 Digest re-verification — first thing, before anything else

`orchestrator.py:32-36`

```python
actual_sha256 = hashlib.sha256(payload).hexdigest()
if actual_sha256 != sha256:
    raise ValueError(...)
```

This exists because `sha256` decides three separate things downstream — the S3 key, the
`artifact_sha256` column, and (via dedup) *which existing record a caller gets back,
including its `status`*. A client that could claim someone else's digest while sending
different bytes would be handed that other version's record, `production` status
included, without the real bytes ever touching storage or evaluation. Added after a final
whole-branch review caught it as a live gap — see §11.

### 3.3 Dedup short-circuit — conditional on whether there's new evidence

`orchestrator.py:42-55`

```python
if fixture_payload is None:
    existing = find_existing_fn(...)
    if existing is not None:
        return {..., "deduplicated": True}
```

The `if fixture_payload is None` guard is load-bearing, not incidental. Dedup only fires
when there is *nothing new to evaluate* — a fixture-bearing upload always runs the full
pipeline, even against an identical, already-registered hash+name, because otherwise the
fixture the caller just attached would be silently discarded and the version permanently
stranded at `pending`. (This was itself a bug caught and fixed in review; see §11.)

### 3.4 The unsandboxed seam, named explicitly in comments

`orchestrator.py:59-66` and `:125-130` both cloudpickle-load untrusted bytes **in the same
process as the S3/Supabase credentials** — the model artifact (so Hawkeye can call
`repr()` on it) and the fixture (so labels stay server-side for scoring). Both comments
say the same thing on purpose: a pickle payload executes code on load whether you think of
it as "data" or not, and both loads are named as belonging behind the sandboxed
`python-exec` boundary that `predict()` itself already runs behind (§7). This is documented
debt, not undocumented debt.

### 3.5 The tail — one call each to Hawkeye, Nat, Fury, Falcon

```
identify_fn(model)                                    → manifest
save_model_version(status="pending") + save_manifest()
if fixture: evaluate_fn(...)                          → eval_run  (§7)
register_fn(..., verdict=eval_run["verdict"] or None) → registration  (§5)
if registration["status"] == "production":
    configure_fn(...)                                 → monitoring_config  (§8)
return {status: registration["status"], model_id: registration["model_id"], ...}
```

`register_fn` is called **unconditionally**, whether or not a fixture was supplied
(`orchestrator.py:96-104`) — identity linking has to happen even for a bare, un-evaluated
`pending` upload, since it's still part of that model's version history.

---

## 4. Storage — two stores, two different trust models

### 4.1 `S3BlobStore` — `server/storage/models/s3.py`

Content-addressed: `put(sha256, payload)` keys directly on the digest
(`s3.py:30-32`), so the same bytes always land at the same key regardless of which
`model_version` row references them. No fallback credentials — missing
`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` fails loudly via boto3's own chain rather than
silently defaulting to junk values that would fail confusingly later (`s3.py:12-16`).
`S3_ENDPOINT_URL` is the one escape hatch — set it and the identical code targets R2,
MinIO, or a local gateway with no code change.

### 4.2 `SupabaseMetadataStore` — `server/storage/models/supabase.py`

Every write method (`save_model_version`, `save_manifest`, `save_eval_run`,
`update_model_version_status`, `link_model_version`, `promote_model_version`,
`archive_model_version`) is a single-purpose wrapper over one PostgREST call. The four
mutation methods added for Fury (`:50-139`) all share a pattern the earlier three didn't
originally have: they `.select()` on the update so PostgREST returns affected rows, then
raise if `result.data` is empty —

```python
if not result.data:
    raise ValueError(f"promote_model_version affected no rows for model_version_id={model_version_id!r}")
```

— because a PostgREST update matching zero rows returns HTTP 200 with an empty array, not
an error. Without this check, a row that silently failed to update would be
indistinguishable from one that succeeded. Added in the same final-review pass as §3.2.

### 4.3 Why two stores, not one

Nothing in the codebase couples them beyond passing IDs across the boundary. `orchestrator.py`
never imports Supabase or S3 client libraries directly — it receives `blob_store` and
`metadata_store` as opaque objects satisfying a small protocol (`.put()`; `.save_*`/`.find_*`/
`.update_*`). `server/main.py:33-40`'s `get_build_artifact()` is the only place that
constructs the real ones.

---

## 5. Fury — identity, dedup, promotion, archival

`agents/brain3/fury/registry.py` — 51 lines, **the only agent with no LLM call**, because
everything it does is deterministic bookkeeping: a hash lookup, a name lookup, a status
write.

### 5.1 Identity is an explicit `name`, never inferred

`registry.py:9,22` both start with `metadata_store.find_model(user_id=user_id, name=name)`.
There is no fallback inference from `manifest.model_class` or anything else — two unrelated
models of the same sklearn class from the same user would wrongly collide into one lineage
if identity were inferred rather than declared. `find_model` filters on `(user_id, name)`
(`supabase.py:62-70`); the DB constraint is `UNIQUE (user_id, name)` on the `model` table.

### 5.2 Dedup — keyed on hash **and** name together

`find_existing` (`registry.py:1-12`) looks up the model by name first, *then* checks the
hash against versions of *that* model only (`find_model_version_by_hash(model_id=...)`).
A hash match under a *different* name is deliberately not a hit — identical bytes
registered under a new name is a legitimate new registration (someone re-registering the
same weights under a new lineage), not an accidental duplicate.

### 5.3 Promotion — fully automatic, no comparison against the incumbent

`registry.py:35-51`:

```
verdict != "pass"  → status = "pending" (verdict is None) or "staging_failed"
verdict == "pass"  → find current production version (if any) → archive it
                    → promote_model_version(this one)
                    → status = "production"
```

A passing verdict goes **straight** to `production` — there is no intermediate `staging`
resting state, unlike the schema originally specced. `staging` remains in the status enum and
remains unwritten; the api-fication design deploys automatically on promotion, so no state
exists in which a version has passed and is waiting for a human (`docs/Schemas.md`). One production slot per model,
always: `find_production_version` + `archive_model_version` run before the new promotion,
so "which version is live" never has more than one answer.

**Named, accepted risk, not solved**: nothing compares the new version's scores against the
incumbent's. A version that barely clears its own thresholds can replace one that was
scoring far better. Mitigated only by both `eval_run` rows staying on record for
inspection — not prevented. This is `Schemas.md`'s and the design spec's explicit V1 scope
boundary, not an oversight.

### 5.4 What `register()` returns, and what happens to it

```python
{"model_id": ..., "status": ..., "archived_model_version_id": ...}
```

`orchestrator.py:106-115` threads all three into the final `/ingest` response —
`archived_model_version_id` specifically so a caller can see *what got displaced* by their
own promotion, since that's the only visibility the accepted risk in §5.3 gets.

---

## 6. Hawkeye — identification

`agents/brain1/hawkeye/identify.py` — the entire agent is one LLM call.

```python
def identify(model, client=None) -> dict:
    client = client or _real_client()
    response = client.chat.completions.create(
        model=os.environ.get("HAWKEYE_LLM_MODEL", DEFAULT_MODEL),
        messages=[{"role": "system", "content": _SYSTEM_PROMPT},
                  {"role": "user", "content": repr(model)}],
        response_format={"type": "json_object"},
    )
    return Manifest.model_validate(json.loads(...)).model_dump()
```

`repr(model)` is the entire signal — no artifact introspection, no ONNX graph reading (that's
future scope). `Manifest` (`identify.py:9-14`) is a pydantic model, so a malformed LLM
response fails loudly at validation rather than propagating a half-shaped dict. `task_type`
is deliberately coarse (`identify.py:23,25-26`): the system prompt explicitly tells the model
*not* to distinguish binary from multi-class classification, because that depends on the
evaluation data, not the model object — Nat's mechanism (`labeled_holdout.py:12-20`) resolves
that distinction later, from real label cardinality, rather than Hawkeye guessing from a repr.

Credentials are shared, not per-agent: `VERITY_LLM_API_KEY` / `VERITY_LLM_BASE_URL` are the
same env vars Nat's `resolve.py` reads (`agents/provider.py` holds the shared defaults,
`DEFAULT_BASE_URL`/`DEFAULT_MODEL` — currently Groq). Only the model *id* is
per-agent-overridable (`HAWKEYE_LLM_MODEL` vs `NAT_LLM_MODEL`), so one provider swap moves
every agent at once.

---

## 7. Nat — evaluation

`agents/brain2/nat/evaluate.py` is the orchestrating entry point; it **never raises** —
every failure mode (unsupported fixture, a crashed sandbox, an LLM that returns garbage)
becomes `verdict: "error"` with detail in `error` (`evaluate.py:24-27,75-77`). The reasoning
is explicit in the docstring: *"A gate that cannot be evaluated must still leave a row
behind — the promotion decision has to be re-derivable from rows, not from re-running an
agent."*

### 7.1 The five-step body

```
for_fixture(fixture)                                  → mechanism        (§2.1)
mechanism.profile(data)                               → n_samples, n_classes, ...
resolve_fn(manifest, profile, atlas_section, ...)     → metric_set + thresholds  (LLM, §7.2)
mechanism.run(model_payload, data, execute_fn)        → raw y_pred/y_proba/resource
score(section, metric_set, outputs)                   → scores, skipped  (§7.3, deterministic)
merge_resource(scores, outputs["resource"])           → one flat score dict
apply_thresholds(scores, thresholds)                  → verdict, failed_on
```

### 7.2 What the LLM actually decides — and what it structurally cannot

`resolve.py:96-108` sends the manifest, the dataset profile, the relevant slice of the Atlas
taxonomy (`_ATLAS["ML"]`, `resolve.py:12-21`, transcribed from `Metrics.md`), and the list of
measurable resource metrics. It returns `task_type`, `metric_set`, and `thresholds`. Two
pydantic validators close the two ways this could go wrong:

- `Threshold._known_operator` (`resolve.py:41-46`) rejects any `op` not in `(">=","<=",">","<")`.
- `EvalPlan._thresholds_only_gate_selected_metrics` (`resolve.py:55-65`) rejects a threshold
  naming a metric the plan didn't select — unless it's a `resource.*` metric, which is
  always eligible regardless of `metric_set`, because those are measured unconditionally by
  the sandbox (§7.4), not chosen by the LLM at all.

**Mechanism is never LLM-chosen** — it's derived from `fixture["kind"]` before `resolve()`
is even called (§2.1), so it cannot be hallucinated into evaluating the wrong task type.

### 7.3 Scoring — deterministic, zero LLM, zero I/O

`score.py:96-109`. Two failure modes are structural, not exceptional:

- an unrecognized metric name → `skipped`, reason `"unknown_metric"` (an LLM can propose a
  metric that doesn't exist; this doesn't crash the run)
- a metric needing `y_proba` on a model with no `predict_proba` → `skipped`, reason
  `"requires_proba"`

`apply_thresholds` (`score.py:81-93`) treats a threshold on a *skipped* metric as a failure,
not a silent pass — "a gate nobody could evaluate has not been cleared" (comment, verbatim).

### 7.4 The sandbox — where `predict()` actually runs

`server/execution/sandbox.py` + `runner.py`. `execute()` spawns
`python -m execution.runner` as a subprocess (`sandbox.py`, `_child_env()`), passing through
only `PATH`, `SYSTEMROOT`, `PYTHONPATH`, `TEMP`, `TMP` — every credential
(`SUPABASE_KEY`, `AWS_SECRET_ACCESS_KEY`, `VERITY_LLM_API_KEY`) is withheld. There is a test
proving this holds (`test_the_sandbox_cannot_read_the_servers_credentials`,
`server/tests/test_sandbox.py`) — it sets all three env vars in the parent, gives the child a
model whose `predict()` tries to read them back, and asserts the child sees `None` for all
three.

Inside `runner.py:63-95`, every eval run through this sandbox gets the same instrumented
sample for free, regardless of mechanism:

| Metric | How |
|---|---|
| `latency_p50/p95/p99_ms` | per-row timing over up to 200 rows (`LATENCY_SAMPLE_ROWS`) |
| `throughput_rps` | one batched `predict()` call, rows / elapsed |
| `peak_memory_mb` | `psutil` RSS (Windows: `peak_wset` high-water mark) |
| `cpu_time_s` | `psutil` process CPU times |
| `gpu_memory_mb` | `torch.cuda.max_memory_allocated()` if importable and available, else `null` |

These are named honestly, not oversold, in the comment at `score.py:46-49`: *"feasibility
figures from a single-process, single-client, cold sandbox — not production percentiles
under load."* Real load testing and GPU-aware dispatch are explicitly V3 scope.

Communication with the child is cloudpickle over stdin/stdout (`sandbox.py`↔`runner.py:98-113`),
not a network call — the child imports nothing from `server` or `agents` (module docstring,
`runner.py:1-10`), so it can never accidentally import something that drags a credential in.

---

## 8. Falcon — observability

`agents/brain4/falcon/monitor.py` — **the only agent with no LLM call in it.** Everything
Falcon does is derivable from rows that already exist, so reasoning about it would add
nondeterminism and buy nothing.

### 8.1 Why it needs no model

When Fury promotes a version, `model_version.promoted_from` already points at the `eval_run`
that justified the promotion, and that row already holds measured latency percentiles,
throughput, peak memory, and quality scores. The reference is *lifted from evidence that
already exists* rather than proposed. `configure()` is two dict comprehensions and an insert.

### 8.2 The prefix split

`build_eval_reference()` re-sorts the flat `eval_run.scores` dict using the same
`RESOURCE_PREFIX` Nat namespaced it with (`agents/brain2/nat/score.py`):

```python
for key, value in (scores or {}).items():
    if key.startswith(RESOURCE_PREFIX):
        reference[key[len(RESOURCE_PREFIX):]] = value   # latency_p95_ms, peak_memory_mb, …
    else:
        reference["quality"][key] = value               # accuracy, f1, roc_auc, …
```

Resource metrics are hoisted to the top level because they are what monitoring watches;
quality metrics are nested under `quality` because nothing at V1 can recompute them live —
that needs labels, and production traffic doesn't come with any.

### 8.3 The honesty constraint that shaped the design

The reference carries `"basis": "sandbox_feasibility"`, and **V1 compares nothing and alerts
on nothing.** Those numbers came from a single-process, single-client, cold sandbox; real
latency under concurrency will be materially higher. A baseline that is wrong by construction,
shipped with alerts attached, trains people to ignore alerts — which is worse than no alerting
at all. The `basis` marker exists so V7's rule engine can tell what kind of number it is
reading, and `client/src/components/telemetry-panel.tsx` renders the reference beside the live
numbers as *context*: no comparison, no pass/fail colouring.

This is the weakest point in the current design, and api-fication is what fixes it — once
Verity serves the model itself, the proxy measures real production latency and the reference
can be rebased on a number of the same kind as the one it is compared against.

### 8.4 Telemetry must never be why inference fails

`verity/src/verity/monitor.py` wraps the customer's model in a proxy. The governing rule is
that the monitoring path can never degrade the thing it monitors, which shows up as four
specific decisions:

| Decision | Consequence |
|---|---|
| `put_nowait()` on a bounded queue | a full queue drops events and bumps a counter; it never blocks `predict()` |
| HTTP runs on a background thread | the network never touches the predict path |
| `_call` catches `BaseException`, records, then bare `raise` | the caller gets *their* exception with the original traceback, not a wrapped one |
| `_record` wraps the reporter in `except Exception: pass` | a broken reporter cannot turn a working prediction into a failure |

`object.__setattr__` sets `_model` / `_reporter` because the proxy defines `__getattr__`, and
assigning normally would recurse into it.

### 8.5 The non-fatal asymmetry with Fury

`orchestrator._configure_monitoring()` swallows exceptions; `register_fn` does not. The two
look inconsistent and are deliberately so:

- Fury raising is **correct**. If registration fails, the promotion did not happen, and a 500
  is the truth.
- Falcon raising would be **wrong**. Falcon runs *after* the version is already `production`.
  Failing the request would report a promotion as failed when it genuinely succeeded.

The caller gets `monitoring_config: null` — visible rather than fabricated — and monitoring can
be configured later. api-fication's deploy step inherits this same contract for the same
reason.

### 8.6 Summarisation is a pure function

`server/telemetry.py:summarize()` turns a list of events into counts, error rate, and
percentiles with no I/O and no database access, which is why it is testable with a list of
dicts. Percentiles come from `numpy.percentile`. `truncated` is set when the row limit was
reached, so a caller can distinguish "1000 events" from "at least 1000 events".

---

## 9. Serving — api-fication

Not an agent. Promotion generates a pipeline; the pipeline runs deterministically. Four
modules under `server/serving/`, each with one job.

### 9.1 Why one image per version, and not one image for all of them

A pickled estimator is only reliably loadable against the library versions that wrote it.
A single shared serving image would have to load every customer's pickle with one
scikit-learn, and version skew is *the* failure mode for pickles. So the SDK captures the
training environment at `assemble()` time (`verity/src/verity/environment.py`) and the
image is built from it — the image *is* the training environment, and skew is solved by
construction rather than hoped away.

The capture has to be client-side. Introspecting on the server would faithfully report the
*server's* versions, which is precisely the wrong answer.

A second consequence falls out for free: because the artifact is copied into the build
context, the container holds its own model and needs **zero credentials** — the same
principle as the eval sandbox, arrived at from a different direction.

### 9.2 The input contract is measured, not guessed

`execution.sandbox.introspect()` loads the artifact in the same scrubbed subprocess
`predict()` runs in and reads `n_features_in_`, `feature_names_in_`, `classes_`, and
whether `predict_proba` exists. This runs **at ingest, beside Hawkeye** — structure is a
fact about the object, and only semantics need a language model.

`feature_names` is `None` whenever the estimator was fit on arrays rather than a
DataFrame. That is a real answer, not a failure: it makes the served API positional
(`{"instances": [[1.0, 2.0]]}`) instead of named (`{"instances": [{"age": 1.0}]}`), and
the generated app rejects the wrong shape rather than guessing which column is which.

### 9.3 The app is a checked-in file, not codegen

`serving/app_template.py` is a normal FastAPI app that reads `contract.json` next to
itself. Only `requirements.txt` and one `FROM python:{version}-slim` line are templated.
That is what lets `tests/test_serving_app.py` drive it directly against a temp directory
with Docker nowhere in the loop.

The model loads at **import**, so an artifact that cannot load in the built image means
the container never reports healthy and the deploy fails loudly — rather than succeeding
and failing on a customer's first request.

Named-feature models are handed a DataFrame rather than a bare array. scikit-learn
otherwise warns on *every* `predict()` call for the rest of the container's life.

### 9.4 Dependencies before the model, deliberately

`serving/build.py` renders the Dockerfile with `COPY requirements.txt` and
`RUN pip install` **before** `COPY model.pkl`. Docker then reuses the cached pip layer for
any later model sharing that dependency set — the difference between a 3-minute cold build
and a seconds-long warm one. `tests/test_build_context.py` asserts the ordering, because a
well-meaning reorder would silently cost minutes per deploy and break nothing visible.

### 9.5 The runtime seam

`serving/runtime.py` exposes `build` / `run` / `stop`, and `DockerRuntime` is the only
implementation. Ports are ephemeral — Docker assigns, the code reads back — so there is no
port registry to drift out of sync with reality. Everything above the file talks to the
interface, so an ECS or Fargate runtime is a new class plus one wiring change.

### 9.6 Ordering and teardown

`serving/deploy.py` writes a `building` row, renders, builds, runs, polls `/health`, then
flips the row to `live`. Only **after** that does it stop the container the promotion
displaced. Tearing down first would open a window with nothing serving at all, and
`tests/test_deploy.py` asserts the order rather than trusting it.

Teardown is best-effort: the new version is already live, so failing its deploy because an
already-dead old container could not be stopped would be strictly worse than a stale row.

### 9.7 Non-fatal, for the same reason Falcon is

`orchestrator._deploy()` swallows exceptions and returns `None`, exactly like
`_configure_monitoring`. Deploy runs *after* the version is `production`; a build failure
must not report a promotion that genuinely succeeded as a failure. The `failed` deployment
row carries the reason. Verified live against an unreachable Docker daemon.

Deploy is also skipped outright when `io_schema` is null. Serving a model whose input
contract is unknown would mean guessing it, and a wrong guess returns confident nonsense
instead of an error.

### 9.8 The proxy, and the columns that were finally written

`POST /users/{user_id}/models/{name}/predict` resolves the production version, finds its
live deployment, forwards the body, and returns the container's answer. `user_id` sits in
the path because model names are unique per user, not globally, and no auth exists yet to
supply it implicitly; at V1.5 it collapses to `/models/{name}/predict`.

Three distinct 404s — no such model, promoted but not deployed, no live deployment —
because they are opposite problems with opposite fixes.

Telemetry goes through `serving/sink.py`, a bounded queue drained by a background thread,
mirroring the SDK reporter's contract (§8.4). Recording synchronously would put a database
round trip in front of every prediction. **This is where `telemetry_event.inputs` and
`.prediction` finally get written** — they were created with Falcon and stayed null,
because a customer-hosted model never had reason to ship payloads back. Drift detection
becomes possible here and not before.

Worth seeing side by side, from the live run: the proxy measures ~24 ms end-to-end, while
the `eval_reference` sitting beside it in the same response reads 0.19 ms. Both are honest;
they measure different things. The 128× gap is exactly why Falcon tags its numbers
`"basis": "sandbox_feasibility"` and compares nothing.

---

## 10. The frontend — `client/`

Next.js 16 (App Router), a single client component (`client/src/app/page.tsx`) that talks
**directly** to the FastAPI server — no proxy, no Next.js API route in between.

```
client/src/lib/verity.ts     sha256Hex() (Web Crypto) + ingest() — same wire format
                              as verity.client.assemble(), just from a browser
client/src/components/       verdict-stamp.tsx, evidence-report.tsx,
                              telemetry-panel.tsx — pure render, no logic
client/public/demo/          a pre-baked model.pkl + fixture.pkl (generated once via
                              verity.serialize()/labeled_holdout(), so their bytes are
                              guaranteed to match what the real SDK would produce) —
                              lets the whole loop run with one click, no Python needed
```

Two backend gaps this surfaced and fixed, not just the UI itself: `server/main.py` had **no
CORS configuration at all** (`main.py:26-31` adds it, scoped to `localhost:3000` — no auth
exists yet, so this is intentionally not `allow_origins=["*"]`), and the root `.gitignore`'s
bare `demo/` pattern was silently matching `client/public/demo/` too — the same *category*
of bug as `verity/tests` being gitignored project-wide (§11), scoped to `/demo/` (root-anchored)
once found.

Verified against the real backend, not just `next build`: a Node script replicating the
exact browser flow (fetch the static demo files, hash them with the same algorithm, POST
with real `Origin` headers) confirmed all three response shapes the UI renders — a fresh
promotion to `production`, an archived-replacement, and a deduplicated repeat — against
genuine server responses, not fixtures.

---

## 11. Repo-hygiene bugs found by building the system, not by looking for them

Worth its own section because the pattern repeated twice, and both times the discovery
mechanism was the same: doing real work surfaced a class of bug no amount of staring at the
file would have caught.

**`verity/tests` was gitignored project-wide.** Every commit in this project's history that
claimed to modify an SDK test file had silently no-op'd — `git add` on a gitignored path is
a no-op without `-f`. The suite existed on disk, ran locally, and was never once actually in
git history until this was caught mid-session. Fixed by removing the gitignore line and
committing the previously-invisible files.

**The same category recurred one layer down.** Root `.gitignore`'s `demo/` (meant for the
Titanic demo model at repo root) also matched `client/public/demo/`, a nested, unrelated,
intentionally-committed directory. `.gitignore` patterns without a leading `/` match at any
depth — fixed by anchoring it (`/demo/`).

**Three findings from a deliberate final whole-branch review**, run once after Fury's 11
implementation tasks had each individually passed review — specifically to catch what a
task-scoped review structurally cannot see:
1. The client-supplied `sha256` was never verified against the actual payload (§3.2).
2. Dedup could discard a newly-attached fixture, permanently stranding a version at
   `pending` (§3.3).
3. The four Supabase mutation methods never checked whether their update actually matched a
   row (§4.2).

All three are fixed and independently re-verified in the current code, not just re-reviewed
on paper.

---

## 12. End-to-end trace of one call

`verity.assemble(model, user_id="u", name="fraud-classifier", X_test=X, y_test=y)`, model
not previously seen, against a local server:

```
client.assemble()
 ├ _resolve_fixture(X_test, y_test, None) → verity.fixture.labeled_holdout(X, y)
 │    cloudpickle.dumps({"X": X, "y": y}) → fixture_bytes, sha256(fixture_bytes)
 ├ verity.serialize.serialize(model) → payload, sha256(payload)
 └ verity.transport.upload(...)
      httpx POST /ingest  (multipart: artifact, fixture, sha256, fixture_descriptor, ...)
──────────────────────────────── network ────────────────────────────────
main.ingest()
 └ build_artifact_fn(payload=..., sha256=..., fixture_payload=..., ...)
     orchestrator.build_artifact()
      ├ hashlib.sha256(payload) == claimed sha256?  (§3.2)                    yes
      ├ fixture_payload is not None → skip dedup                             (§3.3)
      ├ blob_store.put(sha256, payload)              → s3://bucket/<sha256>
      ├ model = cloudpickle.loads(payload)
      ├ identify_fn(model)  [Hawkeye, LLM call #1]   → manifest
      ├ save_model_version(status="pending") + save_manifest()
      ├ _evaluate(...)
      │   ├ blob_store.put(fixture_sha256, fixture_payload)
      │   ├ data = cloudpickle.loads(fixture_payload)
      │   └ evaluate_fn(manifest, fixture, data, model_payload)  [Nat]
      │        ├ for_fixture(fixture) → labeled_holdout mechanism            (§2.1)
      │        ├ mechanism.profile(data) → n_samples, n_classes
      │        ├ resolve_fn(...)  [Nat, LLM call #2]  → metric_set, thresholds
      │        ├ mechanism.run(...) → execute_fn(model_payload, X)
      │        │    execution.sandbox.execute()
      │        │      subprocess -m execution.runner, scrubbed env            (§7.4)
      │        │        model.predict(X) + per-row timing + psutil sample
      │        │    → y_pred, y_proba, resource
      │        ├ score(...) → scores, skipped                     (deterministic)
      │        └ apply_thresholds(...) → verdict, failed_on
      ├ save_eval_run(eval_run)
      └ register_fn(verdict=eval_run["verdict"], ...)  [Fury]
           ├ find_model(user_id, name) → None → create_model(...)
           ├ link_model_version(model_version_id, model_id)
           ├ verdict == "pass" → find_production_version → None (first version)
           └ promote_model_version(...) → status "production"
      ├ introspect_fn(payload) → io_schema                      (§9.2)
      │    subprocess -m execution.runner, mode="introspect"
      ├ status == "production" → _configure_monitoring(...)  [Falcon]
      │    ├ build_eval_reference(scores) → resource.* hoisted, quality nested  (§8.2)
      │    └ save_monitoring_config(...) → mcfg_...
      ├ status == "production" → _deploy(...)                    (§9.6)
      │    ├ save_deployment(status="building") → dep_...
      │    ├ render_context(...) → Dockerfile, requirements, app.py, model.pkl
      │    ├ runtime.build(...) → verity-model:mv_...
      │    ├ runtime.run(...) → container_id, ephemeral host_port
      │    ├ wait_healthy(...) → /health answers 200
      │    ├ update_deployment(status="live", host_port=...)
      │    └ archived? → stop that container, mark its row `stopped`
      return {status: "production", model_id: "mdl_...", eval_run: {...},
              monitoring_config: {...}, deployment: {...}, ...}
```

Two LLM calls total, both to the same Groq-compatible endpoint. Exactly one subprocess
spawn. Falcon adds neither: it reads the `eval_run` that was written three lines earlier.
Everything else is in-process Python calling injected collaborators.

A prediction against the served version, afterwards:

```
POST /users/u_1/models/served-demo/predict  {"instances": [[0.0], [3.0]]}
 ├ find_production_version_by_name(u_1, served-demo) → mv_...
 ├ find_live_deployment(mv_...) → dep_..., host_port 58218
 ├ transport.post("http://localhost:58218/predict", json=body)
 │    the container: _encode() validates arity/names → MODEL.predict(X)
 │    → {"predictions": [0, 1], "probabilities": [[...], [...]]}
 └ sink.record({... inputs, prediction, latency_ms ...})   non-blocking  (§9.8)
      background thread, every 5s → save_telemetry_events()
```

When the customer hosts the model themselves instead, the same telemetry arrives on its own
route from the SDK:

```
verity.monitor(model, model_version_id="mv_...")     → MonitoredModel proxy
 └ model.predict(X)
      ├ the real predict() runs and returns (or raises — unchanged, §8.4)
      └ queue.put_nowait(event)          non-blocking; drops rather than waits
           background thread, every flush_interval
            └ POST /telemetry  {events: [...]}   → save_telemetry_events()
```

---

## 13. Extension points

| Seam | Add by | Touches |
|---|---|---|
| New eval mechanism (images, RAG, RL) | new module + one `MECHANISMS` line | `agents/brain2/nat/registry.py` only |
| New metric | new key in `_METRIC_FNS[section]` | `agents/brain2/nat/score.py` only |
| New Atlas section (DL/RL/GenAI) | new key in `_ATLAS`/`_SUPPORTED` | `agents/brain2/nat/resolve.py` only |
| New blob backend (R2, MinIO) | set `S3_ENDPOINT_URL` | zero code |
| New container runtime (ECS, Fargate) | new class with `build`/`run`/`stop` | `serving/runtime.py` + one line in `serving/deploy.py` |
| New sandbox mode | new entry in `_MODES` | `execution/runner.py` only |
| New LLM provider | change `VERITY_LLM_BASE_URL`/`_API_KEY` | zero code, both agents move together |
| Swap any agent/store in a test | pass a fake for the `_fn`/`_store` param | nothing else — that's the whole point of §2.2 |

---

## 14. Repo map — where to look for what

| Path | Contents |
|---|---|
| `server/main.py` | FastAPI app, CORS, four routes (`/ingest`, `/telemetry`, `/models/{id}/telemetry`, `/users/{id}/models/{name}/predict`), DI wiring |
| `server/orchestrator.py` | `build_artifact` — sequences all four agents |
| `server/telemetry.py` | `summarize()` — pure function, no I/O (§8.6) |
| `server/serving/` | `app_template.py` (the served app), `build.py` (context), `runtime.py` (Docker seam), `deploy.py` (orchestration), `sink.py` (non-blocking telemetry) |
| `server/storage/models/` | `S3BlobStore`, `SupabaseMetadataStore` |
| `server/execution/` | `sandbox.py` (`execute` + `introspect`), `runner.py` (child, standalone, dispatches on `mode`) |
| `server/migrations/` | Alembic revisions — `model`, `model_version`, `manifest`, `eval_run`, `telemetry_event`, `monitoring_config` |
| `server/tests/` | 207 tests, hand-written fakes, no `unittest.mock` |
| `agents/provider.py` | shared LLM base URL / default model |
| `agents/brain1/hawkeye/` | identification — one LLM call, one pydantic model |
| `agents/brain2/nat/` | `evaluate.py` (orchestration), `resolve.py` (LLM), `score.py` (deterministic), `registry.py` + `mechanisms/` (dispatch) |
| `agents/brain3/fury/` | `registry.py` — dedup, identity, promotion, archival |
| `agents/brain4/falcon/` | `monitor.py` — the LLM-free agent; reference lifted from `eval_run` |
| `verity/src/verity/` | SDK — `client.py`, `transport.py`, `serialize.py`, `fixture.py`, `monitor.py`, `environment.py`, `cli.py` |
| `verity/tests/` | 45 tests, same conventions as `server/tests/` |
| `client/src/app/page.tsx` | the intake form — the one client component |
| `client/src/lib/verity.ts` | browser-side hashing + `/ingest` call |
| `client/public/demo/` | pre-baked demo model + fixture |
| `demo/serve/` | the hand-written Titanic ONNX service — reference for what api-fication generates |
| `docs/Schemas.md` | the data contract — status enum, table columns, MCP connection shape |
| `docs/Metrics.md` | the Atlas — task → metric taxonomy, transcribed into `resolve.py` |
| `docs/progression.md` | the running build log, entry per milestone |
| `docs/superpowers/specs/` | dated design specs, one per agent |

---

## 15. Recurring design idioms

1. **Pure functions, injected collaborators, lazy real defaults.** Every agent boundary
   (`identify_fn`, `evaluate_fn`, `find_existing_fn`, `register_fn`, `execute_fn`,
   `resolve_fn`) follows `param=None → param or _default_param → deferred import inside`.
2. **Dispatch is a dict, not a branch.** Fixture kind → mechanism, Atlas section → metric
   functions, operator string → comparison lambda. Adding a case never means finding an
   `if` chain.
3. **Never guess what can be measured instead.** Hawkeye reports binary-vs-multiclass as
   unknown rather than guessing from a repr; a threshold on an unmeasurable metric fails
   rather than silently passing; missing credentials fail loudly rather than defaulting to
   junk values.
4. **The row is the evidence, not the agent's memory.** `eval_run` stores thresholds *as
   applied*; a promotion decision must be re-derivable from stored rows, never from
   re-running an agent and hoping it reasons the same way twice.
5. **Untrusted execution is isolated, deliberately and visibly.** `predict()` runs in a
   subprocess with a credential allowlist, proven by a test that tries to defeat it. The
   two remaining unsandboxed pickle loads are named in comments, not hidden.
6. **Test-first, hand-written fakes, zero mocks.** Every store, every agent, every sandbox
   call has a fake built for exactly its interface — nothing in `server/tests/` or
   `verity/tests/` imports `unittest.mock`.
7. **Systemic metrics are measured, not chosen.** Resource metrics are always eligible for
   a threshold regardless of what the LLM's `metric_set` says, because they're instrumented
   unconditionally by the sandbox — quality is negotiable per task, feasibility isn't.
8. **Failure is fatal exactly where it changes the truth.** Fury raising 500s the request,
   because if registration failed the promotion did not happen. Falcon raising is swallowed,
   because it runs after the version is already `production` and failing there would report a
   real success as a failure. The rule is not "be defensive" — it is "the response must match
   what actually happened" (§8.5).
9. **Named risk over hidden safety.** Where something isn't solved — comparative promotion
   gating, non-atomic multi-row registry writes — it's written down as an accepted,
   deliberate gap, not silently absent.
