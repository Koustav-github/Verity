from abc import ABC, abstractmethod
from typing import List

from .testset import TestCase


class QuestionGenerator(ABC):
    """Turns a source chunk into evaluation TestCases (question + answer)."""

    @abstractmethod
    def generate(self, chunk: str) -> List[TestCase]:
        """Generate one or more TestCases from a single context chunk."""
        raise NotImplementedError
