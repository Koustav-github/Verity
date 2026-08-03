import numpy as np

KIND = "labeled_holdout"
MECHANISM = "labeled_holdout"
ATLAS_SECTION = "ML"

# numpy dtype kinds that can carry class labels. A float target is a continuous
# one, so counting its distinct values would invent a class count that isn't real.
_DISCRETE_KINDS = "iuUSObb"


def profile(data):
    X = np.asarray(data["X"])
    y = np.asarray(data["y"])
    return {
        "n_samples": int(X.shape[0]),
        "n_features": int(X.shape[1]) if X.ndim > 1 else 1,
        "n_classes": int(len(np.unique(y))) if y.dtype.kind in _DISCRETE_KINDS else None,
        "y_dtype_kind": y.dtype.kind,
    }


def run(*, model_payload, data, execute_fn):
    """Produce raw outputs for scoring. No metrics are computed here — the mechanism
    knows how to exercise the model, the scoring engine knows what the numbers mean."""
    result = execute_fn(model_payload=model_payload, X=data["X"])
    return {
        "y_true": data["y"],
        "y_pred": result["y_pred"],
        "y_proba": result.get("y_proba"),
        "resource": result.get("resource", {}),
    }
