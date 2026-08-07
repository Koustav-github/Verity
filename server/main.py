import json
import os
import sys
from fastapi import Depends, FastAPI, File, Form, UploadFile
from functools import lru_cache, partial
from dotenv import load_dotenv
from pathlib import Path
from typing import Callable

# agents/ lives one directory up (repo root), alongside server/ and verity/ —
# needed at runtime, not just under pytest (whose pythonpath config doesn't
# apply to a real `uvicorn main:app` process).
sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator import build_artifact
from storage.models.s3 import S3BlobStore
from storage.models.supabase import SupabaseMetadataStore

load_dotenv(Path(__file__).parent / ".env")

app = FastAPI()

@lru_cache
def get_build_artifact():
    blob_store = S3BlobStore(
        bucket=os.environ["S3_BUCKET"],
        region=os.environ["S3_REGION"],
    )
    metadata_store = SupabaseMetadataStore()
    return partial(build_artifact, blob_store=blob_store, metadata_store=metadata_store)

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