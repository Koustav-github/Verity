from types import SimpleNamespace

from verity.interrogation.pinecone_connector import PineconeConnector


class _FakeIndex:
    """Mimics the pinecone v9 Index: list() yields pages of ids, fetch() returns vectors."""

    def __init__(self, data, page_size=2):
        self._data = data  # dict: id -> metadata dict
        self._page_size = page_size
        self.fetched = None

    def list(self, namespace=""):
        # Real pinecone yields pages of ListItem objects (with .id), not bare strings.
        ids = list(self._data.keys())
        for i in range(0, len(ids), self._page_size):
            yield [SimpleNamespace(id=x) for x in ids[i : i + self._page_size]]

    def fetch(self, ids, namespace=""):
        self.fetched = list(ids)
        vectors = {
            i: SimpleNamespace(metadata=self._data[i]) for i in ids if i in self._data
        }
        return SimpleNamespace(vectors=vectors)


def test_fetch_chunks_extracts_text_metadata():
    data = {"v1": {"text": "chunk one"}, "v2": {"text": "chunk two"}}
    conn = PineconeConnector(_index=_FakeIndex(data))
    assert conn.fetch_chunks() == ["chunk one", "chunk two"]


def test_fetch_chunks_respects_limit():
    data = {f"v{i}": {"text": f"c{i}"} for i in range(10)}
    idx = _FakeIndex(data)
    conn = PineconeConnector(_index=idx)
    chunks = conn.fetch_chunks(limit=3)
    assert len(chunks) == 3
    assert len(idx.fetched) == 3  # didn't over-fetch


def test_fetch_chunks_skips_vectors_without_text():
    data = {"v1": {"text": "ok"}, "v2": {"other": "no text here"}}
    conn = PineconeConnector(_index=_FakeIndex(data))
    assert conn.fetch_chunks() == ["ok"]


def test_fetch_chunks_supports_custom_text_key():
    data = {"v1": {"content": "hi"}}
    conn = PineconeConnector(_index=_FakeIndex(data), text_key="content")
    assert conn.fetch_chunks() == ["hi"]
