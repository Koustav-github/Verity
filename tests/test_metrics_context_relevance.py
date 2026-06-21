from verity.metrics.contextrelevance import ContextRelevance


class _PerChunkScorer:
    """Returns a preset score per chunk text."""

    def __init__(self, mapping):
        self.mapping = mapping

    def score(self, question, chunk):
        return self.mapping[chunk]


def test_means_per_chunk_relevance():
    scorer = _PerChunkScorer({"relevant": 1.0, "noise": 0.0})
    cr = ContextRelevance(scorer)
    assert cr.score("q", ["relevant", "noise"]) == 0.5


def test_accepts_single_string_context():
    cr = ContextRelevance(_PerChunkScorer({"one chunk": 0.7}))
    assert cr.score("q", "one chunk") == 0.7


def test_empty_context_scores_zero():
    cr = ContextRelevance(_PerChunkScorer({}))
    assert cr.score("q", []) == 0.0
