import hashlib

import cloudpickle


def build_artifact(
    *,
    payload,
    sha256,
    user_id,
    name,
    args,
    blob_store,
    metadata_store,
    identify_fn=None,
    evaluate_fn=None,
    find_existing_fn=None,
    register_fn=None,
    fixture_payload=None,
    fixture_descriptor=None,
):
    identify_fn = identify_fn or _default_identify
    evaluate_fn = evaluate_fn or _default_evaluate
    find_existing_fn = find_existing_fn or _default_find_existing
    register_fn = register_fn or _default_register

    # The client claims this digest; never trust it as-is. It's used as the S3 key, the
    # artifact_sha256 column, and (via dedup) the identity that decides which existing
    # model_version record a caller gets back — including its status. A client claiming
    # someone else's already-registered digest while sending different bytes must not be
    # able to walk away with that other version's record.
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != sha256:
        raise ValueError(
            f"claimed sha256 {sha256!r} does not match the payload's actual digest {actual_sha256!r}"
        )

    # Dedup only short-circuits when there's nothing new to evaluate. A fixture-bearing
    # upload always runs the full pipeline, even if an identical no-fixture upload was
    # already registered under this name/hash — otherwise the fixture the caller just
    # attached would be silently discarded and the version stranded at `pending` forever.
    if fixture_payload is None:
        existing = find_existing_fn(
            user_id=user_id, sha256=sha256, name=name, metadata_store=metadata_store
        )
        if existing is not None:
            return {
                "model_version_id": existing["id"],
                "artifact_uri": existing["artifact_uri"],
                "status": existing["status"],
                "manifest": None,
                "eval_run": None,
                "deduplicated": True,
                "model_id": existing["model_id"],
            }

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

    eval_record = None
    verdict = None
    eval_run_id = None
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
        verdict = eval_run["verdict"]
        eval_record = {"id": eval_run_id, **eval_run}

    registration = register_fn(
        user_id=user_id,
        name=name,
        model_version_id=model_version_id,
        manifest=manifest,
        verdict=verdict,
        eval_run_id=eval_run_id,
        metadata_store=metadata_store,
    )

    return {
        "model_version_id": model_version_id,
        "artifact_uri": artifact_uri,
        "status": registration["status"],
        "manifest": manifest,
        "eval_run": eval_record,
        "model_id": registration["model_id"],
        "deduplicated": False,
        "archived_model_version_id": registration["archived_model_version_id"],
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


def _default_find_existing(**kwargs):
    from agents.brain3.fury.registry import find_existing

    return find_existing(**kwargs)


def _default_register(**kwargs):
    from agents.brain3.fury.registry import register

    return register(**kwargs)
