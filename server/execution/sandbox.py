import os
import subprocess
import sys
from pathlib import Path

import cloudpickle

_SERVER_DIR = Path(__file__).parent.parent

# The only variables the child inherits. Everything else — SUPABASE_*, AWS_*,
# VERITY_LLM_* — is withheld, so an artifact that runs arbitrary code inside predict()
# finds no credentials there. SYSTEMROOT is not optional on Windows: without it the
# interpreter fails to start.
_PASSTHROUGH_ENV = ("PATH", "SYSTEMROOT", "PYTHONPATH", "TEMP", "TMP")


class SandboxError(Exception):
    """The model could not be executed in the sandbox."""


def _child_env():
    return {name: os.environ[name] for name in _PASSTHROUGH_ENV if name in os.environ}


def _run(job, *, timeout):
    blob = cloudpickle.dumps(job)

    try:
        completed = subprocess.run(
            [sys.executable, "-m", "execution.runner"],
            input=blob,
            capture_output=True,
            timeout=timeout,
            cwd=str(_SERVER_DIR),
            env=_child_env(),
        )
    except subprocess.TimeoutExpired:
        raise SandboxError(f"model execution timed out after {timeout}s") from None

    if not completed.stdout:
        stderr = completed.stderr.decode(errors="replace").strip()
        raise SandboxError(
            f"sandbox exited with code {completed.returncode} and no output: {stderr}"
        )

    # Trusted direction: these bytes were written by our own runner, not by the model.
    result = cloudpickle.loads(completed.stdout)
    if "error" in result:
        error = result["error"]
        raise SandboxError(f"{error['type']}: {error['message']}")
    return result


def execute(*, model_payload, X, timeout=60):
    """Run a model against X in an isolated child process.

    Returns {"y_pred", "y_proba", "resource"}. Raises SandboxError if the child
    crashed, timed out, or returned something unreadable — Nat turns that into an
    `error` verdict rather than letting it escape.
    """
    return _run({"model_bytes": model_payload, "X": X}, timeout=timeout)


def introspect(*, model_payload, timeout=30):
    """Read the fitted estimator's input/output surface, without predicting.

    Same containment as execute(): loading an artifact is arbitrary code execution
    whether or not anything is predicted afterwards, so it happens behind the same
    credential allowlist. Shorter default timeout because nothing here does real work.
    """
    return _run({"model_bytes": model_payload, "mode": "introspect"}, timeout=timeout)
