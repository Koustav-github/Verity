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
