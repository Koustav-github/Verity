from .schemas import EvaluationResult, EvaluationRequest

__version__ = "0.1.0"
__all__ = [
    "Evaluator",
    "EvaluationResult",
    "EvaluationRequest",
]


def __getattr__(name):
    # Lazy import so `import verity` stays lightweight (no torch until Evaluator is used).
    if name == "Evaluator":
        from .evaluator import Evaluator

        return Evaluator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
