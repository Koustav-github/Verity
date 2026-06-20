from verity.interrogation.generator import QuestionGenerator
from verity.interrogation.pipeline import InterrogationPipeline
from verity.interrogation.testset import TestCase, TestSet


class _FakeGenerator(QuestionGenerator):
    """One TestCase per chunk, deterministic."""

    def generate(self, chunk):
        return [
            TestCase(
                question=f"q:{chunk}",
                reference_context=chunk,
                reference_answer="a",
            )
        ]


def test_pipeline_generates_testset_from_chunks():
    pipe = InterrogationPipeline(generator=_FakeGenerator())
    ts = pipe.run(chunks=["c1", "c2"])
    assert isinstance(ts, TestSet)
    assert [c.question for c in ts] == ["q:c1", "q:c2"]


def test_pipeline_samples_n_chunks():
    pipe = InterrogationPipeline(generator=_FakeGenerator())
    ts = pipe.run(chunks=[f"c{i}" for i in range(10)], n=3, seed=42)
    assert len(ts) == 3


def test_pipeline_sampling_is_reproducible_with_seed():
    chunks = [f"c{i}" for i in range(10)]
    a = InterrogationPipeline(_FakeGenerator()).run(chunks=chunks, n=3, seed=42)
    b = InterrogationPipeline(_FakeGenerator()).run(chunks=chunks, n=3, seed=42)
    assert [c.question for c in a] == [c.question for c in b]


def test_pipeline_uses_all_chunks_when_n_exceeds_available():
    pipe = InterrogationPipeline(generator=_FakeGenerator())
    ts = pipe.run(chunks=["c1", "c2"], n=10)
    assert len(ts) == 2
