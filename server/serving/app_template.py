"""The serving app baked into every model-version image.

This is a real, checked-in, unit-testable file — not a string the server renders. Only
requirements.txt and one Dockerfile line are templated. Everything that varies per model
arrives as data in contract.json, which is what lets this app be tested directly against
a temp directory with Docker nowhere in the loop.
"""

import json
import os
from pathlib import Path

import cloudpickle
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# The image lays these down next to this file; the env var exists so tests can point at
# a temp directory instead.
SERVING_DIR = Path(os.getenv("VERITY_SERVING_DIR", Path(__file__).parent))

CONTRACT = json.loads((SERVING_DIR / "contract.json").read_text())
FEATURE_NAMES = CONTRACT.get("feature_names")
N_FEATURES = CONTRACT["n_features"]
HAS_PROBA = CONTRACT.get("has_predict_proba", False)

# Loading happens at import on purpose. Unpickling is arbitrary code execution, and this
# process is the containment boundary for it: the container holds no credentials, no
# store clients, and nothing but this one model. If the artifact cannot load in this
# image, the container never reports healthy and the deploy fails loudly — rather than
# succeeding and then failing on a customer's first request.
MODEL = cloudpickle.loads((SERVING_DIR / "model.pkl").read_bytes())

app = FastAPI(title="Verity model service")


class PredictRequest(BaseModel):
    instances: list


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _row_from_named(instance, index):
    if not isinstance(instance, dict):
        raise HTTPException(
            422, f"instance {index}: expected an object with keys {FEATURE_NAMES}"
        )
    missing = [name for name in FEATURE_NAMES if name not in instance]
    if missing:
        raise HTTPException(422, f"instance {index}: missing feature(s) {missing}")
    unknown = [key for key in instance if key not in FEATURE_NAMES]
    if unknown:
        raise HTTPException(422, f"instance {index}: unknown feature(s) {unknown}")
    # The contract's order wins, not the request's. For an estimator that takes a bare
    # array, column order IS the input contract, and a wrong order produces confidently
    # wrong answers instead of an error.
    return [instance[name] for name in FEATURE_NAMES]


def _row_from_positional(instance, index):
    if isinstance(instance, dict):
        raise HTTPException(
            422,
            f"instance {index}: this model has no feature names; "
            f"send a list of {N_FEATURES} values",
        )
    if len(instance) != N_FEATURES:
        raise HTTPException(
            422, f"instance {index}: expected {N_FEATURES} values, got {len(instance)}"
        )
    return list(instance)


def _as_frame(rows):
    """Hand a named-feature estimator a DataFrame, not a bare array.

    scikit-learn records feature_names_in_ when fit on a DataFrame and warns on every
    single predict() call that receives an array instead. Left alone, that means a
    warning per production request, forever. pandas is guaranteed present here whenever
    it matters: feature names only exist because the model was fit on a DataFrame, which
    means the training environment had pandas, which means the captured requirements
    installed it. The fallback covers the case where that chain is somehow broken.
    """
    try:
        import pandas as pd

        return pd.DataFrame(rows, columns=FEATURE_NAMES, dtype=float)
    except ImportError:
        return np.asarray(rows, dtype=float)


def _encode(instances):
    if not instances:
        raise HTTPException(422, "instances must not be empty")
    if FEATURE_NAMES:
        rows = [_row_from_named(instance, i) for i, instance in enumerate(instances)]
        return _as_frame(rows)
    rows = [_row_from_positional(instance, i) for i, instance in enumerate(instances)]
    return np.asarray(rows, dtype=float)


@app.post("/predict")
def predict(body: PredictRequest) -> dict:
    X = _encode(body.instances)
    predictions = MODEL.predict(X)
    probabilities = MODEL.predict_proba(X) if HAS_PROBA else None
    return {
        "predictions": predictions.tolist(),
        "probabilities": probabilities.tolist() if probabilities is not None else None,
    }
