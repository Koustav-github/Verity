import cloudpickle
import numpy as np
import pytest
from sklearn.linear_model import LinearRegression, LogisticRegression

from execution.sandbox import SandboxError, introspect


def _fitted_classifier_on_arrays():
    X = np.array([[0.0, 1.0], [1.0, 0.0], [0.5, 0.5], [0.2, 0.8]])
    y = np.array([0, 1, 0, 1])
    return LogisticRegression().fit(X, y)


def test_introspect_reports_the_feature_count_of_a_fitted_estimator():
    payload = cloudpickle.dumps(_fitted_classifier_on_arrays())

    assert introspect(model_payload=payload)["n_features"] == 2


def test_introspect_reports_no_feature_names_when_the_model_was_fit_on_arrays():
    payload = cloudpickle.dumps(_fitted_classifier_on_arrays())

    # scikit-learn only sets feature_names_in_ when fit on a DataFrame. Reporting None
    # is what makes the generated API fall back to positional instances rather than
    # inventing column names.
    assert introspect(model_payload=payload)["feature_names"] is None


def test_introspect_reports_feature_names_when_the_model_was_fit_on_a_dataframe():
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame({"age": [1.0, 2.0, 3.0, 4.0], "fare": [4.0, 3.0, 2.0, 1.0]})
    model = LogisticRegression().fit(frame, np.array([0, 1, 0, 1]))
    payload = cloudpickle.dumps(model)

    assert introspect(model_payload=payload)["feature_names"] == ["age", "fare"]


def test_introspect_reports_the_class_labels_as_plain_json_safe_values():
    payload = cloudpickle.dumps(_fitted_classifier_on_arrays())

    classes = introspect(model_payload=payload)["classes"]

    # numpy ints would break the JSON contract written into the image.
    assert classes == [0, 1]
    assert all(type(value) is int for value in classes)


def test_introspect_reports_whether_the_estimator_can_produce_probabilities():
    classifier = cloudpickle.dumps(_fitted_classifier_on_arrays())
    regressor = cloudpickle.dumps(
        LinearRegression().fit(np.array([[1.0], [2.0], [3.0]]), np.array([1.0, 2.0, 3.0]))
    )

    assert introspect(model_payload=classifier)["has_predict_proba"] is True
    assert introspect(model_payload=regressor)["has_predict_proba"] is False


def test_introspect_names_the_estimator_class():
    payload = cloudpickle.dumps(_fitted_classifier_on_arrays())

    assert introspect(model_payload=payload)["estimator_class"] == "LogisticRegression"


def test_introspect_raises_sandbox_error_when_the_payload_is_not_a_model():
    with pytest.raises(SandboxError):
        introspect(model_payload=b"not a pickle at all")


def test_introspection_runs_behind_the_same_credential_allowlist_as_execution(monkeypatch):
    # The same security claim test_sandbox.py makes for execute(): introspection loads
    # untrusted bytes too, so it must inherit the identical scrubbed environment.
    import execution.sandbox as sandbox_module

    monkeypatch.setenv("SUPABASE_KEY", "leaked")
    payload = cloudpickle.dumps(_fitted_classifier_on_arrays())

    assert introspect(model_payload=payload)["estimator_class"] == "LogisticRegression"
    assert "SUPABASE_KEY" not in sandbox_module._child_env()
