import httpx

from verity.environment import capture
from verity.fixture import labeled_holdout
from verity.serialize import serialize
from verity.transport import DEFAULT_TIMEOUT_SECONDS, upload


def assemble(
    model,
    user_id: str,
    name: str,
    endpoint: str = "http://localhost:8000",
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    X_test=None,
    y_test=None,
    fixture: tuple | None = None,
    alert_email: str | None = None,
    **args,
) -> dict:
    """Upload a trained model for identification, evaluation, and monitoring.

    `name` identifies this model across uploads — re-uploading under the same name
    registers a new version of the same model; a new name starts a new one.

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
        name=name,
        args=args,
        endpoint=endpoint,
        client=client,
        fixture_payload=fixture_payload,
        fixture_descriptor=fixture_descriptor,
        environment=capture(),
        alert_email=alert_email,
        timeout=timeout,
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
