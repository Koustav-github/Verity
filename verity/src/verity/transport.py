import json
import httpx

def upload(
        payload: bytes,
        sha256: str,
        user_id: str,
        args: dict,
        endpoint: str,
        client: httpx.Client | None = None,

) -> dict:
    client = client or httpx.Client(timeout=60.0)
    response = client.post(
        f"{endpoint}/ingest",
        files = {"artifact": ("artifact", payload, "application/octet-stream")},
        data = {"user_id": user_id, "sha256":sha256, "args": json.dumps(args)},

    )
    response.raise_for_status()
    return response.json()