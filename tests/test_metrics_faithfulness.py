from verity.metrics.faithfulness import Faithfulness, split_sentences


class _FakeNLI:
    """Maps (premise, hypothesis) -> entailment probability."""

    def __init__(self, mapping):
        self.mapping = mapping

    def entailment(self, premise, hypothesis):
        return self.mapping.get((premise, hypothesis), 0.0)


def _semicolon_split(text):
    return [s.strip() for s in text.split(";") if s.strip()]


def test_fraction_of_supported_claims():
    nli = _FakeNLI({("ctx", "claim A"): 0.9, ("ctx", "claim B"): 0.1})
    f = Faithfulness(nli, threshold=0.5, splitter=_semicolon_split)
    assert f.score("claim A; claim B", "ctx") == 0.5  # 1 of 2 supported


def test_claim_supported_by_any_chunk_maxpool():
    nli = _FakeNLI({("c1", "claim"): 0.2, ("c2", "claim"): 0.8})
    f = Faithfulness(nli, threshold=0.5, splitter=_semicolon_split)
    assert f.score("claim", ["c1", "c2"]) == 1.0  # entailed by c2


def test_empty_answer_scores_zero():
    f = Faithfulness(_FakeNLI({}), splitter=_semicolon_split)
    assert f.score("", "ctx") == 0.0


def test_default_splitter_splits_on_sentence_boundaries():
    assert split_sentences("Paris is in France. It is big!") == [
        "Paris is in France.",
        "It is big!",
    ]
