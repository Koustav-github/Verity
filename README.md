# Verity

**An AI automation tool that fast-tracks model deployment and monitoring.**

Generate one API key for your organization, install the SDK, and Verity automates
everything between a trained model and a monitored production service: it evaluates the
model at staging, gates promotion on the results, registers the version that passes, and
switches on monitoring the moment it serves live traffic. Your model deploys wherever you
want — any cloud, any vendor, on-prem — and an agent reports back.

Every one of those stages already exists as a separate product: a registry here, an eval
harness there, an observability dashboard somewhere else, and a pile of glue code holding
them together. Verity's bet is that the glue *is* the product — the handoffs between
stages are mechanical, and automating them is the difference between a week of wiring and
five minutes.

> Full prior design detail (claim-decomposition faithfulness pipeline, schema, module
> layout, testing plan) isn't lost — it's in git history (`git log -- README.md`). This
> file is now organized around the version roadmap instead.

---

## Current status

The RAG-eval engine (interrogation pipeline + faithfulness / answer-relevance /
context-relevance metrics, local-first with optional LLM escalation) is the part already
built and tested (`verity/`). It is **Nat's scoring engine for V4**, not the starting
point — the roadmap is sequenced by the agentic loop, and the loop has to work end-to-end
for one simple model class before it earns the right to handle RAG.

So `verity/` sits ahead of where the build starts. That is deliberate, not a mistake: it
is a finished component waiting for the pipeline that will call it, and V1 deliberately
uses a simpler eval mechanism (labeled holdout) to prove the loop with fewer moving parts.

Nothing of the agentic pipeline itself exists yet. `server/` is a stub, `client/` is an
untouched scaffold, and `demo/` holds a containerized sklearn model that serves as the
first real test subject for V1.

---

## Roadmap

Philosophy: **the loop is the product.** V1 is the thinnest complete pass through all four
agents for one model class on one platform — not a dashboard with automation bolted on
later. A perfect single agent proves nothing; a shallow end-to-end loop proves the whole
thesis. Every version after V1 widens exactly one axis:

| Axis | Expansion order |
|---|---|
| Model class | ML → DL → LLM/RAG → RL/Agentic |
| Platform | local process → container → orchestrator |
| Agent depth | detect → decide → act → remediate |

Full table schemas and the MCP connection contract live in [`Schemas.md`](Schemas.md);
each version below names only the tables and connections it *adds*.

### V1 — The Loop (ML, local)
**Goal:** "One sklearn model goes from artifact to monitored without being touched by hand."

All four agents, shallow, single path. No multi-tenancy, no dashboard, no alerting.

| Agent | Scope at V1 |
|---|---|
| Hawkeye | sklearn / ONNX artifact → `model_manifest.json`; flags unrecoverable semantics |
| Nat | Atlas lookup → metric set; labeled-holdout eval; pass/fail gate |
| Fury | content-hash version identity; staging → production on gate pass |
| Falcon | in-process SDK; request count, latency percentiles, error rate |

```
artifact → Hawkeye → manifest → Nat → eval_report → Fury → registered version
                                                              ↓
                                              Falcon → monitoring config → telemetry
```

- **MCP:** `filesystem` (read artifact) · `python-exec` (run eval in a sandbox)
- **Stores:** relational (metadata)
- **Tables:** `model` · `model_version` · `manifest` · `eval_run` · `agent_run` · `telemetry_event`
- **Components:** agent orchestrator · manifest generator · metric resolver · eval runner · registry service · ingestion endpoint · SDK

**Deliverable:** point Verity at a `.joblib` and get back a registered, monitored model.

### V1.5 — Multi-tenant foundation
**Goal:** "More than one developer, more than one model."

- Organization / user / API key issuance — one org-scoped key
- Per-org model isolation; free-tier quota (2 monitored models)
- Rate limiting; agent-run audit trail

- **MCP:** `secrets` (credential broker — agents never hold raw customer credentials)
- **Stores:** relational + cache
- **Tables:** `organization` · `user` · `api_key` · `quota` · `audit_log`
- **Components:** auth service · key issuance · rate limiter · tenant scoping middleware

### V2 — MCP Fabric
**Goal:** "Integrations are configuration, not code."

Where the agentic system stops hardcoding tools and starts discovering them. Every
external system Verity touches — registries, object stores, eval backends, observability
platforms — sits behind an MCP server rather than a bespoke client.

- Per-org MCP server registry
- Connection lifecycle: register → capability discovery → health check → revoke
- Tool allow-listing and scope enforcement per connection
- First external servers wired: MLflow (registry), object store (artifacts)

- **MCP:** the connection framework itself · `mlflow` · `object-store`
- **Stores:** relational + cache
- **Tables:** `mcp_connection` · `mcp_capability` · `mcp_call_log`
- **Components:** MCP client pool · capability discovery · scope enforcer · call auditor

### V3 — Model class: DL
**Goal:** "Deep learning models, including ones too large to host."

- Hawkeye: computation-graph introspection, multi-input models
- Nat: GPU-aware eval dispatch
- Fury: pointer-custody — registry records artifact *location*, doesn't host GB-scale weights
- Falcon: GPU utilization, GPU memory, resource telemetry

- **MCP:** + `object-store` (weights) · `compute` (GPU eval dispatch)
- **Stores:** + columnar/analytics (telemetry volume outgrows relational here)
- **Tables:** `artifact_pointer` · `resource_sample`
- **Components:** graph introspector · large-artifact handler · resource collector

### V4 — Model class: LLM + RAG
**Goal:** "Prompt-and-retrieval systems, evaluated on truthfulness rather than accuracy."

Where the already-built `verity/` engine lands — see [Current status](#current-status).

- Hawkeye: prompt schema, retriever config, tool bindings (no fixed tensor contract)
- Nat: interrogation pipeline; faithfulness / answer-relevance / context-relevance;
  local-first cascade with LLM escalation only on borderline cases
- Fury: version identity = prompt hash + index snapshot + base model id, not weights
- Falcon: token cost, prompt drift, retrieval quality, conversation traces

- **MCP:** + vector store · LLM provider · embedding provider
- **Tables:** `prompt_version` · `index_snapshot` · `eval_example` · `trace_span`
- **Components:** interrogation harness · judge escalation · trace ingestion

### V5 — Model class: RL + Agentic
**Goal:** "Systems with no held-out test set."

The eval mechanism genuinely forks here — policies and multi-step agents have no labeled
holdout. Nat gains a **second engine**: rollout-based rather than dataset-based, measuring
reward, success rate, and trajectory correctness over N episodes.

- Environment / task spec becomes a first-class registered object (a policy version is
  only meaningful against a pinned environment version)
- Trajectory-level telemetry: which tools fired, how many steps, where it looped

- **MCP:** + environment / simulator servers
- **Tables:** `environment` · `episode` · `trajectory` · `tool_invocation`
- **Components:** rollout harness · episode store · trajectory tracer

### V6 — Platform reach
**Goal:** "Instrument a model without the developer touching their container."

- Sidecar instrumentation (no customer code change)
- Kubernetes operator / DaemonSet deployment
- Cross-OS agent packaging (Windows · macOS · Linux)

- **Tables:** `deployment` · `agent_heartbeat` · `platform_target`
- **Components:** sidecar collector · k8s operator · cross-OS installer

### V7 — Alerting + remediation
**Goal:** "Don't just tell me the problem."

- Threshold and anomaly alerts; notification channels
- Retraining recommendations, candidate generation
- Shadow deployments, A/B comparison

- **MCP:** + notification servers · CI/CD servers (trigger retraining)
- **Tables:** `alert_rule` · `alert_event` · `recommendation` · `experiment`
- **Components:** rule engine · notifier · candidate evaluator

### V8 — Enterprise
**Goal:** "Deployable inside someone else's compliance boundary."

- SSO · RBAC · SOC2
- On-prem / VPC deployment; agent phones home, data doesn't leave
- Full audit trail over agent decisions

- **Tables:** `role` · `permission` · `sso_config` · `agent_decision_log`
- **Components:** identity federation · policy engine · on-prem bundle

### V9 — Convergence
All model classes × all platforms × the full loop, with remediation closing back into
evaluation. The agentic pipeline as one product — direct competition with **Arize AI,
WhyLabs, Langfuse, Datadog**, distinguished by doing the *setup*, not just the watching.

---

## Components (target)

Deliberately unpinned — these are the *slots* the architecture needs, not library choices.
Each gets decided when the version that needs it is built.

| Slot | Responsibility | First needed |
|---|---|---|
| Agent orchestrator | Sequences Hawkeye → Nat → Fury → Falcon; owns retries and state | V1 |
| MCP client pool | Holds connections, enforces scopes, audits every tool call | V1 (fixed set) → V2 (dynamic) |
| Manifest generator | Artifact introspection → `model_manifest.json` | V1 |
| Metric resolver | Task type → metric set, via the Atlas taxonomy | V1 |
| Eval runner | Executes eval jobs next to the model; returns raw outputs | V1 |
| Scoring engine | Turns raw outputs into scores server-side | V1 |
| Registry service | Version identity, lineage, promotion gating | V1 |
| Ingestion endpoint | Receives production telemetry from deployed agents | V1 |
| SDK / agent | Runs in the customer's environment; eval jobs + telemetry | V1 |
| API server | Public HTTP surface | V1 |
| Relational store | Orgs, models, versions, manifests, eval reports, configs | V1 |
| Cache | Sessions, rate-limit counters, capability cache | V1.5 |
| Auth service | Org/user/API-key issuance and verification | V1.5 |
| Secrets broker | Holds customer credentials so agents never do | V1.5 |
| Object store | Model artifacts, test sets, eval payloads | V2 |
| Analytics store | High-volume telemetry, time-series aggregation | V3 |
| Job queue | Async eval dispatch, long-running agent work | V3 |
| Trace collector | Span-level traces for LLM/RAG/agentic systems | V4 |
| Rollout harness | Episode execution for RL and agentic eval | V5 |
| Sidecar collector | Instrumentation without customer code changes | V6 |
| Rule engine | Alert thresholds, anomaly detection | V7 |
| Dashboard UI | Visual surface over everything above | V7 |
| Policy engine | RBAC, on-prem policy enforcement | V8 |

## If picking a single public MVP

> "One API key. Deploy your model anywhere. Verity evaluates it at staging, promotes it
> when it passes, and monitors it in production — automatically, in under five minutes."

Focused enough to build in a few months; broad enough to grow into a full deployment-and-
monitoring automation platform.

---

# Vision — the agentic pipeline

Verity covers the same ground as MLflow — register, evaluate, log, observe — with one
difference that changes everything about the experience: **the developer wires none of
it.** An agentic system does the whole path, from "here is a trained model" to "it is
registered, gated, deployed, and monitored."

Today that path is a week of incidental expertise. Not the model — the *scaffolding*
around it: export formats and their converter quirks, serving code, feature-order
contracts that fail silently when wrong, container builds, platform-correct dependency
pinning, choosing which metrics even apply to your task, and picking thresholds that mean
something. None of it is your problem domain. All of it stands between a trained model and
a monitored one.

Verity's claim is that this scaffolding is mechanical enough to be generated, and that
generating it is the product.

## The four agents

**1. Identification** — reads the model artifact and infers what it is: framework, task
type, input schema, dtypes, output semantics. Everything downstream keys off this, because
"what kind of model is this" determines which evals are meaningful and which metrics are
worth watching.

**2. Evaluation** — selects metrics appropriate to the inferred task, assembles or requests
a test set, runs the model against it at staging, and scores the result. The task → metric
mapping is not invented per run; it is a lookup against the taxonomy in
[`Metrics.md`](Metrics.md), which is the agent's decision table. Classification resolves to
accuracy / precision / recall / ROC-AUC; RAG resolves to context precision / faithfulness /
answer relevance; a forecasting model resolves to MAPE / SMAPE / MASE.

**3. Registry** — records the version, its lineage, its eval scores, and the exact artifact
that produced them, then gates promotion on whether the scores clear their thresholds. A
model that passes moves to production; one that fails stays put with a report naming the
metric that stopped it. Promotion is a consequence of evidence, not a button someone
remembers to press.

**4. Observability** — configures monitoring for the now-live model: which metrics to
collect, what the healthy baseline looks like (derived from the training distribution
rather than guessed), where thresholds sit, and which alerts fire. Monitoring switches on
with the deployment instead of being a follow-up task that never gets done.

```
Trained model
     |
Identification  →  framework · task · schema
     |
Evaluation      →  task-appropriate metrics · staging run · scores
     |
Registry        →  version · lineage · gate → promote or hold
     |
Observability   →  metric set · baselines · thresholds · alerts
     |
Monitored production model
```

## The design principle: agents configure, pipelines execute

"AI agent" and "deployment pipeline" pull against each other. A deployment pipeline earns
trust by being deterministic — the same input produces the same behavior every time. An
agent that reasons afresh on every run, and might promote to production differently on
Tuesday than it did on Monday, is precisely what nobody wants near production.

So the agents run at **configuration time, not run time.** An agent inspects the model once
and *generates* the pipeline — the serving layer, the eval config, the metric set, the
thresholds. That output is concrete: reviewable, diffable, version-controlled. Execution
afterward is deterministic, exactly like any CI system.

This is what makes the automation adoptable. Nobody has to accept "the agent decided to
promote it." They accept a configuration they read and approved, which then ran the same
way it always does. The agent removes the week of wiring; it does not remove the
developer's authority over what ships.

## The MCP layer

The agents do not hardcode their integrations. Every external system the pipeline touches —
model registries, object stores, eval backends, observability platforms, notification
channels — sits behind an **MCP server**, and the agents are MCP clients.

This is what keeps the agentic layer honest. An agent's power is bounded by the connections
its org has registered and the tools those connections allow; it cannot reach anything
nobody granted it. Every tool call is logged before its result is used, so a promotion
decision is reconstructible from rows rather than by re-running an agent and hoping it
reasons the same way twice.

The connection contract, scope model, and per-agent tool matrix are specified in
[`Schemas.md`](Schemas.md#mcp-connection-contract).

## Where this leaves the roadmap

The V1–V9 sequence above is written against this vision: V1 is the thinnest complete pass
through all four agents, and every later version widens exactly one axis. Automation is not
a capability added after the dashboards — it is the reason the product exists, so it is
present from the first version rather than arriving at V7.
