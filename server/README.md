# Verity server

The FastAPI app and the agent orchestrator. Everything that runs on Verity's side of the wire
lives here; `agents/` (one directory up) holds the agent logic itself, imported at runtime via a
`sys.path` insert in [`main.py`](main.py).

## Layout

| Path | What it is |
|---|---|
| `main.py` | the app — three routes, CORS, dependency wiring |
| `orchestrator.py` | `build_artifact()` — sequences Hawkeye → Nat → Fury → Falcon |
| `telemetry.py` | `summarize()` — pure function over telemetry rows, no I/O |
| `storage/models/` | `S3BlobStore` (artifacts), `SupabaseMetadataStore` (rows) |
| `execution/` | `sandbox.py` (parent) and `runner.py` (child) — where untrusted `predict()` runs |
| `migrations/` | Alembic revisions, hand-written (`target_metadata` is `None` by design) |
| `tests/` | hand-written fakes, no `unittest.mock` anywhere |

## Routes

| Route | Purpose |
|---|---|
| `POST /ingest` | the whole pipeline — artifact in, registered and monitored version out |
| `POST /telemetry` | batched live traffic from `verity.monitor()` |
| `GET /models/{model_version_id}/telemetry` | summarised request count, error rate, percentiles |

## Configuration

`main.py` loads `server/.env`. Required:

```
SUPABASE_URL=
SUPABASE_KEY=
S3_BUCKET=
S3_REGION=                # bare region id, e.g. us-east-1 — not the console's display name
VERITY_LLM_API_KEY=       # shared by every agent
```

Optional: `VERITY_LLM_BASE_URL` (defaults to Groq), `S3_ENDPOINT_URL` (point at R2/MinIO
without touching code), and `HAWKEYE_LLM_MODEL` / `NAT_LLM_MODEL` to move one agent to a
different model without moving the others.

AWS credentials come from boto3's standard chain — env vars, shared config, or an instance
role. There is deliberately no fallback default: junk credentials fail in confusing ways,
whereas boto3's missing-credentials error names the problem.

## Running

```powershell
uv sync
uv run alembic upgrade head
uv run uvicorn main:app --reload --port 8000
```

## Tests

```powershell
uv run pytest -q
```

`pyproject.toml` sets `pythonpath = [".", ".."]` so tests import both `server/` modules and
`agents/`. Everything runs offline: no test reaches S3, Supabase, or an LLM.

`tests/test_sandbox.py` is the exception worth knowing about — it spawns a real subprocess with
a real estimator, and asserts that the child *cannot* read `SUPABASE_KEY`. It is a security
claim, so it is tested against the real mechanism rather than a fake.
