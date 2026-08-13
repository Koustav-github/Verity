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
