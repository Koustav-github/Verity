import subprocess
import sys


def test_importing_verity_does_not_import_torch():
    """Top-level `import verity` must stay lightweight (lazy-load principle)."""
    code = (
        "import verity, sys; "
        "heavy = [m for m in sys.modules if m == 'torch' or m.startswith('torch.')]; "
        "assert not heavy, heavy"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_evaluator_still_accessible_lazily():
    """The public API `from verity import Evaluator` must still work."""
    from verity import Evaluator

    assert Evaluator is not None
