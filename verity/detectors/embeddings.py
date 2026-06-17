import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
from sentence_transformers import SentenceTransformer
from .base import Detector

class EmbeddingDetector(Detector):
    """Semantic similarity between response and context."""
    
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        print(f"Loading embedding model: {model_name}...")
        self.model = SentenceTransformer(model_name)
        self.model_name = model_name
    
    def score(self, response: str, context: str) -> float:
        """Compare semantic similarity."""
        response_embedding = self.model.encode(response, convert_to_tensor=False)
        context_embedding = self.model.encode(context, convert_to_tensor=False)
        
        response_embedding = np.array(response_embedding).reshape(1, -1)
        context_embedding = np.array(context_embedding).reshape(1, -1)
        
        similarity = cosine_similarity(response_embedding, context_embedding)[0][0]
        score = max(0.0, min(1.0, similarity))
        
        return float(score)
    
    def get_name(self) -> str:
        return "embedding"