import contextlib

import pytest

from serving.deploy import DeployError, deploy

IO_SCHEMA = {"n_features": 2, "feature_names": None, "classes": [0, 1], "has_predict_proba": True}
ENVIRONMENT = {"python_version": "3.12", "packages": {"numpy": "2.3.5"}}


class FakeStore:
    def __init__(self, live=None):
        self.saved = []
        self.updates = []
        self._live = live

    def save_deployment(self, *, model_version_id, image_tag, status):
        self.saved.append(
            {"model_version_id": model_version_id, "image_tag": image_tag, "status": status}
        )
        return f"dep_{len(self.saved)}"

    def update_deployment(self, *, deployment_id, **fields):
        self.updates.append({"deployment_id": deployment_id, **fields})

    def find_live_deployment(self, *, model_version_id):
        return self._live


class FakeRuntime:
    def __init__(self, build_error=None, run_error=None):
        self.built = []
        self.ran = []
        self.stopped = []
        self.build_error = build_error
        self.run_error = run_error

    def build(self, *, context_dir, tag):
        if self.build_error:
            raise self.build_error
        self.built.append(tag)

    def run(self, *, tag):
        if self.run_error:
            raise self.run_error
        self.ran.append(tag)
        return {"container_id": "c_1", "host_port": 49312, "endpoint_url": "http://localhost:49312"}

    def stop(self, *, container_id):
        self.stopped.append(container_id)


@contextlib.contextmanager
def _fake_tempdir():
    yield "/tmp/fake-context"


def _deploy(store, runtime, *, healthy=True, archived=None, render_fn=None):
    return deploy(
        model_version_id="mv_1",
        payload=b"bytes",
        io_schema=IO_SCHEMA,
        environment=ENVIRONMENT,
        metadata_store=store,
        archived_model_version_id=archived,
        runtime=runtime,
        render_fn=render_fn or (lambda **kwargs: None),
        wait_healthy_fn=lambda **kwargs: healthy,
        tempdir_fn=_fake_tempdir,
    )


def test_a_successful_deploy_records_building_then_live():
    store, runtime = FakeStore(), FakeRuntime()

    result = _deploy(store, runtime)

    assert store.saved[0]["status"] == "building"
    assert store.updates[0]["status"] == "live"
    assert result["status"] == "live"
    assert result["host_port"] == 49312
    assert result["endpoint_url"] == "http://localhost:49312"


def test_a_successful_deploy_builds_and_runs_the_version_tagged_image():
    store, runtime = FakeStore(), FakeRuntime()

    _deploy(store, runtime)

    assert runtime.built == ["verity-model:mv_1"]
    assert runtime.ran == ["verity-model:mv_1"]


def test_the_build_context_receives_the_artifact_and_its_environment():
    captured = {}
    _deploy(FakeStore(), FakeRuntime(), render_fn=lambda **kwargs: captured.update(kwargs))

    assert captured["payload"] == b"bytes"
    assert captured["environment"] == ENVIRONMENT
    assert captured["io_schema"] == IO_SCHEMA


def test_a_build_failure_records_a_failed_row_carrying_the_reason():
    store = FakeStore()
    runtime = FakeRuntime(build_error=RuntimeError("no space left on device"))

    with pytest.raises(DeployError):
        _deploy(store, runtime)

    assert store.updates[0]["status"] == "failed"
    assert "no space left on device" in store.updates[0]["error"]["message"]
    assert store.updates[0]["error"]["type"] == "RuntimeError"


def test_a_build_failure_never_starts_a_container():
    store = FakeStore()
    runtime = FakeRuntime(build_error=RuntimeError("boom"))

    with pytest.raises(DeployError):
        _deploy(store, runtime)

    assert runtime.ran == []
    assert runtime.stopped == []


def test_a_container_that_never_becomes_healthy_is_a_failed_deploy():
    store, runtime = FakeStore(), FakeRuntime()

    with pytest.raises(DeployError):
        _deploy(store, runtime, healthy=False)

    assert store.updates[0]["status"] == "failed"
    # The container was started, so it must not be left running behind a failed row —
    # nothing would ever come back to clean it up.
    assert runtime.stopped == ["c_1"]


def test_promoting_a_new_version_stops_the_container_it_replaced():
    store = FakeStore(live={"id": "dep_old", "container_id": "c_old"})
    runtime = FakeRuntime()

    _deploy(store, runtime, archived="mv_0")

    assert "c_old" in runtime.stopped
    stopped = [u for u in store.updates if u.get("status") == "stopped"]
    assert stopped and stopped[0]["deployment_id"] == "dep_old"


def test_the_replacement_is_live_before_the_old_container_is_torn_down():
    store = FakeStore(live={"id": "dep_old", "container_id": "c_old"})

    _deploy(store, FakeRuntime(), archived="mv_0")

    # Order matters: tearing down first would open a window with nothing serving.
    statuses = [u.get("status") for u in store.updates]
    assert statuses.index("live") < statuses.index("stopped")


def test_archival_teardown_does_not_fail_the_new_deploy_when_the_old_container_is_gone():
    class StubbornRuntime(FakeRuntime):
        def stop(self, *, container_id):
            raise RuntimeError("no such container")

    store = FakeStore(live={"id": "dep_old", "container_id": "c_old"})

    # The new version is already live and serving. Failing its deploy because an
    # already-dead old container could not be stopped would be strictly worse.
    result = _deploy(store, StubbornRuntime(), archived="mv_0")

    assert result["status"] == "live"


def test_nothing_is_torn_down_when_no_version_was_archived():
    store, runtime = FakeStore(), FakeRuntime()

    _deploy(store, runtime, archived=None)

    assert runtime.stopped == []


def test_teardown_is_skipped_when_the_archived_version_had_no_live_deployment():
    store = FakeStore(live=None)
    runtime = FakeRuntime()

    _deploy(store, runtime, archived="mv_0")

    assert runtime.stopped == []


def test_deploy_records_a_failed_row_when_the_default_runtime_cannot_be_constructed(monkeypatch):
    monkeypatch.setenv("VERITY_CONTAINER_RUNTIME", "fargate")
    monkeypatch.delenv("VERITY_FARGATE_ECR_URI", raising=False)
    monkeypatch.delenv("VERITY_FARGATE_EXECUTION_ROLE_ARN", raising=False)
    monkeypatch.delenv("VERITY_FARGATE_SECURITY_GROUP", raising=False)
    store = FakeStore()

    with pytest.raises(DeployError):
        deploy(
            model_version_id="mv_1",
            payload=b"payload",
            io_schema=IO_SCHEMA,
            environment=ENVIRONMENT,
            metadata_store=store,
            render_fn=lambda **kwargs: None,
            wait_healthy_fn=lambda **kwargs: True,
            tempdir_fn=_fake_tempdir,
        )

    assert store.saved[0]["status"] == "building"
    assert store.updates[-1]["status"] == "failed"
    assert "VERITY_FARGATE_ECR_URI" in store.updates[-1]["error"]["message"]
