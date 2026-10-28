from __future__ import annotations

import contextlib
import itertools
import os
import shutil
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from tests.utilities import network_guard

_GUARDED_MARKERS = {"unit", "medium"}
_UNIT_ROOT = Path(__file__).resolve().parents[1] / "unit"
_state: dict[str, object] = {}
_log_numbers = itertools.count()


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
def network_guard_log() -> Path:
    return Path(str(_state["dir"])) / f"violations-{next(_log_numbers)}.log"


@pytest.fixture
def expect_network_block(network_guard_log: Path) -> Iterator[Callable[[str], contextlib.AbstractContextManager[None]]]:
    @contextlib.contextmanager
    def expect(target: str) -> Iterator[None]:
        if not target.strip():
            raise ValueError("expect_network_block needs a non-empty target")
        before = len(network_guard.peek_violations())
        log_name = str(network_guard_log)
        before_log = _read_lines(log_name)
        yield
        in_process = network_guard.peek_violations()[before:]
        from_log = _read_lines(log_name)[len(before_log) :]
        matching = [
            line for line in [*in_process, *from_log] if f"target \"('{target}'" in line or f"target '{target}'" in line
        ]
        if not matching:
            pytest.fail(f"expected the network guard to block {target!r}, but it blocked nothing for it", pytrace=False)
        network_guard.discard_violations(matching)
        kept = [line for line in _read_lines(log_name) if line not in matching]
        if os.path.exists(log_name):
            with open(log_name, "w", encoding="utf-8") as handle:
                handle.write("".join(f"{line}\n" for line in kept))

    yield expect


def _read_lines(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [line.rstrip("\n") for line in handle if line.strip()]


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
