import sys

from verity.environment import capture


def test_capture_reports_the_running_python_minor_version():
    assert capture()["python_version"] == f"{sys.version_info.major}.{sys.version_info.minor}"


def test_capture_records_the_installed_scikit_learn_version():
    import sklearn

    assert capture()["packages"]["scikit-learn"] == sklearn.__version__


def test_capture_omits_packages_that_are_not_installed():
    packages = capture()["packages"]

    # The allowlist is deliberately wider than any one project's dependencies; absent
    # ones must be skipped rather than recorded as null, so the rendered
    # requirements.txt never asks pip for a package that was never there.
    assert all(version is not None for version in packages.values())
    assert set(packages).issubset(
        {"scikit-learn", "numpy", "scipy", "pandas", "xgboost", "lightgbm", "cloudpickle"}
    )


def test_capture_includes_cloudpickle_because_the_container_unpickles_the_artifact():
    # Not a framework, but the format the artifact is written in. A container with a
    # different cloudpickle major cannot be relied on to load the model at all.
    assert "cloudpickle" in capture()["packages"]
