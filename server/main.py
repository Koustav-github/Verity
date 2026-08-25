import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
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
from serving.sink import TelemetrySink
from telemetry import summarize
import registry

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

# Relational storage at V1 (Schemas.md moves telemetry to the analytics store at V3), so
# the read path caps what it pulls. `truncated` in the response says when this bit.
TELEMETRY_READ_LIMIT = 10_000

# Separate, much smaller cap: TELEMETRY_READ_LIMIT bounds what's pulled from the database
# for aggregation, but sending that many raw rows to a browser is a different question.
# `find_telemetry_events` already returns newest-first, so this is a plain slice.
TELEMETRY_TRACE_LIMIT = 50

@lru_cache
def get_blob_store():
    return S3BlobStore(bucket=os.environ["S3_BUCKET"], region=os.environ["S3_REGION"])


@lru_cache
def get_build_artifact():
    metadata_store = SupabaseMetadataStore()
    return partial(build_artifact, blob_store=get_blob_store(), metadata_store=metadata_store)

@lru_cache
def get_metadata_store():
    return SupabaseMetadataStore()


@lru_cache
def get_telemetry_sink():
    sink = TelemetrySink(metadata_store=get_metadata_store())
    sink.start()
    return sink


@lru_cache
def get_systemic_check():
    from agents.brain4.falcon.monitor import check_systemic

    def run(model_version_id):
        check_systemic(model_version_id=model_version_id, metadata_store=get_metadata_store())

    return run


@lru_cache
def get_quality_check():
    from agents.brain4.falcon.monitor import check_quality

    def run(model_version_id):
        check_quality(model_version_id=model_version_id, metadata_store=get_metadata_store())

    return run


@lru_cache
def get_predict_transport():
    import httpx

    return httpx.Client(timeout=30.0)


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
    environment: str | None = Form(None),
    alert_email: str | None = Form(None),
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
        # Absent for uploads from an SDK older than api-fication. The image then falls
        # back to a default Python and an empty pin set, which is worse but not fatal.
        environment=json.loads(environment) if environment else None,
        alert_email=alert_email,
    )

@app.post("/telemetry")
async def ingest_telemetry(
    batch: TelemetryBatch,
    metadata_store=Depends(get_metadata_store),
    systemic_check=Depends(get_systemic_check),
):
    written = metadata_store.save_telemetry_events(
        # mode="json" so `occurred_at` (a datetime) serializes back to an ISO string —
        # the Supabase client can't JSON-serialize a raw datetime object.
        events=[event.model_dump(mode="json") for event in batch.events]
    )
    for model_version_id in {event.model_version_id for event in batch.events}:
        try:
            systemic_check(model_version_id)
        except Exception:  # noqa: BLE001 - detection failing must not fail ingestion
            pass
    return {"written": written}

@app.get("/models/{model_version_id}/telemetry")
async def read_telemetry(
    model_version_id: str,
    hours: float = 24.0,
    metadata_store=Depends(get_metadata_store),
):
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    events = metadata_store.find_telemetry_events(
        model_version_id=model_version_id, since=since, limit=TELEMETRY_READ_LIMIT
    )
    config = metadata_store.find_monitoring_config(model_version_id=model_version_id)
    summary = summarize(
        events=events,
        eval_reference=(config or {}).get("eval_reference"),
        limit=TELEMETRY_READ_LIMIT,
    )
    recent_events = [
        {
            "occurred_at": event.get("occurred_at"),
            "latency_ms": event.get("latency_ms"),
            "status": event.get("status"),
            "error_type": event.get("error_type"),
            "prediction_id": event.get("prediction_id"),
        }
        for event in events[:TELEMETRY_TRACE_LIMIT]
    ]
    return {
        "model_version_id": model_version_id,
        "hours": hours,
        **summary,
        "recent_events": recent_events,
    }


@app.get("/models/{model_version_id}/alerts")
async def read_alerts(
    model_version_id: str,
    metadata_store=Depends(get_metadata_store),
):
    alerts = metadata_store.find_alert_events(model_version_id=model_version_id)
    return {"model_version_id": model_version_id, "alerts": alerts}


@app.get("/users/{user_id}/models")
async def list_models(user_id: str, metadata_store=Depends(get_metadata_store)):
    return {
        "user_id": user_id,
        "models": registry.list_models(user_id=user_id, metadata_store=metadata_store),
    }


@app.get("/models/{model_id}/versions")
async def list_versions(model_id: str, metadata_store=Depends(get_metadata_store)):
    return {
        "model_id": model_id,
        "versions": registry.list_versions(model_id=model_id, metadata_store=metadata_store),
    }


@app.get("/model_versions/{model_version_id}")
async def read_version_detail(
    model_version_id: str, metadata_store=Depends(get_metadata_store)
):
    detail = registry.get_version_detail(
        model_version_id=model_version_id, metadata_store=metadata_store
    )
    if detail is None:
        raise HTTPException(404, f"no model version {model_version_id!r}")
    return detail


@app.get("/model_versions/{model_version_id}/download-urls")
async def read_download_urls(
    model_version_id: str,
    metadata_store=Depends(get_metadata_store),
    blob_store=Depends(get_blob_store),
):
    urls = registry.get_download_urls(
        model_version_id=model_version_id, metadata_store=metadata_store, blob_store=blob_store
    )
    if urls is None:
        raise HTTPException(404, f"no model version {model_version_id!r}")
    return urls


@app.post("/users/{user_id}/models/{name}/predict")
async def predict(
    user_id: str,
    name: str,
    body: dict,
    metadata_store=Depends(get_metadata_store),
    sink=Depends(get_telemetry_sink),
    transport=Depends(get_predict_transport),
):
    # user_id is in the path because model names are unique per user, not globally
    # (Schemas.md: UNIQUE (user_id, name)), and no auth exists yet to supply it
    # implicitly. At V1.5 the API key identifies the org and this collapses to
    # /models/{name}/predict — the extra segment is temporary and load-bearing.
    version = metadata_store.find_production_version_by_name(user_id=user_id, name=name)
    if version is None:
        raise HTTPException(
            404, f"no production version of {name!r} for user {user_id!r}"
        )

    deployment = metadata_store.find_live_deployment(model_version_id=version["id"])
    if deployment is None:
        # Deliberately distinct from the 404 above: "promoted but not deployed" and
        # "no such model" are opposite problems with opposite fixes, and one
        # undifferentiated 404 would conflate them.
        raise HTTPException(
            404,
            f"{name!r} is promoted but not deployed — check its deployment row for the reason",
        )

    # Minted here, not read back from the DB insert: TelemetrySink queues writes, so the
    # database-assigned bigint id does not exist yet when this response is returned. A
    # customer reporting a delayed outcome needs something to correlate against right now.
    prediction_id = f"pred_{uuid.uuid4().hex}"

    started = time.perf_counter()
    try:
        response = transport.post(
            f"{deployment['endpoint_url']}/predict", json=body, timeout=30.0
        )
        prediction = response.json()
    except Exception as exc:  # noqa: BLE001 - the container is out of our process
        _record(sink, version["id"], started, body, None, exc, prediction_id)
        raise HTTPException(
            502, f"model container did not answer: {type(exc).__name__}"
        )

    _record(sink, version["id"], started, body, prediction, None, prediction_id)
    if isinstance(prediction, dict):
        return {**prediction, "prediction_id": prediction_id}
    return {"prediction": prediction, "prediction_id": prediction_id}


def _record(sink, model_version_id, started, inputs, prediction, exc, prediction_id):
    """Telemetry can never be the reason a prediction fails — Falcon's governing rule,
    applied to the proxy. This is also the first place Verity sees production *inputs*,
    which is what makes drift detection possible at all."""
    try:
        sink.record(
            {
                "model_version_id": model_version_id,
                "prediction_id": prediction_id,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "status": "ok" if exc is None else "error",
                "latency_ms": (time.perf_counter() - started) * 1000,
                "inputs": inputs,
                "prediction": prediction,
                "error_type": type(exc).__name__ if exc is not None else None,
            }
        )
    except Exception:  # noqa: BLE001
        pass


class Outcome(BaseModel):
    index: int
    actual: object


class OutcomeBatch(BaseModel):
    # Mirrors TelemetryBatch's cap: a bound on the blast radius of a single request.
    outcomes: list[Outcome] = Field(default=..., max_length=1000)


@app.post("/predictions/{prediction_id}/outcomes")
async def report_outcomes(
    prediction_id: str,
    batch: OutcomeBatch,
    metadata_store=Depends(get_metadata_store),
    quality_check=Depends(get_quality_check),
):
    event = metadata_store.find_telemetry_event_by_prediction_id(prediction_id=prediction_id)
    if event is None:
        raise HTTPException(404, f"no prediction found for {prediction_id!r}")

    predictions = (event.get("prediction") or {}).get("predictions") or []
    # Validated as a full pass over batch.outcomes before any save_label_event call, so
    # a batch containing one bad index writes nothing rather than partially recording.
    for outcome in batch.outcomes:
        if outcome.index < 0 or outcome.index >= len(predictions):
            raise HTTPException(
                422,
                f"index {outcome.index} is outside this prediction's batch of {len(predictions)}",
            )

    for outcome in batch.outcomes:
        metadata_store.save_label_event(
            telemetry_event_id=event["id"], instance_index=outcome.index,
            actual={"y": outcome.actual},
        )

    try:
        quality_check(event["model_version_id"])
    except Exception:  # noqa: BLE001 - detection failing must not fail label reporting
        pass

    return {"recorded": len(batch.outcomes)}
