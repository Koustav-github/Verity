import numpy as np

from agents.brain2.nat.mechanisms import labeled_holdout


def test_profile_reports_shape_and_label_cardinality_for_a_classification_holdout():
    data = {
        "X": np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0], [7.0, 8.0]]),
        "y": np.array([0, 1, 1, 0]),
    }

    profile = labeled_holdout.profile(data)

    assert profile == {
        "n_samples": 4,
        "n_features": 2,
        "n_classes": 2,
        "y_dtype_kind": "i",
    }


def test_profile_reports_no_class_count_for_a_continuous_target():
    data = {
        "X": np.array([[1.0], [2.0], [3.0]]),
        "y": np.array([0.5, 1.5, 2.5]),
    }

    profile = labeled_holdout.profile(data)

    assert profile["n_classes"] is None
    assert profile["y_dtype_kind"] == "f"


def test_run_feeds_the_holdout_features_to_the_executor_and_pairs_outputs_with_the_labels():
    calls = []

    def fake_execute(*, model_payload, X):
        calls.append({"model_payload": model_payload, "X": X})
        return {
            "y_pred": [1, 0],
            "y_proba": [[0.2, 0.8], [0.7, 0.3]],
            "resource": {"latency_p95_ms": 0.4},
        }

    data = {"X": [[1.0], [2.0]], "y": [1, 1]}

    outputs = labeled_holdout.run(
        model_payload=b"pickled-model", data=data, execute_fn=fake_execute
    )

    assert calls == [{"model_payload": b"pickled-model", "X": [[1.0], [2.0]]}]
    assert outputs == {
        "y_true": [1, 1],
        "y_pred": [1, 0],
        "y_proba": [[0.2, 0.8], [0.7, 0.3]],
        "resource": {"latency_p95_ms": 0.4},
    }
