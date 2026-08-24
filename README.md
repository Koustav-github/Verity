# Verity

**An AI automation tool that fast-tracks model deployment and monitoring.**

Generate one API key for your organization, install the SDK, and Verity automates
everything between a trained model and a monitored production service: it evaluates the
model, gates promotion on the results, registers the version that passes, stands it up
behind a callable endpoint, and switches on monitoring the moment it serves live traffic.

Verity serves the promoted version itself — one container per version, with the request
schema derived from the model rather than hand-written. If you'd rather host the model in
your own environment, the SDK wraps it and reports telemetry back: the same monitoring,
without the serving.

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

All four V1 agents are built. Hawkeye (identification), Nat (evaluation), Fury (registry),
and Falcon (observability) are implemented and tested — 255 server tests + 48 SDK tests, TDD
throughout, plus container serving on top of them. One call, `verity.assemble(model, name=..., user_id=..., X_test=..., y_test=...)`,
runs the whole chain: identify the framework and task, evaluate against a labeled holdout with
quality and systemic metrics (latency, memory, CPU, GPU) gating equally, promote straight to
`production` on a pass while archiving whatever version it replaces, and switch monitoring on
for the promoted version. `verity.monitor(model, model_version_id=...)` then reports live
traffic back from wherever the customer serves the model.

All four are verified live end-to-end against real infrastructure — AWS S3, Supabase, and
Groq — not just against unit tests. Falcon's quality check is included in that: 30+
deliberately-wrong outcomes reported live against a promoted model produced a real `quality`
alert. Its systemic check's core comparison math is proven by its unit suite instead
(`test_falcon_detect.py`, `test_falcon_monitor.py`) rather than a live 30-minute soak — the two
windows it compares are 15 real minutes apart and not independently controllable through any
API, so exercising it for real without fabricating timestamps costs wall-clock time out of
proportion to what it would prove beyond what those tests already do.

**Scope, deliberately narrow: tabular / classical ML** — scikit-learn, XGBoost, LightGBM. That
one class gets finished end to end, api-fication and drift metrics included, before a second is
started. Deep learning, LLM/RAG, and RL each widen the model-class axis later (V3–V5 below) and
each brings its own metric set; none of them is being half-built now.

**api-fication is built.** A version promoted to `production` is now built into its own
container image — from the training environment the SDK captured at upload, so a pickle is
never loaded against a different scikit-learn than wrote it — started, health-checked, and
served behind `POST /users/{user_id}/models/{name}/predict`. The request schema is derived from
the model's own introspected surface (`n_features`, `feature_names`, `classes`), never guessed.
Promoting a new version tears down the container it replaced.

Deploy is deliberately **non-fatal**: it runs after the version is already `production`, so a
build failure leaves a `failed` deployment row and a null `deployment` in the response rather
than reporting a promotion that genuinely succeeded as a failure. Verified live by pointing the
server at an unreachable Docker daemon.

The proxy is also the first point at which Verity has ever seen production *inputs* —
`telemetry_event.inputs` and `.prediction` were created with Falcon and stayed null until now.
That is what makes drift detection possible, and why serving came before the drift metrics
rather than after.

**Container serving can now target AWS ECS Fargate, not just local Docker.**
`VERITY_CONTAINER_RUNTIME=docker|fargate` selects the runtime in `deploy.py`, defaulting to
`docker` so nothing about existing local dev changes unless explicitly opted in. Fargate
exists specifically to get CloudWatch Logs for free — the task definition's `awslogs` driver
wires this up with no application code — and a serving process that survives independently of
wherever the local server happens to be running. Task lifecycle matches Docker's: a version's
task runs until a new promotion under the same name replaces it; no autoscaling, no
idle-shutdown, a deliberate scope decision, not a gap found afterward. Live-verified against a
real AWS account: a real image built and pushed to ECR, a real Fargate task reaching `RUNNING`
with a real public IP, answering `/predict` both through Verity's own proxy and by hitting that
IP directly. See `docs/architecture.md` §9.9 for the full account, including a real bug this
work found and fixed — `docker-py`'s image push does not raise on failure by default, so an
early version of the ECR push step silently produced no image while reporting success.

**Falcon now detects and notifies, not just configures and exposes.** Two checks run inline,
triggered by the events that already move data — no scheduler, no poller. `check_systemic`
compares a version's last 15 minutes of live traffic against the 15 minutes before that
(`error_rate`, then `latency_p95_ms`) every time a telemetry batch flushes, whether Verity's own
proxy wrote it or the SDK reported it from a customer-hosted model. `check_quality`
recomputes the exact metrics and thresholds that gated promotion once `POST
/predictions/{prediction_id}/outcomes` — a new endpoint for reporting delayed ground truth
against a `prediction_id` the proxy now mints on every response — has accumulated 30 labels for
a version. Either check firing writes an `alert_event` row first (the source of truth for
whether an alert fired at all) and best-effort emails whoever the model owner registered as
`alert_email`; a dead or unconfigured mail path never erases the row or fails the request that
triggered it. `GET /models/{id}/alerts` reads them back. Still deliberately not solved: one
global threshold for every model, no detection of a customer who simply never reports labels,
and no retry on a failed email — all named, not hidden, in `docs/architecture.md` §8.7-§8.11.

Still not solved: comparing against `eval_reference` itself (§8.3) — the two checks above compare
live traffic against its own immediately preceding history instead, which sidesteps the
cold-sandbox-baseline honesty problem rather than resolving it.

`verity/`'s original interrogation-pipeline eval engine (faithfulness / answer-relevance /
context-relevance) was deleted before this build started; `verity/` is now the client SDK
only, and an active part of the build rather than a finished component waiting on it.

`client/` has a minimal intake form for exercising the loop by hand — upload a model
(bundled demo included, no Python required) and watch all four agents' real output render,
including a live-traffic panel for a monitored version. It's a test harness, not a dashboard:
you can see the model you just uploaded, not browse ones you uploaded before. `demo/serve/`
holds the hand-written Titanic ONNX service used as the first real V1 test subject — the
template api-fication generates from, kept as the reference for what "correct" looks like.

## Repo layout

| Path | Contents |
|---|---|
| `agents/` | the four brains — `brain1/hawkeye`, `brain2/nat`, `brain3/fury`, `brain4/falcon` |
| `server/` | FastAPI app, orchestrator, storage adapters, sandbox, Alembic migrations |
| `verity/` | the client SDK (`assemble`, `monitor`, CLI) |
| `client/` | Next.js intake form for exercising the loop by hand |
| `demo/serve/` | the hand-written reference service |
| `docs/` | [architecture](docs/architecture.md) · [Schemas](docs/Schemas.md) · [Metrics](docs/Metrics.md) · [progression](docs/progression.md) |
| `docs/superpowers/` | dated design specs and implementation plans, one pair per agent |
| `docs/reference/` | study notes on other systems ([MLflow](docs/reference/mlflow.md)) |

[`docs/architecture.md`](docs/architecture.md) is the deep read — how the pieces actually fit
together. [`docs/progression.md`](docs/progression.md) is the build log: what shipped, in what
order, and what each step got wrong on the way.

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

Full table schemas and the MCP connection contract live in [`Schemas.md`](docs/Schemas.md);
each version below names only the tables and connections it *adds*.

### V1 — The Loop (tabular ML)
**Goal:** "One sklearn model goes from artifact to monitored production endpoint without being
touched by hand."

All four agents, shallow, single path. Tabular / classical ML only — scikit-learn, XGBoost,
LightGBM. No multi-tenancy, no dashboard.

| Stage | Scope at V1 |
|---|---|
| Hawkeye | sklearn / XGBoost / LightGBM / ONNX artifact → manifest; introspects the input surface; flags unrecoverable semantics |
| Nat | Atlas lookup → metric set; labeled-holdout eval; quality and systemic metrics gate equally |
| Fury | `name` + content-hash version identity; a passing verdict promotes straight to production |
| *serving* | one container image per promoted version; request schema generated from the introspected surface; Verity proxies `/predict` |
| Falcon | request count, latency percentiles, error rate — recorded by the proxy when Verity serves, by the SDK when the customer does; compares adjacent 15-minute windows and delayed-label accuracy against promotion-time thresholds, notifying in-app and by best-effort email on a breach |

Serving is not a fifth agent. It is generated pipeline, triggered by promotion — see
[agents configure, pipelines execute](#the-design-principle-agents-configure-pipelines-execute).

```
artifact → Hawkeye → manifest → Nat → eval_run → Fury → registered version
                                                             │ promoted
                                              ┌──────────────┴──────────────┐
                                              ▼                             ▼
                                     container image             Falcon → monitoring config
                                              │                             ▲
                                              ▼                             │
                                      live endpoint ─────── telemetry ──────┘
```

- **MCP:** `filesystem` (read artifact) · `python-exec` (run eval in a sandbox)
- **Stores:** relational (metadata) · object store (artifacts)
- **Tables:** `model` · `model_version` · `manifest` · `eval_run` · `monitoring_config` · `deployment` · `telemetry_event` · `label_event` · `alert_event` · `agent_run`
- **Components:** agent orchestrator · manifest generator · metric resolver · eval runner · scoring engine · registry service · image builder · container runtime · inference proxy · ingestion endpoint · SDK

**Deliverable:** hand Verity a trained estimator and get back a registered, served, monitored
model — with a URL you can POST to.

> **Axis note, stated rather than hidden:** the axis table at the top of this roadmap puts
> `local process → container` on the platform axis, implying containers arrive after V1. api-fication moves V1's *serving* onto
> containers early, because running customer pickles in Verity's own process was never an
> acceptable resting state. The orchestrator step (k8s, sidecars, cross-OS agents) is genuinely
> still V6; only the container step comes forward.

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

- **Tables:** `agent_heartbeat` · `platform_target` (`deployment` already exists — api-fication
  created it at V1; V6 adds the columns that describe *which* target a deployment runs on)
- **Components:** sidecar collector · k8s operator · cross-OS installer

### V7 — Alerting + remediation
**Goal:** "Don't just tell me the problem."

Threshold and anomaly alerting moved forward into V1 with Falcon's agentic-observability
feature — see [Current status](#current-status) and `docs/architecture.md` §8.7-§8.11.
What's left here:

- Per-model configurable thresholds (today's `RELATIVE_INCREASE_THRESHOLD` is one global
  constant, not tuned per task); notification channels beyond email
- Retraining recommendations, candidate generation
- Shadow deployments, A/B comparison

- **MCP:** + notification servers · CI/CD servers (trigger retraining)
- **Tables:** `alert_rule` · `recommendation` · `experiment` (`alert_event` itself moved to V1)
- **Components:** rule engine · candidate evaluator

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
| Image builder | Renders a Dockerfile + pinned requirements from the manifest; builds one image per promoted version | V1 |
| Container runtime | Starts, health-checks, and stops serving containers — local Docker or AWS Fargate behind the same interface, selected by `VERITY_CONTAINER_RUNTIME` | V1 |
| Inference proxy | Public `/predict` surface; routes by model name to the live container and records every request as telemetry | V1 |
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

> "One API key. Hand Verity a trained model and get back a monitored endpoint — evaluated,
> gated, served, and watched, automatically, in under five minutes."

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
a test set, runs the model against it in an isolated sandbox, and scores the result. The
systemic side of that run — latency, memory, CPU — is measured rather than chosen, and gates
promotion exactly as the quality metrics do. The task → metric
mapping is not invented per run; it is a lookup against the taxonomy in
[`Metrics.md`](docs/Metrics.md), which is the agent's decision table. Classification resolves to
accuracy / precision / recall / ROC-AUC; RAG resolves to context precision / faithfulness /
answer relevance; a forecasting model resolves to MAPE / SMAPE / MASE.

**3. Registry** — records the version, its lineage, its eval scores, and the exact artifact
that produced them, then gates promotion on whether the scores clear their thresholds. A
model that passes moves to production; one that fails stays put with a report naming the
metric that stopped it. Promotion is a consequence of evidence, not a button someone
remembers to press.

**4. Observability** — configures monitoring for the now-live model: which metrics to collect,
what reference numbers to carry, and eventually where thresholds sit and which alerts fire. The
reference is lifted from the eval run that justified promotion — measured evidence that already
exists, not a guess — and is tagged with what kind of measurement it was, so nothing downstream
mistakes a cold-sandbox figure for a production baseline. Monitoring switches on with the
deployment instead of being a follow-up task that never gets done.

Between registry and observability sits **serving**, which is not an agent: promotion generates
a container and an API for the version that passed, and the generated config is reviewable like
any other pipeline output.

```
Trained model
     |
Identification  →  framework · task · input schema
     |
Evaluation      →  task-appropriate metrics · eval run · scores
     |
Registry        →  version · lineage · gate → promote or hold
     |
Serving         →  generated API · container per version · live endpoint
     |
Observability   →  metric set · references · thresholds · alerts
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
[`Schemas.md`](docs/Schemas.md#mcp-connection-contract).

## Where this leaves the roadmap

The V1–V9 sequence above is written against this vision: V1 is the thinnest complete pass
through all four agents, and every later version widens exactly one axis. Automation is not
a capability added after the dashboards — it is the reason the product exists, so it is
present from the first version rather than arriving at V7.
