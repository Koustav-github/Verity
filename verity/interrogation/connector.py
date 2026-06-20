from abc import ABC, abstractmethod
from typing import List, Optional


class VectorStoreConnector(ABC):
    """Reads source chunk text from a user's vector store.

    Concrete implementations (e.g. Pinecone) connect with user-supplied credentials
    and return the stored chunk text. Sampling is done downstream by the pipeline.
    """

    @abstractmethod
    def fetch_chunks(self, limit: Optional[int] = None) -> List[str]:
        """Return chunk texts, optionally capped at ``limit``."""
        raise NotImplementedError
