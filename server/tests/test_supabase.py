import pytest

from storage.models.supabase import SupabaseMetadataStore


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeTable:
    def __init__(self, name, calls, rows=None):
        self.name = name
        self.calls = calls
        self.rows = rows if rows is not None else []
        self._payload = None
        self._verb = None
        self._filters = []
        self._select_cols = None
        self._gte = None
        self._order = None
        self._limit = None

    def select(self, columns="*"):
        self._verb = "select"
        self._select_cols = columns
        return self

    def insert(self, payload):
        self._verb = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._verb = "update"
        self._payload = payload
        return self

    def upsert(self, payload, on_conflict=None):
        self._verb = "upsert"
        self._payload = payload
        self._on_conflict = on_conflict
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def in_(self, column, values):
        self._filters.append((column, "in", list(values)))
        return self

    def gte(self, column, value):
        self._gte = (column, value)
        return self

    def order(self, column, desc=False):
        self._order = (column, desc)
        return self

    def limit(self, count):
        self._limit = count
        return self

    def _matching_rows(self):
        def row_matches(row):
            for filt in self._filters:
                if len(filt) == 3:
                    col, _op, values = filt
                    if row.get(col) not in values:
                        return False
                else:
                    col, val = filt
                    if row.get(col) != val:
                        return False
            return True

        matches = [row for row in self.rows if row_matches(row)]
        if self._gte is not None:
            column, value = self._gte
            matches = [row for row in matches if row.get(column) >= value]
        if self._order is not None:
            column, desc = self._order
            matches = sorted(matches, key=lambda row: row.get(column), reverse=desc)
        if self._limit is not None:
            matches = matches[: self._limit]
        return matches

    def execute(self):
        if self._verb == "select":
            self.calls.append((self.name, "select", self._select_cols, list(self._filters)))
            return FakeResponse(self._matching_rows())
        if self._verb == "update":
            self.calls.append((self.name, "update", self._payload, list(self._filters)))
            # Real PostgREST returns the affected row(s) here (representation mode) —
            # an update matching zero rows comes back with an empty data array, which is
            # exactly the "silently did nothing" case the four registry mutations must
            # detect and raise on rather than reporting a false success.
            return FakeResponse(self._matching_rows())
        self.calls.append((self.name, self._payload))
        return FakeResponse([])


class FakeSupabaseClient:
    def __init__(self, rows=None):
        self.calls = []
        self.rows = rows or {}

    def table(self, name):
        return FakeTable(name, self.calls, rows=self.rows.get(name, []))


def test_save_model_version_inserts_a_row_and_returns_its_id():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    model_version_id = store.save_model_version(
        sha256="abc123",
        artifact_uri="s3://verity-artifacts/abc123",
        user_id="u_1",
        args={"framework_hint": "sklearn"},
        status="pending",
    )

    assert model_version_id.startswith("mv_")
    assert fake_client.calls == [
        (
            "model_version",
            {
                "id": model_version_id,
                "artifact_sha256": "abc123",
                "artifact_uri": "s3://verity-artifacts/abc123",
                "user_id": "u_1",
                "args": {"framework_hint": "sklearn"},
                "status": "pending",
            },
        )
    ]


def test_save_manifest_inserts_a_row_and_returns_its_id():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    manifest_id = store.save_manifest(
        model_version_id="mv_abc",
        manifest={
            "framework": "sklearn",
            "detected_via": "class name LogisticRegression",
            "model_class": "LogisticRegression",
            "hyperparameters": {"C": 0.5},
        },
    )

    assert manifest_id.startswith("mf_")
    assert fake_client.calls == [
        (
            "manifest",
            {
                "id": manifest_id,
                "model_version_id": "mv_abc",
                "framework": "sklearn",
                "detected_via": "class name LogisticRegression",
                "model_class": "LogisticRegression",
                "hyperparameters": {"C": 0.5},
                "task_type": None,
            },
        )
    ]


EVAL_RUN = {
    "mechanism": "labeled_holdout",
    "metric_set": {"resolved": ["accuracy"], "skipped": []},
    "scores": {"accuracy": 0.94, "resource.latency_p95_ms": 1.1},
    "thresholds": [{"metric": "accuracy", "op": ">=", "value": 0.7}],
    "verdict": "pass",
    "failed_on": [],
    "test_set_ref": "s3://verity/abc123",
    "fixture": {"kind": "labeled_holdout", "uri": "s3://verity/abc123"},
    "error": None,
    "started_at": "2026-08-03T10:00:00+00:00",
    "finished_at": "2026-08-03T10:00:02+00:00",
}


def test_save_eval_run_inserts_the_verdict_and_the_evidence_behind_it():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    eval_run_id = store.save_eval_run(model_version_id="mv_abc", eval_run=EVAL_RUN)

    assert eval_run_id.startswith("evr_")
    assert fake_client.calls == [
        ("eval_run", {"id": eval_run_id, "model_version_id": "mv_abc", **EVAL_RUN})
    ]


def test_update_model_version_status_targets_exactly_one_row():
    fake_client = FakeSupabaseClient(rows={"model_version": [{"id": "mv_abc"}]})
    store = SupabaseMetadataStore(client=fake_client)

    store.update_model_version_status(model_version_id="mv_abc", status="staging")

    assert fake_client.calls == [
        ("model_version", "update", {"status": "staging"}, [("id", "mv_abc")])
    ]


def test_update_model_version_status_raises_when_it_affects_no_rows():
    fake_client = FakeSupabaseClient(rows={"model_version": []})
    store = SupabaseMetadataStore(client=fake_client)

    with pytest.raises(ValueError):
        store.update_model_version_status(model_version_id="mv_missing", status="staging")


def test_find_model_returns_the_row_matching_user_and_name():
    fake_client = FakeSupabaseClient(
        rows={
            "model": [
                {"id": "mdl_1", "user_id": "u_1", "name": "fraud-classifier"},
                {"id": "mdl_2", "user_id": "u_1", "name": "churn-model"},
            ]
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    model = store.find_model(user_id="u_1", name="fraud-classifier")

    assert model == {"id": "mdl_1", "user_id": "u_1", "name": "fraud-classifier"}
    assert fake_client.calls == [
        ("model", "select", "*", [("user_id", "u_1"), ("name", "fraud-classifier")])
    ]


def test_find_model_returns_none_when_no_row_matches():
    fake_client = FakeSupabaseClient(rows={"model": []})
    store = SupabaseMetadataStore(client=fake_client)

    assert store.find_model(user_id="u_1", name="does-not-exist") is None


def test_find_model_version_by_hash_returns_the_matching_version():
    fake_client = FakeSupabaseClient(
        rows={
            "model_version": [
                {"id": "mv_1", "model_id": "mdl_1", "artifact_sha256": "abc123"},
                {"id": "mv_2", "model_id": "mdl_1", "artifact_sha256": "def456"},
            ]
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    version = store.find_model_version_by_hash(model_id="mdl_1", sha256="abc123")

    assert version == {"id": "mv_1", "model_id": "mdl_1", "artifact_sha256": "abc123"}
    assert fake_client.calls == [
        (
            "model_version",
            "select",
            "*",
            [("model_id", "mdl_1"), ("artifact_sha256", "abc123")],
        )
    ]


def test_find_model_version_by_hash_returns_none_when_no_version_matches():
    fake_client = FakeSupabaseClient(rows={"model_version": []})
    store = SupabaseMetadataStore(client=fake_client)

    assert store.find_model_version_by_hash(model_id="mdl_1", sha256="nope") is None


def test_find_production_version_returns_the_current_live_version():
    fake_client = FakeSupabaseClient(
        rows={
            "model_version": [
                {"id": "mv_1", "model_id": "mdl_1", "status": "archived"},
                {"id": "mv_2", "model_id": "mdl_1", "status": "production"},
            ]
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    version = store.find_production_version(model_id="mdl_1")

    assert version == {"id": "mv_2", "model_id": "mdl_1", "status": "production"}
    assert fake_client.calls == [
        ("model_version", "select", "*", [("model_id", "mdl_1"), ("status", "production")])
    ]


def test_find_production_version_returns_none_when_nothing_is_live():
    fake_client = FakeSupabaseClient(
        rows={"model_version": [{"id": "mv_1", "model_id": "mdl_1", "status": "pending"}]}
    )
    store = SupabaseMetadataStore(client=fake_client)

    assert store.find_production_version(model_id="mdl_1") is None


def test_create_model_inserts_a_row_and_returns_its_id():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    model_id = store.create_model(
        user_id="u_1", name="fraud-classifier", model_class="LogisticRegression",
        task_type="classification",
    )

    assert model_id.startswith("mdl_")
    assert fake_client.calls == [
        (
            "model",
            {
                "id": model_id,
                "user_id": "u_1",
                "name": "fraud-classifier",
                "model_class": "LogisticRegression",
                "task_type": "classification",
                "alert_email": None,
            },
        )
    ]


def test_link_model_version_sets_the_model_id_on_one_row():
    fake_client = FakeSupabaseClient(rows={"model_version": [{"id": "mv_abc"}]})
    store = SupabaseMetadataStore(client=fake_client)

    store.link_model_version(model_version_id="mv_abc", model_id="mdl_1")

    assert fake_client.calls == [
        ("model_version", "update", {"model_id": "mdl_1"}, [("id", "mv_abc")])
    ]


def test_link_model_version_raises_when_it_affects_no_rows():
    fake_client = FakeSupabaseClient(rows={"model_version": []})
    store = SupabaseMetadataStore(client=fake_client)

    with pytest.raises(ValueError):
        store.link_model_version(model_version_id="mv_missing", model_id="mdl_1")


def test_promote_model_version_sets_status_and_promoted_from():
    fake_client = FakeSupabaseClient(rows={"model_version": [{"id": "mv_abc"}]})
    store = SupabaseMetadataStore(client=fake_client)

    store.promote_model_version(model_version_id="mv_abc", eval_run_id="evr_1")

    assert fake_client.calls == [
        (
            "model_version",
            "update",
            {"status": "production", "promoted_from": "evr_1"},
            [("id", "mv_abc")],
        )
    ]


def test_promote_model_version_raises_when_it_affects_no_rows():
    fake_client = FakeSupabaseClient(rows={"model_version": []})
    store = SupabaseMetadataStore(client=fake_client)

    with pytest.raises(ValueError):
        store.promote_model_version(model_version_id="mv_missing", eval_run_id="evr_1")


def test_archive_model_version_sets_status_to_archived():
    fake_client = FakeSupabaseClient(rows={"model_version": [{"id": "mv_abc"}]})
    store = SupabaseMetadataStore(client=fake_client)

    store.archive_model_version(model_version_id="mv_abc")

    assert fake_client.calls == [
        ("model_version", "update", {"status": "archived"}, [("id", "mv_abc")])
    ]


def test_archive_model_version_raises_when_it_affects_no_rows():
    fake_client = FakeSupabaseClient(rows={"model_version": []})
    store = SupabaseMetadataStore(client=fake_client)

    with pytest.raises(ValueError):
        store.archive_model_version(model_version_id="mv_missing")


MONITORING_CONFIG = {
    "metrics": ["request_count", "latency_p50_ms", "latency_p95_ms", "latency_p99_ms", "error_rate"],
    "eval_reference": {
        "basis": "sandbox_feasibility",
        "eval_run_id": "evr_1",
        "latency_p95_ms": 0.234,
        "quality": {"accuracy": 1.0},
    },
}


def test_save_monitoring_config_inserts_a_row_and_returns_its_id():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    config_id = store.save_monitoring_config(
        model_version_id="mv_abc", eval_run_id="evr_1", config=MONITORING_CONFIG
    )

    assert config_id.startswith("mcfg_")
    assert fake_client.calls == [
        (
            "monitoring_config",
            {
                "id": config_id,
                "model_version_id": "mv_abc",
                "eval_run_id": "evr_1",
                "metrics": MONITORING_CONFIG["metrics"],
                "eval_reference": MONITORING_CONFIG["eval_reference"],
            },
        )
    ]


def test_find_monitoring_config_returns_the_row_for_that_version():
    fake_client = FakeSupabaseClient(
        rows={
            "monitoring_config": [
                {"id": "mcfg_1", "model_version_id": "mv_abc", "metrics": ["error_rate"]},
                {"id": "mcfg_2", "model_version_id": "mv_other", "metrics": []},
            ]
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    config = store.find_monitoring_config(model_version_id="mv_abc")

    assert config["id"] == "mcfg_1"
    assert fake_client.calls == [
        ("monitoring_config", "select", "*", [("model_version_id", "mv_abc")])
    ]


def test_find_monitoring_config_returns_none_when_the_version_is_not_monitored():
    fake_client = FakeSupabaseClient(rows={"monitoring_config": []})
    store = SupabaseMetadataStore(client=fake_client)

    assert store.find_monitoring_config(model_version_id="mv_nope") is None


def test_save_telemetry_events_inserts_the_whole_batch_at_once():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)
    events = [
        {"model_version_id": "mv_1", "occurred_at": "2026-08-16T10:00:00+00:00",
         "latency_ms": 1.5, "status": "ok", "error_type": None},
        {"model_version_id": "mv_1", "occurred_at": "2026-08-16T10:00:01+00:00",
         "latency_ms": 2.5, "status": "error", "error_type": "ValueError"},
    ]

    written = store.save_telemetry_events(events=events)

    assert written == 2
    assert fake_client.calls == [("telemetry_event", events)]


def test_save_telemetry_events_does_nothing_for_an_empty_batch():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    assert store.save_telemetry_events(events=[]) == 0
    assert fake_client.calls == []


def test_find_telemetry_events_returns_only_this_version_within_the_window():
    fake_client = FakeSupabaseClient(
        rows={
            "telemetry_event": [
                {"id": 1, "model_version_id": "mv_1", "occurred_at": "2026-08-16T10:00:00+00:00"},
                {"id": 2, "model_version_id": "mv_1", "occurred_at": "2026-08-15T10:00:00+00:00"},
                {"id": 3, "model_version_id": "mv_other", "occurred_at": "2026-08-16T10:00:00+00:00"},
            ]
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    events = store.find_telemetry_events(
        model_version_id="mv_1", since="2026-08-16T00:00:00+00:00"
    )

    assert [e["id"] for e in events] == [1]


def test_find_telemetry_events_honours_the_limit():
    rows = [
        {"id": n, "model_version_id": "mv_1", "occurred_at": f"2026-08-16T10:00:0{n}+00:00"}
        for n in range(5)
    ]
    fake_client = FakeSupabaseClient(rows={"telemetry_event": rows})
    store = SupabaseMetadataStore(client=fake_client)

    events = store.find_telemetry_events(
        model_version_id="mv_1", since="2026-08-16T00:00:00+00:00", limit=2
    )

    assert len(events) == 2


def test_save_deployment_inserts_a_building_row_with_a_prefixed_id():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    deployment_id = store.save_deployment(
        model_version_id="mv_1", image_tag="verity-model:mv_1", status="building"
    )

    assert deployment_id.startswith("dep_")
    assert fake_client.calls == [
        (
            "deployment",
            {
                "id": deployment_id,
                "model_version_id": "mv_1",
                "image_tag": "verity-model:mv_1",
                "status": "building",
            },
        )
    ]


def test_update_deployment_writes_only_the_fields_it_was_given():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    store.update_deployment(
        deployment_id="dep_1",
        status="live",
        host_port=49312,
        endpoint_url="http://localhost:49312",
    )

    assert fake_client.calls == [
        (
            "deployment",
            "update",
            {"status": "live", "host_port": 49312, "endpoint_url": "http://localhost:49312"},
            [("id", "dep_1")],
        )
    ]


def test_find_live_deployment_returns_none_when_the_version_never_deployed():
    store = SupabaseMetadataStore(client=FakeSupabaseClient())

    assert store.find_live_deployment(model_version_id="mv_1") is None


def test_find_live_deployment_ignores_failed_and_stopped_rows_for_the_same_version():
    fake_client = FakeSupabaseClient(
        rows={
            "deployment": [
                {"id": "dep_old", "model_version_id": "mv_1", "status": "stopped"},
                {"id": "dep_bad", "model_version_id": "mv_1", "status": "failed"},
                {"id": "dep_now", "model_version_id": "mv_1", "status": "live"},
            ]
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    assert store.find_live_deployment(model_version_id="mv_1")["id"] == "dep_now"


def test_save_manifest_persists_the_serving_columns_when_present():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    store.save_manifest(
        model_version_id="mv_1",
        manifest={
            "framework": "sklearn",
            "io_schema": {"n_features": 2},
            "environment": {"python_version": "3.12", "packages": {}},
        },
    )

    inserted = fake_client.calls[0][1]
    assert inserted["io_schema"] == {"n_features": 2}
    assert inserted["environment"] == {"python_version": "3.12", "packages": {}}


def test_save_manifest_omits_the_serving_columns_for_a_manifest_without_them():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    store.save_manifest(model_version_id="mv_1", manifest={"framework": "sklearn"})

    inserted = fake_client.calls[0][1]
    # Not null-filled: a pre-api-fication manifest must stay distinguishable from one
    # whose introspection ran and failed.
    assert "io_schema" not in inserted
    assert "environment" not in inserted


def test_find_production_version_by_name_returns_none_for_an_unknown_model():
    store = SupabaseMetadataStore(client=FakeSupabaseClient())

    assert store.find_production_version_by_name(user_id="u_1", name="nope") is None


def test_find_production_version_by_name_resolves_the_model_then_its_live_version():
    fake_client = FakeSupabaseClient(
        rows={
            "model": [{"id": "mdl_1", "user_id": "u_1", "name": "fraud"}],
            "model_version": [
                {"id": "mv_1", "model_id": "mdl_1", "status": "archived"},
                {"id": "mv_2", "model_id": "mdl_1", "status": "production"},
            ],
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    assert store.find_production_version_by_name(user_id="u_1", name="fraud")["id"] == "mv_2"


def test_create_model_persists_the_alert_email_when_given():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    store.create_model(
        user_id="u_1", name="fraud", model_class="sklearn",
        task_type="classification", alert_email="ops@example.com",
    )

    assert fake_client.calls[0][1]["alert_email"] == "ops@example.com"


def test_create_model_omits_alert_email_when_not_given():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    store.create_model(user_id="u_1", name="fraud", model_class="sklearn", task_type="classification")

    # Explicit None, not silently dropped: a fresh model legitimately has no configured
    # recipient, and the column should say so rather than never having been written.
    assert fake_client.calls[0][1]["alert_email"] is None


def test_save_monitoring_config_persists_alert_thresholds_when_present():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    store.save_monitoring_config(
        model_version_id="mv_1", eval_run_id="evr_1",
        config={"metrics": ["request_count"], "eval_reference": {},
               "alert_thresholds": {"error_rate_relative_increase": 0.5}},
    )

    inserted = fake_client.calls[0][1]
    assert inserted["alert_thresholds"] == {"error_rate_relative_increase": 0.5}


def test_save_monitoring_config_omits_alert_thresholds_when_absent():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    store.save_monitoring_config(
        model_version_id="mv_1", eval_run_id="evr_1",
        config={"metrics": ["request_count"], "eval_reference": {}},
    )

    assert "alert_thresholds" not in fake_client.calls[0][1]


def test_find_telemetry_event_by_prediction_id_returns_none_when_unknown():
    store = SupabaseMetadataStore(client=FakeSupabaseClient())

    assert store.find_telemetry_event_by_prediction_id(prediction_id="pred_nope") is None


def test_find_telemetry_event_by_prediction_id_finds_the_row():
    fake_client = FakeSupabaseClient(
        rows={"telemetry_event": [{"id": 1, "prediction_id": "pred_abc", "model_version_id": "mv_1"}]}
    )
    store = SupabaseMetadataStore(client=fake_client)

    event = store.find_telemetry_event_by_prediction_id(prediction_id="pred_abc")

    assert event["id"] == 1
    assert event["model_version_id"] == "mv_1"


def test_save_label_event_inserts_with_a_prefixed_id():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    label_id = store.save_label_event(telemetry_event_id=1, instance_index=0, actual={"y": 1})

    assert label_id.startswith("lbl_")
    assert fake_client.calls[0][0] == "label_event"


def test_find_labeled_outcomes_joins_labels_to_their_recorded_predictions():
    fake_client = FakeSupabaseClient(
        rows={
            "telemetry_event": [
                {
                    "id": 1, "model_version_id": "mv_1",
                    "prediction": {"predictions": [1, 0], "probabilities": [[0.1, 0.9], [0.8, 0.2]]},
                }
            ],
            "label_event": [
                {"telemetry_event_id": 1, "instance_index": 0, "actual": {"y": 1}},
                {"telemetry_event_id": 1, "instance_index": 1, "actual": {"y": 0}},
            ],
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    outcomes = store.find_labeled_outcomes(model_version_id="mv_1")

    assert len(outcomes) == 2
    assert outcomes[0] == {"y_true": 1, "y_pred": 1, "y_proba": [0.1, 0.9]}
    assert outcomes[1] == {"y_true": 0, "y_pred": 0, "y_proba": [0.8, 0.2]}


def test_find_labeled_outcomes_excludes_labels_belonging_to_a_different_version_even_under_a_small_limit():
    # label_event has more total rows (across two different model versions) than fit
    # under a small limit, proving the correlation now genuinely scopes by version
    # rather than relying on an arbitrary global slice of the whole table.
    fake_client = FakeSupabaseClient(
        rows={
            "telemetry_event": [
                {"id": 1, "model_version_id": "mv_1", "prediction": {"predictions": [1]}},
            ],
            "label_event": [
                # Other versions' labels are listed FIRST on purpose: under the old,
                # uncorrelated `.limit(limit)` over the whole label_event table, a
                # small limit would consume its budget on these before ever reaching
                # mv_1's own label below, silently starving mv_1 of labels it actually
                # has. ids 2 and 3 belong to telemetry events that are not in mv_1's
                # events_by_id at all.
                {"telemetry_event_id": 2, "instance_index": 0, "actual": {"y": 0}},
                {"telemetry_event_id": 3, "instance_index": 0, "actual": {"y": 0}},
                {"telemetry_event_id": 1, "instance_index": 0, "actual": {"y": 1}},
            ],
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    outcomes = store.find_labeled_outcomes(model_version_id="mv_1", limit=1)

    assert len(outcomes) == 1
    assert outcomes[0] == {"y_true": 1, "y_pred": 1, "y_proba": None}


def test_find_labeled_outcomes_returns_empty_when_the_version_has_no_telemetry_events():
    fake_client = FakeSupabaseClient(rows={"telemetry_event": [], "label_event": []})
    store = SupabaseMetadataStore(client=fake_client)

    assert store.find_labeled_outcomes(model_version_id="mv_1") == []


def test_find_model_by_version_returns_the_owning_models_alert_email():
    fake_client = FakeSupabaseClient(
        rows={
            "model_version": [{"id": "mv_1", "model_id": "mdl_1"}],
            "model": [{"id": "mdl_1", "alert_email": "ops@example.com"}],
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    model = store.find_model_by_version(model_version_id="mv_1")

    assert model["alert_email"] == "ops@example.com"


def test_save_alert_event_inserts_with_a_prefixed_id():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    alert_id = store.save_alert_event(
        model_version_id="mv_1", kind="systemic", metric="error_rate", detail={"recent": 0.3},
    )

    assert alert_id.startswith("alrt_")
    inserted = fake_client.calls[0][1]
    assert inserted["kind"] == "systemic"
    assert inserted["detail"] == {"recent": 0.3}


def test_update_alert_event_writes_emailed_at():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    store.update_alert_event(alert_event_id="alrt_1", emailed_at="2026-08-23T00:00:00+00:00")

    assert fake_client.calls[0] == (
        "alert_event", "update", {"emailed_at": "2026-08-23T00:00:00+00:00"}, [("id", "alrt_1")],
    )


def test_find_alert_events_returns_rows_for_the_version_newest_first():
    fake_client = FakeSupabaseClient(
        rows={
            "alert_event": [
                {"id": "alrt_1", "model_version_id": "mv_1", "created_at": "2026-08-01T00:00:00Z"},
                {"id": "alrt_2", "model_version_id": "mv_1", "created_at": "2026-08-02T00:00:00Z"},
                {"id": "alrt_3", "model_version_id": "mv_2", "created_at": "2026-08-03T00:00:00Z"},
            ]
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    alerts = store.find_alert_events(model_version_id="mv_1")

    assert [a["id"] for a in alerts] == ["alrt_2", "alrt_1"]


def test_find_models_by_user_returns_every_model_for_that_user():
    fake_client = FakeSupabaseClient(
        rows={
            "model": [
                {"id": "mdl_1", "user_id": "u_1", "name": "fraud"},
                {"id": "mdl_2", "user_id": "u_2", "name": "churn"},
            ]
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    models = store.find_models_by_user(user_id="u_1")

    assert models == [{"id": "mdl_1", "user_id": "u_1", "name": "fraud"}]
    assert fake_client.calls == [("model", "select", "*", [("user_id", "u_1")])]


def test_find_models_by_user_returns_an_empty_list_for_an_unknown_user():
    fake_client = FakeSupabaseClient(rows={"model": []})
    store = SupabaseMetadataStore(client=fake_client)

    assert store.find_models_by_user(user_id="nobody") == []


def test_find_model_versions_orders_newest_first():
    fake_client = FakeSupabaseClient(
        rows={
            "model_version": [
                {"id": "mv_1", "model_id": "mdl_1", "created_at": "2026-08-01T00:00:00+00:00"},
                {"id": "mv_2", "model_id": "mdl_1", "created_at": "2026-08-02T00:00:00+00:00"},
                {"id": "mv_3", "model_id": "mdl_2", "created_at": "2026-08-03T00:00:00+00:00"},
            ]
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    versions = store.find_model_versions(model_id="mdl_1")

    assert [v["id"] for v in versions] == ["mv_2", "mv_1"]
    assert fake_client.calls == [
        ("model_version", "select", "*", [("model_id", "mdl_1")])
    ]


def test_find_model_version_returns_the_row_by_id():
    fake_client = FakeSupabaseClient(
        rows={"model_version": [{"id": "mv_1", "status": "production"}]}
    )
    store = SupabaseMetadataStore(client=fake_client)

    assert store.find_model_version(model_version_id="mv_1") == {
        "id": "mv_1",
        "status": "production",
    }


def test_find_model_version_returns_none_when_missing():
    fake_client = FakeSupabaseClient(rows={"model_version": []})
    store = SupabaseMetadataStore(client=fake_client)

    assert store.find_model_version(model_version_id="mv_unknown") is None


def test_find_manifest_returns_the_row_for_that_version():
    fake_client = FakeSupabaseClient(
        rows={"manifest": [{"id": "mf_1", "model_version_id": "mv_1", "framework": "sklearn"}]}
    )
    store = SupabaseMetadataStore(client=fake_client)

    manifest = store.find_manifest(model_version_id="mv_1")

    assert manifest == {"id": "mf_1", "model_version_id": "mv_1", "framework": "sklearn"}


def test_find_manifest_returns_none_when_the_version_has_none():
    fake_client = FakeSupabaseClient(rows={"manifest": []})
    store = SupabaseMetadataStore(client=fake_client)

    assert store.find_manifest(model_version_id="mv_1") is None


def test_find_eval_run_returns_the_most_recent_row():
    fake_client = FakeSupabaseClient(
        rows={
            "eval_run": [
                {
                    "id": "evr_1",
                    "model_version_id": "mv_1",
                    "started_at": "2026-08-01T00:00:00+00:00",
                },
                {
                    "id": "evr_2",
                    "model_version_id": "mv_1",
                    "started_at": "2026-08-02T00:00:00+00:00",
                },
            ]
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    eval_run = store.find_eval_run(model_version_id="mv_1")

    assert eval_run["id"] == "evr_2"


def test_find_eval_run_returns_none_when_never_evaluated():
    fake_client = FakeSupabaseClient(rows={"eval_run": []})
    store = SupabaseMetadataStore(client=fake_client)

    assert store.find_eval_run(model_version_id="mv_1") is None


def test_find_deployment_returns_the_most_recent_row_regardless_of_status():
    fake_client = FakeSupabaseClient(
        rows={
            "deployment": [
                {
                    "id": "dep_1",
                    "model_version_id": "mv_1",
                    "status": "stopped",
                    "created_at": "2026-08-01T00:00:00+00:00",
                },
                {
                    "id": "dep_2",
                    "model_version_id": "mv_1",
                    "status": "live",
                    "created_at": "2026-08-02T00:00:00+00:00",
                },
            ]
        }
    )
    store = SupabaseMetadataStore(client=fake_client)

    deployment = store.find_deployment(model_version_id="mv_1")

    assert deployment["id"] == "dep_2"


def test_find_deployment_returns_none_when_never_deployed():
    fake_client = FakeSupabaseClient(rows={"deployment": []})
    store = SupabaseMetadataStore(client=fake_client)

    assert store.find_deployment(model_version_id="mv_1") is None
