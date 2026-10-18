from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

import benchbox.platforms.dataframe as dataframe_platforms
from benchbox.cli.commands.run import run
from benchbox.cli.config import ConfigManager
from benchbox.core.hooks.platform_hooks import PlatformHookRegistry
from benchbox.core.platform_config import get_platform_config
from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.schemas import DatabaseConfig, SystemProfile
from benchbox.platforms import _DATAFRAME_PLATFORM_INFO, get_platform_adapter, is_dataframe_platform
from benchbox.utils.runtime_env import DriverResolution

pytestmark = [pytest.mark.unit, pytest.mark.medium]

CROSS_CUTTING_OPTIONS = frozenset(
    {"driver_version", "driver_auto_install", "plan_max_depth", "plan_capture_timeout_seconds", "force_recreate"}
)
KNOWN_UNAPPLIED_OPTIONS = {"sqlite": frozenset({"database_name"})}

OPTION_CASES: dict[str, dict[str, tuple[str, str, Any]]] = {
    "emr-serverless": {"compute_resource": ("app-123", "application_id", "app-123")},
    "dataproc": {"compute_resource": ("cluster-1", "cluster_name", "cluster-1")},
    "dataproc-serverless": {},
    "synapse-spark": {
        "adaptive_enabled": ("false", "adaptive_enabled", False),
        "compute_resource": ("pool-1", "spark_pool_name", "pool-1"),
    },
    "fabric-spark": {
        "adaptive_enabled": ("false", "adaptive_enabled", False),
        "compute_resource": ("pool-2", "spark_pool_name", "pool-2"),
    },
    "glue": {},
    "snowpark-connect": {
        "compute_resource": ("WH_CUSTOM", "warehouse", "WH_CUSTOM"),
        "compute_size": ("LARGE", "warehouse_size", "LARGE"),
    },
    "fabric_dw": {
        "server": ("ws.datawarehouse.fabric.microsoft.com", "server", "ws.datawarehouse.fabric.microsoft.com"),
        "workspace": ("ws-1", "workspace", "ws-1"),
        "database": ("wh-1", "database", "wh-1"),
        "auth_method": ("interactive", "auth_method", "interactive"),
        "tenant_id": ("tenant-1", "tenant_id", "tenant-1"),
        "client_id": ("client-1", "client_id", "client-1"),
        "client_secret": ("secret-1", "client_secret", "secret-1"),
        "staging_path": ("stage-1", "staging_path", "stage-1"),
    },
    "pyspark": {
        "driver_memory": ("12g", "driver_memory", "12g"),
        "shuffle_partitions": ("64", "shuffle_partitions", 64),
    },
    "spark": {
        "adaptive_enabled": ("false", "adaptive_enabled", False),
        "java_home": ("/opt/jdk", "java_home", "/opt/jdk"),
        "driver_memory": ("12g", "driver_memory", "12g"),
    },
    "duckdb": {
        "memory_limit": ("2GB", "memory_limit", "2GB"),
        "threads": ("3", "thread_limit", 3),
        "temp_directory": ("/var/tmp/duckdb-spill", "temp_directory", "/var/tmp/duckdb-spill"),
        "plan_max_depth": ("7", "plan_max_depth", 7),
        "plan_capture_timeout_seconds": ("9", "plan_capture_timeout_seconds", 9),
    },
    "sqlite": {
        "database_path": ("/var/tmp/options.db", "database_path", "/var/tmp/options.db"),
        "timeout": ("5.5", "timeout", 5.5),
        "check_same_thread": ("true", "check_same_thread", True),
        "plan_max_depth": ("7", "plan_max_depth", 7),
        "plan_capture_timeout_seconds": ("9", "plan_capture_timeout_seconds", 9),
    },
}

PARITY_CASES = [
    pytest.param(platform, option, *case, id=f"{platform}-{option}")
    for platform, cases in OPTION_CASES.items()
    for option, case in cases.items()
]

SYSTEM_PROFILE = SystemProfile(
    os_name="Linux",
    os_version="6",
    architecture="x86_64",
    cpu_cores_physical=4,
    cpu_cores_logical=4,
    memory_total_gb=32,
    memory_available_gb=16,
    python_version="3.12",
    disk_space_gb=100,
    timestamp=datetime(2026, 1, 1),
)


class AdapterReached(Exception):
    pass


@pytest.fixture(autouse=True)
def _drivers_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "benchbox.cli.database.ensure_driver_version",
        lambda package_name, requested_version, auto_install=False, install_hint=None: DriverResolution(
            package=package_name or "",
            requested=requested_version,
            resolved=requested_version or "local-dev",
            actual=requested_version or "local-dev",
            auto_install_used=False,
            runtime_strategy="current-process",
            runtime_path=None,
            runtime_python_executable=sys.executable,
        ),
    )


def _dry_run(tmp_path: Path, platform: str, *options: str, extra_args: tuple[str, ...] = ()) -> dict[str, Any]:
    preview_dir = Path(tempfile.mkdtemp(prefix="preview-", dir=tmp_path))
    args = ["--platform", platform, "--benchmark", "tpch", "--scale", "0.01", "--non-interactive", *extra_args]
    for option in options:
        args.extend(["--platform-option", option])
    result = CliRunner().invoke(run, [*args, "--dry-run", str(preview_dir)], obj={"config": ConfigManager()})
    assert result.exit_code == 0, result.output
    return json.loads(next(preview_dir.glob("tpch_*.json")).read_text(encoding="utf-8"))


def _dry_run_adapter_class(platform: str) -> type:
    if PlatformRegistry.get_default_mode(platform) == "dataframe" and is_dataframe_platform(f"{platform}-df"):
        return getattr(dataframe_platforms, _DATAFRAME_PLATFORM_INFO[f"{platform}-df"][0])
    return PlatformRegistry.get_adapter_class(platform)


def _constructor_kwargs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str, *options: str, extra_args: tuple[str, ...] = ()
) -> dict[str, Any]:
    adapter_class = _dry_run_adapter_class(platform)
    seen: list[dict[str, Any]] = []

    def spy(self: Any, **kwargs: Any) -> None:
        seen.append(kwargs)
        raise AdapterReached

    monkeypatch.setattr(adapter_class, "__init__", spy)
    _dry_run(tmp_path, platform, *options, extra_args=extra_args)
    assert len(seen) == 1, f"{platform} adapter was not constructed"
    return seen[0]


@pytest.mark.parametrize(("platform", "option", "raw", "kwarg", "expected"), PARITY_CASES)
def test_platform_option_reaches_the_adapter_constructor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str, option: str, raw: str, kwarg: str, expected: Any
) -> None:
    kwargs = _constructor_kwargs(monkeypatch, tmp_path, platform, f"{option}={raw}")

    assert kwargs[kwarg] == expected


@pytest.mark.parametrize("platform", sorted(OPTION_CASES))
def test_every_registered_option_has_a_parity_case(platform: str) -> None:
    registered = set(PlatformHookRegistry.list_option_specs(platform))
    expected = registered - CROSS_CUTTING_OPTIONS - KNOWN_UNAPPLIED_OPTIONS.get(platform, frozenset())
    if platform in {"duckdb", "sqlite"}:
        expected |= {"plan_max_depth", "plan_capture_timeout_seconds"}

    assert set(OPTION_CASES[platform]) == expected


@pytest.mark.parametrize("platform", sorted(OPTION_CASES))
def test_adapter_is_constructed_without_platform_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str
) -> None:
    assert _constructor_kwargs(monkeypatch, tmp_path, platform)


@pytest.mark.parametrize("platform", ["duckdb", "postgresql", "datafusion", "sqlite", "snowflake"])
@pytest.mark.parametrize(("options", "expected"), [((), False), (("force_recreate=true",), True)])
def test_force_recreate_option_reaches_the_adapter_without_force_datagen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str, options: tuple[str, ...], expected: bool
) -> None:
    kwargs = _constructor_kwargs(monkeypatch, tmp_path, platform, *options)

    assert bool(kwargs.get("force_recreate")) is expected


@pytest.mark.parametrize("platform", ["duckdb", "postgresql", "datafusion", "sqlite", "snowflake"])
@pytest.mark.parametrize("force", ["datagen", "all"])
def test_force_datagen_reaches_the_adapter_as_force_recreate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str, force: str
) -> None:
    kwargs = _constructor_kwargs(monkeypatch, tmp_path, platform, extra_args=("--force", force))

    assert kwargs.get("force_recreate") is True


def test_force_upload_alone_does_not_recreate_the_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    kwargs = _constructor_kwargs(monkeypatch, tmp_path, "duckdb", extra_args=("--force", "upload"))

    assert not kwargs.get("force_recreate")


def test_runtime_force_recreate_is_lifted_from_nested_options() -> None:
    config = DatabaseConfig(type="duckdb", name="DuckDB", options={"force_recreate": True})

    platform_config = get_platform_config(config, SYSTEM_PROFILE)

    assert platform_config["force_recreate"] is True
    assert platform_config["force"] is True


def test_duckdb_threads_beat_the_system_profile_value() -> None:
    explicit = DatabaseConfig(
        type="duckdb",
        name="DuckDB",
        options={"threads": 3, "_explicit_platform_options": {"threads": 3}},
    )
    implicit = DatabaseConfig(type="duckdb", name="DuckDB", options={"threads": None})

    assert get_platform_config(explicit, SYSTEM_PROFILE)["thread_limit"] == 3
    assert get_platform_config(implicit, SYSTEM_PROFILE)["thread_limit"] == 4


def test_nested_options_keep_their_shape_after_lifting(tmp_path: Path) -> None:
    payload = _dry_run(tmp_path, "duckdb", "threads=3", "temp_directory=/var/tmp/spill")

    nested = payload["platform_config"]["options"]
    assert nested["threads"] == 3
    assert nested["temp_directory"] == "/var/tmp/spill"
    assert nested["_explicit_platform_options"] == {"threads": 3, "temp_directory": "/var/tmp/spill"}
    assert "thread_limit" not in nested


def _duckdb_set_statements(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *options: str) -> tuple[list[str], Any]:
    payload = _dry_run(tmp_path, "duckdb", *options)
    config = dict(payload["platform_config"])
    config.pop("dry_run", None)
    config["database_path"] = ":memory:"
    adapter = get_platform_adapter("duckdb", **config)
    module = adapter._duckdb_module
    real_connect = module.connect
    statements: list[str] = []

    class Recorder:
        def __init__(self, connection: Any) -> None:
            self._connection = connection

        def execute(self, statement: str, *args: Any, **kwargs: Any) -> Any:
            if statement.startswith("SET "):
                statements.append(statement)
            return self._connection.execute(statement, *args, **kwargs)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._connection, name)

    monkeypatch.setattr(module, "connect", lambda *args, **kwargs: Recorder(real_connect(*args, **kwargs)))
    return statements, adapter.create_connection()


def test_duckdb_connection_is_unchanged_without_threads_or_temp_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from benchbox.core.system import SystemProfiler

    cores = SystemProfiler().get_system_profile().cpu_cores_logical
    statements, connection = _duckdb_set_statements(monkeypatch, tmp_path)

    assert statements == ["SET memory_limit = '8GB'", f"SET threads TO {min(cores, 8)}", "SET default_order = 'ASC'"]
    connection.close()


def test_duckdb_threads_and_temp_directory_are_applied_to_the_connection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spill = tmp_path / "spill"
    statements, connection = _duckdb_set_statements(monkeypatch, tmp_path, "threads=3", f"temp_directory={spill}")

    assert statements == [
        "SET memory_limit = '8GB'",
        f"SET temp_directory = '{spill}'",
        "SET threads TO 3",
        "SET default_order = 'ASC'",
    ]
    assert connection.execute("SELECT current_setting('threads')").fetchone()[0] == 3
    assert connection.execute("SELECT current_setting('temp_directory')").fetchone()[0] == str(spill)
    connection.close()


def test_sqlite_database_path_and_timeout_reach_the_connection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    database = tmp_path / "chosen.db"
    payload = _dry_run(tmp_path, "sqlite", f"database_path={database}", "timeout=5.5")
    config = dict(payload["platform_config"])
    config.pop("dry_run", None)
    adapter = get_platform_adapter("sqlite", **config)
    real_connect = sqlite3.connect
    calls: list[dict[str, Any]] = []

    def spy(path: str, **kwargs: Any) -> sqlite3.Connection:
        calls.append({"path": path, **kwargs})
        return real_connect(path, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", spy)
    adapter.create_connection().close()

    assert calls[0]["path"] == str(database)
    assert calls[0]["timeout"] == 5.5
    assert database.exists()


def test_sqlite_error_message_names_an_option_that_works() -> None:
    from benchbox.core.exceptions import ConfigurationError
    from benchbox.platforms.sqlite import SQLiteAdapter

    with pytest.raises(ConfigurationError, match="database_path"):
        SQLiteAdapter.from_config({})
