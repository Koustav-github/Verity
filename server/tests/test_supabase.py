from storage.models.supabase import SupabaseMetadataStore


class FakeTable:
    def __init__(self, name, calls):
        self.name = name
        self.calls = calls
        self._payload = None

    def insert(self, payload):
        self._payload = payload
        return self

    def execute(self):
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
            },
        )
    ]
