import json
import os
import sys
from fastapi import Depends, FastAPI, Form, UploadFile
from functools import lru_cache, partial
from dotenv import load_dotenv
from pathlib import Path
from typing import Callable

# agents/ lives one directory up (repo root), alongside server/ and verity/ —
# needed at runtime, not just under pytest (whose pythonpath config doesn't
# apply to a real `uvicorn main:app` process).
sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator import build_artifact
from storage.models.seaweedfs import SeaweedFSStore
from storage.models.supabase import SupabaseMetadataStore

load_dotenv(Path(__file__).parent / ".env")

app = FastAPI()

@lru_cache
def get_build_artifact():
    blob_store = SeaweedFSStore(
        bucket=os.environ["SEAWEEDFS_BUCKET"],
        endpoint_url=os.environ["SEAWEEDFS_ENDPOINT_URL"],
    )
    metadata_store = SupabaseMetadataStore()
    return partial(build_artifact, blob_store=blob_store, metadata_store=metadata_store)

@app.post("/ingest")
async def ingest(
    artifact: UploadFile,
    user_id: str = Form(...),
    sha256: str = Form(...),
    args: str = Form("{}"),
    build_artifact_fn: Callable = Depends(get_build_artifact),
):
    payload = await artifact.read()
    return build_artifact_fn(
        payload = payload,
        sha256 = sha256,
        user_id=user_id,
        args=json.loads(args),
    )