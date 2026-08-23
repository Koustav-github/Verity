# FargateRuntime — a second ContainerRuntime implementation

## Context

`server/serving/runtime.py` was built during api-fication with a deliberate seam:
`ContainerRuntime` is a three-method interface (`build`, `run`, `stop`), `DockerRuntime`
is the only implementation, and the module's own docstring says why — *"An ECS or
Fargate runtime later is a new class here plus one wiring change in deploy.py — which is
the entire reason this seam exists at V1, when there is only one implementation."*

This spec is that new class. The AWS resources it deploys against already exist, created
manually against a real account:

| Resource | Value |
|---|---|
| Region | `us-east-1` |
| ECR repository | `504509954111.dkr.ecr.us-east-1.amazonaws.com/verity/verity-model` |
| ECS cluster | `verity-cluster` |
| Task execution role | `arn:aws:iam::504509954111:role/verity-ecs-task-execution-role` |
| VPC | `vpc-0f94cc7e5dbba5086` |
| Subnets (6) | `subnet-0e197b9553ce8700e`, `subnet-0cf2803e0eb1c6c79`, `subnet-05aefe4b43bbf2070`, `subnet-0f3614049e0dee353`, `subnet-0ef7392f6f419ee5d`, `subnet-0ee72c149c94b5a65` |
| Security group | `sg-02fa20ff3f3beae38` (inbound TCP 8000 from `0.0.0.0/0`) |
| CloudWatch log group | `/ecs/verity-model` |

### Settled decisions

| Decision | Choice |
|---|---|
| Compute | ECS Fargate — no EC2 instances to manage |
| Selection | `VERITY_CONTAINER_RUNTIME=docker\|fargate` env var, defaulting to `docker`; nothing changes for existing local dev or tests unless explicitly opted in |
| Task lifecycle | Matches what already exists today: a version's task runs until a new version under the same name is promoted and archives it (the existing teardown path in `deploy.py` already calls `stop()`). **No idle-timeout, no autoscaling.** A promoted version is a continuously-running, continuously-billed task for as long as it's the live version — accepted, matching api-fication's own prior "single replica, no autoscaling" accepted risk |
| Task sizing | Fargate's smallest valid combination — 256 CPU units (.25 vCPU) / 512 MB memory — sized for a small sklearn/XGBoost/LightGBM model, not a training workload |
| Task definition | One family (`verity-model`), one new **revision** registered per deploy — ECS's own versioning primitive, not a family-per-model-version scheme |
| Logs | Wired at deploy time via the task definition's `awslogs` log driver pointing at the existing log group — automatic, no application code involved |
| Networking | `awsvpc` mode, `assignPublicIp=ENABLED` — the server calling this runs locally, not inside the VPC, so tasks need a real public IP to be reachable at all |

## The interface gap this closes

`ContainerRuntime.run()` today returns `{"container_id", "host_port"}`, and
`server/serving/deploy.py:53` builds the endpoint itself: `f"http://localhost:{host_port}"`.
That line assumes every runtime's containers are reachable at `localhost` — true for
Docker, never true for Fargate, which hands back a real IP address on a real network
interface. This was never exercised because there was only one implementation.

**Fix:** `run()` returns `{"container_id", "endpoint_url"}` — the runtime builds its own
URL, since only the runtime knows whether that's `localhost:<port>` or
`http://<public-ip>:8000`. `deploy.py` stops constructing the URL itself and just uses
what it's given. `host_port` becomes optional in the returned dict (meaningful for
Docker's ephemeral port; genuinely absent for Fargate, where the container port is always
the fixed `8000` and the identifying detail is the IP, already inside `endpoint_url`) —
`deployment.host_port` in the database stays nullable and is simply not populated for a
Fargate-backed deployment.

This touches `DockerRuntime.run()` (build its own `endpoint_url` internally, one line),
`deploy.py` (delete the URL-construction line, read `started["endpoint_url"]`), and their
existing tests (`test_deploy.py`'s `FakeRuntime`, `test_runtime_docker.py`'s assertions).

## Architecture

```
FargateRuntime.build(context_dir, tag)
   ├─ delegates the actual `docker build` to an internal DockerRuntime instance —
   │    identical local build, no duplicated Dockerfile-handling logic
   └─ ECR push:
        ├─ boto3 ecr.get_authorization_token() → temporary docker login credentials
        ├─ re-tag the built image as <ecr_repository_uri>:<tag>
        └─ docker push

FargateRuntime.run(tag)
   ├─ ecs.register_task_definition(
   │     family="verity-model",
   │     containerDefinitions=[{image: <ecr_uri>:<tag>, port 8000,
   │       logConfiguration: awslogs → /ecs/verity-model}],
   │     requiresCompatibilities=["FARGATE"], cpu="256", memory="512",
   │     executionRoleArn=<the role ARN>)
   │  → task definition ARN (a new revision of the same family)
   ├─ ecs.run_task(cluster="verity-cluster", taskDefinition=<arn>,
   │     launchType="FARGATE",
   │     networkConfiguration={awsvpcConfiguration: {subnets: [...6...],
   │       securityGroups: [sg-...], assignPublicIp: "ENABLED"}})
   │  → task ARN, status PROVISIONING
   ├─ poll ecs.describe_tasks(...) until lastStatus == "RUNNING"
   │     (separate from deploy.py's existing wait_healthy() — this confirms the task
   │      *exists with a network address*; wait_healthy confirms the *app inside it*
   │      is actually serving)
   ├─ resolve the task's ENI to a public IP via ec2.describe_network_interfaces()
   │     (ECS's own describe_tasks response gives you the ENI id, not the IP directly)
   └─ return {"container_id": <task ARN>, "endpoint_url": f"http://{public_ip}:8000"}

FargateRuntime.stop(container_id)
   └─ ecs.stop_task(cluster="verity-cluster", task=<task ARN>)
```

## Testing

Matching this codebase's existing convention exactly: hand-written fakes for every
`boto3` client used (`ecs`, `ecr`, `ec2`), zero `unittest.mock`, and — mirroring
`test_runtime_docker.py`'s single real-daemon test guarded by `@pytest.mark.docker`
(auto-skipped when no daemon answers) — **one real end-to-end test against actual AWS,
guarded by an explicit opt-in environment variable, not auto-detected credentials.**
Unlike a local Docker daemon, a reachable AWS credential is not a safe signal to run
automatically — it costs real money and takes real time (a Fargate task typically takes
30–90 seconds to reach `RUNNING` with a network address attached), so the marker must
require `VERITY_RUN_FARGATE_LIVE_TEST=1` explicitly set, never inferred.

## Accepted risks, named not solved

- **No autoscaling, no idle-shutdown** — a promoted version is a continuously-billed task
  for its entire time as the live version. Named explicitly because Fargate, unlike local
  Docker, has a real dollar cost attached to this.
- **A replacement's cold start is real** — a new version's task takes tens of seconds to
  reach `RUNNING`, on top of whatever the image build/push took. The existing
  `wait_healthy()` step in `deploy.py` already handles waiting for the app inside to be
  ready; this spec adds a *second*, prior wait for the task to have a network address at
  all — both are real time a promotion-with-deploy request will spend.
- **Public IP, not a stable DNS name.** Every new task gets a fresh IP; there's no load
  balancer or DNS layer in front of it. Acceptable at this scale (single task, single
  region, a `deployment.endpoint_url` row recording whatever IP is current), but a real
  limitation if this needs to look like a stable API from the outside later.

## Out of scope

Autoscaling, multi-replica, idle-based shutdown, a load balancer / stable DNS name, and
tearing down the CloudWatch log group's retention (defaults to never-expire — a real,
separate cost/cleanup decision for later).
