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
        self.client.table("model_version").update({"status": status}).eq(
            "id", model_version_id
        ).execute()

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
