import pytest

from verity.interrogation.generator import QuestionGenerator
from verity.interrogation.testset import TestCase


def test_question_generator_is_abstract():
    with pytest.raises(TypeError):
        QuestionGenerator()


class _FakeGenerator(QuestionGenerator):
    def generate(self, chunk):
        return [
            TestCase(
                question=f"What about {chunk}?",
                reference_context=chunk,
                reference_answer="ans",
            )
        ]


def test_concrete_generator_returns_testcases():
    cases = _FakeGenerator().generate("Paris is in France.")
    assert len(cases) == 1
    assert cases[0].reference_context == "Paris is in France."
    assert cases[0].question == "What about Paris is in France.?"
