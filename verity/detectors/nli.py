from .base import Detector


class NLIDetector(Detector):
    """Local NLI entailment scorer (cross-encoder), for faithfulness checking.

    ``entailment(premise, hypothesis)`` returns P(premise entails hypothesis) in
    0.0-1.0. Heavy deps load lazily, so importing/constructing is cheap and works
    without the ``local`` extra installed.
    """

    def __init__(self, model_name: str = "cross-encoder/nli-deberta-v3-base"):
        self.model_name = model_name
        self._model = None

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name)
        return self._model

    def entailment(self, premise: str, hypothesis: str) -> float:
        """P(premise entails hypothesis). Label order: contradiction/entailment/neutral."""
        import numpy as np

        logits = np.array(
            self._get_model().predict([(premise, hypothesis)])
        ).reshape(-1)
        exp = np.exp(logits - logits.max())
        probs = exp / exp.sum()
        return float(probs[1])  # entailment

    def score(self, premise: str, hypothesis: str) -> float:
        return self.entailment(premise, hypothesis)

    def get_name(self) -> str:
        return "nli"
