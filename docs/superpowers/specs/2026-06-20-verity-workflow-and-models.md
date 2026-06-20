# Verity — Workflow & Models

**Date:** 2026-06-20
**Status:** Workflow reference (companion to the v1 design spec)
**Scope:** the end-to-end workflow only, and the model used at each step.

---

## End-to-End Workflow

```
PHASE 1 — Interrogation (test-set generation)        [Verity]
   vector DB ─▶ connect ─▶ sample chunks ─▶ generate Q+A ─▶ quality filter ─▶ test set

PHASE 2 — Answering                                  [USER]
   user's RAG answers the generated questions → (retrieved_context, answer)

PHASE 3 — Evaluation (metric engine)                 [Verity]
   triples ─▶ faithfulness + answer relevance + context relevance ─▶ scores + report
```

`Phase 1` output feeds `Phase 2` (run by the user), whose output feeds `Phase 3`.
Verity owns Phase 1 and Phase 3; the user owns Phase 2.

---

## PHASE 1 — Interrogation (`verity/interrogation/`)

Generates an evaluation test set from the user's vector database.

| Step | What it does | Model / tool |
|---|---|---|
| 1. Connect | Pull chunk **text** (+ vectors) from the user's vector DB using user-supplied keys | LangChain vectorstore connector (no ML model) |
| 2. Sample | Select a representative subset of chunks | `diverse` = k-means over chunk embeddings; `random` = none. Embeddings: `all-MiniLM-L6-v2` |
| 3. Generate | chunk → `(question, reference_answer)` | **Local default:** `potsawee/t5-large-generation-squad-QuestionAnswer` (~1.5 GB fp16). **Optional upgrade:** user-configured LLM |
| 4. Quality filter | Dedup near-duplicate questions; drop degenerates; verify the answer is grounded in its chunk (answerability) | Dedup: `all-MiniLM-L6-v2`. Answerability: NLI model (shared, see Phase 3) |
| 5. Test set | Emit `(question, reference_context, reference_answer)` → JSONL | none (serialization) |

**Output:** `TestSet` (JSONL) — stable, inspectable, reusable.

---

## PHASE 2 — Answering (user-owned, not built by Verity)

The user runs **their own RAG** over the generated questions and collects:
`(question, retrieved_context, answer)`.

Captured via either:
- explicit collection in user code, or
- the **LangChain `VerityCallback`** (auto-captures question / retrieved context / answer).

No Verity model runs here.

---

## PHASE 3 — Evaluation (`verity/metrics/`)

Scores each `(question, context, answer)` triple on the RAG triad. All metrics are
local-first with an optional, shared LLM escalation tier for borderline cases.

### Faithfulness (answer ↔ context)
| Step | What it does | Model |
|---|---|---|
| 1. Decompose | answer → atomic claims | `propositionizer-wiki-flan-t5-large` (~1.5 GB fp16) |
| 2. Verify | each claim vs full context (chunk + max-pool); entail / contradict | DeBERTa-v3-base NLI cross-encoder (~0.44 GB) |
| 3. Escalate | borderline claims only (capped) | optional LLM (user-configured) |

### Answer relevance (answer ↔ question)
| Step | What it does | Model |
|---|---|---|
| Score | relevance of answer to the question; flag noncommittal answers | cross-encoder / NLI / `all-MiniLM-L6-v2` |
| Escalate | borderline only | optional LLM |

### Context relevance (context ↔ question)
| Step | What it does | Model |
|---|---|---|
| Score | per-chunk relevance to the question → proportion relevant | cross-encoder / NLI / `all-MiniLM-L6-v2` |
| Escalate | borderline only | optional LLM |

### Optional cheap gate
| Step | Model |
|---|---|
| Embedding pre-filter for obvious pass/fail | `all-MiniLM-L6-v2` |

---

## Consolidated Model List

| Purpose | Model | Size | Local? |
|---|---|---|---|
| Question + answer generation | `potsawee/t5-large-generation-squad-QuestionAnswer` | ~1.5 GB fp16 | ✅ |
| Claim decomposition (faithfulness) | `propositionizer-wiki-flan-t5-large` | ~1.5 GB fp16 | ✅ |
| NLI verification + answerability filter | DeBERTa-v3-base NLI cross-encoder | ~0.44 GB | ✅ |
| Embeddings (sampling, dedup, relevance, gate) | `all-MiniLM-L6-v2` | ~0.09 GB | ✅ |
| Escalation / optional richer generation | user-configured LLM (e.g. GPT / Claude) | — | ❌ (opt-in) |

All models are **lazy-loaded** (downloaded only when their step runs) and each file stays
**under 2 GB**.

## Distribution (where the models actually run)

The models do **not** live in each project's venv. Verity ships in two parts:

- **Local engine** — installed **once per machine** (via pipx, auto-spawned), holds the
  models above and runs inference at `localhost:PORT`. Models cached once in
  `~/.cache/huggingface`, shared across every project.
- **Thin SDK client** — `pip install verity` **per venv** (~few MB, HTTP client only, no
  torch/models). Calls the local engine. For generation it pulls vector-DB chunks locally
  and sends only chunk text to the engine — DB keys never leave the machine.

So the heavy models are installed **once per machine, never per virtual environment**, and
**data never leaves the user's machine**. The same client can target a remote engine later
via `endpoint=` (open-core path). See design spec §10a.
