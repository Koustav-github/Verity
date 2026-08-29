import json

from serving.build import image_tag, render_context

ENVIRONMENT = {
    "python_version": "3.12",
    "packages": {"scikit-learn": "1.7.2", "numpy": "2.3.5", "cloudpickle": "3.1.2"},
}
IO_SCHEMA = {
    "n_features": 2,
    "feature_names": ["age", "fare"],
    "classes": [0, 1],
    "has_predict_proba": True,
}


def _render(tmp_path, **overrides):
    render_context(
        dest=tmp_path,
        payload=overrides.get("payload", b"model-bytes"),
        io_schema=overrides.get("io_schema", IO_SCHEMA),
        environment=overrides.get("environment", ENVIRONMENT),
        framework=overrides.get("framework"),
    )
    return tmp_path


def test_render_context_writes_every_file_the_image_needs(tmp_path):
    _render(tmp_path)

    for name in ("Dockerfile", "requirements.txt", "app.py", "contract.json", "model.pkl"):
        assert (tmp_path / name).exists(), name


def test_the_contract_carries_the_io_schema_verbatim(tmp_path):
    _render(tmp_path)

    assert json.loads((tmp_path / "contract.json").read_text()) == IO_SCHEMA


def test_requirements_pin_the_captured_versions_exactly(tmp_path):
    _render(tmp_path)

    lines = (tmp_path / "requirements.txt").read_text().splitlines()

    # `==` not `>=`: the whole point is reproducing the environment that wrote the
    # pickle, and a floating pin reintroduces exactly the skew this design removes.
    assert "scikit-learn==1.7.2" in lines
    assert "numpy==2.3.5" in lines
    assert "cloudpickle==3.1.2" in lines


def test_requirements_include_the_serving_stack_verity_controls(tmp_path):
    _render(tmp_path)

    text = (tmp_path / "requirements.txt").read_text()

    for package in ("fastapi==", "uvicorn", "pydantic=="):
        assert package in text


def test_the_dockerfile_uses_the_captured_python_version(tmp_path):
    _render(tmp_path)

    assert "FROM python:3.12-slim" in (tmp_path / "Dockerfile").read_text()


def test_the_dockerfile_falls_back_to_a_default_python_when_none_was_captured(tmp_path):
    _render(tmp_path, environment={"packages": {}})

    # An upload from before environment capture existed must still be buildable.
    assert "FROM python:3.12-slim" in (tmp_path / "Dockerfile").read_text()


def test_the_dockerfile_installs_dependencies_before_copying_the_model(tmp_path):
    _render(tmp_path)

    text = (tmp_path / "Dockerfile").read_text()

    # This ordering is the entire reason a second model on the same dependency set
    # builds in seconds: Docker reuses the cached pip layer. Asserted rather than
    # assumed, because a well-meaning reorder would silently cost minutes per deploy.
    assert text.index("RUN pip install") < text.index("COPY model.pkl")


def test_the_dockerfile_runs_as_a_non_root_user(tmp_path):
    _render(tmp_path)

    assert "USER appuser" in (tmp_path / "Dockerfile").read_text()


def test_requirements_exclude_framework_only_packages_the_model_does_not_use(tmp_path):
    # xgboost's Linux wheel pulls in nvidia-nccl-cu12 (a ~340MB CUDA library) as a
    # transitive dependency even for CPU-only use. A user who has ever installed
    # xgboost or lightgbm alongside sklearn must not have either dragged into a
    # sklearn model's image -- that turned a real build slow and failure-prone.
    environment = {
        "python_version": "3.12",
        "packages": {
            "scikit-learn": "1.7.2",
            "numpy": "2.3.5",
            "cloudpickle": "3.1.2",
            "xgboost": "3.1.3",
            "lightgbm": "4.6.0",
        },
    }
    _render(tmp_path, environment=environment, framework="sklearn")

    text = (tmp_path / "requirements.txt").read_text()

    assert "scikit-learn==1.7.2" in text
    assert "xgboost" not in text
    assert "lightgbm" not in text


def test_requirements_include_the_frameworks_own_package_when_it_is_used(tmp_path):
    environment = {
        "python_version": "3.12",
        "packages": {"xgboost": "3.1.3", "lightgbm": "4.6.0", "numpy": "2.3.5"},
    }
    _render(tmp_path, environment=environment, framework="xgboost")

    text = (tmp_path / "requirements.txt").read_text()

    assert "xgboost==3.1.3" in text
    assert "lightgbm" not in text


def test_the_dockerfile_installs_libgomp_before_the_pip_layer(tmp_path):
    # LightGBM's compiled Booster dynamically links libgomp.so.1 (OpenMP) at import
    # time -- pip installs the wheel, not the system library, and python:*-slim
    # doesn't carry it. Confirmed live: a real LightGBM deploy 500'd on container
    # startup with "OSError: libgomp.so.1: cannot open shared object file" before
    # this line existed. Small and harmless for models that don't need it.
    _render(tmp_path)

    text = (tmp_path / "Dockerfile").read_text()

    assert "libgomp1" in text
    assert text.index("libgomp1") < text.index("RUN pip install")


def test_the_copied_app_is_the_checked_in_template_not_a_generated_string(tmp_path):
    _render(tmp_path)

    app = (tmp_path / "app.py").read_text()

    # Everything model-specific arrives as data in contract.json. If this file ever
    # starts being templated, it stops being unit-testable on its own.
    assert "contract.json" in app
    assert "VERITY_SERVING_DIR" in app


def test_the_model_bytes_are_written_untouched(tmp_path):
    _render(tmp_path, payload=b"exact-bytes")

    assert (tmp_path / "model.pkl").read_bytes() == b"exact-bytes"


def test_image_tag_is_derived_from_the_model_version_id():
    assert image_tag("mv_abc123") == "verity-model:mv_abc123"
