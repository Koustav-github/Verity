import pytest

from verity.interrogation.connector import VectorStoreConnector


def test_connector_is_abstract():
    with pytest.raises(TypeError):
        VectorStoreConnector()


class _FakeConnector(VectorStoreConnector):
    def fetch_chunks(self, limit=None):
        chunks = ["a", "b", "c"]
        return chunks[:limit] if limit else chunks


def test_concrete_connector_returns_chunks():
    assert _FakeConnector().fetch_chunks() == ["a", "b", "c"]


def test_connector_respects_limit():
    assert _FakeConnector().fetch_chunks(limit=2) == ["a", "b"]
