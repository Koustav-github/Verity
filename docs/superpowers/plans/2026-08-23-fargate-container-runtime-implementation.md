# FargateRuntime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A second `ContainerRuntime` implementation, `FargateRuntime`, that deploys a
promoted model version to AWS ECS Fargate instead of local Docker — selectable via an
environment variable, with local Docker remaining the default so nothing about existing
dev or tests changes unless explicitly opted in.

**Architecture:** `FargateRuntime` composes an internal `DockerRuntime` for the local
`docker build` step, then pushes the built image to ECR, registers a new revision of one
ECS task definition family, launches a Fargate task with a public IP, and polls until
that task has a real network address. A small, deliberate interface fix
(`ContainerRuntime.run()` returning `endpoint_url` directly, rather than `deploy.py`
assuming every runtime is reachable at `localhost`) lands first, since it's what makes a
second runtime implementation actually swappable.

**Tech Stack:** Python 3.12, `boto3` (ECS, ECR, EC2 clients), the `docker` SDK (already a
dependency), pytest.

**Spec:** `docs/superpowers/specs/2026-08-23-fargate-container-runtime-design.md`

## Global Constraints

- **Do not push to any remote without asking first.** Commit normally on `main` (per this
  project's standing commit policy), never push unasked.
- **No `unittest.mock` anywhere.** Hand-written fakes only — for `boto3`'s `ecs`, `ecr`,
  and `ec2` clients, follow the exact pattern already used for `docker` clients in
  `server/tests/test_runtime_docker.py`: a small class exposing only the methods this
  code actually calls, recording what it was called with.
- Every collaborator injectable with a lazy real default: `param=None` → `param or
  _default_param` → deferred import inside the default — matching every existing
  constructor in `server/serving/`.
- Fixed AWS configuration (copied verbatim from the spec, do not alter):
  - Region: `us-east-1`
  - ECR repository: `504509954111.dkr.ecr.us-east-1.amazonaws.com/verity/verity-model`
  - ECS cluster: `verity-cluster`
  - Task execution role ARN: `arn:aws:iam::504509954111:role/verity-ecs-task-execution-role`
  - Subnets: `subnet-0e197b9553ce8700e`, `subnet-0cf2803e0eb1c6c79`, `subnet-05aefe4b43bbf2070`, `subnet-0f3614049e0dee353`, `subnet-0ef7392f6f419ee5d`, `subnet-0ee72c149c94b5a65`
  - Security group: `sg-02fa20ff3f3beae38`
  - CloudWatch log group: `/ecs/verity-model`
  - Task sizing: `cpu="256"`, `memory="512"` (Fargate's smallest valid combination)
- **No autoscaling, no idle-shutdown.** A task runs until the version it serves is
  replaced by a new promotion under the same name — the existing archival-teardown path
  already handles that; nothing new is added here.
- The one test that hits real AWS must be gated behind an explicit opt-in environment
  variable (`VERITY_RUN_FARGATE_LIVE_TEST=1`), never auto-run from the presence of
  credentials alone — real AWS calls cost money and take real time.
- Run server tests from `server/`: `uv run pytest`. Before any run, confirm the venv has
  the right packages: `uv run python -c "import supabase, boto3, docker"` — if that
  fails, run `uv sync --extra dev` first (`uv sync` alone has previously, silently,
  pruned pytest from this venv).

---

### Task 1: Generalize `ContainerRuntime.run()` to return `endpoint_url` directly

**Files:**
- Modify: `server/serving/runtime.py`
- Modify: `server/serving/deploy.py`
- Modify: `server/tests/test_runtime_docker.py`
- Modify: `server/tests/test_deploy.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `ContainerRuntime.run()` (both current and future implementations) returns
  `{"container_id": str, "endpoint_url": str, "host_port": int | None}` — `host_port` is
  present and meaningful for `DockerRuntime` (an ephemeral local port), and `None` for any
  runtime where the concept doesn't apply. `deploy()`'s returned dict and the
  `deployment` row both carry `host_port` as nullable, unchanged from today.

**Why:** `deploy.py:53` currently does `endpoint_url = f"http://localhost:{started['host_port']}"`
— an assumption that is only true for Docker. A runtime that returns a real public IP
(Fargate) has no `localhost` to assume. The fix is for the runtime itself to build its
own URL, since only the runtime knows its own reachability shape.

- [ ] **Step 1: Write the failing tests**

Replace the existing assertion in `server/tests/test_runtime_docker.py`:

```python
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
```

And update the live Docker test's URL construction (same file, keep everything else
identical):

```python
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
```

In `server/tests/test_deploy.py`, update `FakeRuntime.run()`:

```python
    def run(self, *, tag):
        if self.run_error:
            raise self.run_error
        self.ran.append(tag)
        return {"container_id": "c_1", "host_port": 49312, "endpoint_url": "http://localhost:49312"}
```

(The two existing assertions on `result["host_port"]` and `result["endpoint_url"]` in
`test_a_successful_deploy_records_building_then_live` already expect exactly these
values — they should now pass once `deploy()` reads them straight through instead of
constructing `endpoint_url` itself.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd server; uv run pytest tests/test_runtime_docker.py tests/test_deploy.py -v -k "reads_the_assigned or records_building_then_live"`
Expected: FAIL — `AssertionError` on the dict comparison (missing `endpoint_url` key in
the actual result, since `DockerRuntime.run()` doesn't produce it yet).

- [ ] **Step 3: Update `DockerRuntime.run()`**

```python
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
```

- [ ] **Step 4: Update `deploy()` to stop constructing the URL itself**

In `server/serving/deploy.py`, replace:

```python
        started = runtime.run(tag=tag)
        container_id = started["container_id"]
        endpoint_url = f"http://localhost:{started['host_port']}"
```

with:

```python
        started = runtime.run(tag=tag)
        container_id = started["container_id"]
        endpoint_url = started["endpoint_url"]
```

and replace:

```python
        metadata_store.update_deployment(
            deployment_id=deployment_id,
            status="live",
            container_id=container_id,
            host_port=started["host_port"],
            endpoint_url=endpoint_url,
        )
```

with:

```python
        metadata_store.update_deployment(
            deployment_id=deployment_id,
            status="live",
            container_id=container_id,
            host_port=started.get("host_port"),
            endpoint_url=endpoint_url,
        )
```

and replace the final return dict's `"host_port": started["host_port"]` with
`"host_port": started.get("host_port")`.

- [ ] **Step 5: Run to verify it passes**

Run: `cd server; uv run pytest tests/test_runtime_docker.py tests/test_deploy.py -v`
Expected: all pass.

- [ ] **Step 6: Run the full server suite**

Run: `cd server; uv run pytest -q`
Expected: 266 passed (unchanged count — this task only changes *shapes*, not behavior,
for the only runtime that exists so far).

- [ ] **Step 7: Commit**

```bash
git add server/serving/runtime.py server/serving/deploy.py server/tests/test_runtime_docker.py server/tests/test_deploy.py
git commit -m "Generalize ContainerRuntime.run() to return endpoint_url directly"
```

---

### Task 2: `FargateRuntime` — construction and `build()` with an ECR push

**Files:**
- Modify: `server/serving/runtime.py`
- Modify: `server/tests/test_runtime_docker.py`

**Interfaces:**
- Consumes: `DockerRuntime` (Task 1, unchanged interface — `build`, `run`, `stop`).
- Produces: `FargateRuntime.__init__(self, *, region=None, cluster=None, ecr_repository_uri=None,
  execution_role_arn=None, subnets=None, security_group=None, log_group=None,
  docker_runtime=None, ecs_client=None, ecr_client=None, ec2_client=None)` and
  `FargateRuntime.build(self, *, context_dir, tag)`.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_runtime_docker.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd server; uv run pytest tests/test_runtime_docker.py -v -k fargate_build`
Expected: FAIL — `ImportError: cannot import name 'FargateRuntime' from 'serving.runtime'`

- [ ] **Step 3: Implement `FargateRuntime`'s construction and `build()`**

Append to `server/serving/runtime.py`:

```python
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
        self.subnets = subnets or os.environ.get("VERITY_FARGATE_SUBNETS", "").split(",")
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd server; uv run pytest tests/test_runtime_docker.py -v -k fargate_build`
Expected: 3 passed.

- [ ] **Step 5: Run the full server suite**

Run: `cd server; uv run pytest -q`
Expected: 269 passed (266 + 3 new).

- [ ] **Step 6: Commit**

```bash
git add server/serving/runtime.py server/tests/test_runtime_docker.py
git commit -m "Add FargateRuntime construction and build() with ECR push"
```

---

### Task 3: `FargateRuntime.run()` — task definition, launch, and network resolution

**Files:**
- Modify: `server/serving/runtime.py`
- Modify: `server/tests/test_runtime_docker.py`

**Interfaces:**
- Consumes: `self.ecs`, `self.ec2` (Task 2), `self.ecr_repository_uri`,
  `self.execution_role_arn`, `self.cluster`, `self.subnets`, `self.security_group`,
  `self.log_group`, `self.region`.
- Produces: `FargateRuntime.run(self, *, tag) -> {"container_id": str, "endpoint_url": str, "host_port": None}`
  — matching Task 1's generalized `ContainerRuntime.run()` contract exactly.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_runtime_docker.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd server; uv run pytest tests/test_runtime_docker.py -v -k fargate_run`
Expected: FAIL — `TypeError: FargateRuntime.run() missing ...` or `AttributeError`
(method doesn't exist yet).

- [ ] **Step 3: Implement `run()`**

Append to the `FargateRuntime` class in `server/serving/runtime.py`:

```python
    def run(self, *, tag, poll_timeout=120.0, poll_interval=3.0):
        import time

        try:
            version_tag = tag.split(":", 1)[1]
            image_uri = f"{self.ecr_repository_uri}:{version_tag}"

            task_def = self.ecs.register_task_definition(
                family="verity-model",
                networkMode="awsvpc",
                requiresCompatibilities=["FARGATE"],
                cpu="256",
                memory="512",
                executionRoleArn=self.execution_role_arn,
                containerDefinitions=[
                    {
                        "name": "verity-model",
                        "image": image_uri,
                        "portMappings": [{"containerPort": 8000, "protocol": "tcp"}],
                        "logConfiguration": {
                            "logDriver": "awslogs",
                            "options": {
                                "awslogs-group": self.log_group,
                                "awslogs-region": self.region,
                                "awslogs-stream-prefix": "verity",
                            },
                        },
                    }
                ],
            )
            task_def_arn = task_def["taskDefinition"]["taskDefinitionArn"]

            launched = self.ecs.run_task(
                cluster=self.cluster,
                taskDefinition=task_def_arn,
                launchType="FARGATE",
                count=1,
                networkConfiguration={
                    "awsvpcConfiguration": {
                        "subnets": self.subnets,
                        "securityGroups": [self.security_group],
                        "assignPublicIp": "ENABLED",
                    }
                },
            )
            task_arn = launched["tasks"][0]["taskArn"]

            public_ip = self._wait_for_public_ip(
                task_arn, timeout=poll_timeout, interval=poll_interval
            )
            return {
                "container_id": task_arn,
                "endpoint_url": f"http://{public_ip}:8000",
                "host_port": None,
            }
        except ContainerRuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ContainerRuntimeError(f"Fargate task failed to start: {exc}") from exc

    def _wait_for_public_ip(self, task_arn, *, timeout, interval):
        import time

        deadline = time.monotonic() + timeout
        while True:
            described = self.ecs.describe_tasks(cluster=self.cluster, tasks=[task_arn])
            task = described["tasks"][0]
            if task["lastStatus"] == "RUNNING":
                eni_id = self._eni_id_from(task)
                interfaces = self.ec2.describe_network_interfaces(
                    NetworkInterfaceIds=[eni_id]
                )
                return interfaces["NetworkInterfaces"][0]["Association"]["PublicIp"]
            if time.monotonic() >= deadline:
                raise ContainerRuntimeError(
                    f"Fargate task {task_arn} did not reach RUNNING within {timeout}s "
                    f"(last status: {task['lastStatus']})"
                )
            time.sleep(interval)

    @staticmethod
    def _eni_id_from(task):
        for attachment in task.get("attachments", []):
            if attachment["type"] != "ElasticNetworkInterface":
                continue
            for detail in attachment["details"]:
                if detail["name"] == "networkInterfaceId":
                    return detail["value"]
        raise ContainerRuntimeError(f"task {task['taskArn']} has no network interface attached")
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd server; uv run pytest tests/test_runtime_docker.py -v -k fargate_run`
Expected: 4 passed.

- [ ] **Step 5: Run the full server suite**

Run: `cd server; uv run pytest -q`
Expected: 273 passed (269 + 4 new).

- [ ] **Step 6: Commit**

```bash
git add server/serving/runtime.py server/tests/test_runtime_docker.py
git commit -m "Add FargateRuntime.run() — task definition, launch, public IP resolution"
```

---

### Task 4: `FargateRuntime.stop()`

**Files:**
- Modify: `server/serving/runtime.py`
- Modify: `server/tests/test_runtime_docker.py`

**Interfaces:**
- Consumes: `self.ecs`, `self.cluster` (Task 2).
- Produces: `FargateRuntime.stop(self, *, container_id) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/test_runtime_docker.py`:

```python
class FakeEcsClientForStop:
    def __init__(self, raise_on_stop=None):
        self.stop_calls = []
        self._raise = raise_on_stop

    def stop_task(self, **kwargs):
        if self._raise:
            raise self._raise
        self.stop_calls.append(kwargs)


def test_fargate_stop_calls_ecs_stop_task_with_the_cluster_and_task_arn():
    ecs = FakeEcsClientForStop()
    runtime = _fargate_runtime_for_run(ecs_client=ecs)

    runtime.stop(container_id="arn:aws:ecs:task:abc")

    assert ecs.stop_calls == [{"cluster": "verity-cluster", "task": "arn:aws:ecs:task:abc"}]


def test_fargate_stop_wraps_a_failure_in_container_runtime_error():
    ecs = FakeEcsClientForStop(raise_on_stop=RuntimeError("task already stopped"))
    runtime = _fargate_runtime_for_run(ecs_client=ecs)

    with pytest.raises(ContainerRuntimeError):
        runtime.stop(container_id="arn:aws:ecs:task:abc")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd server; uv run pytest tests/test_runtime_docker.py -v -k fargate_stop`
Expected: FAIL — `AttributeError: 'FargateRuntime' object has no attribute 'stop'`

- [ ] **Step 3: Implement `stop()`**

Append to the `FargateRuntime` class:

```python
    def stop(self, *, container_id):
        try:
            self.ecs.stop_task(cluster=self.cluster, task=container_id)
        except Exception as exc:  # noqa: BLE001
            raise ContainerRuntimeError(f"Fargate task failed to stop: {exc}") from exc
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd server; uv run pytest tests/test_runtime_docker.py -v -k fargate_stop`
Expected: 2 passed.

- [ ] **Step 5: Run the full server suite**

Run: `cd server; uv run pytest -q`
Expected: 275 passed (273 + 2 new).

- [ ] **Step 6: Commit**

```bash
git add server/serving/runtime.py server/tests/test_runtime_docker.py
git commit -m "Add FargateRuntime.stop()"
```

---

### Task 5: Wire runtime selection into `deploy.py`'s default

**Files:**
- Modify: `server/serving/deploy.py`
- Create: `server/tests/test_deploy_runtime_selection.py`

**Interfaces:**
- Consumes: `DockerRuntime`, `FargateRuntime` (Tasks 1–4).
- Produces: `_default_runtime()` in `server/serving/deploy.py` reads
  `VERITY_CONTAINER_RUNTIME` (`"docker"` or `"fargate"`, defaulting to `"docker"`) and
  constructs the matching runtime. `deploy()`'s own signature is unchanged — this only
  affects what its lazy default resolves to.

- [ ] **Step 1: Write the failing tests**

```python
# server/tests/test_deploy_runtime_selection.py
import deploy as deploy_module_check  # noqa: F401 - sanity import path check, removed below
```

Replace that placeholder with the real test file:

```python
# server/tests/test_deploy_runtime_selection.py
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd server; uv run pytest tests/test_deploy_runtime_selection.py -v -k says_fargate`
Expected: FAIL — `AssertionError` (currently always returns `DockerRuntime`).

- [ ] **Step 3: Update `_default_runtime()`**

In `server/serving/deploy.py`, replace:

```python
def _default_runtime():
    from serving.runtime import DockerRuntime

    return DockerRuntime()
```

with:

```python
def _default_runtime():
    import os

    from serving.runtime import DockerRuntime, FargateRuntime

    choice = os.environ.get("VERITY_CONTAINER_RUNTIME", "docker")
    if choice == "fargate":
        return FargateRuntime()
    return DockerRuntime()
```

(`FargateRuntime()`'s own constructor, from Task 2, already reads its remaining
configuration — region, cluster, log group with sensible defaults; ECR URI, role ARN,
subnets, security group required — from `VERITY_FARGATE_*` environment variables when not
passed explicitly, so no further wiring is needed here.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd server; uv run pytest tests/test_deploy_runtime_selection.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run the full server suite**

Run: `cd server; uv run pytest -q`
Expected: 278 passed (275 + 3 new).

- [ ] **Step 6: Commit**

```bash
git add server/serving/deploy.py server/tests/test_deploy_runtime_selection.py
git commit -m "Select DockerRuntime or FargateRuntime via VERITY_CONTAINER_RUNTIME"
```

---

### Task 6: The one real live-AWS test, opt-in only

**Files:**
- Modify: `server/tests/test_runtime_docker.py`
- Modify: `server/pyproject.toml`
- Modify: `server/tests/conftest.py`

**Interfaces:**
- Consumes: `FargateRuntime` (Tasks 2–4), real AWS resources listed in Global Constraints.
- Produces: one test marked `@pytest.mark.aws`, skipped unless
  `VERITY_RUN_FARGATE_LIVE_TEST=1` is explicitly set — never inferred from credentials.

- [ ] **Step 1: Register the marker**

In `server/pyproject.toml`, extend the existing `markers` list (find the line added for
`docker:` in api-fication and add beside it):

```toml
markers = [
    "docker: needs a running Docker daemon; skipped automatically when unreachable",
    "aws: hits real AWS resources and costs real money/time; requires VERITY_RUN_FARGATE_LIVE_TEST=1, never auto-detected",
]
```

- [ ] **Step 2: Add the opt-in skip to `conftest.py`**

In `server/tests/conftest.py`, extend `pytest_runtest_setup` (it currently only handles
the `docker` marker):

```python
import os

import pytest


def pytest_runtest_setup(item):
    """Skip daemon-dependent tests when there is no daemon, and skip real-AWS tests
    unless explicitly opted into — keeps the default suite offline, fast, and free."""
    if "docker" in item.keywords:
        try:
            import docker

            docker.from_env().ping()
        except Exception as exc:  # noqa: BLE001 - any failure to reach the daemon is a skip
            pytest.skip(f"docker daemon unavailable: {type(exc).__name__}")

    if "aws" in item.keywords and os.environ.get("VERITY_RUN_FARGATE_LIVE_TEST") != "1":
        pytest.skip("set VERITY_RUN_FARGATE_LIVE_TEST=1 to run this against real AWS")
```

- [ ] **Step 3: Write the live test**

Append to `server/tests/test_runtime_docker.py`:

```python
@pytest.mark.aws
def test_a_real_model_deploys_to_fargate_and_answers_health(tmp_path):
    """The only test that touches real AWS. Skipped unless VERITY_RUN_FARGATE_LIVE_TEST=1.

    Costs real time (a Fargate task takes 30-90s to reach RUNNING) and a small real
    dollar amount (the task runs for the duration of this test, then is stopped).
    """
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

    runtime = FargateRuntime()  # reads all config from VERITY_FARGATE_* env vars
    tag = image_tag("mv_fargate_itest")
    runtime.build(context_dir=str(tmp_path), tag=tag)
    started = runtime.run(tag=tag)
    try:
        assert wait_healthy(url=f"{started['endpoint_url']}/health", timeout=120.0)
    finally:
        runtime.stop(container_id=started["container_id"])
```

- [ ] **Step 4: Run to verify it's skipped by default**

Run: `cd server; uv run pytest tests/test_runtime_docker.py -v -k fargate_and_answers_health`
Expected: 1 skipped, reason "set VERITY_RUN_FARGATE_LIVE_TEST=1 to run this against real AWS".

- [ ] **Step 5: Run the full server suite to confirm nothing else changed**

Run: `cd server; uv run pytest -q`
Expected: 278 passed, 1 skipped (the Docker-marked test may also skip or run depending
on whether a local daemon answers — unrelated to this task).

- [ ] **Step 6: Commit**

```bash
git add server/tests/test_runtime_docker.py server/pyproject.toml server/tests/conftest.py
git commit -m "Add opt-in live Fargate deploy test, gated behind VERITY_RUN_FARGATE_LIVE_TEST"
```

---

### Task 7: Live verification and documentation

**Files:**
- Modify: `README.md`, `docs/Schemas.md`, `docs/architecture.md`, `docs/progression.md`

- [ ] **Step 1: Set the required environment variables and run the live test for real**

```powershell
$env:VERITY_RUN_FARGATE_LIVE_TEST = "1"
$env:VERITY_FARGATE_ECR_URI = "504509954111.dkr.ecr.us-east-1.amazonaws.com/verity/verity-model"
$env:VERITY_FARGATE_EXECUTION_ROLE_ARN = "arn:aws:iam::504509954111:role/verity-ecs-task-execution-role"
$env:VERITY_FARGATE_SECURITY_GROUP = "sg-02fa20ff3f3beae38"
$env:VERITY_FARGATE_SUBNETS = "subnet-0e197b9553ce8700e,subnet-0cf2803e0eb1c6c79,subnet-05aefe4b43bbf2070,subnet-0f3614049e0dee353,subnet-0ef7392f6f419ee5d,subnet-0ee72c149c94b5a65"
cd server
uv run pytest tests/test_runtime_docker.py -v -k fargate_and_answers_health
```

Confirm: the test builds a real image, pushes it to the real ECR repo, launches a real
Fargate task, gets a real public IP, answers a real `GET /health` over the internet, and
stops cleanly. Check the AWS Console (ECS → `verity-cluster` → Tasks, and CloudWatch →
`/ecs/verity-model`) to confirm the task appeared and its logs landed.

- [ ] **Step 2: Deploy a real model end-to-end via `VERITY_CONTAINER_RUNTIME=fargate`**

```powershell
$env:VERITY_CONTAINER_RUNTIME = "fargate"
cd server; uv run uvicorn main:app --port 8000   # one shell, with the env vars above set
```
```powershell
cd verity
uv run python -m verity.cli --demo --user-id fargate-check --name fargate-check-model --endpoint http://127.0.0.1:8000
```

Confirm the response's `deployment.endpoint_url` is a real `http://<public-ip>:8000`, not
`localhost`, and that `curl`-ing `{endpoint_url}/predict` from your own machine returns a
real prediction.

- [ ] **Step 3: Update the docs**

- `docs/Schemas.md` — no schema change in this plan; skip unless Task 7's live run
  reveals `deployment.host_port` needs a comment noting it's null for Fargate-backed rows
  (it does — add one line to that column's note).
- `docs/architecture.md` — extend the serving section with `FargateRuntime`'s file:line
  citations, the `endpoint_url` interface generalization and why it was needed, and the
  `VERITY_CONTAINER_RUNTIME` selection mechanism.
- `README.md` — note that container serving can now target either local Docker or AWS
  Fargate, selected by environment variable, with Fargate requiring the AWS resources
  this plan's spec lists.
- `docs/progression.md` — new entry: what shipped, the interface fix and why it was
  needed, the accepted risks (no autoscaling, cold start on replacement, public IP not a
  stable DNS name), and the real live-Fargate verification results (task ARN, public IP,
  confirmed CloudWatch logs).

- [ ] **Step 4: Full suite**

Run: `cd server; uv run pytest -q` (without `VERITY_RUN_FARGATE_LIVE_TEST` set, so the
default offline suite runs)
Expected: 278 passed, 1 skipped (aws marker) — plus whatever the docker marker resolves to.

---

## Self-Review

**Spec coverage:** every settled decision in the spec maps to a task — the interface
generalization (Task 1), build+ECR push (Task 2), task definition+launch+IP resolution
(Task 3), stop (Task 4), env-var selection defaulting to docker (Task 5), the opt-in-only
live test (Task 6), and documentation matching what was actually verified (Task 7).

**Type consistency:** `FargateRuntime.run()`'s return shape
(`container_id`/`endpoint_url`/`host_port: None`) matches exactly what Task 1 established
as the generalized `ContainerRuntime.run()` contract, and matches what `deploy.py` (Task
1) now reads via `.get("host_port")` rather than assuming its presence. `FargateRuntime`'s
constructor parameter names in Task 2 are the exact names Task 5's `_default_runtime()`
relies on being optional (all default to environment-variable lookups).

**Known gap carried from the spec, not a plan defect:** no autoscaling, no idle-shutdown,
a public IP rather than a stable DNS name — all named explicitly in the spec's Accepted
Risks and repeated in Task 7's documentation step rather than silently absent.
