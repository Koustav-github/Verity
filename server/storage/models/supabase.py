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
            }
        ).execute()
        return manifest_id
