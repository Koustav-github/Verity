"""Where a model container actually runs.

Everything above this file talks to the three-method interface, never to Docker. An ECS
or Fargate runtime later is a new class here plus one wiring change in deploy.py — which
is the entire reason this seam exists at V1, when there is only one implementation.
"""

import time


class ContainerRuntimeError(Exception):
    """The container runtime could not build, start, or stop an image."""


class DockerRuntime:
    """Local Docker, via the docker SDK."""

    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = self._real_client()
        return self._client

    @staticmethod
    def _real_client():
        try:
            import docker

            return docker.from_env()
        except Exception as exc:  # noqa: BLE001 - any failure to reach the daemon
            raise ContainerRuntimeError(f"cannot reach the Docker daemon: {exc}") from exc

    def build(self, *, context_dir, tag):
        try:
            self.client.images.build(path=str(context_dir), tag=tag, rm=True)
        except Exception as exc:  # noqa: BLE001 - the docker SDK raises several types
            raise ContainerRuntimeError(f"image build failed: {exc}") from exc

    def run(self, *, tag):
        try:
            # Ephemeral host port: Docker picks it, we read it back. A fixed-port
            # registry would be one more thing that can disagree with reality.
            container = self.client.containers.run(
                tag, detach=True, ports={"8000/tcp": None}
            )
            container.reload()
            binding = container.ports["8000/tcp"][0]
            host_port = int(binding["HostPort"])
            return {
                "container_id": container.id,
                "host_port": host_port,
                "endpoint_url": f"http://localhost:{host_port}",
            }
        except Exception as exc:  # noqa: BLE001
            raise ContainerRuntimeError(f"container failed to start: {exc}") from exc

    def stop(self, *, container_id):
        try:
            self.client.containers.get(container_id).stop(timeout=10)
        except Exception as exc:  # noqa: BLE001
            raise ContainerRuntimeError(f"container failed to stop: {exc}") from exc


def wait_healthy(*, url, timeout=60.0, client=None, interval=0.5):
    """Poll /health until it answers 200 or the timeout expires.

    A refused connection is the normal state of a container that is still starting, so
    it is a reason to keep waiting rather than a reason to fail.
    """
    if client is None:
        import httpx

        client = httpx.Client(timeout=2.0)

    deadline = time.monotonic() + timeout
    while True:
        try:
            if client.get(url, timeout=2.0).status_code == 200:
                return True
        except Exception:  # noqa: BLE001 - still starting
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)
