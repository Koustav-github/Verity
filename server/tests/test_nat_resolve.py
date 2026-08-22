import json

import pytest

from agents.brain2.nat.resolve import resolve


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


PLAN_JSON = json.dumps(
    {
        "task_type": "binary_classification",
        "metric_set": ["accuracy", "f1", "roc_auc"],
        "thresholds": [
            {"metric": "accuracy", "op": ">=", "value": 0.7},
            {"metric": "resource.latency_p95_ms", "op": "<=", "value": 100},
        ],
        "rationale": "Binary target with balanced classes.",
    }
)

MANIFEST = {"framework": "sklearn", "model_class": "LogisticRegression", "task_type": "classification"}
PROFILE = {"n_samples": 200, "n_features": 6, "n_classes": 2, "y_dtype_kind": "i"}


def test_resolve_parses_the_llm_response_into_an_eval_plan():
    client = FakeClient(PLAN_JSON)

    plan = resolve(
        manifest=MANIFEST,
        profile=PROFILE,
        atlas_section="ML",
        available_resource_metrics=["latency_p95_ms"],
        client=client,
    )

    assert plan == {
        "task_type": "binary_classification",
        "metric_set": ["accuracy", "f1", "roc_auc"],
        "thresholds": [
            {"metric": "accuracy", "op": ">=", "value": 0.7},
            {"metric": "resource.latency_p95_ms", "op": "<=", "value": 100.0},
        ],
        "rationale": "Binary target with balanced classes.",
    }
    call = client.chat.completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}


def test_resolve_puts_the_manifest_and_the_dataset_profile_in_front_of_the_model():
    client = FakeClient(PLAN_JSON)

    resolve(
        manifest=MANIFEST,
        profile=PROFILE,
        atlas_section="ML",
        available_resource_metrics=["latency_p95_ms"],
        client=client,
    )

    user_message = client.chat.completions.calls[0]["messages"][1]["content"]
    assert "LogisticRegression" in user_message
    assert '"n_classes": 2' in user_message


def test_resolve_offers_the_atlas_section_and_the_measurable_resource_metrics():
    client = FakeClient(PLAN_JSON)

    resolve(
        manifest=MANIFEST,
        profile=PROFILE,
        atlas_section="ML",
        available_resource_metrics=["latency_p95_ms", "peak_memory_mb"],
        client=client,
    )

    system_message = client.chat.completions.calls[0]["messages"][0]["content"]
    assert "ROC-AUC" in system_message  # the ML section of the Atlas
    assert "resource.latency_p95_ms" in system_message
    assert "resource.peak_memory_mb" in system_message


def test_resolve_rejects_a_plan_whose_threshold_names_a_metric_it_did_not_select():
    client = FakeClient(
        json.dumps(
            {
                "task_type": "binary_classification",
                "metric_set": ["accuracy"],
                "thresholds": [{"metric": "roc_auc", "op": ">=", "value": 0.8}],
            }
        )
    )

    with pytest.raises(ValueError):
        resolve(
            manifest=MANIFEST,
            profile=PROFILE,
            atlas_section="ML",
            available_resource_metrics=["latency_p95_ms"],
            client=client,
        )


def test_the_llm_client_reads_the_shared_verity_credentials(monkeypatch):
    from agents.brain2.nat import resolve as resolve_module

    monkeypatch.setenv("VERITY_LLM_API_KEY", "shared-key")
    monkeypatch.delenv("VERITY_LLM_BASE_URL", raising=False)
    captured = {}

    class FakeOpenAI:
        def __init__(self, api_key, base_url):
            captured["api_key"] = api_key
            captured["base_url"] = base_url

    monkeypatch.setattr(resolve_module, "_openai_class", lambda: FakeOpenAI)

    resolve_module._real_client()

    assert captured["api_key"] == "shared-key"
    assert captured["base_url"] == "https://api.groq.com/openai/v1"


def test_resolve_defaults_to_the_configured_groq_model(monkeypatch):
    monkeypatch.delenv("NAT_LLM_MODEL", raising=False)
    client = FakeClient(PLAN_JSON)

    resolve(
        manifest=MANIFEST,
        profile=PROFILE,
        atlas_section="ML",
        available_resource_metrics=["latency_p95_ms"],
        client=client,
    )

    # Asserts against the shared constant, not a literal — see the matching note in
    # test_hawkeye_identify.py. The test pins "the provider default is used", not which
    # model that happens to be this month.
    from agents.provider import DEFAULT_MODEL

    assert client.chat.completions.calls[0]["model"] == DEFAULT_MODEL


def test_resolve_rejects_a_plan_with_an_unusable_comparison_operator():
    client = FakeClient(
        json.dumps(
            {
                "task_type": "binary_classification",
                "metric_set": ["accuracy"],
                "thresholds": [{"metric": "accuracy", "op": "roughly", "value": 0.8}],
            }
        )
    )

    with pytest.raises(ValueError):
        resolve(
            manifest=MANIFEST,
            profile=PROFILE,
            atlas_section="ML",
            available_resource_metrics=["latency_p95_ms"],
            client=client,
        )
