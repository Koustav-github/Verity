import numpy as np
from sklearn import metrics


def _positive_proba(outputs):
    # sklearn's predict_proba returns one column per class; the binary metrics
    # want the positive class only.
    proba = np.asarray(outputs["y_proba"])
    return proba[:, 1] if proba.ndim == 2 and proba.shape[1] == 2 else proba


# Keyed by Atlas section (Metrics.md), so V4/V5 add a table rather than a branch.
# needs_proba marks metrics that score ranked confidences rather than hard labels —
# they're skipped, not failed, when the model has no predict_proba.
_METRIC_FNS = {
    "ML": {
        "accuracy": (lambda o: metrics.accuracy_score(o["y_true"], o["y_pred"]), False),
        "precision": (lambda o: metrics.precision_score(o["y_true"], o["y_pred"]), False),
        "recall": (lambda o: metrics.recall_score(o["y_true"], o["y_pred"]), False),
        "f1": (lambda o: metrics.f1_score(o["y_true"], o["y_pred"]), False),
        "balanced_accuracy": (
            lambda o: metrics.balanced_accuracy_score(o["y_true"], o["y_pred"]),
            False,
        ),
        "mcc": (lambda o: metrics.matthews_corrcoef(o["y_true"], o["y_pred"]), False),
        "roc_auc": (lambda o: metrics.roc_auc_score(o["y_true"], _positive_proba(o)), True),
        "pr_auc": (
            lambda o: metrics.average_precision_score(o["y_true"], _positive_proba(o)),
            True,
        ),
        "log_loss": (lambda o: metrics.log_loss(o["y_true"], _positive_proba(o)), True),
        "mae": (lambda o: metrics.mean_absolute_error(o["y_true"], o["y_pred"]), False),
        "mse": (lambda o: metrics.mean_squared_error(o["y_true"], o["y_pred"]), False),
        "rmse": (lambda o: metrics.root_mean_squared_error(o["y_true"], o["y_pred"]), False),
        "r2": (lambda o: metrics.r2_score(o["y_true"], o["y_pred"]), False),
        "mape": (
            lambda o: metrics.mean_absolute_percentage_error(o["y_true"], o["y_pred"]),
            False,
        ),
    },
}


RESOURCE_PREFIX = "resource."

# The systemic sample the execution harness is contracted to return on every run,
# whatever mechanism ran inside it. Drawn from the Serving / Infra sections of
# Metrics.md. These are feasibility figures from a single-process, single-client
# sandbox — not production percentiles under load, which are Falcon's job.
RESOURCE_METRICS = [
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "throughput_rps",
    "peak_memory_mb",
    "cpu_time_s",
    "gpu_memory_mb",
]


def merge_resource(scores, resource):
    """Fold the harness's systemic sample into the score map under a reserved prefix.

    Quality metrics keep their plain Atlas names; resource metrics are namespaced so a
    threshold can gate on either without the two ever colliding.
    """
    merged = dict(scores)
    for name, value in resource.items():
        merged[f"{RESOURCE_PREFIX}{name}"] = value
    return merged


_OPS = {
    ">=": lambda actual, target: actual >= target,
    "<=": lambda actual, target: actual <= target,
    ">": lambda actual, target: actual > target,
    "<": lambda actual, target: actual < target,
}


def apply_thresholds(*, scores, thresholds):
    """Compare scores against the thresholds as applied, returning (verdict, failed_on).

    A threshold whose metric was skipped or could not be measured is a failure, not a
    silent pass — a gate nobody could evaluate has not been cleared.
    """
    failed_on = []
    for threshold in thresholds:
        actual = scores.get(threshold["metric"])
        op = _OPS.get(threshold["op"])
        if actual is None or op is None or not op(actual, threshold["value"]):
            failed_on.append({**threshold, "actual": actual})
    return ("pass" if not failed_on else "fail"), failed_on


def score(*, section, metric_set, outputs):
    fns = _METRIC_FNS[section]
    scores = {}
    skipped = []
    for metric in metric_set:
        if metric not in fns:
            skipped.append({"metric": metric, "reason": "unknown_metric"})
            continue
        fn, needs_proba = fns[metric]
        if needs_proba and outputs.get("y_proba") is None:
            skipped.append({"metric": metric, "reason": "requires_proba"})
            continue
        scores[metric] = float(fn(outputs))
    return scores, skipped
