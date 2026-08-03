"""Constructors for the evaluation fixtures Verity knows how to run.

One function per fixture kind. The `kind` in the descriptor is what the server uses to
pick an eval mechanism, so widening Verity to a new model class adds a constructor here
and a mechanism server-side — it does not change the upload path.

Kinds the server understands today: labeled_holdout.
"""

import hashlib

import cloudpickle


def _bundle(kind, body, spec):
    payload = cloudpickle.dumps(body)
    digest = hashlib.sha256(payload).hexdigest()
    return payload, {"kind": kind, "sha256": digest, "spec": spec}


def _n_rows(data):
    shape = getattr(data, "shape", None)
    return int(shape[0]) if shape else len(data)


def _n_features(X):
    shape = getattr(X, "shape", None)
    if shape:
        return int(shape[1]) if len(shape) > 1 else 1
    first = X[0] if len(X) else []
    return len(first) if hasattr(first, "__len__") else 1


def labeled_holdout(X, y):
    """A held-out set of features and their true labels, for supervised models."""
    n_samples, n_labels = _n_rows(X), _n_rows(y)
    if n_samples != n_labels:
        raise ValueError(
            f"X has {n_samples} rows but y has {n_labels} labels; "
            "an eval scored against misaligned labels is worse than no eval"
        )
    return _bundle(
        "labeled_holdout",
        {"X": X, "y": y},
        {"n_samples": n_samples, "n_features": _n_features(X)},
    )
