import json

import pytest

from agents.brain1.hawkeye.identify import identify


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content):
        self.message = FakeMessage(content)


class FakeResponse:
    def __init__(self, content):
        self.choices = [FakeChoice(content)]


class FakeCompletions:
    def __init__(self, response_content):
        self.response_content = response_content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse(self.response_content)


class FakeChat:
    def __init__(self, response_content):
        self.completions = FakeCompletions(response_content)


class FakeClient:
    def __init__(self, response_content):
        self.chat = FakeChat(response_content)


def test_identify_parses_the_llm_response_into_a_manifest_dict():
    response_json = json.dumps(
        {
            "framework": "sklearn",
            "detected_via": "class name LogisticRegression",
            "model_class": "LogisticRegression",
            "hyperparameters": {"C": 0.5, "max_iter": 200},
            "task_type": "classification",
        }
    )
    client = FakeClient(response_json)

    manifest = identify(object(), client=client)

    assert manifest == {
        "framework": "sklearn",
        "detected_via": "class name LogisticRegression",
        "model_class": "LogisticRegression",
        "hyperparameters": {"C": 0.5, "max_iter": 200},
        "task_type": "classification",
    }
    assert len(client.chat.completions.calls) == 1
    call = client.chat.completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}


def test_identify_raises_a_clear_error_when_the_llm_omits_a_required_field():
    response_json = json.dumps({"detected_via": "something"})  # missing "framework"
    client = FakeClient(response_json)

    with pytest.raises(ValueError):
        identify(object(), client=client)


def test_identify_ignores_unexpected_extra_fields_from_the_llm():
    response_json = json.dumps(
        {
            "framework": "sklearn",
            "model_class": "LogisticRegression",
            "some_field_the_llm_made_up": "ignored",
        }
    )
    client = FakeClient(response_json)

    manifest = identify(object(), client=client)

    assert "some_field_the_llm_made_up" not in manifest
    assert manifest["framework"] == "sklearn"


def test_identify_reports_the_task_type_that_selects_the_atlas_row_downstream():
    response_json = json.dumps(
        {
            "framework": "sklearn",
            "model_class": "LogisticRegression",
            "task_type": "classification",
        }
    )
    client = FakeClient(response_json)

    manifest = identify(object(), client=client)

    assert manifest["task_type"] == "classification"


def test_identify_leaves_task_type_unset_rather_than_guessing_it():
    response_json = json.dumps({"framework": "onnx", "model_class": "unknown"})
    client = FakeClient(response_json)

    manifest = identify(object(), client=client)

    assert manifest["task_type"] is None


def test_the_llm_client_reads_the_shared_verity_credentials(monkeypatch):
    from agents.brain1.hawkeye import identify as identify_module

    monkeypatch.setenv("VERITY_LLM_API_KEY", "shared-key")
    monkeypatch.setenv("VERITY_LLM_BASE_URL", "https://example.test/v1/")
    captured = {}

    class FakeOpenAI:
        def __init__(self, api_key, base_url):
            captured["api_key"] = api_key
            captured["base_url"] = base_url

    monkeypatch.setattr(identify_module, "_openai_class", lambda: FakeOpenAI)

    identify_module._real_client()

    assert captured == {
        "api_key": "shared-key",
        "base_url": "https://example.test/v1/",
    }


def test_identify_defaults_to_the_configured_groq_model(monkeypatch):
    monkeypatch.delenv("HAWKEYE_LLM_MODEL", raising=False)
    client = FakeClient(json.dumps({"framework": "sklearn"}))

    identify(object(), client=client)

    assert client.chat.completions.calls[0]["model"] == "llama-3.3-70b-versatile"


def test_the_llm_client_falls_back_to_groq_when_no_base_url_is_set(monkeypatch):
    from agents.brain1.hawkeye import identify as identify_module

    monkeypatch.setenv("VERITY_LLM_API_KEY", "shared-key")
    monkeypatch.delenv("VERITY_LLM_BASE_URL", raising=False)
    captured = {}

    class FakeOpenAI:
        def __init__(self, api_key, base_url):
            captured["base_url"] = base_url

    monkeypatch.setattr(identify_module, "_openai_class", lambda: FakeOpenAI)

    identify_module._real_client()

    assert captured["base_url"] == "https://api.groq.com/openai/v1"
