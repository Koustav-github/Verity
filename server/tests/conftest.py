import os

import pytest


def pytest_runtest_setup(item):
    """Skip daemon-dependent tests when there is no daemon, and skip real-AWS tests
    unless explicitly opted into — keeps the default suite offline, fast, and free.
    """
    if "docker" not in item.keywords and "aws" not in item.keywords:
        return
    if "docker" in item.keywords:
        try:
            import docker

            docker.from_env().ping()
        except Exception as exc:  # noqa: BLE001 - any failure to reach the daemon is a skip
            pytest.skip(f"docker daemon unavailable: {type(exc).__name__}")

    if "aws" in item.keywords and os.environ.get("VERITY_RUN_FARGATE_LIVE_TEST") != "1":
        pytest.skip("set VERITY_RUN_FARGATE_LIVE_TEST=1 to run this against real AWS")
