import pytest

from agents.brain2.nat.score import score


def test_score_computes_requested_ml_metrics_from_predictions():
    outputs = {
        "y_true": [0, 0, 1, 1],
        "y_pred": [0, 0, 1, 0],
        "y_proba": None,
    }

    scores, skipped = score(section="ML", metric_set=["accuracy", "recall"], outputs=outputs)

    assert scores == {"accuracy": 0.75, "recall": 0.5}
    assert skipped == []


def test_score_skips_an_unrecognized_metric_instead_of_raising():
    outputs = {"y_true": [0, 1], "y_pred": [0, 1], "y_proba": None}

    scores, skipped = score(section="ML", metric_set=["accuracy", "vibes"], outputs=outputs)

    assert scores == {"accuracy": 1.0}
    assert skipped == [{"metric": "vibes", "reason": "unknown_metric"}]


def test_score_computes_roc_auc_from_positive_class_probabilities():
    outputs = {
        "y_true": [0, 0, 1, 1],
        "y_pred": [0, 0, 1, 1],
        "y_proba": [[0.9, 0.1], [0.8, 0.2], [0.3, 0.7], [0.2, 0.8]],
    }

    scores, skipped = score(section="ML", metric_set=["roc_auc"], outputs=outputs)

    assert scores == {"roc_auc": 1.0}
    assert skipped == []


def test_score_skips_a_probability_metric_when_the_model_has_no_predict_proba():
    outputs = {"y_true": [0, 0, 1, 1], "y_pred": [0, 0, 1, 1], "y_proba": None}

    scores, skipped = score(section="ML", metric_set=["accuracy", "roc_auc"], outputs=outputs)

    assert scores == {"accuracy": 1.0}
    assert skipped == [{"metric": "roc_auc", "reason": "requires_proba"}]


def test_score_wires_every_classification_metric_to_the_right_function():
    # Deliberately imperfect and class-imbalanced so the metrics take different
    # values — a name mapped to the wrong function shows up as a changed number.
    outputs = {
        "y_true": [0, 0, 0, 1, 1],
        "y_pred": [0, 1, 0, 1, 1],
        "y_proba": [[0.9, 0.1], [0.4, 0.6], [0.8, 0.2], [0.5, 0.5], [0.2, 0.8]],
    }
    metric_set = [
        "accuracy", "precision", "recall", "f1", "balanced_accuracy",
        "mcc", "roc_auc", "pr_auc", "log_loss",
    ]

    scores, skipped = score(section="ML", metric_set=metric_set, outputs=outputs)

    assert skipped == []
    assert scores == pytest.approx({
        "accuracy": 0.8,
        "precision": 2 / 3,
        "recall": 1.0,
        "f1": 0.8,
        "balanced_accuracy": 5 / 6,
        "mcc": 2 / 3,
        "roc_auc": 5 / 6,
        "pr_auc": 5 / 6,
        "log_loss": 0.432217106,
    })


def test_score_wires_every_regression_metric_to_the_right_function():
    outputs = {
        "y_true": [3.0, -0.5, 2.0, 7.0],
        "y_pred": [2.5, 0.0, 2.0, 8.0],
        "y_proba": None,
    }
    metric_set = ["mae", "mse", "rmse", "r2", "mape"]

    scores, skipped = score(section="ML", metric_set=metric_set, outputs=outputs)

    assert skipped == []
    assert scores == pytest.approx({
        "mae": 0.5,
        "mse": 0.375,
        "rmse": 0.375 ** 0.5,
        "r2": 1 - 1.5 / 29.1875,
        "mape": 0.32738095,
    })
