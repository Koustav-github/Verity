import random
from typing import Iterable, List, Optional

from .generator import QuestionGenerator
from .testset import TestCase, TestSet


class InterrogationPipeline:
    """Runs a QuestionGenerator over source chunks and aggregates a TestSet."""

    def __init__(self, generator: QuestionGenerator):
        self.generator = generator

    def run(
        self,
        chunks: Iterable[str],
        n: Optional[int] = None,
        seed: Optional[int] = None,
    ) -> TestSet:
        chunks = list(chunks)
        if n is not None and n < len(chunks):
            chunks = random.Random(seed).sample(chunks, n)

        cases: List[TestCase] = []
        for chunk in chunks:
            cases.extend(self.generator.generate(chunk))
        return TestSet(cases=cases)
