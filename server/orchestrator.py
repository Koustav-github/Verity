def build_artifact(*, payload, sha256, user_id, args, blob_store, metadata_store):
    artifact_uri = blob_store.put(sha256, payload)
    model_version_id = metadata_store.save_model_version(
        sha256=sha256,
        artifact_uri=artifact_uri,
        user_id=user_id,
        args=args,
        status="pending",
    )
    return {
        "model_version_id": model_version_id,
        "artifact_uri": artifact_uri,
        "status": "pending",
    }
