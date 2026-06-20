# Verity — RAG Evaluation Pipeline (v1) Design

**Date:** 2026-06-18
**Status:** Approved design, pre-implementation
**Scope owner:** Koustav Manna

---

## 1. Vision & Product Framing

Verity is an evaluation layer that "sticks to" other people's AI applications and tells
them how well their LLM/RAG system is behaving — eventually as continuous, scheduled
(daily/weekly) eval reports.

> **Verity ships in two parts (see §10a):** a **local engine** installed *once per
> machine* (holds the models + runs inference at `localhost`), and a **thin SDK client**
> installed *per project/venv* (~few MB, just an HTTP client). The client makes API calls
> to the local engine — so the heavy models are never duplicated per virtual environment,
> and **data never leaves the user's machine.**
>
> It is **not** a website, app, dashboard, or *remote* hosted service. Verity produces
> **data** (metric scores + serializable reports). Storage, scheduling, visualization, and
> alerting are the **user's own infrastructure** or a third-party tool — **Verity never
> ships a UI.** Everything below respects this boundary.

The long-term product is **operational monitoring of RAG/LLM apps**, but Verity's slice
of it is only the eval engine + report data. The boxes marked `[user / 3rd-party]` are
**not** built by Verity:

```
[user] Their AI app ──(question, context, answer) traces──▶ [user] trace store / logs
                                                                  │  user schedules
                                                                  ▼  (cron, daily/weekly)
                                              ┌─────────────────────────────────────┐
                                              │ VERITY (SDK)                        │
                                              │  eval engine (RAG triad)  ← §4–§5   │
                                              │       │                             │
                                              │       ▼                             │
                                              │  serialized report (to_dict/JSON)   │
                                              └─────────────────────────────────────┘
                                                                  │
                                                                  ▼
                            [user / 3rd-party] their store · their dashboard · their alerts
                                              (e.g. Langfuse / Arize Phoenix / their DB)
```

The engine + the serialized report are what v1 builds. Trace capture, storage,
scheduling, dashboards, and alerting are the user's responsibility (or deferred
integrations — see §12).

### Layered mental model

The broad eval space (general LLM eval, RAG eval, GenAI eval, hallucination detection,
agentic eval, operational monitoring) is **not** six parallel products — it is layers
over one engine:

```
Layer 3  Operational / monitoring     ← run continuously, alert, track     (deferred)
Layer 2  Domain suites (bundles)      ← RAG-eval (v1), Agentic-eval, General-eval
Layer 1  Metrics                      ← faithfulness, answer-relevance, context-relevance
Layer 0  Primitives (detectors)       ← NLI, embeddings, claim-extract, LLM-judge
```

v1 ships **one wedge** (RAG eval) while keeping the Layer 0/1 boundary clean so the
other bundles are future configuration, not rewrites.

---

## 2. v1 Scope (committed)

**Ship:** a general **RAG evaluation pipeline** that scores other people's
`(question, context, answer)` triples on the **RAG triad**:

| Metric | What it judges | Evaluates | Inputs |
|---|---|---|---|
| **Context relevance** | Is the retrieved context relevant to the question? | the **retriever** | question + context |
| **Faithfulness** | Is the answer grounded in the context? (hallucination) | the **generator** | context + answer |
| **Answer relevance** | Does the answer address the question? | the **generator** | question + answer |

Together they triangulate *where* a RAG app fails — the diagnostic value is "which part
broke," not three bare numbers.

**Plus:** a thin **batch runner** — takes a file of triples → produces an aggregated,
serializable report (the daily/weekly story), consuming traces the user already logs.

### Never Verity's job (permanent non-goals — Verity stays an SDK)
- Hosted service / web app / dashboards / alerting UI / data storage. Verity emits data;
  the user (or a 3rd-party tool) stores, visualizes, and alerts.

### Deferred SDK features (future versions — see §12 roadmap)
- Auto-instrumentation / trace auto-capture helpers (v1 consumes existing logs instead)
- Agentic eval (tool-call correctness, trajectory eval) — separate engine
- General-purpose safety/toxicity/bias/PII/jailbreak metrics
- RAGAS library dependency (reimplementing the triad locally; see §3)

---

## 3. Core Principle: Local-First Cascade (no per-call LLM)

Every metric follows the same cost model — **local models by default, LLM only on
borderline cases (escalation), capped per call.**

We deliberately **do not use RAGAS's faithfulness implementation.** We keep RAGAS's
*definition* of faithfulness (decompose answer into atomic claims → verify each is
inferable from context → score = supported / total), but RAGAS performs **both** the
claim extraction and the verification with an LLM on **every** evaluation. We replace
both LLM steps with local models, reserving the LLM for hard cases only.

**Honest tradeoff:** local NLI is weaker than a strong LLM judge on subtle/compositional
claims. The escalation tier exists precisely to close that gap — hard cases route to the
LLM, the easy majority stay local and free.

`ragas` is therefore **dropped from `requirements.txt`** (it is LLM-first/heavy and would
drag the per-call LLM path back in).

---

## 4. Faithfulness Pipeline (the claim cascade)

The unit of analysis is the **claim**, not the sentence. Claims are atomic and
self-contained, so coreference and "meaning distributed across sentences" are resolved at
decomposition time rather than papered over at scoring time.

```
answer ─▶ [1] ClaimExtractor (local seq2seq) ─▶ [atomic claims]
                                                     │
context ─────────────┐                               ▼
                     └─▶ [2] NLI verify each claim vs FULL context
                             (chunk + max-pool entailment & contradiction)
                                                     │
                           per-claim: entail / neutral / contradict probs
                                                     │
                             ┌───────────────────────┼───────────────────┐
                         high entail            uncertain band        high contradict
                         = SUPPORTED          (only if LLM enabled)    = CONTRADICTED
                                                     ▼
                                      [3] LLM escalation (borderline only,
                                           capped top-k) → verdict + rationale
                                                     │
                                                     ▼
                           [4] Aggregate → faithfulness score + per-claim attribution
```

### The composition problem (and how this design solves it)

Naive sentence-vs-sentence NLI fails two ways:
- **Premise side:** a claim may only be supported by *several context sentences combined*
  (multi-hop). Pairwise sentence NLI misses this.
- **Response side:** splitting the answer breaks coreference / distributed meaning.

Three mechanisms address it:
1. **Claims are atomic & self-contained** (resolved at extraction) — fixes the response
   side.
2. **Premise = the whole context**, chunked with **max-pool** entailment, so support can
   come from anywhere in the context — fixes the premise side.
3. **Uncertain-band routing** sends genuinely ambiguous/compositional cases to the LLM
   rather than guessing.

---

## 5. Answer Relevance & Context Relevance

Both follow the **same cascade philosophy**: local approximation by default + shared LLM
escalation on borderline cases. (RAGAS does these with an LLM too; we keep them local.)

- **Answer relevance** — does the answer address the question? Local default: a
  cross-encoder / NLI / embedding relevance signal between `question` and `answer`.
  Borderline → LLM escalation.
- **Context relevance** — how much of the retrieved context is relevant to the question?
  Local default: per-context-chunk relevance (cross-encoder / NLI / embedding) vs the
  question, aggregated to a proportion-relevant score. Borderline → LLM escalation.

All three metrics share **one coherent cost model** and the **same escalation tier**.

---

## 6. Modules (small, single-purpose)

- `verity/claims/extractor.py` — `ClaimExtractor`: `decompose(answer) -> list[str]`.
  Wraps the local seq2seq model, lazy-loaded.
- `verity/detectors/nli.py` — `NLIDetector(Detector)`: implements existing
  `score(claim, context) -> float` (entailment) for ABC compat, plus
  `classify(claim, context) -> NLIScore` (3-way probs). Does chunk + max-pool.
- `verity/metrics/faithfulness.py` — orchestrates extract → verify → band-route →
  optional escalate → aggregate.
- `verity/metrics/answer_relevance.py` — answer-relevance metric (local + escalation).
- `verity/metrics/context_relevance.py` — context-relevance metric (local + escalation).
- `verity/pipeline.py` — `RagTriadPipeline`: runs the three metrics over a triple,
  assembles the result.
- `verity/escalation/llm_judge.py` — `LLMJudge`: optional, constructed only when an
  API key/model is configured; verifies a single borderline claim/relevance case against
  context, returns verdict + rationale. Shared by all metrics.
- `verity/batch/runner.py` — batch runner: file of triples → aggregated report.
- Keep `EmbeddingDetector` as an optional cheap **gate**, not on the critical path.

### Interface cleanup (existing code)
`base.Detector.score(self, *args, **kwargs)` is too loose — it documents no real
contract while the evaluator always calls `score(response, context)`. Tighten the
abstract signature to the real one (`score(self, response: str, context: str) -> float`)
as part of this work.

---

## 7. Schema Changes (`schemas.py`)

Add:
- `Verdict` enum: `SUPPORTED` / `CONTRADICTED` / `UNSUPPORTED` / `ESCALATED`.
- `ClaimVerdict` dataclass: claim text, entailment score, contradiction score, label,
  `escalated: bool`, LLM rationale (if escalated), best supporting context snippet.
- Extend `EvaluationResult` with:
  - `faithfulness: float`, `answer_relevance: float`, `context_relevance: float`
  - `claims: list[ClaimVerdict]`
  - keep `final_score`, `alerts`, `recommendations`, `metadata`, `timestamp`.

**Serialization (required, not optional).** Continuous monitoring needs serializable,
timestamped results to store and trend. Add `to_dict()` on `EvaluationResult` that handles
the `datetime` (`.isoformat()`) and nested `ClaimVerdict`s. (This resolves the earlier
"do we need a serializer?" question — yes, the monitoring vision requires it.)

Keep dataclasses (no Pydantic) for the internal schema; revisit Pydantic only if/when a
validated external API is added.

---

## 8. Parameter Surface (developer-facing)

**Principle: the default call needs only `question`, `context`, `answer`.** Everything
else is opt-in behind sane defaults.

### A. Per-call inputs
| Param | Required | Purpose |
|---|---|---|
| `answer` (`response`) | ✅ | The LLM output under test |
| `context` | ✅ | The grounding source |
| `question` (`prompt`) | ✅ for triad | Needed for answer/context relevance |
| `ground_truth` | optional | Reference answer, enables correctness signal later |

### B. Configuration knobs (set once on the evaluator; all defaulted)
- **Decomposition:** `claim_model` (large-fp16 default / base), `max_claims`
- **NLI verification:** `entailment_threshold` (~0.7), `contradiction_threshold` (~0.5),
  `chunk_size` / chunking strategy
- **LLM escalation (inert unless enabled):** `enable_llm_escalation` (default `False`),
  `llm_model` + API key, `uncertain_band` = `(0.4, 0.7)`, `max_escalations` (top-k cap)
- **Aggregation / gating:** `aggregation` (`mean` default | `min` | `fraction_supported`),
  `alert_threshold`, `use_embedding_gate`

### C. Output signals (how they examine the LLM)
- `faithfulness`, `answer_relevance`, `context_relevance`, `final_score`
- per-claim `ClaimVerdict[]` (claim, entail/contradict scores, label, escalated?,
  rationale, supporting snippet)
- derived metrics: `% claims supported`, `# contradicted`, `# unsupported`, `# escalated`
- `alerts` (e.g. `hallucination_risk`, `contradiction`), `recommendations`
- `metadata`: model versions, escalation count, timings

A + C are the contract everyone uses; B stays hidden behind defaults.

---

## 9. Aggregation, Escalation Policy, Error Handling

- **final_score** combines the three triad metrics (default: mean; configurable).
- **Faithfulness aggregation:** mean per-claim support; **any `CONTRADICTED` claim →
  `hallucination_risk` alert** regardless of the mean (contradiction is the strong
  signal).
- **Escalation policy:** a claim/case in the uncertain band escalates **only if LLM
  configured**, and escalations are **capped at top-k** most-uncertain per call (cost
  guard).
- **Graceful degradation:**
  - No LLM configured or LLM call fails → keep the local verdict, note it in metadata
    (never hard-fail).
  - No claims extracted / empty answer → neutral score + alert.
  - Long context → handled by chunking.

---

## 10. Models & Size Budget

The models live in the **local engine** (§10a), not in each project's venv. They are
downloaded **once per machine** (cached in `~/.cache/huggingface`, shared across all
projects) and **lazy-loaded** (only the models for the steps actually used).

Constraint: **no single model file ≥ 2 GB**.

- **Decomposer:** `propositionizer-wiki-flan-t5-large`, loaded **fp16 ≈ 1.5 GB**
  (single file < 2 GB). Fallback: base-size variant (~1 GB) at some quality cost.
- **NLI verifier:** DeBERTa-v3-base NLI cross-encoder ≈ **0.44 GB** (3-way
  entail/neutral/contradict — needed for the contradiction signal). Vectara HHEM is a
  smaller alternative but emits only one consistency score (loses contradiction), so not
  the default.
- Combined footprint ~2 GB on the machine, loaded on demand — **never per venv.**

---

## 10a. Distribution Architecture (local engine + thin client)

Verity is distributed as **two components** so the heavy models are installed once per
machine while each project keeps a tiny dependency. This is the Ollama / LM Studio model.

```
ONCE PER MACHINE                          PER PROJECT (per venv)
┌─ Verity Engine ──────────┐              ┌─ pip install verity ─┐
│  models + inference       │◀── HTTP ────│  thin client (~few MB)│
│  runs at localhost:PORT   │  localhost  │  HTTP client only      │
└──────────────────────────┘              └───────────────────────┘
     (install via pipx; auto-spawned)          (in every venv, tiny)
```

- **Verity Engine** — installed once per machine, holds the models (§10) and runs
  inference, exposing local endpoints (`POST /v1/evaluate`, `POST /v1/generate`) at
  `localhost:PORT`. Default install via **pipx**; **auto-spawned** by the client on first
  call (fallback: manual `verity serve`).
- **Verity SDK (thin client)** — `pip install verity` per venv, ~few MB, **no torch, no
  models** — just an HTTP client. For generation it pulls chunks from the vector DB
  *locally* and sends only the **chunk text** to the engine (DB keys never leave the
  machine).

Why this shape (it satisfies every constraint at once):

| Constraint | Resolution |
|---|---|
| Disk space / no per-venv torch | heavy install **once per machine** |
| Virtual-env ergonomics | thin client per venv (~few MB) |
| Privacy (data never leaves machine) | engine is **localhost** |
| Our infra cost | **zero** — no GPUs to run |

**Same client, optional remote endpoint.** The client always talks to an engine via an
address; `Evaluator()` defaults to `localhost`, while
`Evaluator(endpoint="https://…")` can point at a *remote* engine later (the open-core
upsell). Local-now and hosted-later are the same SDK, different endpoint — the backend is
pluggable for free.

**Version compatibility:** client and engine negotiate an API version on connect, so an
older pinned client doesn't break against a newer machine-wide engine.

---

## 11. Testing

- **Orchestration tests** with **injected fake extractor/NLI/judge** (deterministic, no
  model download) for band-routing, aggregation, escalation logic.
- **Composition fixture:** a claim supported only by two context sentences *combined* →
  asserts full-context max-pool catches it (the exact worry that drove the claim-level
  design).
- **Contradiction fixture** and a **clean-faithful fixture**.
- **Triad fixtures:** cases isolating each failure mode (bad retrieval → context
  relevance drops; hallucination → faithfulness drops; off-topic → answer relevance
  drops).
- **Real-model tests** behind a pytest marker so the default suite stays fast.
- **Batch runner test:** small triples file → expected aggregated report shape.

---

## 12. Roadmap / Deferred (architecture aims here; v1 does not build it)

Ordered roughly by expected sequence:

1. **Answer/context relevance hardening** — better local models, calibration.
2. **Monitoring helpers (SDK-side only — Verity still builds no UI/storage):**
   - Sampling helpers (e.g. 5% + all low-scoring) so users keep cost sane at production
     volume.
   - Serialized, timestamped report format suitable for users to store and trend (built on
     §7 serialization) — Verity emits it; the **user owns the store**.
   - **Exporters to existing observability backends** (Langfuse / Arize Phoenix / generic
     JSON) so users plug results into *their own* dashboards/alerting. Verity ships the
     exporter, never the dashboard.
3. **Auto-capture / instrumentation helpers** — optional library helpers to wrap users'
   LLM calls and collect traces (fast-follow; v1 consumes existing logs instead). Still a
   library, not a service.
4. **Additional Layer-1 metrics** (each is one metric on the same engine, not a new
   product): safety/toxicity, bias & fairness, PII/privacy leakage,
   prompt-injection/jailbreak robustness, instruction-following & format/schema
   adherence, summarization quality, multimodal, code-gen.
5. **Additional Layer-2 bundles:** General LLM eval suite; **Agentic eval** (tool-call
   correctness, multi-step trajectory eval — a *separate engine*, explicitly kept out of
   v1's design).
6. **Correctness vs `ground_truth`** — use the optional reference for answer-correctness
   scoring.
7. **Pydantic / validated external API** — revisit only when a public API surface is
   added.

---

## 13. Locked Decisions (summary)

1. **Wedge:** general RAG eval pipeline (RAG triad) over `(question, context, answer)`.
2. **Cost model:** local-first cascade, LLM only on borderline (escalation), capped.
3. **Faithfulness:** claim-level decomposition (not sentence-level) + local NLI vs full
   context (chunk + max-pool); RAGAS *definition* kept, RAGAS *implementation* rejected.
4. **Relevance metrics:** local approximation + shared LLM escalation.
5. **Models:** propositionizer fp16-large decomposer (~1.5 GB) + DeBERTa-v3 NLI
   (~0.44 GB), lazy-loaded, under the 2 GB/file cap.
6. **Serialization:** required (`to_dict()`, timestamped) — the bridge to monitoring.
7. **Dependencies:** drop `ragas`; reimplement the triad locally.
8. **Delivery:** Verity *is* an SDK — importable client + thin batch-runner entrypoint,
   consuming existing traces. No *remote* hosted service, storage, UI, or dashboards built
   by Verity (the user owns those).
9. **Distribution (§10a):** two components — a **local engine** installed once per machine
   (pipx, auto-spawned, holds the models, runs at `localhost`) + a **thin SDK client** per
   venv (~few MB, HTTP only). Heavy models never duplicate per venv; data never leaves the
   machine. Same client can target a remote engine later (`endpoint=`) as the open-core
   path.

