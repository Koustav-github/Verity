import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Union


@dataclass
class TestCase:
    """One generated evaluation item: a question with its source chunk and answer."""

    __test__ = False  # not a pytest test class

    question: str
    reference_context: str
    reference_answer: str


@dataclass
class TestSet:
    """A collection of generated TestCases."""

    __test__ = False  # not a pytest test class

    cases: List[TestCase] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.cases)

    def __iter__(self):
        return iter(self.cases)

    def to_jsonl(self, path: Union[str, Path]) -> None:
        """Write the test set as JSON Lines — one TestCase object per line."""
        with Path(path).open("w", encoding="utf-8") as f:
            for case in self.cases:
                f.write(json.dumps(asdict(case), ensure_ascii=False) + "\n")

    @classmethod
    def from_jsonl(cls, path: Union[str, Path]) -> "TestSet":
        """Load a test set from a JSON Lines file."""
        cases: List[TestCase] = []
        with Path(path).open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    cases.append(TestCase(**json.loads(line)))
        return cls(cases=cases)
