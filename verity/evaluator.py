from typing import List, Optional
from .schemas import EvaluationResult
from .detectors.embeddings import EmbeddingDetector

class Evaluator:
    """Main evaluator for LLM outputs."""
    
    def __init__(self, model: str = "gpt-3.5-turbo", verbose: bool = False):
        self.model = model
        self.verbose = verbose
        
        # Initialize detectors
        self.detectors = {
            "embedding": EmbeddingDetector(),
        }
    
    def evaluate(
        self,
        prompt: str,
        response: str,
        context: str,
        ground_truth: Optional[str] = None,
        detectors: Optional[List[str]] = None
    ) -> EvaluationResult:
        """Evaluate an LLM output."""
        
        if detectors is None:
            detectors = ["embedding"]
        
        if self.verbose:
            print(f"Running detectors: {detectors}")
        
        # Run detectors
        scores = {}
        for detector_name in detectors:
            if detector_name not in self.detectors:
                raise ValueError(f"Unknown detector: {detector_name}")
            
            detector = self.detectors[detector_name]
            if self.verbose:
                print(f"Running {detector_name}...")
            
            score = detector.score(response, context)
            scores[detector_name] = score
            
            if self.verbose:
                print(f"  → {detector_name}: {score:.2f}")
        
        # Aggregate
        final_score = sum(scores.values()) / len(scores) if scores else 0.5
        
        # Generate alerts
        alerts = []
        if final_score < 0.6:
            alerts.append("hallucination_risk")
        
        # Generate recommendations
        recommendations = []
        if final_score < 0.7:
            recommendations.append("Add more context to improve score")
        
        return EvaluationResult(
            final_score=final_score,
            detector_scores=scores,
            alerts=alerts,
            recommendations=recommendations,
            metadata={"model": self.model}
        )