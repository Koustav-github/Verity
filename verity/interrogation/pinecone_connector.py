from typing import List, Optional

from .connector import VectorStoreConnector


class PineconeConnector(VectorStoreConnector):
    """Reads chunk text from a Pinecone index (v9 SDK).

    The user supplies their Pinecone API key and either an index ``host`` (the
    "database link") or an ``index_name``. Chunk text is read from vector metadata
    under ``text_key`` (default "text"). Credentials never leave the machine.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        index_name: Optional[str] = None,
        host: Optional[str] = None,
        namespace: str = "",
        text_key: str = "text",
        _index=None,
    ):
        self.api_key = api_key
        self.index_name = index_name
        self.host = host
        self.namespace = namespace
        self.text_key = text_key
        self._index = _index  # injected for tests; real index built lazily otherwise

    def _get_index(self):
        if self._index is None:
            from pinecone import Pinecone  # optional dependency

            pc = Pinecone(api_key=self.api_key)
            self._index = (
                pc.Index(host=self.host) if self.host else pc.Index(self.index_name)
            )
        return self._index

    def fetch_chunks(self, limit: Optional[int] = None) -> List[str]:
        index = self._get_index()

        ids: List[str] = []
        for page in index.list(namespace=self.namespace):
            for item in page:
                # pinecone yields ListItem objects (with .id); tolerate bare strings too
                ids.append(item.id if hasattr(item, "id") else item)
            if limit is not None and len(ids) >= limit:
                ids = ids[:limit]
                break

        if not ids:
            return []

        response = index.fetch(ids=ids, namespace=self.namespace)
        chunks: List[str] = []
        for vid in ids:
            vector = response.vectors.get(vid)
            if vector is None:
                continue
            metadata = getattr(vector, "metadata", None) or {}
            text = metadata.get(self.text_key)
            if text:
                chunks.append(text)
        return chunks
