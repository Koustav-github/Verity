import importlib
import json
import sys

import cloudpickle
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sklearn.linear_model import LinearRegression, LogisticRegression


def _build_app(tmp_path, model, contract, monkeypatch):
    """Materialise a serving directory and import the template against it.

    The template resolves its own directory, so the test writes a real one and points
    the module at it — the same arrangement the built image produces, minus Docker.
    """
    (tmp_path / "model.pkl").write_bytes(cloudpickle.dumps(model))
    (tmp_path / "contract.json").write_text(json.dumps(contract))
    monkeypatch.setenv("VERITY_SERVING_DIR", str(tmp_path))
    # The module reads its contract and model at import, so each test needs a genuinely
    # fresh execution. Popping sys.modules alone is not enough: `from serving import
    # app_template` would hand back the parent package's cached attribute without ever
    # re-running the module body, and every test after the first would silently reuse
    # the first one's contract.
    sys.modules.pop("serving.app_template", None)
    app_template = importlib.import_module("serving.app_template")

    return TestClient(app_template.app)


def _classifier():
    X = np.array([[0.0, 1.0], [1.0, 0.0], [0.5, 0.5], [0.2, 0.8]])
    return LogisticRegression().fit(X, np.array([0, 1, 0, 1]))


POSITIONAL = {
    "n_features": 2,
    "feature_names": None,
    "classes": [0, 1],
    "has_predict_proba": True,
}
NAMED = {
    "n_features": 2,
    "feature_names": ["age", "fare"],
    "classes": [0, 1],
    "has_predict_proba": True,
}


def test_health_reports_ok_once_the_model_has_loaded(tmp_path, monkeypatch):
    client = _build_app(tmp_path, _classifier(), POSITIONAL, monkeypatch)

    assert client.get("/health").json() == {"status": "ok"}


def test_predict_accepts_positional_instances_when_there_are_no_feature_names(
    tmp_path, monkeypatch
):
    client = _build_app(tmp_path, _classifier(), POSITIONAL, monkeypatch)

    body = client.post("/predict", json={"instances": [[0.0, 1.0], [1.0, 0.0]]}).json()

    assert len(body["predictions"]) == 2
    assert len(body["probabilities"]) == 2


def test_predict_accepts_named_instances_and_orders_them_by_the_contract(
    tmp_path, monkeypatch
):
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame({"age": [0.0, 1.0, 0.5, 0.2], "fare": [1.0, 0.0, 0.5, 0.8]})
    model = LogisticRegression().fit(frame, np.array([0, 1, 0, 1]))
    client = _build_app(tmp_path, model, NAMED, monkeypatch)

    # Keys deliberately supplied in the wrong order. The contract's order must win,
    # because for an estimator taking a bare array, column order IS the input contract
    # and getting it wrong yields confident nonsense rather than an error.
    reversed_keys = client.post("/predict", json={"instances": [{"fare": 1.0, "age": 0.0}]})
    natural_keys = client.post("/predict", json={"instances": [{"age": 0.0, "fare": 1.0}]})

    assert reversed_keys.json()["predictions"] == natural_keys.json()["predictions"]


def test_predict_rejects_a_positional_instance_of_the_wrong_length(tmp_path, monkeypatch):
    client = _build_app(tmp_path, _classifier(), POSITIONAL, monkeypatch)

    response = client.post("/predict", json={"instances": [[1.0]]})

    assert response.status_code == 422
    assert "expected 2 values" in response.json()["detail"]


def test_predict_rejects_a_named_instance_missing_a_feature(tmp_path, monkeypatch):
    client = _build_app(tmp_path, _classifier(), NAMED, monkeypatch)

    response = client.post("/predict", json={"instances": [{"age": 1.0}]})

    assert response.status_code == 422
    assert "fare" in response.json()["detail"]


def test_predict_rejects_an_unknown_feature_name(tmp_path, monkeypatch):
    client = _build_app(tmp_path, _classifier(), NAMED, monkeypatch)

    response = client.post(
        "/predict", json={"instances": [{"age": 1.0, "fare": 2.0, "nope": 3.0}]}
    )

    assert response.status_code == 422
    assert "nope" in response.json()["detail"]


def test_predict_rejects_a_positional_instance_for_a_named_model(tmp_path, monkeypatch):
    client = _build_app(tmp_path, _classifier(), NAMED, monkeypatch)

    response = client.post("/predict", json={"instances": [[1.0, 2.0]]})

    # Silently accepting a bare list here would mean guessing which column is which.
    assert response.status_code == 422


def test_predict_returns_null_probabilities_for_an_estimator_without_predict_proba(
    tmp_path, monkeypatch
):
    model = LinearRegression().fit(
        np.array([[1.0, 2.0], [3.0, 4.0]]), np.array([1.0, 2.0])
    )
    contract = {
        "n_features": 2,
        "feature_names": None,
        "classes": None,
        "has_predict_proba": False,
    }
    client = _build_app(tmp_path, model, contract, monkeypatch)

    body = client.post("/predict", json={"instances": [[1.0, 2.0]]}).json()

    assert body["probabilities"] is None
    assert len(body["predictions"]) == 1


def test_predict_rejects_an_empty_instances_list(tmp_path, monkeypatch):
    client = _build_app(tmp_path, _classifier(), POSITIONAL, monkeypatch)

    assert client.post("/predict", json={"instances": []}).status_code == 422


def test_a_named_feature_model_is_predicted_without_a_sklearn_feature_name_warning(
    tmp_path, monkeypatch, recwarn
):
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame({"age": [0.0, 1.0, 0.5, 0.2], "fare": [1.0, 0.0, 0.5, 0.8]})
    model = LogisticRegression().fit(frame, np.array([0, 1, 0, 1]))
    client = _build_app(tmp_path, model, NAMED, monkeypatch)

    client.post("/predict", json={"instances": [{"age": 0.0, "fare": 1.0}]})

    # Without this, sklearn warns once per prediction, forever, in production.
    assert not [w for w in recwarn if "does not have valid feature names" in str(w.message)]
