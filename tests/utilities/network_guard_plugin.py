from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.utilities import network_guard

_GUARDED_MARKERS = {"unit", "medium"}
_UNIT_ROOT = Path(__file__).resolve().parents[1] / "unit"
_state: dict[str, object] = {}


def pytest_configure(config: pytest.Config) -> None:
    directory = tempfile.mkdtemp(prefix="benchbox-network-guard-")
    previous = {key: os.environ.get(key) for key in (network_guard.ENV_DIR, "PYTHONPATH")}
    _state.update(dir=directory, previous=previous)
    os.environ[network_guard.ENV_DIR] = directory
    site = network_guard.site_dir()
    existing = previous["PYTHONPATH"] or ""
    if site not in existing.split(os.pathsep):
        os.environ["PYTHONPATH"] = f"{site}{os.pathsep}{existing}" if existing else site


def pytest_unconfigure(config: pytest.Config) -> None:
    previous = _state.pop("previous", {})
    for key, value in dict(previous).items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    directory = _state.pop("dir", None)
    if directory:
        shutil.rmtree(str(directory), ignore_errors=True)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    missing = []
    for item in items:
        for marker in item.iter_markers("allow_network"):
            reason = marker.kwargs.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                missing.append(item.nodeid)
                break
    if missing:
        raise pytest.UsageError(
            "@pytest.mark.allow_network requires a non-empty reason=, missing on:\n" + "\n".join(missing)
        )


def is_guarded(request: pytest.FixtureRequest) -> bool:
    node = request.node
    names = {marker.name for marker in node.iter_markers()}
    in_unit_tree = _under_unit_tree(node)
    if "allow_network" in names:
        return False
    if names & {"integration", "live_integration"} and not in_unit_tree:
        return False
    if in_unit_tree:
        return True
    if names & _GUARDED_MARKERS:
        return True
    return False


def _under_unit_tree(node: pytest.Item) -> bool:
    try:
        return _UNIT_ROOT in Path(str(node.path)).resolve().parents
    except (AttributeError, OSError):
        return False


@pytest.fixture
def network_guard_log(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("network_guard") / "violations.log"


@pytest.fixture
def expect_network_block(network_guard_log: Path) -> Iterator[None]:
    yield
    violations = network_guard.take_violations()
    log_name = str(network_guard_log)
    if os.path.exists(log_name):
        with open(log_name, encoding="utf-8") as handle:
            violations.extend(line for line in handle if line.strip())
        os.unlink(log_name)
    if not violations:
        pytest.fail("expect_network_block was requested but the guard blocked nothing", pytrace=False)


@pytest.fixture(autouse=True)
def _network_guard(request: pytest.FixtureRequest) -> Iterator[None]:
    directory = _state.get("dir")
    if not directory or not is_guarded(request):
        yield
        return
    log = request.getfixturevalue("network_guard_log")
    switch = Path(str(directory)) / network_guard.SWITCH_NAME
    switch.write_text(str(log), encoding="utf-8")
    network_guard.install()
    network_guard.take_violations()
    try:
        yield
    finally:
        violations = network_guard.take_violations()
        network_guard.uninstall()
        switch.unlink(missing_ok=True)
    log_name = str(log)
    if os.path.exists(log_name):
        with open(log_name, encoding="utf-8") as handle:
            violations.extend(line.rstrip("\n") for line in handle if line.strip())
    if violations:
        pytest.fail("\n".join(dict.fromkeys(violations)), pytrace=False)
