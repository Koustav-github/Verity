import json
import os
import sys
from datetime import datetime
from fastapi import Depends, FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from functools import lru_cache, partial
from dotenv import load_dotenv
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field
from typing import Callable, Literal

# agents/ lives one directory up (repo root), alongside server/ and verity/ —
# needed at runtime, not just under pytest (whose pythonpath config doesn't
# apply to a real `uvicorn main:app` process).
sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator import build_artifact
from storage.models.s3 import S3BlobStore
from storage.models.supabase import SupabaseMetadataStore

load_dotenv(Path(__file__).parent / ".env")

app = FastAPI()

# Dev-only, matching the local Next.js frontend in client/ — no auth exists yet (V1.5),
# so this is scoped to known local origins rather than "*".
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@lru_cache
def get_build_artifact():
    blob_store = S3BlobStore(
        bucket=os.environ["S3_BUCKET"],
        region=os.environ["S3_REGION"],
    )
    metadata_store = SupabaseMetadataStore()
    return partial(build_artifact, blob_store=blob_store, metadata_store=metadata_store)

@lru_cache
def get_metadata_store():
    return SupabaseMetadataStore()


class TelemetryEvent(BaseModel):
    # `model_version_id` collides with pydantic v2's protected `model_` namespace, which
    # would emit a warning and can shadow BaseModel internals. Opting out of the
    # protection is correct here: the field name is fixed by the Schemas.md column name.
    model_config = ConfigDict(protected_namespaces=())

    model_version_id: str
    occurred_at: datetime
    status: Literal["ok", "error", "timeout"]
    latency_ms: float | None = None
    error_type: str | None = None


class TelemetryBatch(BaseModel):
    # The SDK batches at 100 events per request; 1000 gives 10x headroom while bounding
    # the blast radius of a single request (memory exhaustion, an enormous DB insert).
    events: list[TelemetryEvent] = Field(default=..., max_length=1000)


@app.post("/ingest")
async def ingest(
    artifact: UploadFile,
    user_id: str = Form(...),
    name: str = Form(...),
    sha256: str = Form(...),
    args: str = Form("{}"),
    fixture: UploadFile | None = File(None),
    fixture_descriptor: str | None = Form(None),
    build_artifact_fn: Callable = Depends(get_build_artifact),
):
    payload = await artifact.read()
    # No fixture means no eval: the version is stored and identified, and stays
    # `pending` until something gives Nat data to judge it against.
    fixture_payload = await fixture.read() if fixture is not None else None
    return build_artifact_fn(
        payload = payload,
        sha256 = sha256,
        user_id=user_id,
        name=name,
        args=json.loads(args),
        fixture_payload=fixture_payload,
        fixture_descriptor=json.loads(fixture_descriptor) if fixture_descriptor else None,
    )

@app.post("/telemetry")
async def ingest_telemetry(
    batch: TelemetryBatch,
    metadata_store=Depends(get_metadata_store),
):
    written = metadata_store.save_telemetry_events(
        # mode="json" so `occurred_at` (a datetime) serializes back to an ISO string —
        # the Supabase client can't JSON-serialize a raw datetime object.
        events=[event.model_dump(mode="json") for event in batch.events]
    )
    return {"written": written}