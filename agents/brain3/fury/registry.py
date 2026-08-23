def find_existing(*, user_id, sha256, name, metadata_store):
    """A byte-for-byte re-upload under the same name is a true no-op.

    Checked before anything else runs — before the S3 write, before Hawkeye, before Nat
    — so an exact repeat costs nothing beyond this lookup. A hash match under a
    *different* name is deliberately not a hit: identical bytes registered under a new
    name is a legitimate new registration, not an accidental duplicate.
    """
    model = metadata_store.find_model(user_id=user_id, name=name)
    if model is None:
        return None
    return metadata_store.find_model_version_by_hash(model_id=model["id"], sha256=sha256)


def register(*, user_id, name, model_version_id, manifest, verdict, eval_run_id, metadata_store, alert_email=None):
    """Link this version to its model's identity, and promote it if it earned that.

    Identity linking happens unconditionally — a pending or failed version is still
    part of a model's version history and needs to be findable as such. Promotion only
    happens on a passing verdict.
    """
    model = metadata_store.find_model(user_id=user_id, name=name)
    if model is None:
        model_id = metadata_store.create_model(
            user_id=user_id,
            name=name,
            model_class=manifest.get("model_class"),
            task_type=manifest.get("task_type"),
            alert_email=alert_email,
        )
    else:
        # An existing model's alert_email is not updated by a later upload — this is
        # about who is notified for a *model*, decided once at its creation, not a
        # setting a later version's upload can silently change.
        model_id = model["id"]

    metadata_store.link_model_version(model_version_id=model_version_id, model_id=model_id)

    if verdict != "pass":
        status = "pending"
        if verdict is not None:
            status = "staging_failed"
            metadata_store.update_model_version_status(
                model_version_id=model_version_id, status=status
            )
        return {"model_id": model_id, "status": status, "archived_model_version_id": None}

    incumbent = metadata_store.find_production_version(model_id=model_id)
    archived_id = None
    if incumbent is not None:
        metadata_store.archive_model_version(model_version_id=incumbent["id"])
        archived_id = incumbent["id"]

    metadata_store.promote_model_version(model_version_id=model_version_id, eval_run_id=eval_run_id)
    return {"model_id": model_id, "status": "production", "archived_model_version_id": archived_id}
