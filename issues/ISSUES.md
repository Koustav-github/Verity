# Issues we hit building Verity, and how we fixed them

A running log of real problems found while building this project — not designed-for edge
cases, but things that actually broke, misbehaved, or lied, discovered by running the real
system rather than by inspection. Ordered roughly by when they surfaced. Most of these are
also noted in-line in `docs/progression.md`; this file exists to make them scannable as a
single list.

---

### 1. The repo still contained a whole different, abandoned project

**Problem:** The root `pyproject.toml` declared a package `verity-eval` ("local-first RAG
evaluation") with `torch`, `transformers`, `sentence-transformers`, and `pinecone` as
dependencies — plus a matching `requirements.txt`, `uv.lock`, a 1.1GB `.venv`, and a
`verity_eval.egg-info/`. Its `[tool.setuptools.packages.find]` even claimed the SDK's
`verity*` package name from the wrong directory.

**Fix:** Deleted all of it. The real packages are `verity/pyproject.toml` (the SDK) and
`server/pyproject.toml` (the server) — nothing at the root needed to exist.

---

### 2. Groq retired the model we depended on

**Problem:** `llama-3.3-70b-versatile` started returning 404 `model_not_found`, silently
breaking both LLM agents (Hawkeye and Nat) at once.

**Fix:** `GET /openai/v1/models` on the live key showed what was actually reachable;
switched the shared default to `openai/gpt-oss-120b` in `server/agents/provider.py`. Two
tests had pinned the old model name as a bare string literal and were failing for the
wrong reason — rewritten to assert against the `DEFAULT_MODEL` constant instead, so they
pin "the provider default is used," not which model that happens to be this month.

---

### 3. Two `.gitignore` patterns were silently too broad

**Problem:** `verity/tests` was gitignored, so the SDK's own test suite had been silently
a no-op in every commit that touched it — it existed on disk and looked committed, but
never actually entered git history. Separately, the root `.gitignore`'s bare `demo/`
pattern was also matching `client/public/demo/`, silently excluding committed frontend
demo assets.

**Fix:** Un-ignored `verity/tests`. Scoped the demo pattern to `/demo/` (root only) so it
stops catching `client/public/demo/`.

---

### 4. The SDK's own documented usage example didn't work

**Problem:** `verity/src/verity/` had no `__init__.py` at all, so the documented usage
example — `from verity import assemble` — raised `ImportError`. Nobody had actually run it
as written.

**Fix:** Added `verity/src/verity/__init__.py` exposing `assemble` and `monitor` at the
package root. Verified live.

---

### 5. `docker-py`'s `images.push()` doesn't raise on a failed push

**Problem:** While building the Fargate runtime, an early version of `_push_to_ecr` called
`client.images.push(...)` and trusted it to raise on failure. It doesn't — a failed push
returns a streamed log with an `error` entry buried in it, silently ignored unless the
caller checks. A real push against the real ECR repo produced **zero images** while
`build()` reported success; only surfaced because the live test then failed downstream
with a confusing `CannotPullContainerError`.

**Fix:** Request the decoded stream (`stream=True, decode=True`) and raise
`ContainerRuntimeError` on any `error` entry. A follow-up review caught a second bug in
the fix itself — it double-wrapped its own exception message — fixed in the same pass.

---

### 6. A runtime seam had a hidden assumption baked in

**Problem:** `deploy.py` hardcoded `f"http://localhost:{host_port}"` instead of trusting
the container runtime to report its own reachable address. Invisible the whole time Docker
was the only runtime (`localhost` was always correct by construction) — only surfaced once
Fargate, a second runtime, needed to hand back a public IP instead.

**Fix:** `run()` now returns `endpoint_url` directly from whichever runtime built it;
`host_port` became optional/nullable, meaningful for Docker's ephemeral port and absent
for Fargate.

---

### 7. `uv sync` without `--extra dev` silently strands you on the wrong interpreter

**Problem:** Hit at least twice, in two different fresh worktrees. Without `--extra dev`,
`uv sync` prunes dev-only packages (pytest included) from the venv, so `uv run pytest`
quietly falls back to a different interpreter entirely. The suite kept "passing" — one
test that should have skipped instead silently passed by accident, against packages that
weren't the ones actually shipping.

**Fix:** Run `uv sync --extra dev` in every fresh worktree before trusting a green test
run. Caught the first time because a task reviewer rejected a report for lacking real
pasted command output — which is what surfaced the wrong-interpreter problem at all.

---

### 8. The SDK's HTTP timeout was shorter than a real cold deploy

**Problem:** A model's very first deploy includes a full Docker image build (~3 minutes
cold). The SDK's HTTP client had a 60-second timeout, so the client reported `ReadTimeout`
for a request the server went on to complete successfully seconds later.

**Fix:** Raised the SDK's timeout to 600s and made it configurable.

---

### 9. Throughput timing counted one-time cold-start cost as steady-state speed

**Problem:** A model's first `predict()` call after unpickling can carry real one-time
setup cost — thread-pool init, lazy compilation, memory-mapping. LightGBM's `Booster` is a
concrete case: ~0.6ms/row once warm, but its very first call on an 8-row batch took
~1.5 seconds. The sandbox's throughput measurement wrapped that first call in its timing,
so it measured cold-start cost and reported it as the model's serving speed.

**Fix:** Added an untimed warmup `predict()` call before the timed throughput batch in
`server/execution/runner.py`. Proven with a hand-written `SlowFirstCall` test fixture:
7.99 rps before the fix, correctly fast after.

---

### 10. LightGBM crashed on container startup — a missing system library

**Problem:** LightGBM's compiled `Booster` dynamically links `libgomp.so.1` (OpenMP) at
import time. `pip install` ships the wheel, not the system library, and the
`python:*-slim` base image doesn't carry it. A real LightGBM deploy 500'd on container
startup with `OSError: libgomp.so.1: cannot open shared object file` — the model never
even got a chance to run.

**Fix:** Added `apt-get install -y libgomp1` to the Dockerfile template, before the pip
layer (so it doesn't invalidate Docker's dependency-layer cache).

---

### 11. Every model's image was pinning libraries it never used — including a 340MB CUDA package

**Problem:** `requirements.txt` for a model's serving image pinned **every** ML library
Verity's SDK found installed on the training machine (`verity/src/verity/environment.py`),
not just the ones that model actually needs. Once xgboost and lightgbm were installed
locally (to verify multi-library support), every subsequent upload — including a pure
scikit-learn `VotingClassifier` with no xgboost or lightgbm anywhere in it — got both
pinned into its image regardless. xgboost's Linux wheel pulls in `nvidia-nccl-cu12`, a
~340MB CUDA library, as a transitive dependency even for pure CPU use. That turned an
unrelated sklearn model's build slow and prone to failing mid-download — confirmed live:
a Titanic model upload's deploy step failed with `pip install ... returned a non-zero
code: 1`, and reproducing the exact install by hand showed the 340MB download as the
obvious weak point.

**Fix:** `server/serving/build.py` now only pins a framework-specific package (xgboost,
lightgbm) when the model's own detected `manifest.framework` actually matches it — a
`sklearn` model's image no longer contains either, no matter what else happens to be
installed on the machine that trained it. `framework` now flows from the manifest through
`orchestrator.py` → `deploy.py` → `render_context()`.

---

### 12. No timeout on the LLM calls could freeze the entire server

**Problem:** Both Hawkeye's and Nat's LLM clients (`server/agents/brain1/hawkeye/identify.py`,
`server/agents/brain2/nat/resolve.py`) were constructed with no explicit request timeout.
The underlying SDK's own default is ~10 minutes, plus internal retries on top. Since every
agent call runs synchronously inside an async route with no thread-pool offload, one slow
LLM response blocks the **entire single-threaded server** — every route, not just the
upload in progress — for however long that call takes. Confirmed live: an upload hung for
several minutes, during which the frontend couldn't even list existing models, because the
whole process was blocked on one pending LLM call.

**Fix:** Added a shared `DEFAULT_LLM_TIMEOUT_S = 30.0` in `server/agents/provider.py`,
passed explicitly into both agents' client construction. A slow provider now fails fast
with a clear error instead of taking the whole server down with it.

---

### 13. Local Docker resource buildup degraded the entire server, not just deploys

**Problem:** After heavy local testing, Docker Desktop had accumulated 76 stopped
containers, 32 images (13.89GB), and 108 build-cache layers (9.3GB). Because Verity's
Docker SDK calls run synchronously inside async FastAPI routes with no thread-pool
offload, an overloaded Docker daemon blocked the entire single-threaded server — even
unrelated simple `GET` requests stopped responding, which looked exactly like a broken
promotion/versioning bug from the outside but had nothing to do with that code at all.

**Fix:** `docker container prune -f`, `docker image prune -a -f`, `docker builder prune -f`
cleared it immediately. Not a code bug — a standing operational risk worth monitoring
whenever local Docker-based deploys get exercised heavily.

---

### 14. Host disk exhaustion broke image builds while Docker reported 918GB free

**Problem:** Model image builds started failing at the pip layer with
`ContainerRuntimeError: image build failed: ... 'pip install --no-cache-dir -r
requirements.txt' returned a non-zero code: 2`. The exit code was the tell — pip's `2` is
`UNKNOWN_ERROR` (an unexpected exception), *not* a dependency-resolution failure, which is
`1`. Resolving the exact same requirement set by hand with `pip install --dry-run`
succeeded cleanly, proving the pins themselves were fine.

The real cause was underneath Docker entirely: the host's `C:` drive had **1.6 GB free**.
Docker Desktop's WSL2 VM disk is a *sparse* `.vhdx` living on `C:`, so `df` inside the VM
cheerfully reported `918G` available — space the VHDX could only actually deliver by
growing the host file, which had nowhere left to grow. Installing
numpy + scipy + pandas + scikit-learn needs well over 1.6GB of extraction and layer space,
so writes failed partway through and pip aborted. The misleading part is that every
diagnostic *inside* the container says there is plenty of room.

**Fix:** Deleted ~28GB of accumulated images (34.68GB → 6.65GB), dropping VM usage from
39GB to 13GB. Worth understanding why this works: the host's free space **does not
change** — a VHDX never shrinks on its own — but freeing blocks *inside* the VM lets new
builds reuse already-allocated space instead of forcing the host file to expand. The
symptom to watch for is this specific combination: pip exit code 2, a dependency set that
resolves fine on its own, and a host volume near full while the container claims hundreds
of free gigabytes.

---

### 15. A killed mid-deploy request leaves a permanently stuck "building" row

**Problem:** If the server process is killed (or crashes) while a deploy is in flight, the
`deployment` row it already wrote stays at `status: "building"` forever, with
`container_id`/`endpoint_url` null — indistinguishable from "still legitimately in
progress" unless someone checks `docker images` directly and confirms no image was ever
built for that version.

**Fix:** No code fix — this is an inherent consequence of forcibly interrupting a
synchronous in-flight request. The practical remedy is re-uploading the same model, which
creates a fresh version and a fresh deploy attempt from scratch.

---

### 16. The registry API leaked raw database columns to the frontend

**Problem:** `list_versions` in `server/registry.py` was a bare passthrough of whatever the
database returned, including internal columns like `artifact_sha256` that had no reason to
reach the browser.

**Fix:** Scoped the response to project only `{id, status, created_at}`, matching how
`list_models` already behaved. Found during the model-registry-dashboard's final review,
not by a test.

---

### 17. The frontend had no UI state for a torn-down deployment

**Problem:** `DeploymentCard` handled `live`, `failed`, and `building`, but not `stopped` —
an archived/replaced version's card rendered an empty section instead of explaining what
happened to it.

**Fix:** Added the `stopped` branch, explaining that a newer production version replaced
it and it no longer serves traffic.

---

### 18. A real user notebook had silent dependency-order bugs

**Problem:** The demo Titanic notebook (`demo/titanic.ipynb`) had a duplicate `Name`-column
drop that would `KeyError`, `Embarked`'s NaN-fill positioned *after* the `OrdinalEncoder`
call in visual cell order (`OrdinalEncoder` doesn't accept NaN — would `ValueError`), and
an abandoned one-hot-encoding detour for `Embarked` that had been started and undone
twice. None of this was visible from reading the cells in order — it only became clear by
reverse-engineering the notebook's *actual* historical execution order from its own
captured outputs.

**Fix:** Consolidated ~30 cells down to 5, in correct dependency order: imports → load +
full preprocessing (NaN-fills before encoding) → train/test split → training → upload.
Verified by running the consolidated preprocessing standalone against the real CSV: zero
NaNs, correct dtypes.
