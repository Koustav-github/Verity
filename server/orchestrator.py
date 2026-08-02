import cloudpickle


def build_artifact(*, payload, sha256, user_id, args, blob_store, metadata_store, identify_fn=None):
    identify_fn = identify_fn or _default_identify

    artifact_uri = blob_store.put(sha256, payload)

    # Deserializing here means we're loading whatever bytes the client sent,
    # which is only safe because this is our own dev/test payload today.
    # Before this handles untrusted customer uploads, this must move into
    # the sandboxed executor (Schemas.md's `python-exec` MCP scope) instead
    # of running in the same process as SeaweedFS/Supabase credentials.
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
    return {
        "model_version_id": model_version_id,
        "artifact_uri": artifact_uri,
        "status": "pending",
        "manifest": manifest,
    }


def _default_identify(model):
    from agents.brain1.hawkeye.identify import identify

    return identify(model)
