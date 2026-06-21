from .base import Detector


class EmbeddingDetector(Detector):
    """Semantic similarity between two texts via a local embedding model.

    Heavy deps (sentence-transformers / sklearn) are imported lazily and the model
    loads on first ``score()`` call, so importing/constructing this is cheap and
    works without the ``local`` extra installed.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return self._model

    def score(self, text_a: str, text_b: str) -> float:
        """Cosine similarity of two texts, clamped to 0.0-1.0 (higher = closer)."""
        import numpy as np
        from sklearn.metrics.pairwise import cosine_similarity

        model = self._get_model()
        emb_a = np.array(model.encode(text_a, convert_to_tensor=False)).reshape(1, -1)
        emb_b = np.array(model.encode(text_b, convert_to_tensor=False)).reshape(1, -1)
        similarity = cosine_similarity(emb_a, emb_b)[0][0]
        return float(max(0.0, min(1.0, similarity)))

    def get_name(self) -> str:
        return "embedding"
