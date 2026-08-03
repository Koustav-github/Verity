import cloudpickle
import pytest
from sklearn.linear_model import LogisticRegression

from agents.brain2.nat.score import RESOURCE_METRICS
from execution.sandbox import SandboxError, execute


def a_fitted_classifier():
    return LogisticRegression().fit([[0.0], [1.0], [2.0], [3.0]], [0, 0, 1, 1])


def test_execute_returns_predictions_from_the_model_in_a_child_process():
    payload = cloudpickle.dumps(a_fitted_classifier())

    result = execute(model_payload=payload, X=[[0.0], [3.0]])

    assert list(result["y_pred"]) == [0, 1]


def test_execute_returns_class_probabilities_when_the_model_exposes_them():
    payload = cloudpickle.dumps(a_fitted_classifier())

    result = execute(model_payload=payload, X=[[0.0], [3.0]])

    assert result["y_proba"] is not None
    assert len(result["y_proba"][0]) == 2


def test_execute_returns_no_probabilities_for_a_model_without_predict_proba():
    class LabelsOnly:
        def predict(self, X):
            return [0 for _ in X]

    payload = cloudpickle.dumps(LabelsOnly())

    result = execute(model_payload=payload, X=[[0.0], [3.0]])

    assert result["y_proba"] is None


def test_execute_measures_the_full_systemic_sample_the_scoring_engine_expects():
    payload = cloudpickle.dumps(a_fitted_classifier())

    result = execute(model_payload=payload, X=[[0.0], [1.0], [2.0], [3.0]])

    assert sorted(result["resource"]) == sorted(RESOURCE_METRICS)
    assert result["resource"]["latency_p50_ms"] > 0
    assert result["resource"]["peak_memory_mb"] > 0
    assert result["resource"]["throughput_rps"] > 0
    assert result["resource"]["cpu_time_s"] >= 0


def test_the_sandbox_cannot_read_the_servers_credentials(monkeypatch):
    monkeypatch.setenv("SUPABASE_KEY", "service-role-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "blob-store-secret")
    monkeypatch.setenv("VERITY_LLM_API_KEY", "llm-secret")

    class EnvSnooper:
        def predict(self, X):
            import os

            return [
                os.environ.get("SUPABASE_KEY"),
                os.environ.get("AWS_SECRET_ACCESS_KEY"),
                os.environ.get("VERITY_LLM_API_KEY"),
            ]

    payload = cloudpickle.dumps(EnvSnooper())

    result = execute(model_payload=payload, X=[[0.0]])

    assert list(result["y_pred"]) == [None, None, None]


def test_a_model_that_raises_surfaces_as_a_sandbox_error_naming_the_cause():
    class Exploding:
        def predict(self, X):
            raise ValueError("feature order mismatch")

    payload = cloudpickle.dumps(Exploding())

    with pytest.raises(SandboxError) as excinfo:
        execute(model_payload=payload, X=[[0.0]])

    assert "feature order mismatch" in str(excinfo.value)


def test_a_model_that_never_returns_is_killed_at_the_timeout():
    class Hanging:
        def predict(self, X):
            import time

            time.sleep(30)

    payload = cloudpickle.dumps(Hanging())

    with pytest.raises(SandboxError) as excinfo:
        execute(model_payload=payload, X=[[0.0]], timeout=2)

    assert "timed out" in str(excinfo.value)
