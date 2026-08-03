from storage.models.supabase import SupabaseMetadataStore


class FakeTable:
    def __init__(self, name, calls):
        self.name = name
        self.calls = calls
        self._payload = None
        self._verb = None
        self._filters = None

    def insert(self, payload):
        self._verb = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._verb = "update"
        self._payload = payload
        return self

    def eq(self, column, value):
        self._filters = (column, value)
        return self

    def execute(self):
        if self._verb == "update":
            self.calls.append((self.name, "update", self._payload, self._filters))
        else:
            self.calls.append((self.name, self._payload))


class FakeSupabaseClient:
    def __init__(self):
        self.calls = []

    def table(self, name):
        return FakeTable(name, self.calls)


def test_save_model_version_inserts_a_row_and_returns_its_id():
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    model_version_id = store.save_model_version(
        sha256="abc123",
        artifact_uri="seaweedfs://verity-artifacts/abc123",
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
                "artifact_uri": "seaweedfs://verity-artifacts/abc123",
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
    "test_set_ref": "seaweedfs://verity/abc123",
    "fixture": {"kind": "labeled_holdout", "uri": "seaweedfs://verity/abc123"},
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
    fake_client = FakeSupabaseClient()
    store = SupabaseMetadataStore(client=fake_client)

    store.update_model_version_status(model_version_id="mv_abc", status="staging")

    assert fake_client.calls == [
        ("model_version", "update", {"status": "staging"}, ("id", "mv_abc"))
    ]
