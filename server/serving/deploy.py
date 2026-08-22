"""Turn a promoted model version into a running container.

Every step is recorded on the `deployment` row as it happens, so a failure leaves an
explanation behind rather than a log line. The caller (the orchestrator) treats a raised
DeployError as non-fatal — see its comment there for why.
"""

import tempfile

HEALTH_TIMEOUT_SECONDS = 60


class DeployError(Exception):
    """The version could not be stood up. The deployment row says why."""


def deploy(
    *,
    model_version_id,
    payload,
    io_schema,
    environment,
    metadata_store,
    archived_model_version_id=None,
    runtime=None,
    render_fn=None,
    wait_healthy_fn=None,
    tempdir_fn=None,
):
    runtime = runtime or _default_runtime()
    render_fn = render_fn or _default_render
    wait_healthy_fn = wait_healthy_fn or _default_wait_healthy
    tempdir_fn = tempdir_fn or tempfile.TemporaryDirectory

    tag = _image_tag(model_version_id)
    deployment_id = metadata_store.save_deployment(
        model_version_id=model_version_id, image_tag=tag, status="building"
    )

    container_id = None
    try:
        with tempdir_fn() as context_dir:
            render_fn(
                dest=context_dir,
                payload=payload,
                io_schema=io_schema,
                environment=environment,
            )
            runtime.build(context_dir=context_dir, tag=tag)

        started = runtime.run(tag=tag)
        container_id = started["container_id"]
        endpoint_url = f"http://localhost:{started['host_port']}"

        if not wait_healthy_fn(
            url=f"{endpoint_url}/health", timeout=HEALTH_TIMEOUT_SECONDS
        ):
            raise DeployError(
                f"container did not report healthy within {HEALTH_TIMEOUT_SECONDS}s — "
                "the likeliest cause is the artifact failing to load in the built image"
            )

        metadata_store.update_deployment(
            deployment_id=deployment_id,
            status="live",
            container_id=container_id,
            host_port=started["host_port"],
            endpoint_url=endpoint_url,
        )
    except Exception as exc:
        # A started-but-unhealthy container must not be left running behind a `failed`
        # row — nothing would ever come back to clean it up.
        if container_id is not None:
            _quietly_stop(runtime, container_id)
        metadata_store.update_deployment(
            deployment_id=deployment_id,
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        raise DeployError(str(exc)) from exc

    # Only after the replacement is confirmed live. Tearing down first would open a
    # window with nothing serving at all.
    if archived_model_version_id:
        _tear_down(
            metadata_store=metadata_store,
            runtime=runtime,
            model_version_id=archived_model_version_id,
        )

    return {
        "id": deployment_id,
        "status": "live",
        "host_port": started["host_port"],
        "endpoint_url": endpoint_url,
        "image_tag": tag,
    }


def _tear_down(*, metadata_store, runtime, model_version_id):
    """Stop the container the newly promoted version displaced.

    Deliberately best-effort. The new version is already live and serving; failing its
    deploy because an already-dead old container could not be stopped would be strictly
    worse than leaving a stale row behind.
    """
    try:
        previous = metadata_store.find_live_deployment(model_version_id=model_version_id)
        if not previous:
            return
        if previous.get("container_id"):
            _quietly_stop(runtime, previous["container_id"])
        metadata_store.update_deployment(deployment_id=previous["id"], status="stopped")
    except Exception:  # noqa: BLE001
        return


def _quietly_stop(runtime, container_id):
    try:
        runtime.stop(container_id=container_id)
    except Exception:  # noqa: BLE001
        pass


def _image_tag(model_version_id):
    from serving.build import image_tag

    return image_tag(model_version_id)


def _default_render(**kwargs):
    from serving.build import render_context

    return render_context(**kwargs)


def _default_runtime():
    from serving.runtime import DockerRuntime

    return DockerRuntime()


def _default_wait_healthy(**kwargs):
    from serving.runtime import wait_healthy

    return wait_healthy(**kwargs)
