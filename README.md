# Verity

**An ML observability and evaluation platform.**

Deploy your model anywhere, add a few lines of SDK, and get latency, logs, drift
detection, hallucination/faithfulness estimates, and alerts — one product covering the
whole path from "is it up" to "is it still telling the truth."

> Full prior design detail (claim-decomposition faithfulness pipeline, schema, module
> layout, testing plan) isn't lost — it's in git history (`git log -- README.md`). This
> file is now organized around the version roadmap instead.

---

## Current status

The RAG-eval engine (interrogation pipeline + faithfulness / answer-relevance /
context-relevance metrics, local-first with optional LLM escalation) is the part already
built and tested (`verity/`). It's the seed of **V4–V5** below, not V1 — the roadmap is
sequenced by what a *new* user needs first (basic observability), which comes before the
evaluation depth that's already implemented. Reconciling "what exists" with "where it
lands in this sequence" is open work, not yet decided.

---

## Roadmap

Philosophy: don't compete with Arize / Datadog / Langfuse on day one. Build the narrowest
useful product first, then expand one dimension at a time — general observability → ML
monitoring → LLM observability → evaluation → RAG specialization → alerting → automation
— only reaching enterprise territory once every earlier slice has real users.

### V1 — Core Observability MVP (2–4 weeks)
**Goal:** "I can monitor any deployed ML model."

- User authentication
- Project / model registration
- Python SDK
- Prediction logging
- Latency tracking
- Error tracking
- Basic dashboard

```
Customer Model → SDK → FastAPI API → PostgreSQL → Dashboard
```

**Metrics:** request count · P50/P95 latency · error rate · throughput

**Deliverable:** *"Add 3 lines of code and get a dashboard."*

### V1.5 — Production-Ready MVP
- Docker support
- API keys
- Multi-tenancy
- Team management
- Rate limiting
- Log search
- Organization support · multiple models per organization

**Storage:** PostgreSQL + Redis

### V2 — ML Monitoring
**Goal:** "Know when my model is degrading."

- Data drift detection
- Feature drift
- Ground-truth ingestion
- Accuracy tracking
- Precision / recall / F1

**Storage:** PostgreSQL + ClickHouse

### V3 — LLM Observability
**Goal:** "Monitor GPT/RAG applications."

- Prompt logging
- Token tracking
- Cost estimation
- Conversation traces
- OpenTelemetry support

### V4 — Evaluation Engine
**Goal:** "Tell me if my model is performing poorly."

- LLM-as-a-judge
- Hallucination estimation
- Faithfulness
- Relevance
- Toxicity
- User feedback

```
Response → Judge Model → Score → Dashboard
```

*Already-built local-first cascade (non-LLM faithfulness/relevance via NLI + embeddings,
LLM escalation only on borderline cases) is the cost-efficient alternative to
judge-every-call sketched here.*

### V5 — RAG Support
- Context utilization
- Retrieval quality
- Chunk relevance
- Citation accuracy

```
RAG Score:    94%
Faithfulness: 97%
Chunk #12 causing failures.
```

*This is where Verity would attract AI startups specifically.*

### V6 — Alerting
- Slack / Discord / Email / PagerDuty

```
ALERT
Latency       > 200ms
Drift         > 0.8
Hallucination +4%
```

### V7 — Automation
**Goal:** "Don't just tell me the problem."

- Retraining recommendations
- Candidate model generation
- Shadow deployments
- A/B testing

```
Model v1: 92%
Model v2: 96%
Recommendation: Deploy v2.
```

### V8 — Enterprise
- SSO · RBAC · SOC2
- On-prem / private-cloud deployment
- Audit logs

```
Customer VPC → Your Agent → Telemetry → Dashboard
```

### V9 — AI Platform
Observability + evaluation + monitoring + alerting + retraining + RAG analytics +
enterprise support, in one product — direct competition with **Arize AI, WhyLabs,
Langfuse, Datadog**.

---

## Tech stack (target)

| Component | Tech |
|---|---|
| Frontend | Next.js |
| Backend | FastAPI |
| SDK | Python |
| Queue | Kafka |
| Metadata | PostgreSQL |
| Analytics | ClickHouse |
| Cache | Redis |
| Dashboard | React + ECharts |
| Traces | OpenTelemetry |
| Logs | Loki |
| Deployment | Docker |
| Scale | Kubernetes |

## Timeline (target)

| Month(s) | Milestone |
|---|---|
| 1 | V1 |
| 2 | V2 |
| 3 | V3 |
| 4 | V4 |
| 5 | V5 + V6 |
| 6 | V7 |
| 7–12 | Enterprise (V8) |

## If picking a single public MVP

> "Deploy your model anywhere. Install our SDK. Get latency, logs, drift detection,
> hallucination estimates, and alerts in under five minutes."

Focused enough to build in a few months; broad enough to grow into a full ML
observability product.
