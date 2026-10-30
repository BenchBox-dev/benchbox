from __future__ import annotations

import argparse
import base64
import datetime as _dt
import inspect
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

GIB = 1024**3
TRACE_SCHEMA_VERSION = 2
DEFAULT_DRIVER_TIMEOUT_S = 300
DEFAULT_SAMPLE_INTERVAL_S = 2.0
DEFAULT_HTTP_TIMEOUT_S = 2.0
_MEMORY_VALUE_RE = re.compile(r"^\s*(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>[KMGTPE]?i?B)?\s*$", re.I)
_MEMORY_UNITS = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}
_DECIMAL_GIB_EQUIVALENCE = 1000**3


CLI_DESCRIPTION = (
    "Measured ClickHouse streaming-memory traces for UAT calibration.\n"
    "\n"
    "The calibration TODO deliberately keeps measurement separate from the compose\n"
    "memory-admission policy.  This module records the quantities that policy is\n"
    "allowed to consume; it never turns a host-capacity guess into a memory limit.\n"
    "\n"
    "Use the module as a command wrapper around a real UAT cell or sweep::\n"
    "\n"
    '    uv run -- python -m tests.uat.clickhouse_memory       --output "$BENCHBOX_OUTPUT_DIR/clickhouse-memory-1g.json"       --rung baseline-1g -- -- benchbox run --platform clickhouse-server ...\n'
    "\n"
    "The trace is useful even when a cell fails: the failure, timeout, server\n"
    "responsiveness, host memory, engine memory, and ClickHouse counters remain in\n"
    "one atomic JSON artifact.  A trace is not considered calibration evidence\n"
    "unless it has at least one successful responsiveness sample and passes the\n"
    "native-streaming/no-legacy-batch guards.\n"
)


def runtime_limit_matches_rung(
    runtime_limit_bytes: int, requested_memory_gib: float, *, requested_bytes: int | None = None
) -> bool:

    if requested_bytes is not None:
        return runtime_limit_bytes in {requested_bytes, int(requested_bytes / GIB * _DECIMAL_GIB_EQUIVALENCE)}
    lower_limit = int(requested_memory_gib * _DECIMAL_GIB_EQUIVALENCE)
    upper_limit = int(requested_memory_gib * GIB)
    return runtime_limit_bytes in {lower_limit, upper_limit}


_REQUIRED_CLICKHOUSE_METRICS = frozenset(
    {
        "metric.MemoryTracking",
        "async.MemoryResident",
        "event.InsertedRows",
        "event.InsertedBytes",
    }
)
_MEMORY_FAILURE_MARKERS = (
    "memory limit exceeded",
    "out of memory",
    "oomkilled",
    "cgroup",
)


@dataclass(frozen=True)
class MemoryRung:
    name: str
    requested_memory_gib: float
    load_memory_gib: float
    driver_timeout_s: int

    def __post_init__(self) -> None:
        if not self.name or self.name == "baseline-1000-row":
            raise ValueError("memory rung name must identify a memory envelope")
        if self.requested_memory_gib <= 0 or self.load_memory_gib <= 0:
            raise ValueError("memory rung sizes must be positive")
        if self.driver_timeout_s <= 0:
            raise ValueError("memory rung driver timeout must be positive")


DEFAULT_MEMORY_RUNGS: tuple[MemoryRung, ...] = (
    MemoryRung("baseline-1g", 1.0, 1.0, DEFAULT_DRIVER_TIMEOUT_S),
    MemoryRung("candidate-4g", 4.0, 4.0, DEFAULT_DRIVER_TIMEOUT_S),
    MemoryRung("candidate-5.25g", 5.25, 5.25, DEFAULT_DRIVER_TIMEOUT_S),
    MemoryRung("candidate-8g", 8.0, 8.0, DEFAULT_DRIVER_TIMEOUT_S),
    MemoryRung("candidate-12g", 12.0, 12.0, DEFAULT_DRIVER_TIMEOUT_S),
)


@dataclass(frozen=True)
class HostMemorySample:
    available_gib: float | None
    free_gib: float | None
    swap_used_percent: float | None


@dataclass(frozen=True)
class EngineMemorySample:
    engine: str | None
    service: str | None
    usage_bytes: int | None
    limit_bytes: int | None
    raw_status: str | None = None
    oom_killed: bool | None = None
    running: bool | None = None


@dataclass(frozen=True)
class TraceSample:
    observed_at_utc: str
    elapsed_s: float
    host: HostMemorySample
    engine: EngineMemorySample
    clickhouse_metrics: dict[str, float]
    responsiveness_ms: float | None
    server_reachable: bool


@dataclass
class ClickHouseMemoryTrace:
    platform: str
    rung: MemoryRung
    started_at_utc: str
    ended_at_utc: str | None = None
    source_commit: str | None = None
    driver_timeout_s: int | None = None
    driver_timeout_source: str | None = None
    native_streaming: bool = True
    application_batch_rows: int | None = None
    outcome: str = "running"
    failure_reason: str | None = None
    samples: list[TraceSample] = field(default_factory=list)

    @property
    def _calibration_samples(self) -> list[TraceSample]:
        first_complete = next(
            (
                index
                for index, sample in enumerate(self.samples)
                if _REQUIRED_CLICKHOUSE_METRICS.issubset(sample.clickhouse_metrics)
            ),
            None,
        )
        return [] if first_complete is None else self.samples[first_complete:]

    @property
    def valid_for_calibration(self) -> bool:
        samples = self._calibration_samples
        if not samples or not self.native_streaming:
            return False
        if self.application_batch_rows is not None:
            return False
        if not self.driver_timeout_source or self.driver_timeout_s != self.rung.driver_timeout_s:
            return False
        if any(sample.responsiveness_ms is None or not math.isfinite(sample.responsiveness_ms) for sample in samples):
            return False
        engine_samples = [sample.engine for sample in samples]
        for sample in samples:
            if (
                sample.host.available_gib is None
                or not math.isfinite(sample.host.available_gib)
                or sample.host.available_gib < 0
            ):
                return False
            if sample.server_reachable is not True:
                return False
            if not _REQUIRED_CLICKHOUSE_METRICS.issubset(sample.clickhouse_metrics):
                return False
            if any(not math.isfinite(sample.clickhouse_metrics[name]) for name in _REQUIRED_CLICKHOUSE_METRICS):
                return False
            if sample.engine.usage_bytes is None or sample.engine.limit_bytes is None:
                return False
            if sample.engine.oom_killed is not False or sample.engine.running is not True:
                return False
            if not runtime_limit_matches_rung(sample.engine.limit_bytes, self.rung.requested_memory_gib):
                return False
        if any(sample.oom_killed is True for sample in engine_samples):
            return False
        if any(
            sample.usage_bytes is not None
            and sample.limit_bytes is not None
            and sample.usage_bytes > sample.limit_bytes
            for sample in engine_samples
        ):
            return False
        return self.outcome == "passed" and self.failure_reason is None

    @property
    def peak_engine_usage_bytes(self) -> int | None:
        values = [
            sample.engine.usage_bytes for sample in self._calibration_samples if sample.engine.usage_bytes is not None
        ]
        return max(values) if values else None

    @property
    def peak_host_available_gib(self) -> float | None:
        values = [
            sample.host.available_gib for sample in self._calibration_samples if sample.host.available_gib is not None
        ]
        return min(values) if values else None

    @property
    def max_responsiveness_ms(self) -> float | None:
        values = [
            sample.responsiveness_ms for sample in self._calibration_samples if sample.responsiveness_ms is not None
        ]
        return max(values) if values else None

    @property
    def runtime_memory_limit_bytes(self) -> int | None:
        values = [
            sample.engine.limit_bytes for sample in self._calibration_samples if sample.engine.limit_bytes is not None
        ]
        return max(values) if values else None

    @property
    def memory_limit_exceeded(self) -> bool:
        if any(
            sample.engine.usage_bytes is not None
            and sample.engine.limit_bytes is not None
            and sample.engine.usage_bytes > sample.engine.limit_bytes
            for sample in self.samples
        ):
            return True
        failure = (self.failure_reason or "").lower()
        return any(marker in failure for marker in _MEMORY_FAILURE_MARKERS)

    def finish(self, *, outcome: str, failure_reason: str | None = None) -> None:
        if outcome not in {"passed", "failed", "timed-out", "aborted"}:
            raise ValueError(f"unsupported trace outcome {outcome!r}")
        self.ended_at_utc = utc_now()
        self.outcome = outcome
        self.failure_reason = failure_reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_schema_version": TRACE_SCHEMA_VERSION,
            "platform": self.platform,
            "rung": asdict(self.rung),
            "started_at_utc": self.started_at_utc,
            "ended_at_utc": self.ended_at_utc,
            "source_commit": self.source_commit,
            "driver_timeout_s": self.driver_timeout_s,
            "driver_timeout_source": self.driver_timeout_source,
            "loader_contract": {
                "native_streaming": self.native_streaming,
                "application_batch_rows": self.application_batch_rows,
                "legacy_1000_row_fallback": self.application_batch_rows == 1000,
            },
            "outcome": self.outcome,
            "failure_reason": self.failure_reason,
            "summary": {
                "sample_count": len(self.samples),
                "calibration_sample_count": len(self._calibration_samples),
                "valid_for_calibration": self.valid_for_calibration,
                "peak_engine_usage_bytes": self.peak_engine_usage_bytes,
                "runtime_memory_limit_bytes": self.runtime_memory_limit_bytes,
                "driver_timeout_source": self.driver_timeout_source,
                "oom_killed": self._oom_killed_summary(),
                "memory_limit_exceeded": self.memory_limit_exceeded,
                "minimum_host_available_gib": self.peak_host_available_gib,
                "maximum_responsiveness_ms": self.max_responsiveness_ms,
            },
            "samples": [asdict(sample) for sample in self.samples],
        }

    def _oom_killed_summary(self) -> bool | None:
        values = [sample.engine.oom_killed for sample in self.samples]
        if not values or any(value is None for value in values):
            return None
        return any(values)


def utc_now() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat().replace("+00:00", "Z")


def parse_memory_bytes(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = _MEMORY_VALUE_RE.match(str(value).split("/", 1)[0])
    if not match:
        return None
    unit = (match.group("unit") or "b").lower()
    multiplier = _MEMORY_UNITS.get(unit)
    return None if multiplier is None else int(float(match.group("value")) * multiplier)


def parse_engine_stats(text: str, *, engine: str | None, service: str | None) -> EngineMemorySample:
    payload: Any = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = candidate[0] if isinstance(candidate, list) and candidate else candidate
        break
    if isinstance(payload, dict):
        usage = payload.get("MemUsage") or payload.get("MemUsageBytes") or payload.get("memory_usage")
        limit = payload.get("MemLimit") or payload.get("MemLimitBytes") or payload.get("memory_limit")
        if limit is None and isinstance(usage, str) and "/" in usage:
            usage, limit = usage.split("/", 1)
        raw_status = str(payload.get("Name") or payload.get("Container") or "") or None
    else:
        table_row = next(
            (
                line.strip()
                for line in text.splitlines()
                if line.strip() and not line.lstrip().upper().startswith(("CONTAINER ID", "NAME"))
            ),
            "",
        )
        table_match = re.match(r"^\S+\s+(?P<name>\S+)\s+\S+\s+(?P<usage>\S+\s*/\s*\S+)", table_row)
        if table_match is None:
            return EngineMemorySample(engine, service, None, None, text.strip() or None)
        usage = table_match.group("usage").split("/", 1)[0]
        limit = table_match.group("usage").split("/", 1)[1]
        raw_status = table_match.group("name")
    return EngineMemorySample(
        engine=engine,
        service=service,
        usage_bytes=parse_memory_bytes(usage),
        limit_bytes=parse_memory_bytes(limit),
        raw_status=raw_status,
    )


def parse_engine_inspect(text: str, *, engine: str | None, service: str | None) -> EngineMemorySample:
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError:
        return EngineMemorySample(engine, service, None, None, text.strip() or None)
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    state = payload.get("State", {}) if isinstance(payload, dict) else {}
    if not isinstance(state, dict):
        return EngineMemorySample(engine, service, None, None, text.strip() or None)
    raw_status = str(state.get("Status") or "") or None
    return EngineMemorySample(
        engine=engine,
        service=service,
        usage_bytes=None,
        limit_bytes=None,
        raw_status=raw_status,
        oom_killed=state.get("OOMKilled") if isinstance(state.get("OOMKilled"), bool) else None,
        running=state.get("Running") if isinstance(state.get("Running"), bool) else None,
    )


def verify_native_streaming_loader() -> tuple[bool, str]:
    try:
        from benchbox.platforms.base.data_loading import ClickHouseNativeHandler

        delimited = inspect.getsource(ClickHouseNativeHandler._load_delimited_via_client_insert)
        parquet = inspect.getsource(ClickHouseNativeHandler._load_parquet_via_client_insert)
    except (ImportError, OSError, TypeError) as exc:
        return False, f"could not inspect ClickHouse native loader: {exc}"
    for name, source in (("delimited", delimited), ("parquet", parquet)):
        if (
            "row_generator" not in source
            or "connection.execute(" not in source
            or "settings=self._server_insert_settings()" not in source
        ):
            return False, f"ClickHouse {name} path is not a native generator insert"
        if "executemany(" in source or "batch_size = 1000" in source:
            return False, f"ClickHouse {name} path contains an application batch fallback"
    return True, "native generator insert contract verified"


def read_host_memory() -> HostMemorySample:
    try:
        import psutil

        virtual = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return HostMemorySample(
            available_gib=virtual.available / GIB,
            free_gib=virtual.free / GIB,
            swap_used_percent=float(swap.percent),
        )
    except (ImportError, OSError, RuntimeError, ValueError, AttributeError):
        return HostMemorySample(None, None, None)


def _http_query(
    host_port: int,
    query: str,
    *,
    username: str,
    password: str,
    timeout_s: float,
) -> str:
    request = urllib.request.Request(
        f"http://127.0.0.1:{host_port}/",
        data=query.encode("utf-8"),
        method="POST",
    )
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    request.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return response.read().decode("utf-8", errors="replace")


def read_clickhouse_metrics(
    host_port: int,
    *,
    username: str = "default",
    password: str = "benchbox",
    timeout_s: float = DEFAULT_HTTP_TIMEOUT_S,
) -> tuple[dict[str, float], float | None]:
    metrics: dict[str, float] = {}
    queries = (
        (
            "metric",
            "SELECT metric AS name, value FROM system.metrics WHERE metric IN ('MemoryTracking') FORMAT JSONEachRow",
        ),
        (
            "async",
            "SELECT metric AS name, value FROM system.asynchronous_metrics "
            "WHERE metric IN ('MemoryResident','MemoryVirtual','OSMemoryAvailable','OSMemoryFree') FORMAT JSONEachRow",
        ),
        (
            "event",
            "SELECT event AS name, value FROM system.events "
            "WHERE event IN ('InsertedRows','InsertedBytes') FORMAT JSONEachRow",
        ),
    )
    for prefix, query in queries:
        try:
            for line in _http_query(
                host_port, query, username=username, password=password, timeout_s=timeout_s
            ).splitlines():
                row = json.loads(line)
                metrics[f"{prefix}.{row['name']}"] = float(row["value"])
        except (OSError, urllib.error.URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError):
            continue
    started = time.perf_counter()
    try:
        _http_query(host_port, "SELECT 1", username=username, password=password, timeout_s=timeout_s)
    except (OSError, urllib.error.URLError, TimeoutError):
        return metrics, None
    return metrics, (time.perf_counter() - started) * 1000.0


CommandRunner = Callable[[Sequence[str]], tuple[int, str, str]]


def _run_stats_command(argv: Sequence[str]) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=DEFAULT_HTTP_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)
    return completed.returncode, completed.stdout, completed.stderr


def _compose_stats_argv(
    *,
    engine: str,
    project_name: str,
    compose_files: Iterable[Path],
    service: str,
) -> list[str]:
    del compose_files
    return [engine, "stats", "--no-stream", "--format", "{{json .}}", f"{project_name}-{service}-1"]


def _inspect_argv(*, engine: str, project_name: str, service: str) -> list[str]:
    return [engine, "inspect", f"{project_name}-{service}-1"]


class MemoryTraceCollector:
    def __init__(
        self,
        *,
        trace: ClickHouseMemoryTrace,
        output_path: Path,
        host_port: int = 8123,
        username: str = "default",
        password: str = "benchbox",
        interval_s: float = DEFAULT_SAMPLE_INTERVAL_S,
        engine: str | None = None,
        project_name: str | None = None,
        compose_files: Iterable[Path] = (),
        service: str = "clickhouse",
        command_runner: CommandRunner = _run_stats_command,
    ) -> None:
        if interval_s <= 0:
            raise ValueError("memory trace interval must be positive")
        self.trace = trace
        self.output_path = Path(output_path)
        self.host_port = host_port
        self.username = username
        self.password = password
        self.interval_s = interval_s
        self.engine = engine
        self.project_name = project_name
        self.compose_files = tuple(compose_files)
        self.service = service
        self.command_runner = command_runner
        self._started_mono: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("memory trace collector already started")
        self._started_mono = time.monotonic()
        write_trace(self.output_path, self.trace)
        self._sample_once()
        self._thread = threading.Thread(target=self._run, name="clickhouse-memory-trace", daemon=True)
        self._thread.start()

    def stop(self, *, outcome: str, failure_reason: str | None = None) -> ClickHouseMemoryTrace:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(self.interval_s * 2, 5.0))
        self.trace.finish(outcome=outcome, failure_reason=failure_reason)
        write_trace(self.output_path, self.trace)
        return self.trace

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            self._sample_once()

    def _sample_once(self) -> None:
        started = self._started_mono or time.monotonic()
        host = read_host_memory()
        metrics, responsiveness_ms = read_clickhouse_metrics(
            self.host_port, username=self.username, password=self.password
        )
        engine_sample = EngineMemorySample(self.engine, self.service, None, None)
        if self.engine and self.project_name:
            argv = _compose_stats_argv(
                engine=self.engine,
                project_name=self.project_name,
                compose_files=self.compose_files,
                service=self.service,
            )
            returncode, stdout, stderr = self.command_runner(argv)
            engine_sample = parse_engine_stats(
                stdout if returncode == 0 else stderr,
                engine=self.engine,
                service=self.service,
            )
            inspect_returncode, inspect_stdout, inspect_stderr = self.command_runner(
                _inspect_argv(engine=self.engine, project_name=self.project_name, service=self.service)
            )
            inspect_sample = parse_engine_inspect(
                inspect_stdout if inspect_returncode == 0 else inspect_stderr,
                engine=self.engine,
                service=self.service,
            )
            engine_sample = replace(
                engine_sample,
                oom_killed=inspect_sample.oom_killed,
                running=inspect_sample.running,
            )
        self.trace.samples.append(
            TraceSample(
                observed_at_utc=utc_now(),
                elapsed_s=max(0.0, time.monotonic() - started),
                host=host,
                engine=engine_sample,
                clickhouse_metrics=metrics,
                responsiveness_ms=responsiveness_ms,
                server_reachable=responsiveness_ms is not None,
            )
        )


def write_trace(path: Path, trace: ClickHouseMemoryTrace) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(trace.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_trace(path: Path) -> ClickHouseMemoryTrace:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("trace_schema_version") != TRACE_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported ClickHouse memory trace schema {payload.get('trace_schema_version')!r}; "
            f"expected {TRACE_SCHEMA_VERSION}"
        )
    rung = MemoryRung(**payload["rung"])
    samples = [
        TraceSample(
            observed_at_utc=sample["observed_at_utc"],
            elapsed_s=float(sample["elapsed_s"]),
            host=HostMemorySample(**sample["host"]),
            engine=EngineMemorySample(**sample["engine"]),
            clickhouse_metrics={str(key): float(value) for key, value in sample["clickhouse_metrics"].items()},
            responsiveness_ms=sample["responsiveness_ms"],
            server_reachable=bool(sample["server_reachable"]),
        )
        for sample in payload.get("samples", [])
    ]
    loader_contract = payload.get("loader_contract", {})
    return ClickHouseMemoryTrace(
        platform=payload["platform"],
        rung=rung,
        started_at_utc=payload["started_at_utc"],
        ended_at_utc=payload.get("ended_at_utc"),
        source_commit=payload.get("source_commit"),
        driver_timeout_s=payload.get("driver_timeout_s"),
        driver_timeout_source=payload.get("driver_timeout_source"),
        native_streaming=bool(loader_contract.get("native_streaming", False)),
        application_batch_rows=loader_contract.get("application_batch_rows"),
        outcome=payload.get("outcome", "aborted"),
        failure_reason=payload.get("failure_reason"),
        samples=samples,
    )


def select_lowest_successful_rung(traces: Iterable[ClickHouseMemoryTrace]) -> MemoryRung:
    valid = sorted(
        (trace for trace in traces if trace.valid_for_calibration), key=lambda trace: trace.rung.requested_memory_gib
    )
    if not valid:
        raise ValueError("no valid passing memory trace; do not publish a memory floor or limit")
    return valid[0].rung


def rung_matrix_text(rungs: Iterable[MemoryRung] = DEFAULT_MEMORY_RUNGS) -> str:
    lines = ["name\trequested_memory_gib\tload_memory_gib\tdriver_timeout_s"]
    lines.extend(
        f"{rung.name}\t{rung.requested_memory_gib:g}\t{rung.load_memory_gib:g}\t{rung.driver_timeout_s}"
        for rung in rungs
    )
    return "\n".join(lines)


def summarize_command_failure(output: str, returncode: int) -> str:
    patterns = (
        "memory limit exceeded",
        "oomkilled",
        "out of memory",
        "timed out",
        "timeout",
        "cgroup",
    )
    for line in output.splitlines():
        compact = " ".join(line.split())
        if compact and any(pattern in compact.lower() for pattern in patterns):
            return compact[:500]
    return f"wrapped command exited {returncode}"


def parse_driver_timeout(command: Sequence[str]) -> int | None:
    for index, argument in enumerate(command[:-1]):
        if argument != "--platform-option":
            continue
        option = command[index + 1]
        if not option.startswith("send_receive_timeout="):
            continue
        try:
            value = int(option.split("=", 1)[1])
        except ValueError:
            return None
        return value if value > 0 else None
    return None


def default_clickhouse_driver_timeout_s() -> int:
    try:
        from benchbox.platforms.clickhouse.setup import ClickHouseSetupMixin

        source = inspect.getsource(ClickHouseSetupMixin._setup_server_mode)
    except (ImportError, OSError, TypeError) as exc:
        raise RuntimeError(f"could not inspect ClickHouse driver timeout default: {exc}") from exc
    match = re.search(
        r"send_receive_timeout\s*=\s*config\.get\(\s*['\"]send_receive_timeout['\"]\s*,\s*(\d+)\s*\)",
        source,
    )
    if match is None:
        raise RuntimeError("ClickHouse server setup does not expose a measurable send_receive_timeout default")
    return int(match.group(1))


def resolve_driver_timeout(command: Sequence[str]) -> tuple[int, str]:
    explicit = parse_driver_timeout(command)
    if explicit is not None:
        return explicit, "command platform option"
    return default_clickhouse_driver_timeout_s(), "ClickHouseSetupMixin server default"


def _find_rung(name: str) -> MemoryRung:
    for rung in DEFAULT_MEMORY_RUNGS:
        if rung.name == name:
            return rung
    raise argparse.ArgumentTypeError(
        f"unknown rung {name!r}; choose one of {', '.join(r.name for r in DEFAULT_MEMORY_RUNGS)}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rung", type=_find_rung, default=DEFAULT_MEMORY_RUNGS[0])
    parser.add_argument("--host-port", type=int, default=8123)
    parser.add_argument("--interval-s", type=float, default=DEFAULT_SAMPLE_INTERVAL_S)
    parser.add_argument("--engine")
    parser.add_argument("--project-name")
    parser.add_argument("--compose-file", action="append", type=Path, default=[])
    parser.add_argument("--service", default="clickhouse")
    parser.add_argument("--username", default="default")
    parser.add_argument("--password", default="benchbox")
    parser.add_argument("--source-commit", default=os.environ.get("GIT_COMMIT"))
    parser.add_argument("--application-batch-rows", type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    while command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a command is required after --")

    native_streaming, loader_reason = verify_native_streaming_loader()
    try:
        observed_timeout_s, timeout_source = resolve_driver_timeout(command)
    except RuntimeError as exc:
        trace = ClickHouseMemoryTrace(
            platform="clickhouse-server",
            rung=args.rung,
            started_at_utc=utc_now(),
            source_commit=args.source_commit,
            native_streaming=native_streaming,
            failure_reason=str(exc),
        )
        trace.finish(outcome="aborted", failure_reason=str(exc))
        write_trace(args.output, trace)
        return 2
    trace = ClickHouseMemoryTrace(
        platform="clickhouse-server",
        rung=args.rung,
        started_at_utc=utc_now(),
        source_commit=args.source_commit,
        driver_timeout_s=observed_timeout_s,
        driver_timeout_source=timeout_source,
        native_streaming=native_streaming,
        application_batch_rows=args.application_batch_rows,
    )
    if not native_streaming:
        trace.finish(outcome="aborted", failure_reason=loader_reason)
        write_trace(args.output, trace)
        return 2
    write_trace(args.output, trace)
    collector = MemoryTraceCollector(
        trace=trace,
        output_path=args.output,
        host_port=args.host_port,
        username=args.username,
        password=args.password,
        interval_s=args.interval_s,
        engine=args.engine,
        project_name=args.project_name,
        compose_files=args.compose_file,
        service=args.service,
    )
    collector.start()
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        collector.stop(outcome="failed", failure_reason=f"command could not start: {exc}")
        return 1
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    outcome = "passed" if completed.returncode == 0 else "failed"
    reason = (
        None
        if completed.returncode == 0
        else summarize_command_failure(completed.stdout + completed.stderr, completed.returncode)
    )
    collector.stop(outcome=outcome, failure_reason=reason)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
