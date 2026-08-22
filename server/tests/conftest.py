import pytest


def pytest_runtest_setup(item):
    """Skip daemon-dependent tests when there is no daemon.

    Keeps the default suite offline and fast. The alternative — failing when Docker
    isn't running — would make the suite lie about the health of code that is fine.
    """
    if "docker" not in item.keywords:
        return
    try:
        import docker

        docker.from_env().ping()
    except Exception as exc:  # noqa: BLE001 - any failure to reach the daemon is a skip
        pytest.skip(f"docker daemon unavailable: {type(exc).__name__}")
