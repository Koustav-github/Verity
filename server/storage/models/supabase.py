import os
import uuid


class SupabaseMetadataStore:
    def __init__(self, client=None):
        if client is not None:
            self.client = client
        else:
            from supabase import create_client

            self.client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    def save_model_version(self, *, sha256, artifact_uri, user_id, args, status):
        model_version_id = f"mv_{uuid.uuid4().hex}"
        self.client.table("model_version").insert(
            {
                "id": model_version_id,
                "artifact_sha256": sha256,
                "artifact_uri": artifact_uri,
                "user_id": user_id,
                "args": args,
                "status": status,
            }
        ).execute()
        return model_version_id

    def save_manifest(self, *, model_version_id, manifest):
        manifest_id = f"mf_{uuid.uuid4().hex}"
        self.client.table("manifest").insert(
            {
                "id": manifest_id,
                "model_version_id": model_version_id,
                "framework": manifest["framework"],
                "detected_via": manifest.get("detected_via"),
                "model_class": manifest.get("model_class"),
                "hyperparameters": manifest.get("hyperparameters"),
                "task_type": manifest.get("task_type"),
            }
        ).execute()
        return manifest_id

    def save_eval_run(self, *, model_version_id, eval_run):
        eval_run_id = f"evr_{uuid.uuid4().hex}"
        self.client.table("eval_run").insert(
            {"id": eval_run_id, "model_version_id": model_version_id, **eval_run}
        ).execute()
        return eval_run_id

    def update_model_version_status(self, *, model_version_id, status):
        result = (
            self.client.table("model_version")
            .update({"status": status})
            .eq("id", model_version_id)
            .execute()
        )
        if not result.data:
            raise ValueError(
                f"update_model_version_status affected no rows for model_version_id={model_version_id!r}"
            )

    def find_model(self, *, user_id, name):
        result = (
            self.client.table("model")
            .select("*")
            .eq("user_id", user_id)
            .eq("name", name)
            .execute()
        )
        return result.data[0] if result.data else None

    def find_model_version_by_hash(self, *, model_id, sha256):
        result = (
            self.client.table("model_version")
            .select("*")
            .eq("model_id", model_id)
            .eq("artifact_sha256", sha256)
            .execute()
        )
        return result.data[0] if result.data else None

    def find_production_version(self, *, model_id):
        result = (
            self.client.table("model_version")
            .select("*")
            .eq("model_id", model_id)
            .eq("status", "production")
            .execute()
        )
        return result.data[0] if result.data else None

    def create_model(self, *, user_id, name, model_class, task_type):
        model_id = f"mdl_{uuid.uuid4().hex}"
        self.client.table("model").insert(
            {
                "id": model_id,
                "user_id": user_id,
                "name": name,
                "model_class": model_class,
                "task_type": task_type,
            }
        ).execute()
        return model_id

    def link_model_version(self, *, model_version_id, model_id):
        result = (
            self.client.table("model_version")
            .update({"model_id": model_id})
            .eq("id", model_version_id)
            .execute()
        )
        if not result.data:
            raise ValueError(
                f"link_model_version affected no rows for model_version_id={model_version_id!r}"
            )

    def promote_model_version(self, *, model_version_id, eval_run_id):
        result = (
            self.client.table("model_version")
            .update({"status": "production", "promoted_from": eval_run_id})
            .eq("id", model_version_id)
            .execute()
        )
        if not result.data:
            raise ValueError(
                f"promote_model_version affected no rows for model_version_id={model_version_id!r}"
            )

    def archive_model_version(self, *, model_version_id):
        result = (
            self.client.table("model_version")
            .update({"status": "archived"})
            .eq("id", model_version_id)
            .execute()
        )
        if not result.data:
            raise ValueError(
                f"archive_model_version affected no rows for model_version_id={model_version_id!r}"
            )

    def save_monitoring_config(self, *, model_version_id, eval_run_id, config):
        config_id = f"mcfg_{uuid.uuid4().hex}"
        self.client.table("monitoring_config").insert(
            {
                "id": config_id,
                "model_version_id": model_version_id,
                "eval_run_id": eval_run_id,
                "metrics": config["metrics"],
                "eval_reference": config["eval_reference"],
            }
        ).execute()
        return config_id

    def find_monitoring_config(self, *, model_version_id):
        result = (
            self.client.table("monitoring_config")
            .select("*")
            .eq("model_version_id", model_version_id)
            .execute()
        )
        return result.data[0] if result.data else None

    def save_telemetry_events(self, *, events):
        """Batch insert. The SDK batches on its side, so this never sees one row at a time.

        Unlike the registry mutations, a zero-row result is not checked for: this is an
        insert, not an update — PostgREST raises on a failed insert (e.g. an FK violation
        for an unknown model_version_id) rather than silently affecting nothing.
        """
        if not events:
            return 0
        self.client.table("telemetry_event").insert(events).execute()
        return len(events)

    def find_telemetry_events(self, *, model_version_id, since, limit=10_000):
        result = (
            self.client.table("telemetry_event")
            .select("*")
            .eq("model_version_id", model_version_id)
            .gte("occurred_at", since)
            .order("occurred_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
