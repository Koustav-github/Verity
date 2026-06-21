#answer relevance tells us whether the answer that the LLM provided is valid and relevant to the question asked or not


class AnswerRelevance:
    """Offline answer-relevance metric: semantic similarity of question <-> answer.

    Uses an injected scorer with ``score(text_a, text_b) -> float`` (e.g. a local
    embedding model). No LLM-as-judge.
    """

    def __init__(self, scorer):
        self.scorer = scorer

    def score(self, question: str, answer: str) -> float:
        if not answer or not answer.strip():
            return 0.0
        return self.scorer.score(question, answer)
