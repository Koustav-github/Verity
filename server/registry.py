"""Read-side registry queries: list models, list versions, one version's full detail,
and its downloadable-artifact URLs.

Separate from orchestrator.py deliberately: orchestrator.build_artifact() builds its
response from values it just computed mid-pipeline; these functions build the identical
*shape* by re-querying already-stored rows, for a version that may have been created any
time in the past. One assembles from memory during a write; these assemble from storage
for a read.
"""


def list_models(*, user_id, metadata_store):
    models = metadata_store.find_models_by_user(user_id=user_id)
    result = []
    for model in models:
        production_version = metadata_store.find_production_version(model_id=model["id"])
        result.append(
            {
                "id": model["id"],
                "name": model["name"],
                "model_class": model.get("model_class"),
                "task_type": model.get("task_type"),
                "created_at": model.get("created_at"),
                "production_version_id": production_version["id"] if production_version else None,
            }
        )
    return result


def list_versions(*, model_id, metadata_store):
    versions = metadata_store.find_model_versions(model_id=model_id)
    return [
        {"id": v["id"], "status": v["status"], "created_at": v.get("created_at")}
        for v in versions
    ]


def get_version_detail(*, model_version_id, metadata_store):
    version = metadata_store.find_model_version(model_version_id=model_version_id)
    if version is None:
        return None
    return {
        "model_version_id": model_version_id,
        "artifact_uri": version["artifact_uri"],
        "status": version["status"],
        "manifest": metadata_store.find_manifest(model_version_id=model_version_id),
        "eval_run": metadata_store.find_eval_run(model_version_id=model_version_id),
        "model_id": version["model_id"],
        "monitoring_config": metadata_store.find_monitoring_config(model_version_id=model_version_id),
        "deployment": metadata_store.find_deployment(model_version_id=model_version_id),
    }


def get_download_urls(*, model_version_id, metadata_store, blob_store):
    version = metadata_store.find_model_version(model_version_id=model_version_id)
    if version is None:
        return None

    artifact_url = blob_store.presigned_url(version["artifact_sha256"])

    fixture_url = None
    eval_run = metadata_store.find_eval_run(model_version_id=model_version_id)
    if eval_run and eval_run.get("fixture"):
        fixture_url = blob_store.presigned_url(eval_run["fixture"]["sha256"])

    return {
        "model_version_id": model_version_id,
        "artifact_url": artifact_url,
        "fixture_url": fixture_url,
    }
