from serving.deploy import _default_runtime
from serving.runtime import DockerRuntime, FargateRuntime


def test_default_runtime_is_docker_when_the_env_var_is_unset(monkeypatch):
    monkeypatch.delenv("VERITY_CONTAINER_RUNTIME", raising=False)

    assert isinstance(_default_runtime(), DockerRuntime)


def test_default_runtime_is_docker_when_the_env_var_says_docker(monkeypatch):
    monkeypatch.setenv("VERITY_CONTAINER_RUNTIME", "docker")

    assert isinstance(_default_runtime(), DockerRuntime)


def test_default_runtime_is_fargate_when_the_env_var_says_fargate(monkeypatch):
    monkeypatch.setenv("VERITY_CONTAINER_RUNTIME", "fargate")
    monkeypatch.setenv("VERITY_FARGATE_ECR_URI", "504509954111.dkr.ecr.us-east-1.amazonaws.com/verity/verity-model")
    monkeypatch.setenv("VERITY_FARGATE_EXECUTION_ROLE_ARN", "arn:aws:iam::504509954111:role/verity-ecs-task-execution-role")
    monkeypatch.setenv("VERITY_FARGATE_SECURITY_GROUP", "sg-02fa20ff3f3beae38")
    monkeypatch.setenv(
        "VERITY_FARGATE_SUBNETS",
        "subnet-0e197b9553ce8700e,subnet-0cf2803e0eb1c6c79,subnet-05aefe4b43bbf2070,"
        "subnet-0f3614049e0dee353,subnet-0ef7392f6f419ee5d,subnet-0ee72c149c94b5a65",
    )

    assert isinstance(_default_runtime(), FargateRuntime)
