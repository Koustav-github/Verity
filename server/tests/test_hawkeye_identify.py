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
        }
    )
    client = FakeClient(response_json)

    manifest = identify(object(), client=client)

    assert manifest == {
        "framework": "sklearn",
        "detected_via": "class name LogisticRegression",
        "model_class": "LogisticRegression",
        "hyperparameters": {"C": 0.5, "max_iter": 200},
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
