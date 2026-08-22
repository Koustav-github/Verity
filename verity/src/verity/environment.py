"""Capture the environment the model was trained in.

This runs client-side and must stay there. The server introspecting its own installed
versions would describe the wrong machine entirely: a pickle is only reliably loadable
against the library versions that wrote it, and those live here, not on the server.
"""

import sys

# Distribution names as pip knows them, not import names — importlib.metadata and the
# rendered requirements.txt both key on the distribution.
PACKAGES = (
    "scikit-learn",
    "numpy",
    "scipy",
    "pandas",
    "xgboost",
    "lightgbm",
    # The artifact's serialization format, not a modelling library. The serving
    # container is what unpickles it, and cloudpickle's format is not guaranteed
    # stable across majors.
    "cloudpickle",
)


def capture() -> dict:
    packages = {}
    for name in PACKAGES:
        found = _version(name)
        if found is not None:
            packages[name] = found
    return {
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "packages": packages,
    }


def _version(name):
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(name)
    except PackageNotFoundError:
        return None
