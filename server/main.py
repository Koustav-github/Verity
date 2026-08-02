import json
from fastapi import Depends, FastAPI, Form, UploadFile
from typing import Callable

from orchestrator import build_artifact

app = FastAPI()

def get_build_artifact():
    return build_artifact

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