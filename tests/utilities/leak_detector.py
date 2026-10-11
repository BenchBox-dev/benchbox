from __future__ import annotations

import json
import os
import sys
from collections import deque
from collections.abc import Generator, Iterator
from pathlib import Path
from typing import Any

import pytest

REGISTERED_GLOBALS: tuple[tuple[str, str, Any], ...] = (
    ("benchbox.utils.printing", "_QUIET", False),
    ("benchbox.utils.config_interface", "_config_provider", None),
    ("rich.prompt", "Confirm", None),
    ("benchbox.platforms.credentials.bigquery", "Confirm", None),
)
LIBRARY_IMPORT_ENV: tuple[tuple[str, str, str], ...] = (
    ("ARROW_DEFAULT_MEMORY_POOL", "system", "snowflake.connector.options"),
)
_BASELINE_KEY = pytest.StashKey[dict[str, Any]]()
_LEAK_KEY = pytest.StashKey[list[str]]()
_TRACE_BUFFER: deque[dict[str, Any]] = deque(maxlen=256)
_TRACE_FAILURES: set[tuple[str, str]] = set()


def _read_global(module: str, attr: str, default: Any) -> Any:
    mod = sys.modules.get(module)
    return getattr(mod, attr, default) if mod is not None else default


def snapshot() -> dict[str, Any]:
    modules = {module for module, _, _ in REGISTERED_GLOBALS if module in sys.modules}
    return {
        "cwd": os.getcwd(),
        "env": dict(os.environ),
        "modules": {module: sys.modules[module] for module in modules},
        "globals": {f"{m}.{a}": _read_global(m, a, default) for m, a, default in REGISTERED_GLOBALS},
    }


def _trace_record(item: pytest.Item, phase: str) -> dict[str, Any] | None:
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if worker is None or not os.environ.get("BENCHBOX_XDIST_TRACE_DIR"):
        return None
    modules: dict[str, int | None] = {}
    globals_: dict[str, int | None] = {}
    for module, attr, default in REGISTERED_GLOBALS:
        loaded = sys.modules.get(module)
        modules[module] = id(loaded) if loaded is not None else None
        value = getattr(loaded, attr, default) if loaded is not None else default
        globals_[f"{module}.{attr}"] = id(value) if value is not None else None
    return {
        "worker": worker,
        "nodeid": item.nodeid,
        "phase": phase,
        "modules": modules,
        "globals": globals_,
    }


def _append_trace(item: pytest.Item, phase: str) -> None:
    record = _trace_record(item, phase)
    if record is not None:
        _TRACE_BUFFER.append(record)


def _write_failure_trace(item: pytest.Item, phase: str) -> None:
    record = _trace_record(item, f"failure:{phase}")
    if record is None:
        return
    failure = (item.nodeid, phase)
    if failure in _TRACE_FAILURES:
        return
    _TRACE_FAILURES.add(failure)
    _TRACE_BUFFER.append(record)
    directory = Path(os.environ["BENCHBOX_XDIST_TRACE_DIR"])
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{record['worker']}.jsonl"
    with target.open("a", encoding="utf-8") as stream:
        event = {"failed_nodeid": item.nodeid, "phase": phase, "events": list(_TRACE_BUFFER)}
        stream.write(json.dumps(event, sort_keys=True) + "\n")


def _restore_module(module_name: str, module: Any) -> None:
    sys.modules[module_name] = module
    parent_name, _, attribute = module_name.rpartition(".")
    parent = sys.modules.get(parent_name)
    if parent is not None:
        setattr(parent, attribute, module)


def restore_global(item: pytest.Item, module: str, attr: str, default: Any) -> None:
    if item.stash.get(_BASELINE_KEY, None) is not None:
        return
    mod = sys.modules.get(module)
    if mod is not None:
        setattr(mod, attr, default)


def detect_and_restore(baseline: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    try:
        cwd = os.getcwd()
    except FileNotFoundError:
        cwd = None
    if cwd != baseline["cwd"]:
        problems.append("cwd")
        os.chdir(baseline["cwd"])

    env = dict(os.environ)
    old = baseline["env"]
    for key, value, module in LIBRARY_IMPORT_ENV:
        if key not in old and env.get(key) == value and module in sys.modules:
            old[key] = value
    for key in sorted(set(old) | set(env)):
        if (key in old) != (key in env) or old.get(key) != env.get(key):
            problems.append(f"env[{key}]")
            if key in old:
                os.environ[key] = old[key]
            else:
                os.environ.pop(key, None)

    for module_name, before in baseline["modules"].items():
        if sys.modules.get(module_name) is not before:
            problems.append(f"sys.modules[{module_name}]")
            _restore_module(module_name, before)

    for module, attr, default in REGISTERED_GLOBALS:
        if module not in baseline["modules"]:
            continue
        name = f"{module}.{attr}"
        before = baseline["globals"][name]
        if _read_global(module, attr, default) is not before:
            problems.append(name)
            mod = sys.modules.get(module)
            if mod is not None:
                setattr(mod, attr, before)
    return problems


def _check_item(item: pytest.Item) -> None:
    baseline = item.stash.get(_BASELINE_KEY, None)
    if baseline is None:
        return
    expected = f"{item.nodeid} (teardown)"
    if os.environ.get("PYTEST_CURRENT_TEST") == expected:
        baseline["env"]["PYTEST_CURRENT_TEST"] = expected
    elif "PYTEST_CURRENT_TEST" not in os.environ:
        baseline["env"].pop("PYTEST_CURRENT_TEST", None)
    trace = _trace_record(item, "teardown")
    problems = detect_and_restore(baseline)
    if problems:
        if trace is not None:
            trace["leaks"] = problems
            _TRACE_BUFFER.append(trace)
        item.stash[_LEAK_KEY] = problems
        pytest.fail("test leaked process state past teardown (restored): " + ", ".join(problems), pytrace=False)


def pytest_runtest_call(item: pytest.Item) -> None:
    _append_trace(item, "call")


@pytest.fixture(autouse=True)
def _hermetic_state(request: pytest.FixtureRequest) -> Iterator[None]:
    item = request.node
    unit_dir = request.config.rootpath / "tests" / "unit"
    if item.path.is_relative_to(unit_dir) or item.get_closest_marker("unit") is not None:
        item.stash[_BASELINE_KEY] = snapshot()
        item.addfinalizer(lambda: _check_item(item))
    yield


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]) -> Generator[None, Any, Any]:
    report = yield
    if report.failed:
        _write_failure_trace(item, report.when)
    if report.when == "teardown" and item.stash.get(_LEAK_KEY, None):
        report.outcome = "failed"
        if hasattr(report, "wasxfail"):
            del report.wasxfail
    return report
