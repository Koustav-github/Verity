from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime

@dataclass
class EvaluationResult:
    """Result of evaluating an LLM output."""
    
    final_score: float
    detector_scores: Dict[str, float]
    alerts: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    
    def __str__(self):
        return f"""
Verity Evaluation Result
========================
Final Score: {self.final_score:.2f}
Detectors: {self.detector_scores}
Alerts: {', '.join(self.alerts) if self.alerts else 'None'}
Recommendations: {', '.join(self.recommendations) if self.recommendations else 'None'}
        """

@dataclass
class EvaluationRequest:
    """Input to the evaluator."""
    prompt: str
    response: str
    context: str
    ground_truth: Optional[str] = None
    detectors: List[str] = field(default_factory=lambda: ["embedding"])
    metadata: Dict = field(default_factory=dict)