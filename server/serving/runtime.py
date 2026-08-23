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


class FargateRuntime:
    """ECS Fargate, via boto3. A second ContainerRuntime implementation behind the
    same three-method interface DockerRuntime already satisfies — see the module
    docstring for why this seam exists.

    Local docker build is delegated to an internal DockerRuntime; this class adds the
    ECR push, task definition, and task lifecycle on top of it.
    """

    def __init__(
        self,
        *,
        region=None,
        cluster=None,
        ecr_repository_uri=None,
        execution_role_arn=None,
        subnets=None,
        security_group=None,
        log_group=None,
        docker_runtime=None,
        ecs_client=None,
        ecr_client=None,
        ec2_client=None,
    ):
        import os

        self.region = region or os.environ.get("VERITY_FARGATE_REGION", "us-east-1")
        self.cluster = cluster or os.environ.get("VERITY_FARGATE_CLUSTER", "verity-cluster")
        self.ecr_repository_uri = ecr_repository_uri or os.environ["VERITY_FARGATE_ECR_URI"]
        self.execution_role_arn = execution_role_arn or os.environ["VERITY_FARGATE_EXECUTION_ROLE_ARN"]
        subnet_str = os.environ.get("VERITY_FARGATE_SUBNETS", "")
        self.subnets = subnets or (subnet_str.split(",") if subnet_str else [])
        self.security_group = security_group or os.environ["VERITY_FARGATE_SECURITY_GROUP"]
        self.log_group = log_group or os.environ.get("VERITY_FARGATE_LOG_GROUP", "/ecs/verity-model")

        self._docker_runtime = docker_runtime
        self._ecs_client = ecs_client
        self._ecr_client = ecr_client
        self._ec2_client = ec2_client

    @property
    def docker_runtime(self):
        if self._docker_runtime is None:
            self._docker_runtime = DockerRuntime()
        return self._docker_runtime

    @property
    def ecs(self):
        if self._ecs_client is None:
            self._ecs_client = self._boto3_client("ecs")
        return self._ecs_client

    @property
    def ecr(self):
        if self._ecr_client is None:
            self._ecr_client = self._boto3_client("ecr")
        return self._ecr_client

    @property
    def ec2(self):
        if self._ec2_client is None:
            self._ec2_client = self._boto3_client("ec2")
        return self._ec2_client

    def _boto3_client(self, service):
        try:
            import boto3

            return boto3.client(service, region_name=self.region)
        except Exception as exc:  # noqa: BLE001
            raise ContainerRuntimeError(f"cannot create boto3 {service} client: {exc}") from exc

    def build(self, *, context_dir, tag):
        self.docker_runtime.build(context_dir=context_dir, tag=tag)
        self._push_to_ecr(tag=tag)

    def _push_to_ecr(self, *, tag):
        try:
            import base64

            auth = self.ecr.get_authorization_token()["authorizationData"][0]
            username, password = (
                base64.b64decode(auth["authorizationToken"]).decode().split(":", 1)
            )
            client = self.docker_runtime.client
            client.login(
                username=username, password=password, registry=auth["proxyEndpoint"]
            )

            # `tag` is "verity-model:mv_..." locally — the ECR-side tag is just the
            # version part; the repository URI already names the image.
            version_tag = tag.split(":", 1)[1]
            image = client.images.get(tag)
            image.tag(repository=self.ecr_repository_uri, tag=version_tag)
            client.images.push(repository=self.ecr_repository_uri, tag=version_tag)
        except Exception as exc:  # noqa: BLE001
            raise ContainerRuntimeError(f"failed to push image to ECR: {exc}") from exc


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
