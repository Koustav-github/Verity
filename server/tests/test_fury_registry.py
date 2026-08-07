from agents.brain3.fury.registry import find_existing


class FakeMetadataStore:
    def __init__(self, models=None, versions=None):
        self.models = models or {}
        self.versions = versions or {}

    def find_model(self, *, user_id, name):
        return self.models.get((user_id, name))

    def find_model_version_by_hash(self, *, model_id, sha256):
        return self.versions.get((model_id, sha256))


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
