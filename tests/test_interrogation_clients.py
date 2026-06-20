from types import SimpleNamespace

from verity.interrogation.clients import OpenAICompatibleClient


def _fake_openai(content, capture):
    """Mimic the openai SDK shape: client.chat.completions.create(...)."""

    def create(**kwargs):
        capture.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )

    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )


def test_complete_returns_message_content():
    client = OpenAICompatibleClient(model="m", _client=_fake_openai("hello", {}))
    assert client.complete("hi") == "hello"


def test_complete_sends_prompt_as_user_message_with_model():
    capture = {}
    client = OpenAICompatibleClient(model="gpt-x", _client=_fake_openai("ok", capture))
    client.complete("PROMPT_TEXT")
    assert capture["model"] == "gpt-x"
    assert capture["messages"] == [{"role": "user", "content": "PROMPT_TEXT"}]
