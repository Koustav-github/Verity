# contextRelevance tells us that the context/chunks retrieved from the database by the model is valid for the given question or not

from typing import List, Union


class ContextRelevance:
    """Offline context-relevance metric: how relevant the retrieved chunks are to
    the question. Scores each chunk's semantic similarity to the question and
    aggregates (mean) -> retrieval signal-to-noise. No LLM-as-judge.

    Uses an injected scorer with ``score(text_a, text_b) -> float``.
    """

    def __init__(self, scorer):
        self.scorer = scorer

    def score(self, question: str, contexts: Union[str, List[str]]) -> float:
        if isinstance(contexts, str):
            contexts = [contexts]
        if not contexts:
            return 0.0
        scores = [self.scorer.score(question, chunk) for chunk in contexts]
        return sum(scores) / len(scores)
