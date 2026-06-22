from dataclasses import dataclass
from typing import List, Union


@dataclass
class ItemScore:
    """Triad scores for a single (question, context, answer) item."""

    question: str
    answer: str
    answer_relevance: float
    context_relevance: float
    faithfulness: float


@dataclass
class EvalReport:
    """Aggregated results over a batch of items."""

    items: List[ItemScore]

    def _mean(self, attr: str) -> float:
        if not self.items:
            return 0.0
        return sum(getattr(i, attr) for i in self.items) / len(self.items)

    @property
    def mean_answer_relevance(self) -> float:
        return self._mean("answer_relevance")

    @property
    def mean_context_relevance(self) -> float:
        return self._mean("context_relevance")

    @property
    def mean_faithfulness(self) -> float:
        return self._mean("faithfulness")


class EvalRunner:
    """Scores RAG outputs on the offline triad (answer + context relevance).

    Takes already-built metric objects (each with ``score(...)``), so it stays
    decoupled from how those metrics are implemented.
    """

    def __init__(self, answer_relevance, context_relevance, faithfulness):
        self.answer_relevance = answer_relevance
        self.context_relevance = context_relevance
        self.faithfulness = faithfulness

    def evaluate(
        self, question: str, context: Union[str, List[str]], answer: str
    ) -> ItemScore:
        return ItemScore(
            question=question,
            answer=answer,
            answer_relevance=self.answer_relevance.score(question, answer),
            context_relevance=self.context_relevance.score(question, context),
            faithfulness=self.faithfulness.score(answer, context),
        )

    def evaluate_batch(self, items: List[dict]) -> EvalReport:
        scored = [
            self.evaluate(it["question"], it["context"], it["answer"]) for it in items
        ]
        return EvalReport(items=scored)
