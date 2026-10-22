# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging
import os
import shutil
from types import FrameType

os.environ.setdefault("POLARS_MAX_THREADS", "2")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
os.environ.setdefault("BENCHBOX_MAKE_TIMINGS", "0")
_GIT_LOCAL_ENV_FALLBACK = [
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
    "GIT_OBJECT_DIRECTORY",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_GRAFT_FILE",
    "GIT_INDEX_FILE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_REPLACE_REF_BASE",
    "GIT_PREFIX",
    "GIT_SHALLOW_FILE",
    "GIT_COMMON_DIR",
]
try:
    import subprocess as _subprocess

    _git_local_env = _subprocess.run(
        ["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True, check=True, timeout=10
    ).stdout.split()
except (OSError, _subprocess.SubprocessError):
    _git_local_env = []
for _git_local_key in {*_git_local_env, *_GIT_LOCAL_ENV_FALLBACK}:
    os.environ.pop(_git_local_key, None)

import sys
import time
import warnings
from pathlib import Path
from typing import Any

import pytest

pytest.register_assert_rewrite("tests.utilities.leak_detector")
from tests.utilities.leak_detector import restore_global
from tests.utilities.session_isolation import active as isolated_session_active

pytest_plugins = [
    "tests.fixtures.database_fixtures",
    "tests.fixtures.test_data_fixtures",
    "tests.fixtures.result_dict_fixtures",
    "tests.fixtures.platform_fixtures",
    "tests.fixtures.utility_fixtures",
    "tests.utilities.leak_detector",
]


@pytest.fixture
def joinorder_canonical_tiny(tmp_path: Path) -> Path:
    source = Path(__file__).parent / "fixtures" / "joinorder_canonical_tiny"
    target = tmp_path / "joinorder_canonical_tiny"
    shutil.copytree(source, target)
    return target


_test_lock_fd: int | None = None
_test_databases_created = False
_lock_waiter: Any = None
_lock_waiter_attempted = False


def _load_lock_waiter() -> Any:
    global _lock_waiter, _lock_waiter_attempted
    if _lock_waiter_attempted:
        return _lock_waiter
    _lock_waiter_attempted = True
    try:
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "scripts" / "local_validation.py"
        spec = importlib.util.spec_from_file_location("benchbox_local_validation", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _lock_waiter = module
    except Exception:
        _lock_waiter = None
    return _lock_waiter


def _lock_wait_seconds() -> float:
    try:
        return max(0.0, float(os.environ.get("BENCHBOX_TEST_LOCK_WAIT_SECONDS", "3600") or "3600"))
    except ValueError:
        return 3600.0


def _get_test_lock_path() -> Path:
    lock_dir = os.environ.get("BENCHBOX_TEST_LOCK_DIR")
    base_dir = Path(lock_dir).expanduser() if lock_dir else Path.home() / ".benchbox"
    return base_dir / "test.lock"


def _should_acquire_test_lock(config: pytest.Config) -> bool:
    if isolated_session_active():
        return False
    if hasattr(config, "workerinput"):
        return False
    if os.environ.get("BENCHBOX_SKIP_TEST_LOCK"):
        return False
    try:
        n = config.option.numprocesses
    except AttributeError:
        return False
    return bool(n)


def _is_xdist_remote_exec_namespace(globals_dict: dict[str, Any]) -> bool:
    return globals_dict.get("__name__") == "__channelexec__" and callable(globals_dict.get("worker_title"))


def _suppress_xdist_worker_title(start_frame: FrameType | None = None) -> bool:
    import inspect

    frame = start_frame or inspect.currentframe()
    if frame is None:
        return False
    if start_frame is None:
        frame = frame.f_back

    try:
        while frame is not None:
            if _is_xdist_remote_exec_namespace(frame.f_globals):
                frame.f_globals["worker_title"] = lambda title: None
                return True
            frame = frame.f_back
    finally:
        del frame

    return False


def pytest_configure(config) -> None:
    global _test_lock_fd

    if sys.platform == "darwin" and hasattr(config, "workerinput"):
        _suppress_xdist_worker_title()

    if _should_acquire_test_lock(config):
        test_lock_path = _get_test_lock_path()
        test_lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(test_lock_path), os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0), 0o644)
        waiter = _load_lock_waiter()
        wait_seconds = 0.0 if waiter is None else _lock_wait_seconds()
        lock_error: Exception | None = None
        if waiter is not None and wait_seconds > 0:
            try:
                waiter.wait_on_fd(fd, test_lock_path, wait_seconds)
            except TimeoutError as exc:
                lock_error = exc
            except BaseException:
                os.close(fd)
                raise
        else:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError) as exc:
                lock_error = exc
        if lock_error is not None:
            holder_info = waiter.read_holder(test_lock_path) if waiter is not None else "(could not read lock file)"
            os.close(fd)
            waited_note = (
                f"  Waited    : {wait_seconds:g}s (BENCHBOX_TEST_LOCK_WAIT_SECONDS)\n" if wait_seconds > 0 else ""
            )
            sys.stderr.write(
                f"\n\033[91m[benchbox] BLOCKED: A parallel test run is still active.\033[0m\n"
                f"  Lock file : {test_lock_path}\n"
                f"  Holder    : {holder_info}\n"
                f"{waited_note}\n"
                f"  Options:\n"
                f"    \u2022 Wait for the other run to finish and retry.\n"
                f"    \u2022 Kill the other run, then retry.\n"
                f"    \u2022 Run single-threaded (no lock):  pytest -n 0 ...\n"
                f"    \u2022 Bypass lock (dangerous):         BENCHBOX_SKIP_TEST_LOCK=1 pytest ...\n\n"
            )
            sys.stderr.flush()
            os._exit(1)
        if waiter is not None:
            waiter.write_holder(fd, test_lock_path, phase="pytest-session", gate="xdist")
        else:
            started = time.strftime("%Y-%m-%d %H:%M:%S")
            cmd = " ".join(sys.argv[:4])
            try:
                os.ftruncate(fd, 0)
                os.write(fd, f"pid:{os.getpid()} started:{started} phase:pytest-session cmd:{cmd}\n".encode())
            except OSError:
                pass
        _test_lock_fd = fd

    try:
        import duckdb as _duckdb_mod

        _original_duckdb_connect = _duckdb_mod.connect

        def _limited_duckdb_connect(*args, **kwargs):
            cfg = kwargs.get("config") or {}
            if isinstance(cfg, dict) and "threads" not in cfg:
                cfg["threads"] = "2"
                kwargs["config"] = cfg
            return _original_duckdb_connect(*args, **kwargs)

        _duckdb_mod.connect = _limited_duckdb_connect
    except ImportError:
        pass


def pytest_unconfigure(config) -> None:
    global _test_lock_fd
    if _test_lock_fd is not None:
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(_test_lock_fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(_test_lock_fd, fcntl.LOCK_UN)
            os.close(_test_lock_fd)
        except OSError:
            pass
        _test_lock_fd = None


def _warn_on_unreasoned_skip_markers(items) -> None:
    if os.environ.get("BENCHBOX_SKIP_REASON_CHECK"):
        return
    offenders: list[str] = []
    for item in items:
        for marker in item.iter_markers():
            if marker.name not in {"skip", "skipif", "xfail"}:
                continue
            if marker.kwargs.get("reason"):
                continue
            if marker.name in {"skip", "xfail"} and marker.args and isinstance(marker.args[0], str):
                continue
            offenders.append(f"{item.nodeid}: @pytest.mark.{marker.name} without reason=")
    if offenders:
        import warnings

        for line in offenders[:20]:
            warnings.warn(line, UserWarning, stacklevel=0)
        if len(offenders) > 20:
            warnings.warn(
                f"... and {len(offenders) - 20} more skip/xfail markers without reason=",
                UserWarning,
                stacklevel=0,
            )


def _items_require_test_databases(items) -> bool:
    return any(item.get_closest_marker("integration") or item.get_closest_marker("database") for item in items)


def _create_test_databases() -> None:
    global _test_databases_created
    if _test_databases_created:
        return

    import subprocess
    import sys

    test_db_dir = Path(__file__).parent / "databases"
    test_db_dir.mkdir(exist_ok=True)

    create_script = test_db_dir / "create_test_databases.py"
    if create_script.exists():
        try:
            result = subprocess.run(
                [sys.executable, str(create_script)],
                capture_output=True,
                text=True,
                cwd=str(Path(__file__).parent.parent),
            )
            if result.returncode != 0:
                print(f"Warning: Failed to create test databases: {result.stderr}")
        except Exception as e:
            print(f"Warning: Error creating test databases: {e}")

    _test_databases_created = True


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(session, config, items) -> None:
    from tests.duration_policy import (
        current_test_tier,
        is_bootstrap_artifact,
        load_durations,
        t1_budget_violations,
        validate_markers,
    )

    try:
        tier = current_test_tier()
        durations = load_durations()
    except (OSError, ValueError) as exc:
        raise pytest.UsageError(f"test duration policy is invalid: {exc}") from exc

    duration_bootstrap = is_bootstrap_artifact() or os.environ.get(
        "BENCHBOX_TEST_DURATION_BOOTSTRAP", ""
    ).strip().lower() in {
        "1",
        "true",
        "yes",
    }
    violations: list[str] = []
    for item in items:
        violations.extend(validate_markers(item))
        if tier == "t1":
            violations.extend(t1_budget_violations(item, durations, allow_missing=duration_bootstrap))
        if tier in {"t1", "t2"} and item.get_closest_marker("quarantine") is not None:
            item.add_marker(pytest.mark.skip(reason=f"quarantined outside T3 ({tier})"))
    if violations:
        raise pytest.UsageError("test duration policy violations:\n" + "\n".join(violations))

    if _items_require_test_databases(items):
        _create_test_databases()


def pytest_collection_finish(session) -> None:
    _warn_on_unreasoned_skip_markers(session.items)


@pytest.fixture(autouse=True)
def _reset_global_quiet_state(request, _hermetic_state):
    yield
    restore_global(request.node, "benchbox.utils.printing", "_QUIET", False)


def _benchbox_loggers() -> dict[str, logging.Logger]:
    return {
        name: logger
        for name, logger in list(logging.root.manager.loggerDict.items())
        if isinstance(logger, logging.Logger) and (name == "benchbox" or name.startswith("benchbox."))
    }


def _snapshot_logger(logger: logging.Logger) -> tuple[int, list[logging.Handler], bool, bool]:
    return logger.level, list(logger.handlers), logger.propagate, logger.disabled


def _restore_logger(logger: logging.Logger, state: tuple[int, list[logging.Handler], bool, bool]) -> None:
    logger.level = state[0]
    logger.handlers[:] = state[1]
    logger.propagate = state[2]
    logger.disabled = state[3]


@pytest.fixture(autouse=True)
def _restore_logging_state():
    root_state = _snapshot_logger(logging.getLogger())
    known = {name: (logger, _snapshot_logger(logger)) for name, logger in _benchbox_loggers().items()}
    yield
    _restore_logger(logging.getLogger(), root_state)
    for name, logger in _benchbox_loggers().items():
        if name in known:
            _restore_logger(logger, known[name][1])
        else:
            _restore_logger(logger, (logging.NOTSET, [], True, False))
    logging.root.manager._clear_cache()


@pytest.fixture(autouse=True)
def _reset_deprecation_notices():
    from benchbox.core.deprecation_notices import begin_run

    begin_run()
    yield
    begin_run()


@pytest.fixture(autouse=True)
def _reset_global_config_provider(request, _hermetic_state):
    yield
    restore_global(request.node, "benchbox.utils.config_interface", "_config_provider", None)


def pytest_sessionfinish(session, exitstatus) -> None:
    from pathlib import Path

    test_db_dir = Path(__file__).parent / "databases"

    if test_db_dir.exists():
        for db_file in test_db_dir.glob("*.duckdb"):
            try:
                db_file.unlink()
            except Exception as e:
                print(f"Warning: Could not remove {db_file}: {e}")


def pytest_terminal_summary(terminalreporter, config, exitstatus) -> None:
    if not config.pluginmanager.hasplugin("_cov"):
        return

    try:
        import io

        import coverage

        threshold = 80.0

        cov = coverage.Coverage(data_file=os.environ.get("COVERAGE_FILE", ".coverage"), config_file="pyproject.toml")
        cov.load()

        buf = io.StringIO()
        total = cov.report(ignore_errors=True, file=buf)

        if total < threshold:
            terminalreporter.write_sep(
                "-",
                f"WARNING: Test coverage {total:.2f}% is below threshold {threshold:.0f}%",
            )
    except Exception as e:  # pragma: no cover
        terminalreporter.write_line(f"Note: Coverage warning check skipped: {e}")


def _tree_digest(root: Path) -> dict[str, tuple[int, str]]:
    import hashlib

    return {
        str(path.relative_to(root)): (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture(scope="session")
def tpch_sf001_data(tmp_path_factory: pytest.TempPathFactory):
    import shutil

    from benchbox.cli.config import ConfigManager
    from benchbox.core.benchmark_loader import get_benchmark_instance
    from benchbox.core.config import BenchmarkConfig
    from benchbox.core.system import SystemProfiler
    from benchbox.utils.output_path import normalize_output_root

    cache_root = tmp_path_factory.mktemp("shared_tpch_sf001")
    cached_dir = cache_root / "tpch_sf001"
    assert normalize_output_root(str(cache_root), "tpch", 0.01) == str(cached_dir)
    cli_config = ConfigManager()
    compress = cli_config.get("output.compression.enabled", False) and os.getenv(
        "BENCHBOX_NO_COMPRESSION", ""
    ).lower() not in ["true", "1", "yes", "on"]
    config = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=0.01,
        compress_data=compress,
        compression_type=cli_config.get("output.compression.type", "zstd"),
        compression_level=cli_config.get("output.compression.level", None),
    )
    benchmark = get_benchmark_instance(
        config,
        SystemProfiler().get_system_profile(),
        output_dir=cached_dir,
        quiet=True,
    )
    benchmark.generate_data()
    generated = _tree_digest(cached_dir)
    assert "_datagen_manifest.json" in generated

    def copy_to(destination: Path) -> str:
        shutil.copytree(cache_root, destination)
        return str(destination)

    yield copy_to
    assert _tree_digest(cached_dir) == generated
