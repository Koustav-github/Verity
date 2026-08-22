"""FastAPI service wrapping the Titanic survival ONNX model."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import numpy as np
import onnxruntime as rt
from fastapi import FastAPI
from pydantic import BaseModel, Field

# The ONNX graph takes a bare float array with no column names, so this order
# *is* the input contract — it must match the training frame. A wrong order
# produces confidently wrong predictions rather than an error.
FEATURE_ORDER = ["Pclass", "Sex", "Age", "Fare", "Relatives", "Embarked"]

# Encodings applied during training, kept here so callers send readable values
# and can't silently get the mapping backwards.
SEX_ENCODING = {"male": 0.0, "female": 1.0}
EMBARKED_ENCODING = {"C": 0.0, "Q": 1.0, "S": 2.0}  # OrdinalEncoder, alphabetical

MODEL_PATH = Path(os.getenv("MODEL_PATH", Path(__file__).parent / "model.onnx"))

# Built once at import: session construction is the expensive part, and failing
# here stops the container from ever reporting healthy.
_session = rt.InferenceSession(str(MODEL_PATH), providers=["CPUExecutionProvider"])
_input = _session.get_inputs()[0]

# Guard against serving a model exported from a different feature set.
if _input.shape[1] != len(FEATURE_ORDER):
    raise RuntimeError(
        f"{MODEL_PATH.name} expects {_input.shape[1]} features, "
        f"but FEATURE_ORDER defines {len(FEATURE_ORDER)}"
    )

app = FastAPI(title="Titanic Survival Model", version="1.0.0")


class Passenger(BaseModel):
    pclass: Literal[1, 2, 3]
    sex: Literal["male", "female"]
    age: float = Field(..., ge=0, le=120)
    fare: float = Field(..., ge=0)
    relatives: int = Field(..., ge=0, description="SibSp + Parch")
    embarked: Literal["C", "Q", "S"]


class Prediction(BaseModel):
    survived: bool
    survival_probability: float


def _encode(passengers: list[Passenger]) -> np.ndarray:
    rows = []
    for p in passengers:
        values = {
            "Pclass": float(p.pclass),
            "Sex": SEX_ENCODING[p.sex],
            "Age": p.age,
            "Fare": p.fare,
            "Relatives": float(p.relatives),
            "Embarked": EMBARKED_ENCODING[p.embarked],
        }
        rows.append([values[column] for column in FEATURE_ORDER])
    # ONNX runs in float32 while sklearn trained in float64, so the cast is required.
    return np.array(rows, dtype=np.float32)


def _predict(passengers: list[Passenger]) -> list[Prediction]:
    labels, probabilities = _session.run(None, {_input.name: _encode(passengers)})
    # Classes are [0, 1], so column 1 is P(survived).
    return [
        Prediction(survived=bool(label), survival_probability=float(probs[1]))
        for label, probs in zip(labels, probabilities)
    ]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/predict", response_model=Prediction)
def predict(passenger: Passenger) -> Prediction:
    return _predict([passenger])[0]


@app.post("/predict/batch", response_model=list[Prediction])
def predict_batch(passengers: list[Passenger]) -> list[Prediction]:
    return _predict(passengers)
