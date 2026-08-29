"""Render the directory Docker builds a model-version image from.

Pure filesystem work: no Docker, no network, no credentials. That is exactly what makes
the whole build path testable without a daemon anywhere in the loop.
"""

import json
import shutil
from pathlib import Path

_TEMPLATE = Path(__file__).parent / "app_template.py"

# Pinned by Verity, not by the customer: this is our serving stack, and a customer's
# training environment has no opinion about it.
SERVING_REQUIREMENTS = (
    "fastapi==0.116.1",
    "uvicorn[standard]==0.35.0",
    "pydantic==2.12.5",
)

# `environment.packages` reflects everything Verity's SDK found installed on the
# *training* machine, not what this particular model actually needs -- a user who has
# ever installed xgboost or lightgbm gets them pinned into every model's image, sklearn
# included. That bit us for real: xgboost's Linux wheel drags in nvidia-nccl-cu12, a
# ~340MB CUDA library, as a transitive dependency even for CPU-only use, which turned an
# unrelated sklearn model's image build slow and prone to failing mid-download. Only pin
# a framework-specific package when the manifest says the model actually belongs to it.
_FRAMEWORK_ONLY_PACKAGES = {
    "xgboost": {"xgboost"},
    "lightgbm": {"lightgbm"},
}

_DOCKERFILE = """FROM python:{python_version}-slim

ENV PYTHONUNBUFFERED=1 \\
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# LightGBM's compiled Booster dynamically links libgomp.so.1 (OpenMP) at import
# time; pip installs the wheel, not the system library, and python:*-slim doesn't
# carry it. Small and harmless for models that don't need it -- cheaper than
# special-casing which frameworks require which native libraries.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \\
    && rm -rf /var/lib/apt/lists/*

# Dependencies in their own layer, before the model: a second model on the same
# dependency set then reuses this layer and builds in seconds instead of minutes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY contract.json .
COPY model.pkl .

RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# curl isn't in the slim image, so probe /health with stdlib urllib instead.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \\
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
"""


def image_tag(model_version_id: str) -> str:
    return f"verity-model:{model_version_id}"


def render_context(*, dest, payload, io_schema, environment, framework=None) -> None:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    (dest / "model.pkl").write_bytes(payload)
    (dest / "contract.json").write_text(json.dumps(io_schema))
    shutil.copyfile(_TEMPLATE, dest / "app.py")
    (dest / "requirements.txt").write_text(_render_requirements(environment, framework))
    (dest / "Dockerfile").write_text(
        _DOCKERFILE.format(python_version=(environment or {}).get("python_version") or "3.12")
    )


def _render_requirements(environment, framework=None) -> str:
    # `==` throughout, never `>=`: reproducing the environment that wrote the pickle is
    # the entire purpose of building per-version images. A floating pin reintroduces the
    # version skew this design exists to remove.
    packages = dict((environment or {}).get("packages", {}))
    for fw, names in _FRAMEWORK_ONLY_PACKAGES.items():
        if fw != framework:
            for name in names:
                packages.pop(name, None)
    pins = [f"{name}=={version}" for name, version in sorted(packages.items())]
    return "\n".join([*pins, *SERVING_REQUIREMENTS]) + "\n"
