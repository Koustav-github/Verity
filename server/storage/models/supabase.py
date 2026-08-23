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
        row = {
            "id": manifest_id,
            "model_version_id": model_version_id,
            "framework": manifest["framework"],
            "detected_via": manifest.get("detected_via"),
            "model_class": manifest.get("model_class"),
            "hyperparameters": manifest.get("hyperparameters"),
            "task_type": manifest.get("task_type"),
        }
        # Added only when actually present. A manifest written before api-fication
        # legitimately carries none of these, and sending explicit nulls would make
        # every pre-existing row indistinguishable from one whose introspection failed.
        for column in ("io_schema", "environment", "serving_pattern"):
            if manifest.get(column) is not None:
                row[column] = manifest[column]
        self.client.table("manifest").insert(row).execute()
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

    def create_model(self, *, user_id, name, model_class, task_type, alert_email=None):
        model_id = f"mdl_{uuid.uuid4().hex}"
        self.client.table("model").insert(
            {
                "id": model_id,
                "user_id": user_id,
                "name": name,
                "model_class": model_class,
                "task_type": task_type,
                "alert_email": alert_email,
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
        row = {
            "id": config_id,
            "model_version_id": model_version_id,
            "eval_run_id": eval_run_id,
            "metrics": config["metrics"],
            "eval_reference": config["eval_reference"],
        }
        if "alert_thresholds" in config:
            row["alert_thresholds"] = config["alert_thresholds"]
        self.client.table("monitoring_config").insert(row).execute()
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

    def save_deployment(self, *, model_version_id, image_tag, status):
        deployment_id = f"dep_{uuid.uuid4().hex}"
        self.client.table("deployment").insert(
            {
                "id": deployment_id,
                "model_version_id": model_version_id,
                "image_tag": image_tag,
                "status": status,
            }
        ).execute()
        return deployment_id

    def update_deployment(self, *, deployment_id, **fields):
        # Open-ended on purpose: the build path fills container_id/host_port/endpoint_url
        # in the same round trip that flips the status to `live`, and the teardown path
        # writes only a status. Unlike the registry mutations, a zero-row update is not
        # raised on — a deployment row that has vanished must not fail a live request.
        self.client.table("deployment").update(fields).eq("id", deployment_id).execute()

    def find_live_deployment(self, *, model_version_id):
        response = (
            self.client.table("deployment")
            .select("*")
            .eq("model_version_id", model_version_id)
            .eq("status", "live")
            .limit(1)
            .execute()
        )
        return response.data[0] if response.data else None

    def find_production_version_by_name(self, *, user_id, name):
        model = self.find_model(user_id=user_id, name=name)
        if model is None:
            return None
        return self.find_production_version(model_id=model["id"])

    def find_telemetry_event_by_prediction_id(self, *, prediction_id):
        result = (
            self.client.table("telemetry_event")
            .select("*")
            .eq("prediction_id", prediction_id)
            .execute()
        )
        return result.data[0] if result.data else None

    def save_label_event(self, *, telemetry_event_id, instance_index, actual):
        label_id = f"lbl_{uuid.uuid4().hex}"
        # Upsert on (telemetry_event_id, instance_index): a corrected label overwrites
        # the earlier one instead of accumulating a duplicate that would double-count
        # in the next quality check.
        self.client.table("label_event").upsert(
            {
                "id": label_id,
                "telemetry_event_id": telemetry_event_id,
                "instance_index": instance_index,
                "actual": actual,
            },
            on_conflict="telemetry_event_id,instance_index",
        ).execute()
        return label_id

    def find_labeled_outcomes(self, *, model_version_id, limit=1000):
        events = (
            self.client.table("telemetry_event")
            .select("*")
            .eq("model_version_id", model_version_id)
            .limit(limit)
            .execute()
        ).data or []
        events_by_id = {event["id"]: event for event in events}
        if not events_by_id:
            return []

        labels = (
            self.client.table("label_event")
            .select("*")
            .in_("telemetry_event_id", list(events_by_id.keys()))
            .execute()
        ).data or []

        outcomes = []
        for label in labels:
            event = events_by_id.get(label["telemetry_event_id"])
            if event is None:
                continue
            index = label["instance_index"]
            prediction = event.get("prediction") or {}
            probabilities = prediction.get("probabilities")
            outcomes.append(
                {
                    # Every writer of label_event.actual (the outcomes route in Task 7)
                    # wraps the value as {"y": ...}; no other shape is ever produced.
                    "y_true": label["actual"]["y"],
                    "y_pred": prediction["predictions"][index],
                    "y_proba": probabilities[index] if probabilities is not None else None,
                }
            )
        return outcomes

    def find_model_by_version(self, *, model_version_id):
        version = (
            self.client.table("model_version")
            .select("*")
            .eq("id", model_version_id)
            .execute()
        )
        if not version.data:
            return None
        model = (
            self.client.table("model")
            .select("*")
            .eq("id", version.data[0]["model_id"])
            .execute()
        )
        return model.data[0] if model.data else None

    def save_alert_event(self, *, model_version_id, kind, metric, detail):
        alert_id = f"alrt_{uuid.uuid4().hex}"
        self.client.table("alert_event").insert(
            {
                "id": alert_id,
                "model_version_id": model_version_id,
                "kind": kind,
                "metric": metric,
                "detail": detail,
            }
        ).execute()
        return alert_id

    def update_alert_event(self, *, alert_event_id, emailed_at):
        self.client.table("alert_event").update({"emailed_at": emailed_at}).eq(
            "id", alert_event_id
        ).execute()

    def find_alert_events(self, *, model_version_id):
        result = (
            self.client.table("alert_event")
            .select("*")
            .eq("model_version_id", model_version_id)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []
