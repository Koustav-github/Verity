import httpx

from verity.client import assemble


def test_assemble_serializes_the_model_and_uploads_it():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"model_version_id": "mv_123", "status": "pending"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    model = {"kind": "fake-model", "weights": [1, 2, 3]}

    result = assemble(
        model,
        user_id="u_1",
        name="fraud-classifier",
        endpoint="http://verity-server.test",
        client=client,
    )

    assert result == {"model_version_id": "mv_123", "status": "pending"}
    assert captured["request"].url == "http://verity-server.test/ingest"
    assert b"u_1" in captured["request"].content
    assert b"fraud-classifier" in captured["request"].content


def _mock_client(captured):
    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"status": "staging"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_holdout_passed_to_assemble_travels_with_the_model():
    captured = {}

    assemble(
        {"kind": "fake-model"},
        user_id="u_1",
        name="fraud-classifier",
        endpoint="http://verity-server.test",
        client=_mock_client(captured),
        X_test=[[0.0], [1.0]],
        y_test=[0, 1],
    )

    content = captured["request"].content
    assert b'name="fixture"' in content
    assert b"labeled_holdout" in content


def test_a_prebuilt_fixture_can_be_passed_directly_for_kinds_without_a_shortcut():
    captured = {}
    fixture = (b"prebuilt-bytes", {"kind": "labeled_holdout", "sha256": "def456"})

    assemble(
        {"kind": "fake-model"},
        user_id="u_1",
        name="fraud-classifier",
        endpoint="http://verity-server.test",
        client=_mock_client(captured),
        fixture=fixture,
    )

    assert b"prebuilt-bytes" in captured["request"].content


def test_supplying_only_half_of_a_holdout_is_rejected_before_anything_is_uploaded():
    captured = {}

    try:
        assemble(
            {"kind": "fake-model"},
            user_id="u_1",
            name="fraud-classifier",
            endpoint="http://verity-server.test",
            client=_mock_client(captured),
            X_test=[[0.0], [1.0]],
        )
    except ValueError as exc:
        assert "y_test" in str(exc)
    else:
        raise AssertionError("expected a ValueError when y_test is missing")

    assert "request" not in captured


def test_extra_keyword_arguments_are_still_forwarded_as_args():
    captured = {}

    assemble(
        {"kind": "fake-model"},
        user_id="u_1",
        name="fraud-classifier",
        endpoint="http://verity-server.test",
        client=_mock_client(captured),
        framework_hint="sklearn",
    )

    assert b"framework_hint" in captured["request"].content
