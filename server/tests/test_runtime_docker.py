import cloudpickle
import numpy as np
import pytest
from sklearn.linear_model import LogisticRegression

from serving.runtime import ContainerRuntimeError, DockerRuntime, wait_healthy


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class FakeHttpClient:
    """Returns each queued status in turn, then repeats the last one forever.

    `None` means "connection refused", which is what a container that is still booting
    actually does.
    """

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if status is None:
            raise ConnectionError("refused")
        return FakeResponse(status)


def test_wait_healthy_returns_true_as_soon_as_health_answers_ok():
    client = FakeHttpClient([200])

    assert wait_healthy(url="http://localhost:1/health", timeout=5.0, client=client) is True


def test_wait_healthy_keeps_polling_while_the_container_is_still_starting():
    # A container that refuses connections for a moment is normal, not a failure.
    client = FakeHttpClient([None, None, 200])

    assert wait_healthy(
        url="http://localhost:1/health", timeout=5.0, client=client, interval=0.01
    ) is True
    assert client.calls == 3


def test_wait_healthy_gives_up_at_the_timeout():
    client = FakeHttpClient([None])

    assert wait_healthy(
        url="http://localhost:1/health", timeout=0.2, client=client, interval=0.01
    ) is False


def test_docker_runtime_reads_the_assigned_host_port_back_after_starting():
    class FakeContainer:
        id = "container-abc"
        ports = {"8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "49312"}]}

        def reload(self):
            pass

    class FakeContainers:
        def __init__(self):
            self.run_kwargs = None

        def run(self, tag, **kwargs):
            self.run_kwargs = {"tag": tag, **kwargs}
            return FakeContainer()

    class FakeDocker:
        def __init__(self):
            self.containers = FakeContainers()

    fake = FakeDocker()
    runtime = DockerRuntime(client=fake)

    result = runtime.run(tag="verity-model:mv_1")

    assert result == {
        "container_id": "container-abc",
        "host_port": 49312,
        "endpoint_url": "http://localhost:49312",
    }
    # Ephemeral port: Docker assigns, we read back. No port registry to drift.
    assert fake.containers.run_kwargs["ports"] == {"8000/tcp": None}
    assert fake.containers.run_kwargs["detach"] is True


def test_docker_runtime_wraps_a_build_failure_in_a_container_runtime_error():
    class FakeImages:
        def build(self, **kwargs):
            raise ValueError("dependency resolution failed")

    class FakeDocker:
        images = FakeImages()

    runtime = DockerRuntime(client=FakeDocker())

    with pytest.raises(ContainerRuntimeError) as excinfo:
        runtime.build(context_dir="/tmp/ctx", tag="verity-model:mv_1")

    # The reason has to survive: it is what lands in deployment.error.
    assert "dependency resolution failed" in str(excinfo.value)


def test_docker_runtime_wraps_a_stop_failure_rather_than_leaking_the_sdk_exception():
    class FakeContainers:
        def get(self, container_id):
            raise KeyError("no such container")

    class FakeDocker:
        containers = FakeContainers()

    runtime = DockerRuntime(client=FakeDocker())

    with pytest.raises(ContainerRuntimeError):
        runtime.stop(container_id="gone")


@pytest.mark.docker
def test_a_real_image_builds_starts_and_answers_health(tmp_path):
    """The only test that needs a Docker daemon. Skipped when one isn't reachable."""
    from serving.build import image_tag, render_context

    model = LogisticRegression().fit(
        np.array([[0.0, 1.0], [1.0, 0.0], [0.5, 0.5], [0.2, 0.8]]),
        np.array([0, 1, 0, 1]),
    )
    render_context(
        dest=tmp_path,
        payload=cloudpickle.dumps(model),
        io_schema={
            "n_features": 2,
            "feature_names": None,
            "classes": [0, 1],
            "has_predict_proba": True,
        },
        environment={
            "python_version": "3.12",
            "packages": {
                "scikit-learn": _installed("scikit-learn"),
                "numpy": _installed("numpy"),
                "cloudpickle": _installed("cloudpickle"),
            },
        },
    )

    runtime = DockerRuntime()
    tag = image_tag("mv_itest")
    runtime.build(context_dir=str(tmp_path), tag=tag)
    started = runtime.run(tag=tag)
    try:
        assert wait_healthy(url=f"{started['endpoint_url']}/health", timeout=90.0)
    finally:
        runtime.stop(container_id=started["container_id"])


def _installed(distribution):
    from importlib.metadata import version

    return version(distribution)
