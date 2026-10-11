from __future__ import annotations

import os
import platform as host_platform
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from benchbox.utils.clock import elapsed_seconds, mono_time
from scripts.tuning_evidence.harness import ArmHandle, ArmSpec, QueryOutcome, SettleRecord, classify_error

DEFAULT_MAX_CHECKSUM_ROWS = 1_000_000
SESSION_BENCHMARK_TYPE = "olap"
QUERY_LOG_CHUNK = 200

LAYOUTS: dict[str, dict[str, tuple[str | None, str]]] = {
    "none": {"lineitem": (None, "l_orderkey, l_linenumber"), "orders": (None, "o_orderkey")},
    "sort": {
        "lineitem": (None, "l_shipdate, l_orderkey, l_linenumber"),
        "orders": (None, "o_orderdate, o_orderkey"),
    },
    "sort_year": {
        "lineitem": ("toYear(l_shipdate)", "l_shipdate, l_orderkey, l_linenumber"),
        "orders": ("toYear(o_orderdate)", "o_orderdate, o_orderkey"),
    },
    "sort_quarter": {
        "lineitem": ("toYYYYMM(l_shipdate) DIV 3", "l_shipdate, l_orderkey, l_linenumber"),
        "orders": ("toYYYYMM(o_orderdate) DIV 3", "o_orderdate, o_orderkey"),
    },
    "sort_month": {
        "lineitem": ("toYYYYMM(l_shipdate)", "l_shipdate, l_orderkey, l_linenumber"),
        "orders": ("toYYYYMM(o_orderdate)", "o_orderdate, o_orderkey"),
    },
    "none_year": {
        "lineitem": ("toYear(l_shipdate)", "l_orderkey, l_linenumber"),
        "orders": ("toYear(o_orderdate)", "o_orderkey"),
    },
    "none_month": {
        "lineitem": ("toYYYYMM(l_shipdate)", "l_orderkey, l_linenumber"),
        "orders": ("toYYYYMM(o_orderdate)", "o_orderkey"),
    },
}

_OLAP_PACK: dict[str, Any] = {
    "max_bytes_in_join": 4294967296,
    "optimize_aggregation_in_order": 1,
    "group_by_two_level_threshold": 100000,
    "max_bytes_before_external_group_by": 4294967296,
    "max_bytes_before_external_sort": 4294967296,
}

PACKS: dict[str, dict[str, Any]] = {
    "olap": {**_OLAP_PACK, "join_algorithm": "grace_hash", "grace_hash_join_initial_buckets": 8},
    "gh": {"join_algorithm": "grace_hash", "grace_hash_join_initial_buckets": 8},
    "agg": {"optimize_aggregation_in_order": 1},
    "gb": {
        "group_by_two_level_threshold": 100000,
        "max_bytes_before_external_group_by": 4294967296,
        "max_bytes_before_external_sort": 4294967296,
    },
    "mbj": {"max_bytes_in_join": 4294967296},
    "hash": {"join_algorithm": "hash"},
    "cand": {**_OLAP_PACK, "join_algorithm": "hash"},
    "pack": dict(_OLAP_PACK),
}

Runner = Callable[..., subprocess.CompletedProcess]


def rewrite_layout_ddl(ddl: str, target_database: str, table: str, layout: tuple[str | None, str] | None) -> str:
    rewritten = re.sub(r"^CREATE TABLE \S+", f"CREATE TABLE `{target_database}`.`{table}`", ddl, count=1)
    if layout is None:
        return rewritten
    partition, order = layout
    engine = "ENGINE = MergeTree"
    if partition:
        engine += f"\nPARTITION BY {partition}"
    engine += f"\nORDER BY ({order})"
    return re.sub(r"\nENGINE = .*$", "\n" + engine, rewritten, flags=re.S)


def format_setting(value: Any) -> str:
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(value)


def parse_memory_events(text: str) -> dict[str, int]:
    events: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            events[parts[0]] = int(parts[1])
    return events


def resolve_arm_tuning(load: str, platform: str, benchmark: str) -> dict[str, Any]:
    from rich.console import Console

    from benchbox.cli.config import ConfigManager
    from benchbox.cli.tuning_resolver import TuningSource, resolve_template_reference, resolve_tuning
    from benchbox.cli.tuning_runtime import build_baseline_unified_config
    from benchbox.core.tuning.interface import UnifiedTuningConfiguration

    manager = ConfigManager()
    resolution = resolve_tuning(
        load, platform, benchmark, manager, Console(quiet=True), quiet=True, non_interactive=True, mode="sql"
    )
    if resolution.config_file:
        config = manager.load_unified_tuning_config(resolution.config_file, platform)
    elif resolution.source is TuningSource.BASELINE:
        config = build_baseline_unified_config()
    else:
        config = UnifiedTuningConfiguration()
    return {
        "enabled": resolution.enabled,
        "config": config,
        "mode": resolution.canonical_mode,
        "source": resolution.source.value,
        "template": resolve_template_reference(resolution.config_file),
        "config_hash": config.get_configuration_hash(),
        "warnings": list(resolution.warnings),
    }


def git_state(root: Path, runner: Runner = subprocess.run) -> dict[str, Any]:
    try:
        commit = runner(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True)
        status = runner(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        return {"error": str(exc)}
    return {"commit": commit.stdout.strip(), "dirty": bool(status.stdout.strip())}


class TaggedClient:
    def __init__(self, client: Any) -> None:
        self.client = client
        self.pending_query_id: str | None = None

    def execute(self, query: str, *args: Any, **kwargs: Any) -> Any:
        if self.pending_query_id is not None:
            kwargs.setdefault("query_id", self.pending_query_id)
            self.pending_query_id = None
        return self.client.execute(query, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)


def raw_connection(connection: Any) -> Any:
    return connection.client if isinstance(connection, TaggedClient) else connection


class ContainerProbe:
    def __init__(self, cli: str, container: str, runner: Runner = subprocess.run) -> None:
        self.cli = cli
        self.container = container
        self.runner = runner

    def _run(self, *args: str) -> dict[str, Any]:
        command = [self.cli, *args]
        try:
            completed = self.runner(command, capture_output=True, text=True, timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"command": command, "returncode": None, "stdout": "", "stderr": str(exc)}
        return {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }

    def versions(self) -> dict[str, Any]:
        versions = {"cli": self.cli, "cli_version": self._run("--version")}
        if Path(self.cli).name == "mocker":
            try:
                completed = self.runner(
                    ["container", "--version"], capture_output=True, text=True, timeout=60, check=False
                )
                versions["container_runtime_version"] = completed.stdout.strip() or completed.stderr.strip()
            except (OSError, subprocess.TimeoutExpired) as exc:
                versions["container_runtime_version"] = f"unavailable: {exc}"
        return versions

    def read(self, path: str) -> dict[str, Any]:
        return self._run("exec", self.container, "cat", path)

    def memory_events(self) -> dict[str, int] | None:
        result = self.read("/sys/fs/cgroup/memory.events")
        return parse_memory_events(result["stdout"]) if result["returncode"] == 0 else None

    def stats(self) -> dict[str, Any]:
        return self._run("stats", "--no-stream", self.container)


class AdapterSeam:
    platform_name = ""
    cold_cache_support = "unsupported"

    def __init__(
        self,
        *,
        benchmark: str,
        scale_factor: float,
        work_dir: Path,
        run_tag: str,
        data_dir: Path | None = None,
        max_checksum_rows: int = DEFAULT_MAX_CHECKSUM_ROWS,
        repo_root: Path | None = None,
    ) -> None:
        self.benchmark_id = benchmark
        self.scale_factor = scale_factor
        self.work_dir = work_dir
        self.run_tag = run_tag
        self.data_dir = data_dir
        self.max_checksum_rows = max_checksum_rows
        self.repo_root = repo_root or Path.cwd()
        self.benchmark: Any = None
        self.system_profile: Any = None

    def prepare(self) -> dict[str, Any]:
        import benchbox
        from benchbox.core.benchmark_loader import get_benchmark_instance
        from benchbox.core.runner import LifecyclePhases, run_benchmark_lifecycle
        from benchbox.core.schemas import BenchmarkConfig
        from benchbox.core.system import SystemProfiler
        from benchbox.utils.path_utils import resolve_benchmark_runs_dir
        from benchbox.utils.scale_factor import format_benchmark_name

        self.system_profile = SystemProfiler().get_system_profile()
        data_dir = self.data_dir or (
            resolve_benchmark_runs_dir() / "datagen" / format_benchmark_name(self.benchmark_id, self.scale_factor)
        )
        config = BenchmarkConfig(
            name=self.benchmark_id,
            display_name=self.benchmark_id.upper(),
            scale_factor=self.scale_factor,
            test_execution_type="data_only",
        )
        self.benchmark = get_benchmark_instance(config, self.system_profile, output_dir=data_dir)
        started = mono_time()
        run_benchmark_lifecycle(
            config,
            None,
            self.system_profile,
            phases=LifecyclePhases(generate=True, load=False, execute=False),
            benchmark_instance=self.benchmark,
            enable_resource_monitoring=False,
        )
        profile = self.system_profile
        return {
            "platform": self.platform_name,
            "benchmark": self.benchmark_id,
            "scale_factor": self.scale_factor,
            "data_dir": str(data_dir),
            "datagen_seconds": elapsed_seconds(started),
            "benchbox_version": getattr(benchbox, "__version__", "unknown"),
            "git": git_state(self.repo_root),
            "host": {
                "node": host_platform.node(),
                "platform": host_platform.platform(),
                "machine": host_platform.machine(),
                "python": sys.version.split()[0],
                "cpu_count": os.cpu_count(),
                "cpu_model": getattr(profile, "cpu_model", None),
                "memory_total_gb": getattr(profile, "memory_total_gb", None),
            },
            "cold_cache_support": self.cold_cache_support,
            "query_source": "benchmark queries in the adapter's dialect, default substitution parameters",
        }

    def adapter_options(self, arm: ArmSpec, database: str) -> dict[str, Any]:
        return {}

    def database_name(self, arm: ArmSpec) -> str:
        return f"te_{self.run_tag}_{arm.name}".lower()

    def remove_database(self, adapter: Any, database: str) -> None:
        return None

    def connection_kwargs(self, database: str) -> dict[str, Any]:
        return {}

    def configure_adapter(self, adapter: Any, database: str) -> None:
        return None

    def build_adapter(self, arm: ArmSpec, tuning: Mapping[str, Any], database: str) -> Any:
        from benchbox.core.platform_config import get_platform_config
        from benchbox.core.schemas import DatabaseConfig
        from benchbox.platforms import get_platform_adapter

        database_config = DatabaseConfig(type=self.platform_name, name=f"tuning_evidence_{arm.name}")
        config = get_platform_config(
            database_config,
            self.system_profile,
            benchmark_name=self.benchmark_id,
            scale_factor=self.scale_factor,
            tuning_config=tuning["config"],
        )
        config.update(self.adapter_options(arm, database))
        config.update(
            tuning_enabled=tuning["enabled"],
            tuning_source=tuning["source"],
            tuning_source_file=tuning["template"],
        )
        adapter = get_platform_adapter(self.platform_name, **config)
        adapter.benchmark_instance = self.benchmark
        adapter.scale_factor = self.scale_factor
        self.configure_adapter(adapter, database)
        return adapter

    def load(self, arm: ArmSpec, loaded: Mapping[str, ArmHandle]) -> ArmHandle:
        if arm.layout:
            return self.load_layout(arm, loaded[str(arm.layout_from)])
        from benchbox.core.loaded_tables import require_loaded_tables
        from benchbox.core.tuning.applied_ledger import AppliedTuningLedger

        tuning = resolve_arm_tuning(arm.load, self.platform_name, self.benchmark_id)
        database = self.database_name(arm)
        adapter = self.build_adapter(arm, tuning, database)
        effective = adapter.get_effective_tuning_configuration()
        tuning_warnings: list[str] = []
        if adapter.tuning_enabled and effective:
            errors, tuning_warnings = effective.validate_for_platform_detailed(adapter.canonical_platform_type)
            if errors:
                raise ValueError(f"arm {arm.name}: invalid tuning configuration: {'; '.join(errors)}")
        self.remove_database(adapter, database)
        started = mono_time()
        connection = adapter.create_connection(**self.connection_kwargs(database))
        adapter.benchmark = self.benchmark
        adapter._applied_tuning_ledger = AppliedTuningLedger()
        adapter._applied_layout_operations = []
        schema_seconds, _, data_seconds, table_stats, _, _ = adapter._setup_fresh_database_phases(
            self.benchmark, connection, effective
        )
        require_loaded_tables(self.benchmark, table_stats)
        load_seconds = elapsed_seconds(started)
        fold = getattr(adapter, "_fold_layout_operations_into_ledger", None)
        if callable(fold):
            fold()
        handle = ArmHandle(
            spec=arm,
            connection=connection,
            database=database,
            load_seconds=load_seconds,
            adapter=adapter,
            details={
                "load_mechanism": "adapter",
                "tuning": {key: value for key, value in tuning.items() if key != "config"},
                "schema_seconds": schema_seconds,
                "data_loading_seconds": data_seconds,
                "table_rows": dict(table_stats),
                "applied_tuning": adapter._applied_tuning_ledger.describe_outcome(),
                "tuning_warnings": list(tuning_warnings),
                "tuning_dropped": [
                    {"intent": item.intent, "reason": item.reason} for item in adapter._applied_tuning_ledger.dropped
                ],
                "load_includes_adapter_settle": self.load_includes_settle(adapter),
            },
        )
        self.start_session(handle)
        return handle

    def load_includes_settle(self, adapter: Any) -> bool:
        return False

    def load_layout(self, arm: ArmSpec, source: ArmHandle) -> ArmHandle:
        raise ValueError(f"{self.platform_name} does not support named layout variants")

    def start_session(self, handle: ArmHandle) -> None:
        if handle.spec.pack and handle.spec.pack not in PACKS:
            raise ValueError(f"unknown settings pack {handle.spec.pack!r}; known: {sorted(PACKS)}")
        handle.adapter.tuning_enabled = handle.spec.session == "tuned"
        handle.connection = self.wrap(handle.connection)
        self.configure_session(handle, handle.connection)
        handle.details["session"] = handle.spec.session
        handle.details["engine_version"] = self.engine_version(handle)
        handle.details["session_settings"] = self.session_settings(handle)
        handle.details["notes"] = self.notes(handle)

    def configure_session(self, handle: ArmHandle, connection: Any) -> None:
        handle.adapter.configure_for_benchmark(connection, SESSION_BENCHMARK_TYPE)
        if handle.spec.pack:
            raise ValueError(f"{self.platform_name} does not support settings packs")

    def refresh_session(self, handle: ArmHandle) -> None:
        return None

    def wrap(self, connection: Any) -> Any:
        return connection

    def engine_version(self, handle: ArmHandle) -> str | None:
        return None

    def session_settings(self, handle: ArmHandle) -> dict[str, Any]:
        return {}

    def notes(self, handle: ArmHandle) -> list[str]:
        return []

    def queries(self, handle: ArmHandle) -> dict[str, str]:
        queries = handle.adapter._get_dialect_queries(
            self.benchmark, benchmark_slug=self.benchmark_id, connection=raw_connection(handle.connection)
        )
        return {str(query_id): sql for query_id, sql in queries.items()}

    def connect(self, handle: ArmHandle) -> Any:
        from benchbox.platforms.base.connection_wrappers import open_stream_connection

        connection = open_stream_connection(handle.adapter, raw_connection(handle.connection), SESSION_BENCHMARK_TYPE)
        self.configure_session(handle, connection)
        return connection

    def release(self, handle: ArmHandle, connection: Any) -> None:
        close = getattr(connection, "close", None)
        if callable(close):
            close()

    def settle(self, handle: ArmHandle) -> SettleRecord:
        return SettleRecord(hook="noop", settled=True, waited_seconds=0.0, detail={"background_work_checked": False})

    def tag(self, connection: Any) -> str | None:
        return None

    def arm_timeout(self, connection: Any, timeout_seconds: float) -> tuple[threading.Timer | None, threading.Event]:
        return None, threading.Event()

    def run_query(
        self, handle: ArmHandle, connection: Any, query_id: str, sql: str, timeout_seconds: float
    ) -> QueryOutcome:
        from benchbox.core.results.result_digest import compute_result_digest
        from benchbox.platforms.base.result_capture import materialized_result_validation

        captured: list[Any] = []
        structural = getattr(self.benchmark, "validate_query_result", None)

        def capture(captured_id: str, rows: Any) -> None:
            captured.append(rows)
            if callable(structural):
                structural(captured_id, rows)

        engine_query_id = self.tag(connection)
        timer, fired = self.arm_timeout(connection, timeout_seconds)
        started = mono_time()
        try:
            with materialized_result_validation(capture):
                result = handle.adapter.execute_query(
                    connection,
                    sql,
                    query_id,
                    benchmark_type=self.benchmark_id,
                    scale_factor=self.scale_factor,
                    validate_row_count=False,
                )
        except Exception as exc:
            result = {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}
        finally:
            if timer is not None:
                timer.cancel()
        wall = elapsed_seconds(started)
        elapsed = result.get("execution_time_seconds")
        elapsed = wall if elapsed is None else float(elapsed)
        if result.get("status") != "SUCCESS":
            error = str(result.get("error") or result.get("status") or "failed")
            return QueryOutcome(
                ok=False,
                elapsed_seconds=elapsed,
                wall_seconds=wall,
                error=error[:2000],
                error_kind=classify_error(error, timed_out=fired.is_set()),
                engine_query_id=engine_query_id,
            )
        rows = captured[-1] if captured else None
        row_count = len(rows) if rows is not None else result.get("rows_returned")
        checksum = compute_result_digest(rows) if rows is not None and len(rows) <= self.max_checksum_rows else None
        return QueryOutcome(
            ok=True,
            elapsed_seconds=elapsed,
            wall_seconds=wall,
            rows=row_count,
            checksum=checksum,
            engine_query_id=engine_query_id,
        )

    def drop_caches(self, handle: ArmHandle, connection: Any) -> str:
        return "unsupported"

    def cost(self, handle: ArmHandle, engine_query_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        return {}

    def round_evidence(self) -> dict[str, Any]:
        return {}

    def run_evidence(self) -> dict[str, Any]:
        return {"blockers": []}

    def close(self, handle: ArmHandle, *, keep: bool) -> None:
        handle.adapter.close_connection(raw_connection(handle.connection))
        if not keep:
            self.remove_database(handle.adapter, handle.database)


class DuckDBSeam(AdapterSeam):
    platform_name = "duckdb"

    def database_path(self, database: str) -> Path:
        return self.work_dir / f"{database}.duckdb"

    def adapter_options(self, arm: ArmSpec, database: str) -> dict[str, Any]:
        return {"database_path": str(self.database_path(database))}

    def remove_database(self, adapter: Any, database: str) -> None:
        path = self.database_path(database)
        for candidate in (path, path.with_name(path.name + ".wal")):
            candidate.unlink(missing_ok=True)

    def engine_version(self, handle: ArmHandle) -> str | None:
        return str(handle.connection.execute("SELECT version()").fetchone()[0])

    def arm_timeout(self, connection: Any, timeout_seconds: float) -> tuple[threading.Timer | None, threading.Event]:
        fired = threading.Event()

        def interrupt() -> None:
            fired.set()
            connection.interrupt()

        timer = threading.Timer(timeout_seconds, interrupt)
        timer.daemon = True
        timer.start()
        return timer, fired


class ClickHouseSeam(AdapterSeam):
    def __init__(self, *, timeout_seconds: float = 300.0, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.timeout_seconds = timeout_seconds

    def configure_adapter(self, adapter: Any, database: str) -> None:
        adapter.max_execution_time = int(self.timeout_seconds)

    def engine_version(self, handle: ArmHandle) -> str | None:
        return str(raw_connection(handle.connection).execute("SELECT version()")[0][0])

    def session_settings(self, handle: ArmHandle) -> dict[str, Any]:
        rows = raw_connection(handle.connection).execute(
            "SELECT name, value FROM system.settings WHERE changed ORDER BY name"
        )
        return dict(rows)


class ChDBSeam(ClickHouseSeam):
    platform_name = "clickhouse-local"

    def database_path(self, database: str) -> Path:
        return self.work_dir / f"{database}.chdb"

    def adapter_options(self, arm: ArmSpec, database: str) -> dict[str, Any]:
        return {"database_path": str(self.database_path(database))}

    def remove_database(self, adapter: Any, database: str) -> None:
        path = self.database_path(database)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

    def connection_kwargs(self, database: str) -> dict[str, Any]:
        return {"database_path": str(self.database_path(database))}

    def connect(self, handle: ArmHandle) -> Any:
        raise ValueError("throughput streams are not supported on chDB: its client is shared within the process")


class ClickHouseServerSeam(ClickHouseSeam):
    platform_name = "clickhouse-server"
    cold_cache_support = "partial: drops the mark, uncompressed and query caches; the guest page cache is kept"
    cache_statements = ("SYSTEM DROP MARK CACHE", "SYSTEM DROP UNCOMPRESSED CACHE", "SYSTEM DROP QUERY CACHE")

    def __init__(
        self,
        *,
        host: str = "localhost",
        port: int = 9000,
        user: str = "default",
        password: str = "",
        container: str | None = None,
        container_cli: str | None = None,
        settle_timeout_seconds: float = 600.0,
        probe: ContainerProbe | None = None,
        settle_sleep: Callable[[float], None] = time.sleep,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.settle_timeout_seconds = settle_timeout_seconds
        self.settle_sleep = settle_sleep
        cli = container_cli or ("mocker" if sys.platform == "darwin" else "docker")
        self.probe = probe or (ContainerProbe(cli, container) if container else None)
        self.events_start: dict[str, int] | None = None
        self.container_start: dict[str, Any] = {}

    def prepare(self) -> dict[str, Any]:
        environment = super().prepare()
        if self.probe is not None:
            memory_max = self.probe.read("/sys/fs/cgroup/memory.max")
            self.events_start = self.probe.memory_events()
            self.container_start = {
                "container": self.probe.container,
                **self.probe.versions(),
                "memory_max": memory_max["stdout"] if memory_max["returncode"] == 0 else None,
                "memory_max_probe": memory_max,
                "memory_events_start": self.events_start,
                "stats_start": self.probe.stats(),
            }
        environment["container"] = self.container_start or None
        return environment

    def adapter_options(self, arm: ArmSpec, database: str) -> dict[str, Any]:
        return {"host": self.host, "port": self.port, "username": self.user, "password": self.password}

    def configure_adapter(self, adapter: Any, database: str) -> None:
        super().configure_adapter(adapter, database)
        adapter.database = database
        adapter.send_receive_timeout = int(self.timeout_seconds) + 60

    def connection_kwargs(self, database: str) -> dict[str, Any]:
        return {"database": database}

    def remove_database(self, adapter: Any, database: str) -> None:
        adapter.drop_database(database=database)

    def load_includes_settle(self, adapter: Any) -> bool:
        return bool(adapter.tuning_enabled)

    def configure_session(self, handle: ArmHandle, connection: Any) -> None:
        handle.adapter.configure_for_benchmark(connection, SESSION_BENCHMARK_TYPE)
        if not handle.spec.pack:
            return
        settings = PACKS[handle.spec.pack]
        client = raw_connection(connection)
        for name, value in settings.items():
            client.execute(f"SET {name} = {format_setting(value)}")
        effective = dict(
            client.execute(
                "SELECT name, value FROM system.settings WHERE name IN %(names)s", {"names": tuple(settings)}
            )
        )
        wrong = {name: effective.get(name) for name, value in settings.items() if effective.get(name) != str(value)}
        if wrong:
            raise RuntimeError(f"settings pack {handle.spec.pack} did not apply: {wrong}")

    def refresh_session(self, handle: ArmHandle) -> None:
        self.configure_session(handle, handle.connection)

    def wrap(self, connection: Any) -> Any:
        return connection if isinstance(connection, TaggedClient) else TaggedClient(connection)

    def notes(self, handle: ArmHandle) -> list[str]:
        notes = []
        if handle.spec.session == "tuned" and self.benchmark_id.lower() == "tpch":
            notes.append("the tuned session runs Q21 with the shipped per-query join_algorithm = 'hash' setting")
        return notes

    def load_layout(self, arm: ArmSpec, source: ArmHandle) -> ArmHandle:
        if arm.layout not in LAYOUTS:
            raise ValueError(f"unknown layout {arm.layout!r}; known: {sorted(LAYOUTS)}")
        tuning = resolve_arm_tuning("notuning", self.platform_name, self.benchmark_id)
        database = self.database_name(arm)
        adapter = self.build_adapter(arm, tuning, database)
        self.remove_database(adapter, database)
        source_client = raw_connection(source.connection)
        tables = [
            row[0]
            for row in source_client.execute(
                "SELECT name FROM system.tables WHERE database = %(database)s ORDER BY name",
                {"database": source.database},
            )
        ]
        started = mono_time()
        connection = adapter.create_connection(**self.connection_kwargs(database))
        layout = LAYOUTS[str(arm.layout)]
        for table in tables:
            ddl = source_client.execute(f"SHOW CREATE TABLE `{source.database}`.`{table}`")[0][0]
            connection.execute(rewrite_layout_ddl(ddl, database, table, layout.get(table.lower())))
            connection.execute(f"INSERT INTO `{database}`.`{table}` SELECT * FROM `{source.database}`.`{table}`")
        handle = ArmHandle(
            spec=arm,
            connection=connection,
            database=database,
            load_seconds=elapsed_seconds(started),
            adapter=adapter,
            details={
                "load_mechanism": f"copy of arm {source.spec.name} with layout {arm.layout}",
                "layout": {table: layout.get(table.lower()) for table in tables},
                "load_includes_adapter_settle": False,
            },
        )
        self.start_session(handle)
        return handle

    def connect(self, handle: ArmHandle) -> Any:
        connection = self.wrap(handle.adapter.create_connection(**self.connection_kwargs(handle.database)))
        self.configure_session(handle, connection)
        return connection

    def release(self, handle: ArmHandle, connection: Any) -> None:
        handle.adapter.close_connection(raw_connection(connection))

    def settle(self, handle: ArmHandle) -> SettleRecord:
        from benchbox.platforms.clickhouse.merge_settle import wait_for_merges_to_settle

        result = wait_for_merges_to_settle(
            raw_connection(handle.connection), timeout_seconds=self.settle_timeout_seconds, sleep=self.settle_sleep
        )
        return SettleRecord(
            hook="clickhouse_merge_settle",
            settled=result.settled,
            waited_seconds=result.waited_seconds,
            detail={"active_parts": result.active_parts, "timeout_seconds": self.settle_timeout_seconds},
        )

    def tag(self, connection: Any) -> str | None:
        if not isinstance(connection, TaggedClient):
            return None
        query_id = f"te-{self.run_tag}-{uuid.uuid4().hex}"
        connection.pending_query_id = query_id
        return query_id

    def drop_caches(self, handle: ArmHandle, connection: Any) -> str:
        client = raw_connection(connection)
        failed = []
        for statement in self.cache_statements:
            try:
                client.execute(statement)
            except Exception as exc:
                failed.append(f"{statement}: {type(exc).__name__}")
        return "partial: " + "; ".join(failed) if failed else "engine caches dropped"

    def cost(self, handle: ArmHandle, engine_query_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        if not engine_query_ids:
            return {}
        client = raw_connection(handle.connection)
        client.execute("SYSTEM FLUSH LOGS")
        costs: dict[str, dict[str, Any]] = {}
        ids = list(engine_query_ids)
        for start in range(0, len(ids), QUERY_LOG_CHUNK):
            rows = client.execute(
                "SELECT query_id, type, read_rows, read_bytes, memory_usage, query_duration_ms, "
                "ProfileEvents['SelectedParts'], ProfileEvents['SelectedMarks'] "
                "FROM system.query_log WHERE type != 'QueryStart' AND query_id IN %(ids)s",
                {"ids": tuple(ids[start : start + QUERY_LOG_CHUNK])},
            )
            for query_id, kind, read_rows, read_bytes, memory, duration_ms, parts, marks in rows:
                costs[query_id] = {
                    "type": str(kind),
                    "read_rows": read_rows,
                    "read_bytes": read_bytes,
                    "memory_usage": memory,
                    "query_duration_ms": duration_ms,
                    "selected_parts": parts,
                    "selected_marks": marks,
                }
        return costs

    def round_evidence(self) -> dict[str, Any]:
        if self.probe is None:
            return {}
        return {"stats": self.probe.stats(), "memory_events": self.probe.memory_events()}

    def run_evidence(self) -> dict[str, Any]:
        if self.probe is None:
            return {
                "container": None,
                "blockers": ["no container named: guest memory.max, stats and OOM state were not captured"],
            }
        events_end = self.probe.memory_events()
        blockers = []
        oom_kills = None
        if self.events_start is None or events_end is None:
            blockers.append("OOM state unknown: memory.events could not be read")
        else:
            oom_kills = events_end.get("oom_kill", 0) - self.events_start.get("oom_kill", 0)
            if oom_kills > 0:
                blockers.append(f"the container recorded {oom_kills} OOM kills during the run")
        if not self.container_start.get("memory_max"):
            blockers.append("guest memory.max could not be read")
        notes = []
        if Path(self.probe.cli).name == "mocker":
            notes.append("ClickHouse server ran in a Linux VM under Apple container, not under Linux Docker")
        return {
            **self.container_start,
            "memory_events_end": events_end,
            "oom_kill_delta": oom_kills,
            "oom_killed": None if oom_kills is None else oom_kills > 0,
            "stats_end": self.probe.stats(),
            "notes": notes,
            "blockers": blockers,
        }


SEAMS: dict[str, type[AdapterSeam]] = {
    "duckdb": DuckDBSeam,
    "clickhouse-server": ClickHouseServerSeam,
    "clickhouse-local": ChDBSeam,
}
