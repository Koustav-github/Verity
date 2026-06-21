from verity.runner import EvalRunner


class _FakeAnswerRelevance:
    def score(self, question, answer):
        return {"a1": 0.9, "a2": 0.5}[answer]


class _FakeContextRelevance:
    def score(self, question, context):
        return 0.6


def test_evaluate_returns_both_triad_scores():
    r = EvalRunner(_FakeAnswerRelevance(), _FakeContextRelevance())
    res = r.evaluate(question="q", context=["ctx"], answer="a1")
    assert res.question == "q"
    assert res.answer_relevance == 0.9
    assert res.context_relevance == 0.6


def test_evaluate_batch_reports_per_item_and_means():
    r = EvalRunner(_FakeAnswerRelevance(), _FakeContextRelevance())
    report = r.evaluate_batch(
        [
            {"question": "q1", "context": ["c"], "answer": "a1"},
            {"question": "q2", "context": ["c"], "answer": "a2"},
        ]
    )
    assert len(report.items) == 2
    assert report.mean_answer_relevance == 0.7   # (0.9 + 0.5) / 2
    assert report.mean_context_relevance == 0.6
