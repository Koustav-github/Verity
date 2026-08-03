import cloudpickle

# A verdict that isn't a clean pass leaves the version held, never promoted. Fury
# decides production; this only decides whether the version earned a look.
_STATUS_FOR_VERDICT = {"pass": "staging"}


def build_artifact(
    *,
    payload,
    sha256,
    user_id,
    args,
    blob_store,
    metadata_store,
    identify_fn=None,
    evaluate_fn=None,
    fixture_payload=None,
    fixture_descriptor=None,
):
    identify_fn = identify_fn or _default_identify
    evaluate_fn = evaluate_fn or _default_evaluate

    artifact_uri = blob_store.put(sha256, payload)

    # Deserializing here means we're loading whatever bytes the client sent,
    # which is only safe because this is our own dev/test payload today.
    # Before this handles untrusted customer uploads, this must move into
    # the sandboxed executor (Schemas.md's `python-exec` MCP scope) instead
    # of running in the same process as the S3/Supabase credentials.
    # Note this is now the ONLY unsandboxed load left: the eval itself runs
    # through execution/sandbox.py, which withholds every credential.
    model = cloudpickle.loads(payload)
    manifest = identify_fn(model)

    model_version_id = metadata_store.save_model_version(
        sha256=sha256,
        artifact_uri=artifact_uri,
        user_id=user_id,
        args=args,
        status="pending",
    )
    metadata_store.save_manifest(model_version_id=model_version_id, manifest=manifest)

    status = "pending"
    eval_record = None
    if fixture_payload is not None:
        eval_run = _evaluate(
            evaluate_fn=evaluate_fn,
            manifest=manifest,
            payload=payload,
            fixture_payload=fixture_payload,
            fixture_descriptor=fixture_descriptor,
            blob_store=blob_store,
        )
        eval_run_id = metadata_store.save_eval_run(
            model_version_id=model_version_id, eval_run=eval_run
        )
        status = _STATUS_FOR_VERDICT.get(eval_run["verdict"], "staging_failed")
        metadata_store.update_model_version_status(
            model_version_id=model_version_id, status=status
        )
        eval_record = {"id": eval_run_id, **eval_run}

    return {
        "model_version_id": model_version_id,
        "artifact_uri": artifact_uri,
        "status": status,
        "manifest": manifest,
        "eval_run": eval_record,
    }


def _evaluate(
    *, evaluate_fn, manifest, payload, fixture_payload, fixture_descriptor, blob_store
):
    fixture_sha256 = fixture_descriptor["sha256"]
    fixture_uri = blob_store.put(fixture_sha256, fixture_payload)
    fixture = {**fixture_descriptor, "uri": fixture_uri}

    # Same unsandboxed-unpickle debt as the model above, and for honesty's sake it is
    # not a lesser one: a pickle payload executes code on load whether we think of it
    # as "data" or not. It happens here so the labels stay server-side for scoring
    # while only the features cross into the sandbox. Both loads belong behind the
    # `python-exec` boundary; that move is tracked with the Hawkeye one.
    data = cloudpickle.loads(fixture_payload)

    return evaluate_fn(
        manifest=manifest,
        fixture=fixture,
        data=data,
        model_payload=payload,
    )


def _default_identify(model):
    from agents.brain1.hawkeye.identify import identify

    return identify(model)


def _default_evaluate(**kwargs):
    from agents.brain2.nat.evaluate import evaluate
    from execution.sandbox import execute

    return evaluate(execute_fn=execute, **kwargs)
