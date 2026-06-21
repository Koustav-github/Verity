from verity.metrics.answerrelevance import AnswerRelevance


class _FakeScorer:
    def __init__(self, value):
        self.value = value
        self.calls = []

    def score(self, a, b):
        self.calls.append((a, b))
        return self.value


def test_scores_question_against_answer():
    scorer = _FakeScorer(0.83)
    ar = AnswerRelevance(scorer)
    assert ar.score("What is X?", "X is a thing") == 0.83
    assert scorer.calls == [("What is X?", "X is a thing")]


def test_empty_answer_scores_zero():
    ar = AnswerRelevance(_FakeScorer(0.9))
    assert ar.score("What is X?", "") == 0.0
