import json
import httpx

# /ingest is synchronous all the way through: identify, evaluate in a sandbox, register,
# and — since api-fication — build a container image. A cold build (no cached pip layer
# for this dependency set) genuinely takes minutes, and 60s silently turned that into a
# client-side ReadTimeout on a request the server went on to complete successfully.
# Warm builds finish in seconds; this ceiling only matters the first time.
DEFAULT_TIMEOUT_SECONDS = 600.0

def upload(
        payload: bytes,
        sha256: str,
        user_id: str,
        name: str,
        args: dict,
        endpoint: str,
        client: httpx.Client | None = None,
        fixture_payload: bytes | None = None,
        fixture_descriptor: dict | None = None,
        environment: dict | None = None,
        alert_email: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,

) -> dict:
    client = client or httpx.Client(timeout=timeout)
    files = {"artifact": ("artifact", payload, "application/octet-stream")}
    data = {"user_id": user_id, "name": name, "sha256":sha256, "args": json.dumps(args)}

    # Sent only when there is something to evaluate — the server reads their absence
    # as "identify this version but don't judge it yet".
    if fixture_payload is not None:
        files["fixture"] = ("fixture", fixture_payload, "application/octet-stream")
        data["fixture_descriptor"] = json.dumps(fixture_descriptor)

    # Sent whenever the caller captured one. The server needs it to build a serving
    # image against the versions that wrote the pickle, not the ones it happens to run.
    if environment is not None:
        data["environment"] = json.dumps(environment)

    # Where a human is notified when Falcon detects something off. Optional, and only
    # meaningful on the upload that first creates the model — later versions of an
    # existing model don't need to repeat it.
    if alert_email is not None:
        data["alert_email"] = alert_email

    response = client.post(
        f"{endpoint}/ingest",
        files = files,
        data = data,

    )
    response.raise_for_status()
    return response.json()
