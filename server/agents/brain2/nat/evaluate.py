from datetime import datetime, timezone

from agents.brain2.nat.registry import for_fixture
from agents.brain2.nat.score import (
    RESOURCE_METRICS,
    apply_thresholds,
    merge_resource,
    score,
)


def evaluate(
    *,
    manifest,
    fixture,
    data,
    model_payload,
    execute_fn,
    resolve_fn=None,
    now_fn=None,
):
    """Run one evaluation and return an `eval_run` payload.

    Never raises: an unsupported fixture, a planning failure, or a crash inside the
    sandbox all come back as verdict "error". A gate that cannot be evaluated must
    still leave a row behind — the promotion decision has to be re-derivable from
    rows, not from re-running an agent.
    """
    resolve_fn = resolve_fn or _default_resolve
    now_fn = now_fn or _default_now

    eval_run = {
        "mechanism": None,
        "metric_set": {"resolved": [], "skipped": []},
        "scores": {},
        "thresholds": [],
        "verdict": "error",
        "failed_on": [],
        "test_set_ref": fixture.get("uri"),
        "fixture": fixture,
        "error": None,
        "started_at": now_fn(),
        "finished_at": None,
    }

    try:
        mechanism = for_fixture(fixture)
        eval_run["mechanism"] = mechanism.MECHANISM

        profile = mechanism.profile(data)
        plan = resolve_fn(
            manifest=manifest,
            profile=profile,
            atlas_section=mechanism.ATLAS_SECTION,
            available_resource_metrics=RESOURCE_METRICS,
        )
        eval_run["thresholds"] = plan["thresholds"]

        outputs = mechanism.run(
            model_payload=model_payload, data=data, execute_fn=execute_fn
        )
        scores, skipped = score(
            section=mechanism.ATLAS_SECTION,
            metric_set=plan["metric_set"],
            outputs=outputs,
        )
        eval_run["metric_set"] = {"resolved": plan["metric_set"], "skipped": skipped}
        eval_run["scores"] = merge_resource(scores, outputs["resource"])

        verdict, failed_on = apply_thresholds(
            scores=eval_run["scores"], thresholds=plan["thresholds"]
        )
        eval_run["verdict"] = verdict
        eval_run["failed_on"] = failed_on
    except Exception as exc:
        eval_run["verdict"] = "error"
        eval_run["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        eval_run["finished_at"] = now_fn()

    return eval_run


def _default_resolve(**kwargs):
    from agents.brain2.nat.resolve import resolve

    return resolve(**kwargs)


def _default_now():
    return datetime.now(timezone.utc).isoformat()
