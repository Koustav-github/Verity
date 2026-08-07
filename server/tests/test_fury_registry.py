from agents.brain3.fury.registry import find_existing, register


class FakeMetadataStore:
    def __init__(self, models=None, versions=None):
        self.models = models or {}
        self.versions = versions or {}
        self.created_models = []
        self.linked = []
        self.status_updates = []
        self.promotions = []
        self.archived = []
        self._next_model_id = 1

    def find_model(self, *, user_id, name):
        return self.models.get((user_id, name))

    def find_model_version_by_hash(self, *, model_id, sha256):
        return self.versions.get((model_id, sha256))

    def create_model(self, *, user_id, name, model_class, task_type):
        model_id = f"mdl_{self._next_model_id}"
        self._next_model_id += 1
        self.created_models.append(
            {"id": model_id, "user_id": user_id, "name": name,
             "model_class": model_class, "task_type": task_type}
        )
        self.models[(user_id, name)] = {"id": model_id}
        return model_id

    def link_model_version(self, *, model_version_id, model_id):
        self.linked.append((model_version_id, model_id))

    def update_model_version_status(self, *, model_version_id, status):
        self.status_updates.append((model_version_id, status))

    def find_production_version(self, *, model_id):
        return None

    def promote_model_version(self, *, model_version_id, eval_run_id):
        self.promotions.append((model_version_id, eval_run_id))

    def archive_model_version(self, *, model_version_id):
        self.archived.append(model_version_id)


def test_find_existing_returns_none_when_no_model_exists_under_that_name():
    store = FakeMetadataStore()

    assert find_existing(
        user_id="u_1", sha256="abc123", name="fraud-classifier", metadata_store=store
    ) is None


def test_find_existing_returns_none_when_the_model_exists_but_the_hash_does_not_match():
    store = FakeMetadataStore(models={("u_1", "fraud-classifier"): {"id": "mdl_1"}})

    assert find_existing(
        user_id="u_1", sha256="abc123", name="fraud-classifier", metadata_store=store
    ) is None


def test_find_existing_returns_the_version_when_both_the_name_and_hash_match():
    store = FakeMetadataStore(
        models={("u_1", "fraud-classifier"): {"id": "mdl_1"}},
        versions={("mdl_1", "abc123"): {"id": "mv_1", "status": "production"}},
    )

    result = find_existing(
        user_id="u_1", sha256="abc123", name="fraud-classifier", metadata_store=store
    )

    assert result == {"id": "mv_1", "status": "production"}


MANIFEST = {"framework": "sklearn", "model_class": "LogisticRegression", "task_type": "classification"}


def test_register_creates_a_model_on_the_first_upload_under_a_new_name():
    store = FakeMetadataStore()

    result = register(
        user_id="u_1", name="fraud-classifier", model_version_id="mv_1",
        manifest=MANIFEST, verdict=None, eval_run_id=None, metadata_store=store,
    )

    assert store.created_models == [
        {
            "id": result["model_id"], "user_id": "u_1", "name": "fraud-classifier",
            "model_class": "LogisticRegression", "task_type": "classification",
        }
    ]


def test_register_reuses_the_existing_model_on_a_second_upload_under_the_same_name():
    store = FakeMetadataStore(models={("u_1", "fraud-classifier"): {"id": "mdl_existing"}})

    result = register(
        user_id="u_1", name="fraud-classifier", model_version_id="mv_2",
        manifest=MANIFEST, verdict=None, eval_run_id=None, metadata_store=store,
    )

    assert store.created_models == []
    assert result["model_id"] == "mdl_existing"


def test_register_links_the_model_version_regardless_of_verdict():
    store = FakeMetadataStore()

    result = register(
        user_id="u_1", name="fraud-classifier", model_version_id="mv_1",
        manifest=MANIFEST, verdict=None, eval_run_id=None, metadata_store=store,
    )

    assert store.linked == [("mv_1", result["model_id"])]


def test_register_with_no_verdict_reports_pending_and_writes_no_status():
    store = FakeMetadataStore()

    result = register(
        user_id="u_1", name="fraud-classifier", model_version_id="mv_1",
        manifest=MANIFEST, verdict=None, eval_run_id=None, metadata_store=store,
    )

    assert result["status"] == "pending"
    assert result["archived_model_version_id"] is None
    assert store.status_updates == []


def test_register_with_a_failing_verdict_writes_staging_failed():
    store = FakeMetadataStore()

    result = register(
        user_id="u_1", name="fraud-classifier", model_version_id="mv_1",
        manifest=MANIFEST, verdict="fail", eval_run_id="evr_1", metadata_store=store,
    )

    assert result["status"] == "staging_failed"
    assert store.status_updates == [("mv_1", "staging_failed")]
    assert store.promotions == []


def test_register_with_an_error_verdict_also_writes_staging_failed():
    store = FakeMetadataStore()

    result = register(
        user_id="u_1", name="fraud-classifier", model_version_id="mv_1",
        manifest=MANIFEST, verdict="error", eval_run_id="evr_1", metadata_store=store,
    )

    assert result["status"] == "staging_failed"
    assert store.status_updates == [("mv_1", "staging_failed")]


def test_register_with_a_passing_verdict_promotes_the_version_to_production():
    store = FakeMetadataStore()

    result = register(
        user_id="u_1", name="fraud-classifier", model_version_id="mv_1",
        manifest=MANIFEST, verdict="pass", eval_run_id="evr_1", metadata_store=store,
    )

    assert result["status"] == "production"
    assert store.promotions == [("mv_1", "evr_1")]


def test_register_with_a_passing_verdict_and_no_incumbent_archives_nothing():
    store = FakeMetadataStore()

    result = register(
        user_id="u_1", name="fraud-classifier", model_version_id="mv_1",
        manifest=MANIFEST, verdict="pass", eval_run_id="evr_1", metadata_store=store,
    )

    assert store.archived == []
    assert result["archived_model_version_id"] is None


def test_register_with_a_passing_verdict_archives_the_current_production_version():
    class StoreWithIncumbent(FakeMetadataStore):
        def find_production_version(self, *, model_id):
            return {"id": "mv_old"}

    store = StoreWithIncumbent(models={("u_1", "fraud-classifier"): {"id": "mdl_1"}})

    result = register(
        user_id="u_1", name="fraud-classifier", model_version_id="mv_new",
        manifest=MANIFEST, verdict="pass", eval_run_id="evr_1", metadata_store=store,
    )

    assert store.archived == ["mv_old"]
    assert result["archived_model_version_id"] == "mv_old"
    assert store.promotions == [("mv_new", "evr_1")]
