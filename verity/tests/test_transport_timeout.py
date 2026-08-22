import httpx

from verity.transport import upload


def test_default_client_has_a_generous_timeout_for_slow_llm_backed_requests(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)

    upload(
        payload=b"bytes",
        sha256="abc",
        user_id="u1",
        name="test-model",
        args={},
        endpoint="http://example.test",
    )

    assert captured["kwargs"]["timeout"] >= 30


def test_the_default_timeout_survives_a_cold_container_build():
    from verity.transport import DEFAULT_TIMEOUT_SECONDS

    # A cold image build inside a synchronous /ingest genuinely takes minutes. The old
    # 60s default turned that into a client-side ReadTimeout on a request the server
    # went on to complete — the client reported failure for work that succeeded.
    assert DEFAULT_TIMEOUT_SECONDS >= 300
