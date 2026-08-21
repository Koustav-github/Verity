from agents.brain2.nat.score import RESOURCE_PREFIX

# The V1 metric set, fixed. Falcon does not choose these per model — the README's V1 scope
# for Falcon is exactly "request count, latency percentiles, error rate", and unlike Nat's
# quality metrics there is nothing task-dependent about them: every served model has
# requests, latency, and errors.
METRICS = [
    "request_count",
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "error_rate",
]


def build_eval_reference(*, eval_run_id, scores):
    """Split an eval_run's flat score map into resource values and quality values.

    The `basis` marker is not decoration. These numbers come from a single-process,
    single-client, cold sandbox — they are a feasibility reference, NOT a production
    baseline, and production latency under real concurrency will be materially higher.
    Nothing in V1 compares against them; they are recorded for context and for V7's rule
    engine, which must be able to tell what kind of number it is reading.
    """
    reference = {"basis": "sandbox_feasibility", "eval_run_id": eval_run_id, "quality": {}}
    for key, value in (scores or {}).items():
        if key.startswith(RESOURCE_PREFIX):
            reference[key[len(RESOURCE_PREFIX):]] = value
        else:
            reference["quality"][key] = value
    return reference


def configure(*, model_version_id, eval_run_id, eval_run, metadata_store):
    """Switch monitoring on for a version that just reached production.

    Deterministic, like Fury: the reference is lifted from evidence that already exists
    (the eval_run that promoted this version), so there is nothing to guess and no LLM
    call to make.
    """
    config = {
        "metrics": METRICS,
        "eval_reference": build_eval_reference(
            eval_run_id=eval_run_id, scores=eval_run.get("scores", {})
        ),
    }
    config_id = metadata_store.save_monitoring_config(
        model_version_id=model_version_id, eval_run_id=eval_run_id, config=config
    )
    return {"id": config_id, **config}
