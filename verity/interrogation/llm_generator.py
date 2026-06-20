import json
from typing import List

from .generator import QuestionGenerator
from .testset import TestCase

_PROMPT = """\
You are creating an evaluation test set from a single source passage.
Generate realistic questions a user might ask, INCLUDING questions that require
reasoning or applying the information to a specific case (not just fact lookup).

STRICT GROUNDING RULES (critical):
- Use ONLY information explicitly stated in the passage. Do NOT use outside knowledge.
- Every "answer" must be fully answerable from the passage alone.
- If a detail (e.g. names, dates, figures) is not in the passage, do NOT ask about it.

Return ONLY a JSON array of objects with keys "question" and "answer".

Passage:
{chunk}
"""


def _strip_code_fences(raw: str) -> str:
    """Remove a leading ```/```json fence and trailing ``` if present."""
    text = raw.strip()
    if text.startswith("```"):
        newline = text.find("\n")
        text = text[newline + 1 :] if newline != -1 else ""
        if "```" in text:
            text = text[: text.rfind("```")]
    return text.strip()


class LLMQuestionGenerator(QuestionGenerator):
    """Opt-in generator backed by an (injected) LLM client.

    The client must expose ``complete(prompt: str) -> str``. This keeps the
    generator decoupled from any specific provider and unit-testable with a fake.
    """

    def __init__(self, client):
        self.client = client

    def build_prompt(self, chunk: str) -> str:
        return _PROMPT.format(chunk=chunk)

    def generate(self, chunk: str) -> List[TestCase]:
        raw = self.client.complete(self.build_prompt(chunk))
        try:
            items = json.loads(_strip_code_fences(raw))
        except (json.JSONDecodeError, TypeError, AttributeError):
            return []

        if not isinstance(items, list):
            return []

        cases: List[TestCase] = []
        for item in items:
            if not isinstance(item, dict) or "question" not in item or "answer" not in item:
                continue
            cases.append(
                TestCase(
                    question=item["question"],
                    reference_context=chunk,
                    reference_answer=item["answer"],
                )
            )
        return cases
