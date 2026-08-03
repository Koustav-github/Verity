import json
import os

from pydantic import BaseModel, field_validator, model_validator

from agents.brain2.nat.score import RESOURCE_PREFIX
from agents.provider import DEFAULT_BASE_URL, DEFAULT_MODEL

# The task -> metric lookup tables from Metrics.md, verbatim. Nat does not invent the
# mapping per run; it reads it off the Atlas, which is the agent's decision table.
_ATLAS = {
    "ML": """| Task | Common Metrics |
| Classification | Accuracy, Precision, Recall, F1, ROC-AUC, PR-AUC, Log Loss, Balanced Accuracy, MCC, Cohen's Kappa |
| Multi-Class Classification | Top-1 Accuracy, Top-K Accuracy, Macro F1, Micro F1, Weighted F1 |
| Regression | MAE, MSE, RMSE, RMSLE, R2, Adjusted R2, MAPE, SMAPE, Median AE |
| Clustering | Silhouette Score, Davies-Bouldin, Calinski-Harabasz, Adjusted Rand Index, NMI |
| Time Series | MAPE, SMAPE, MAE, RMSE, MASE, WAPE, Forecast Bias |
| Anomaly Detection | Precision, Recall, FPR, TPR, F1, AUROC, Average Precision |
| Recommendation | CTR, MAP, MRR, NDCG, Hit Rate, Recall@K, Precision@K, Coverage |
| Ranking | NDCG, MRR, Precision@K, Recall@K, MAP |
| Survival Analysis | Concordance Index (C-index), Brier Score |""",
}

# Only these are actually computable today; anything else the Atlas lists would be
# resolved and then skipped at scoring time, which wastes a gate rather than setting one.
_SUPPORTED = {
    "ML": [
        "accuracy", "precision", "recall", "f1", "balanced_accuracy", "mcc",
        "roc_auc", "pr_auc", "log_loss", "mae", "mse", "rmse", "r2", "mape",
    ],
}

_OPS = (">=", "<=", ">", "<")


class Threshold(BaseModel):
    metric: str
    op: str
    value: float

    @field_validator("op")
    @classmethod
    def _known_operator(cls, op):
        if op not in _OPS:
            raise ValueError(f"threshold operator must be one of {_OPS}, got {op!r}")
        return op


class EvalPlan(BaseModel):
    task_type: str
    metric_set: list[str]
    thresholds: list[Threshold]
    rationale: str | None = None

    @model_validator(mode="after")
    def _thresholds_only_gate_selected_metrics(self):
        gateable = set(self.metric_set)
        for threshold in self.thresholds:
            if threshold.metric.startswith(RESOURCE_PREFIX):
                continue
            if threshold.metric not in gateable:
                raise ValueError(
                    f"threshold names {threshold.metric!r}, which is not in metric_set"
                )
        return self


def _system_prompt(atlas_section, available_resource_metrics):
    resource_lines = "\n".join(
        f"- {RESOURCE_PREFIX}{name}" for name in available_resource_metrics
    )
    return f"""You are Nat, a model-evaluation agent. Given a model manifest and a profile of \
the evaluation dataset, choose the metrics worth measuring and the thresholds this model \
must clear to be promoted.

Pick metrics from this taxonomy, matching the task to its row:

{_ATLAS[atlas_section]}

Respond with a JSON object with these fields:
- task_type: the specific task, e.g. "binary_classification", "multiclass_classification", "regression"
- metric_set: a list of metric names to compute. Use ONLY these exact identifiers: \
{", ".join(_SUPPORTED[atlas_section])}
- thresholds: a list of objects {{"metric", "op", "value"}} where op is one of >=, <=, >, <. \
Every threshold's metric must appear in metric_set, or be one of the resource metrics below.
- rationale: one sentence on why this metric set fits the task

The harness also measures these systemic metrics on every run, and you may set thresholds \
on them even though they are not in metric_set:
{resource_lines}

Set thresholds that mean something for this dataset — consider class balance and sample \
count. Do not select a metric that cannot be computed from the profile you were given."""


def resolve(*, manifest, profile, atlas_section, available_resource_metrics, client=None):
    client = client or _real_client()
    user_content = json.dumps({"manifest": manifest, "dataset_profile": profile}, indent=2)
    response = client.chat.completions.create(
        model=os.environ.get("NAT_LLM_MODEL", DEFAULT_MODEL),
        messages=[
            {"role": "system", "content": _system_prompt(atlas_section, available_resource_metrics)},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
    )
    raw = json.loads(response.choices[0].message.content)
    return EvalPlan.model_validate(raw).model_dump()


def _openai_class():
    from openai import OpenAI

    return OpenAI


def _real_client():
    # Same shared credentials as Hawkeye; only NAT_LLM_MODEL is per-agent.
    return _openai_class()(
        api_key=os.environ["VERITY_LLM_API_KEY"],
        base_url=os.environ.get("VERITY_LLM_BASE_URL", DEFAULT_BASE_URL),
    )
