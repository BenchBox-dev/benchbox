from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from tests.uat import docker_assets, matrix
from tests.uat.preflight_budget import MemorySnapshot

HERMETIC_FREE_MEMORY_GIB = 64.0

HERMETIC_FREE_SPACE_GIB = 500.0


@contextmanager
def platform_reachability(value: bool = True, *, probe=None):
    with patch("tests.uat.phases.execute.platform_is_reachable", return_value=value):
        if probe is None:
            with patch("tests.uat.phases.execute.probe_platform_reachability", return_value=value):
                yield
        else:
            with patch("tests.uat.phases.execute.probe_platform_reachability", side_effect=probe):
                yield


def docker_verb(argv) -> str:
    if "up" in argv:
        return "up"
    if "ps" in argv:
        return "ps"
    if "stats" in argv:
        return "stats"
    if "logs" in argv:
        return "logs"
    return "down"


def healthy_ps_stdout(service: str = "svc") -> str:
    return f"NAME      IMAGE     COMMAND   STATUS\n{service}   img       cmd       Up 5 seconds\n"


@pytest.fixture(autouse=True)
def isolate_reachability_cache():
    matrix._REACHABILITY_CACHE.clear()
    yield
    matrix._REACHABILITY_CACHE.clear()


@pytest.fixture(autouse=True)
def isolate_container_cli_resolution(monkeypatch):
    docker_assets.resolve_container_cli.cache_clear()
    monkeypatch.setenv(docker_assets.CONTAINER_CLI_ENV_VAR, "docker")
    monkeypatch.setattr(docker_assets, "_which_container_cli", lambda cli: f"/usr/bin/{cli}")
    yield
    docker_assets.resolve_container_cli.cache_clear()


@pytest.fixture(autouse=True)
def isolate_free_memory_reading(monkeypatch):
    monkeypatch.setattr(
        "tests.uat.phases.execute.default_free_memory_reader",
        lambda: MemorySnapshot(free_gib=HERMETIC_FREE_MEMORY_GIB, swap_used_percent=0.0),
    )


@pytest.fixture(autouse=True)
def isolate_free_space_reading(monkeypatch):
    monkeypatch.setattr(
        "tests.uat.phases.execute.default_free_space_reader",
        lambda _path: HERMETIC_FREE_SPACE_GIB,
    )
    monkeypatch.setattr(
        "tests.uat.preflight_budget.free_space_gib",
        lambda _path: HERMETIC_FREE_SPACE_GIB,
    )
