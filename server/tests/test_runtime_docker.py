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


from serving.runtime import FargateRuntime

ECR_REPO = "504509954111.dkr.ecr.us-east-1.amazonaws.com/verity/verity-model"


class FakeDockerRuntimeForFargate:
    """Stands in for the DockerRuntime FargateRuntime delegates local builds to."""

    def __init__(self):
        self.built = []
        self.client = FakeDockerClientForPush()

    def build(self, *, context_dir, tag):
        self.built.append({"context_dir": context_dir, "tag": tag})


class FakeDockerClientForPush:
    def __init__(self):
        self.login_calls = []
        self.tag_calls = []
        self.push_calls = []
        self.images = self

    def login(self, **kwargs):
        self.login_calls.append(kwargs)

    def get(self, tag):
        return FakeImageForPush(self)

    def push(self, repository, tag=None):
        self.push_calls.append({"repository": repository, "tag": tag})


class FakeImageForPush:
    def __init__(self, client):
        self._client = client

    def tag(self, repository, tag):
        self._client.tag_calls.append({"repository": repository, "tag": tag})


class FakeEcrClient:
    def __init__(self):
        import base64

        token = base64.b64encode(b"AWS:fake-password").decode()
        self._token = token

    def get_authorization_token(self):
        return {
            "authorizationData": [
                {
                    "authorizationToken": self._token,
                    "proxyEndpoint": "https://504509954111.dkr.ecr.us-east-1.amazonaws.com",
                }
            ]
        }


def _fargate_runtime(docker_runtime=None, ecr_client=None):
    return FargateRuntime(
        region="us-east-1",
        cluster="verity-cluster",
        ecr_repository_uri=ECR_REPO,
        execution_role_arn="arn:aws:iam::504509954111:role/verity-ecs-task-execution-role",
        subnets=["subnet-a", "subnet-b"],
        security_group="sg-abc",
        log_group="/ecs/verity-model",
        docker_runtime=docker_runtime or FakeDockerRuntimeForFargate(),
        ecs_client=object(),  # unused by build()
        ecr_client=ecr_client or FakeEcrClient(),
        ec2_client=object(),  # unused by build()
    )


def test_fargate_build_delegates_the_local_build_to_docker_runtime():
    docker_runtime = FakeDockerRuntimeForFargate()
    runtime = _fargate_runtime(docker_runtime=docker_runtime)

    runtime.build(context_dir="/tmp/ctx", tag="verity-model:mv_1")

    assert docker_runtime.built == [{"context_dir": "/tmp/ctx", "tag": "verity-model:mv_1"}]


def test_fargate_build_logs_in_to_ecr_using_the_real_authorization_token():
    docker_runtime = FakeDockerRuntimeForFargate()
    runtime = _fargate_runtime(docker_runtime=docker_runtime)

    runtime.build(context_dir="/tmp/ctx", tag="verity-model:mv_1")

    login = docker_runtime.client.login_calls[0]
    assert login["username"] == "AWS"
    assert login["password"] == "fake-password"
    assert login["registry"] == "https://504509954111.dkr.ecr.us-east-1.amazonaws.com"


def test_fargate_build_tags_and_pushes_the_image_under_the_ecr_uri():
    docker_runtime = FakeDockerRuntimeForFargate()
    runtime = _fargate_runtime(docker_runtime=docker_runtime)

    runtime.build(context_dir="/tmp/ctx", tag="verity-model:mv_1")

    assert docker_runtime.client.tag_calls == [{"repository": ECR_REPO, "tag": "mv_1"}]
    assert docker_runtime.client.push_calls == [{"repository": ECR_REPO, "tag": "mv_1"}]


def test_fargate_subnets_default_to_empty_list_when_not_provided_and_env_unset(monkeypatch):
    monkeypatch.delenv("VERITY_FARGATE_SUBNETS", raising=False)

    runtime = FargateRuntime(
        region="us-east-1",
        cluster="verity-cluster",
        ecr_repository_uri=ECR_REPO,
        execution_role_arn="arn:aws:iam::504509954111:role/verity-ecs-task-execution-role",
        subnets=None,
        security_group="sg-abc",
        log_group="/ecs/verity-model",
        docker_runtime=FakeDockerRuntimeForFargate(),
        ecs_client=object(),
        ecr_client=FakeEcrClient(),
        ec2_client=object(),
    )

    assert runtime.subnets == []


class FakeEcsClientForRun:
    def __init__(self, task_statuses=None):
        self.registered = []
        self.run_task_calls = []
        self.describe_calls = 0
        # Each describe_tasks call pops the next status; last one repeats.
        self._statuses = list(task_statuses or ["RUNNING"])

    def register_task_definition(self, **kwargs):
        self.registered.append(kwargs)
        return {"taskDefinition": {"taskDefinitionArn": "arn:aws:ecs:task-def:1"}}

    def run_task(self, **kwargs):
        self.run_task_calls.append(kwargs)
        return {"tasks": [{"taskArn": "arn:aws:ecs:task:abc", "lastStatus": "PROVISIONING"}]}

    def describe_tasks(self, *, cluster, tasks):
        self.describe_calls += 1
        status = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        return {
            "tasks": [
                {
                    "taskArn": tasks[0],
                    "lastStatus": status,
                    "attachments": [
                        {
                            "type": "ElasticNetworkInterface",
                            "details": [{"name": "networkInterfaceId", "value": "eni-xyz"}],
                        }
                    ],
                }
            ]
        }


class FakeEc2ClientForRun:
    def describe_network_interfaces(self, *, NetworkInterfaceIds):
        assert NetworkInterfaceIds == ["eni-xyz"]
        return {"NetworkInterfaces": [{"Association": {"PublicIp": "203.0.113.10"}}]}


def _fargate_runtime_for_run(ecs_client=None, ec2_client=None):
    return FargateRuntime(
        region="us-east-1",
        cluster="verity-cluster",
        ecr_repository_uri=ECR_REPO,
        execution_role_arn="arn:aws:iam::504509954111:role/verity-ecs-task-execution-role",
        subnets=["subnet-a", "subnet-b"],
        security_group="sg-abc",
        log_group="/ecs/verity-model",
        docker_runtime=FakeDockerRuntimeForFargate(),
        ecs_client=ecs_client or FakeEcsClientForRun(),
        ecr_client=FakeEcrClient(),
        ec2_client=ec2_client or FakeEc2ClientForRun(),
    )


def test_fargate_run_registers_a_task_definition_naming_the_pushed_image():
    ecs = FakeEcsClientForRun()
    runtime = _fargate_runtime_for_run(ecs_client=ecs)

    runtime.run(tag="verity-model:mv_1")

    registered = ecs.registered[0]
    assert registered["family"] == "verity-model"
    assert registered["requiresCompatibilities"] == ["FARGATE"]
    assert registered["cpu"] == "256"
    assert registered["memory"] == "512"
    assert registered["executionRoleArn"] == "arn:aws:iam::504509954111:role/verity-ecs-task-execution-role"
    container_def = registered["containerDefinitions"][0]
    assert container_def["image"] == f"{ECR_REPO}:mv_1"
    assert container_def["portMappings"] == [{"containerPort": 8000, "protocol": "tcp"}]
    assert container_def["logConfiguration"]["options"]["awslogs-group"] == "/ecs/verity-model"


def test_fargate_run_launches_with_a_public_ip_in_the_configured_network():
    ecs = FakeEcsClientForRun()
    runtime = _fargate_runtime_for_run(ecs_client=ecs)

    runtime.run(tag="verity-model:mv_1")

    launch = ecs.run_task_calls[0]
    assert launch["cluster"] == "verity-cluster"
    assert launch["launchType"] == "FARGATE"
    network = launch["networkConfiguration"]["awsvpcConfiguration"]
    assert network["subnets"] == ["subnet-a", "subnet-b"]
    assert network["securityGroups"] == ["sg-abc"]
    assert network["assignPublicIp"] == "ENABLED"


def test_fargate_run_polls_until_running_then_resolves_the_public_ip():
    ecs = FakeEcsClientForRun(task_statuses=["PROVISIONING", "PENDING", "RUNNING"])
    runtime = _fargate_runtime_for_run(ecs_client=ecs)

    result = runtime.run(tag="verity-model:mv_1")

    assert ecs.describe_calls == 3
    assert result == {
        "container_id": "arn:aws:ecs:task:abc",
        "endpoint_url": "http://203.0.113.10:8000",
        "host_port": None,
    }


def test_fargate_run_gives_up_if_the_task_never_reaches_running():
    ecs = FakeEcsClientForRun(task_statuses=["PROVISIONING"])
    runtime = _fargate_runtime_for_run(ecs_client=ecs)

    with pytest.raises(ContainerRuntimeError):
        runtime.run(tag="verity-model:mv_1", poll_timeout=0.2, poll_interval=0.05)
