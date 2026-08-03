import httpx

from verity.fixture import labeled_holdout
from verity.serialize import serialize
from verity.transport import upload


def assemble(
    model,
    user_id: str,
    endpoint: str = "http://localhost:8000",
    client: httpx.Client | None = None,
    X_test=None,
    y_test=None,
    fixture: tuple | None = None,
    **args,
) -> dict:
    """Upload a trained model for identification, evaluation, and monitoring.

    Pass X_test/y_test to have the model evaluated and gated in the same call. Pass
    `fixture` instead — a (payload, descriptor) pair from verity.fixture — for kinds
    that have no keyword shortcut. With neither, the model is identified and stored
    but left unevaluated.
    """
    fixture_payload, fixture_descriptor = _resolve_fixture(X_test, y_test, fixture)
    payload, sha256 = serialize(model)
    return upload(
        payload=payload,
        sha256=sha256,
        user_id=user_id,
        args=args,
        endpoint=endpoint,
        client=client,
        fixture_payload=fixture_payload,
        fixture_descriptor=fixture_descriptor,
    )


def _resolve_fixture(X_test, y_test, fixture):
    if fixture is not None:
        return fixture
    if X_test is None and y_test is None:
        return None, None
    # Rejected here rather than server-side: a half-specified holdout is a mistake in
    # the caller's script, and finding out after the upload helps nobody.
    if X_test is None or y_test is None:
        raise ValueError("X_test and y_test must be given together, or neither")
    return labeled_holdout(X_test, y_test)
