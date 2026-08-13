import httpx

from verity.transport import upload


def test_upload_posts_artifact_bytes_and_metadata_to_ingest_endpoint():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"model_version_id": "mv_123", "status": "pending"})

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = upload(
        payload=b"fake-artifact-bytes",
        sha256="abc123",
        user_id="u_1",
        name="fraud-classifier",
        args={"framework_hint": "sklearn"},
        endpoint="http://verity-server.test",
        client=client,
    )

    request = captured["request"]
    assert request.url == "http://verity-server.test/ingest"
    assert request.method == "POST"
    assert b"fake-artifact-bytes" in request.content
    assert b"abc123" in request.content
    assert b"u_1" in request.content
    assert b"fraud-classifier" in request.content
    assert result == {"model_version_id": "mv_123", "status": "pending"}


def test_upload_sends_the_fixture_as_a_second_file_with_its_descriptor():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"status": "staging"})

    client = httpx.Client(transport=httpx.MockTransport(handler))

    upload(
        payload=b"fake-artifact-bytes",
        sha256="abc123",
        user_id="u_1",
        name="fraud-classifier",
        args={},
        endpoint="http://verity-server.test",
        client=client,
        fixture_payload=b"fake-fixture-bytes",
        fixture_descriptor={"kind": "labeled_holdout", "sha256": "def456"},
    )

    content = captured["request"].content
    assert b"fake-fixture-bytes" in content
    assert b'name="fixture"' in content
    assert b"labeled_holdout" in content


def test_upload_omits_the_fixture_parts_entirely_when_there_is_no_fixture():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"status": "pending"})

    client = httpx.Client(transport=httpx.MockTransport(handler))

    upload(
        payload=b"fake-artifact-bytes",
        sha256="abc123",
        user_id="u_1",
        name="fraud-classifier",
        args={},
        endpoint="http://verity-server.test",
        client=client,
    )

    content = captured["request"].content
    assert b'name="fixture"' not in content
    assert b"fixture_descriptor" not in content
