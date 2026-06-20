from verity.interrogation.connector import VectorStoreConnector
from verity.interrogation.generator import QuestionGenerator
from verity.interrogation.testset import TestCase, TestSet
from verity.interrogation.testset_generator import TestsetGenerator


class _FakeConnector(VectorStoreConnector):
    def __init__(self):
        self.limit = "unset"

    def fetch_chunks(self, limit=None):
        self.limit = limit
        return ["c1", "c2"]


class _FakeGen(QuestionGenerator):
    def generate(self, chunk):
        return [
            TestCase(question=f"q:{chunk}", reference_context=chunk, reference_answer="a")
        ]


def test_produces_testset_from_connector():
    gen = TestsetGenerator(connector=_FakeConnector(), generator=_FakeGen())
    ts = gen.generate()
    assert isinstance(ts, TestSet)
    assert [c.question for c in ts] == ["q:c1", "q:c2"]


def test_generate_passes_fetch_limit_to_connector():
    conn = _FakeConnector()
    TestsetGenerator(connector=conn, generator=_FakeGen()).generate(fetch_limit=5)
    assert conn.limit == 5
