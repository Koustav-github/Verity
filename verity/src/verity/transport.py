import json
import httpx

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

) -> dict:
    client = client or httpx.Client(timeout=60.0)
    files = {"artifact": ("artifact", payload, "application/octet-stream")}
    data = {"user_id": user_id, "name": name, "sha256":sha256, "args": json.dumps(args)}

    # Sent only when there is something to evaluate — the server reads their absence
    # as "identify this version but don't judge it yet".
    if fixture_payload is not None:
        files["fixture"] = ("fixture", fixture_payload, "application/octet-stream")
        data["fixture_descriptor"] = json.dumps(fixture_descriptor)

    response = client.post(
        f"{endpoint}/ingest",
        files = files,
        data = data,

    )
    response.raise_for_status()
    return response.json()
