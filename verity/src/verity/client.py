import httpx

from verity.serialize import serialize
from verity.transport import upload


def assemble(
    model,
    user_id: str,
    endpoint: str = "http://localhost:8000",
    client: httpx.Client | None = None,
    **args,
) -> dict:
    payload, sha256 = serialize(model)
    return upload(
        payload=payload,
        sha256=sha256,
        user_id=user_id,
        args=args,
        endpoint=endpoint,
        client=client,
    )
