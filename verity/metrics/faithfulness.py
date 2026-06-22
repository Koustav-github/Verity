#failthfulness tells us the number of claims in the answer that are also mentioned in the context and the model is not making up stuffs.

#*Important future prospect* :- ASR Attack success rate....number of jailbreaks successfull/number of jailbreaks attempted. Higer the number more the model prone to jailbreaks.

import re
from typing import Callable, List, Optional, Union

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> List[str]:
    """Lean offline claim splitter: break text into sentences."""
    return [s.strip() for s in _SENTENCE_BOUNDARY.split(text.strip()) if s.strip()]


class Faithfulness:
    """Offline faithfulness: fraction of answer claims entailed by the context.

    Splits the answer into claims, then checks each is supported by the context via
    a local NLI model (entailment), max-pooled across context chunks. No LLM-as-judge.

    ``nli`` must expose ``entailment(premise, hypothesis) -> float`` (0.0-1.0).
    """

    def __init__(
        self,
        nli,
        threshold: float = 0.5,
        splitter: Optional[Callable[[str], List[str]]] = None,
    ):
        self.nli = nli
        self.threshold = threshold
        self.splitter = splitter or split_sentences

    def score(self, answer: str, context: Union[str, List[str]]) -> float:
        chunks = [context] if isinstance(context, str) else list(context)
        claims = self.splitter(answer)
        if not claims:
            return 0.0

        supported = 0
        for claim in claims:
            best = max(
                (self.nli.entailment(chunk, claim) for chunk in chunks), default=0.0
            )
            if best >= self.threshold:
                supported += 1
        return supported / len(claims)

