from __future__ import annotations

import hashlib
import logging
import os
import platform
import re
import statistics
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from benchbox.core.errors import PlanCaptureError, SerializationError
from benchbox.core.results.builder import (
    _BENCHMARK_FAMILY,
    normalize_benchmark_id,
)
from benchbox.core.results.metrics import percentile_ms, sample_stdev_ms
from benchbox.core.results.models import QUERY_RUN_TYPE_MEASUREMENT
from benchbox.core.results.query_execution import (
    query_duration_ms_from_legacy,
    query_execution_from_legacy_dict,
    query_execution_to_legacy_dict,
)
from benchbox.core.results.query_plan_models import (
    DEFAULT_PLAN_MAX_DEPTH,
    DEFAULT_RAW_OUTPUT_MAX_BYTES,
    DEFAULT_RAW_OUTPUT_POLICY,
)
from benchbox.platforms.base.models import (
    PowerTestPhase,
    QueryExecution,
    SetupPhase,
    ThroughputStream,
    ThroughputTestPhase,
)
from benchbox.platforms.base.runtime_metadata import build_default_normalized_result_metadata
from benchbox.platforms.base.utils import is_non_interactive
from benchbox.utils.clock import elapsed_seconds
from benchbox.utils.printing import quiet_console
from benchbox.utils.timeout_manager import run_with_timeout

MaterializedRows = Sequence[Sequence[object]]
MaterializedRowsSource = MaterializedRows | Callable[[], MaterializedRows]
MaterializedResultValidator = Callable[[str, MaterializedRows], None]

_MATERIALIZED_RESULT_VALIDATOR: ContextVar[MaterializedResultValidator | None] = ContextVar(
    "benchbox_materialized_result_validator",
    default=None,
)


@contextmanager
def materialized_result_validation(validator: MaterializedResultValidator) -> Iterator[None]:
    token = _MATERIALIZED_RESULT_VALIDATOR.set(validator)
    try:
        yield
    finally:
        _MATERIALIZED_RESULT_VALIDATOR.reset(token)


def materialized_result_validation_active() -> bool:
    return _MATERIALIZED_RESULT_VALIDATOR.get() is not None


def apply_materialized_result_validation(
    result: dict[str, Any],
    query_id: str,
    materialized_rows: MaterializedRowsSource | None,
) -> None:
    materialized_validator = _MATERIALIZED_RESULT_VALIDATOR.get()
    if materialized_validator is None or result["status"] != "SUCCESS":
        return

    try:
        if materialized_rows is None:
            raise RuntimeError("platform adapter did not expose materialized rows for structural validation")
        rows = materialized_rows if isinstance(materialized_rows, Sequence) else materialized_rows()
        materialized_validator(str(query_id), rows)
    except Exception as exc:
        result["status"] = "FAILED"
        result["error"] = str(exc) or type(exc).__name__


try:
    from benchbox.core.results.models import ExecutionPhases, QueryDefinition
except ImportError:  # pragma: no cover
    ExecutionPhases = None
    QueryDefinition = None

try:
    from benchbox.core.validation import ValidationResult
except ImportError:  # pragma: no cover
    ValidationResult = None


_DML_LEADING_RE = re.compile(r"^(?:INSERT|UPDATE|DELETE|MERGE|COPY|REPLACE|UPSERT)\b", re.IGNORECASE)
_DML_AFTER_CTE_RE = re.compile(r"\b(?:INSERT|UPDATE|DELETE|MERGE)\b|\b(?:REPLACE|UPSERT)\s+INTO\b", re.IGNORECASE)
_CREATE_TABLE_PREFIX_RE = re.compile(
    r"^CREATE\s+(?:OR\s+REPLACE\s+)?(?:GLOBAL\s+|LOCAL\s+)?(?:TEMP(?:ORARY)?\s+|UNLOGGED\s+)?TABLE\b",
    re.IGNORECASE,
)
_CREATE_MATERIALIZED_VIEW_PREFIX_RE = re.compile(
    r"^CREATE\s+(?:OR\s+REPLACE\s+)?MATERIALIZED\s+VIEW\b",
    re.IGNORECASE,
)
_AS_QUERY_RE = re.compile(r"AS\s*\(*\s*(?:SELECT|WITH|VALUES|TABLE|FROM)\b", re.IGNORECASE)
_SELECT_LEADING_RE = re.compile(r"^SELECT\b", re.IGNORECASE)
_WITH_LEADING_RE = re.compile(r"^WITH\b", re.IGNORECASE)
_WORD_CHARS_RE = re.compile(r"\w")


def _plan_capture_key(query_id: Any, sql: str) -> str:
    sql_digest = hashlib.sha256(str(sql).encode("utf-8")).hexdigest()[:16]
    return f"{query_id}#{sql_digest}"


_PLAN_CAPTURE_KEY_SUFFIX_RE = re.compile(r"#[0-9a-f]{16}$")


def _plan_capture_public_id(query_id: str) -> str:
    return _PLAN_CAPTURE_KEY_SUFFIX_RE.sub("", query_id)


def _has_top_level_keyword(statement: str, keyword: str, followed_by: re.Pattern[str] | None = None) -> bool:
    keyword = keyword.upper()
    klen = len(keyword)
    depth = 0
    i = 0
    n = len(statement)
    while i < n:
        ch = statement[i]
        if ch == "'":
            i += 1
            while i < n:
                if statement[i] == "'":
                    if i + 1 < n and statement[i + 1] == "'":
                        i += 2
                        continue
                    break
                i += 1
        elif ch == '"':
            i += 1
            while i < n and statement[i] != '"':
                i += 1
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and ch.upper() == keyword[0] and statement[i : i + klen].upper() == keyword:
            before_ok = i == 0 or not _WORD_CHARS_RE.match(statement[i - 1])
            after_ok = i + klen >= n or not _WORD_CHARS_RE.match(statement[i + klen])
            if before_ok and after_ok and (followed_by is None or followed_by.match(statement, i)):
                return True
        i += 1
    return False


def _strip_leading_sql_comments(statement: str) -> str:
    statement = statement.lstrip()
    while True:
        if statement.startswith("--"):
            newline_pos = statement.find("\n")
            if newline_pos == -1:
                return ""
            statement = statement[newline_pos + 1 :].lstrip()
        elif statement.startswith("/*"):
            end = statement.find("*/")
            if end == -1:
                return ""
            statement = statement[end + 2 :].lstrip()
        else:
            return statement


def is_dml_query(query: str) -> bool:
    statement = _strip_leading_sql_comments(query)
    if not statement:
        return False
    if _DML_LEADING_RE.match(statement):
        return True
    if _WITH_LEADING_RE.match(statement) and _DML_AFTER_CTE_RE.search(statement):
        return True
    if (
        _CREATE_TABLE_PREFIX_RE.match(statement) or _CREATE_MATERIALIZED_VIEW_PREFIX_RE.match(statement)
    ) and _has_top_level_keyword(statement, "AS", followed_by=_AS_QUERY_RE):
        return True
    if _SELECT_LEADING_RE.match(statement) and _has_top_level_keyword(statement, "INTO"):
        return True
    return False


def _extract_result_field(result: Any, attr: str, default: Any = None) -> Any:
    if hasattr(result, attr):
        return getattr(result, attr)
    if isinstance(result, dict):
        return result.get(attr, default)
    return default


def _coerce_time_seconds(result: Any) -> float | None:
    duration_ms = query_duration_ms_from_legacy(result)
    return None if duration_ms is None else duration_ms / 1000.0


def _build_latency_stats(values: list[float]) -> dict[str, Any] | None:

    if not values:
        return None

    sorted_values = sorted(values)
    count = len(sorted_values)
    values_ms = [v * 1000.0 for v in sorted_values]
    stats_seconds = {
        "count": count,
        "min": sorted_values[0],
        "max": sorted_values[-1],
        "mean": statistics.fmean(sorted_values),
        "median": statistics.median(sorted_values),
        "p90": percentile_ms(values_ms, 0.90) / 1000.0,
        "p95": percentile_ms(values_ms, 0.95) / 1000.0,
        "p99": percentile_ms(values_ms, 0.99) / 1000.0,
        "stdev": sample_stdev_ms(values_ms) / 1000.0,
    }

    stats_milliseconds = {key: (value * 1000.0 if key != "count" else value) for key, value in stats_seconds.items()}

    return {"seconds": stats_seconds, "milliseconds": stats_milliseconds}


def _build_numeric_stats(values: list[float]) -> dict[str, Any] | None:
    if not values:
        return None

    sorted_values = sorted(values)
    count = len(sorted_values)
    values_float = [float(v) for v in sorted_values]
    return {
        "count": count,
        "min": sorted_values[0],
        "max": sorted_values[-1],
        "mean": statistics.fmean(sorted_values),
        "median": statistics.median(sorted_values),
        "p90": percentile_ms(values_float, 0.90),
        "p95": percentile_ms(values_float, 0.95),
        "stdev": sample_stdev_ms(values_float),
    }


def _collect_process_metrics(process: Any) -> dict[str, Any]:
    process_snapshot: dict[str, Any] = {}
    try:
        with process.oneshot():
            try:
                mem_info = process.memory_full_info()
            except AttributeError:
                mem_info = process.memory_info()

            rss_mb = round(mem_info.rss / (1024 * 1024), 2) if mem_info else None
            vms = getattr(mem_info, "vms", None)
            vms_mb = round(vms / (1024 * 1024), 2) if vms else None

            process_snapshot.update(
                {
                    "pid": process.pid,
                    "name": process.name(),
                    "status": process.status(),
                    "memory_mb": rss_mb,
                    "virtual_memory_mb": vms_mb,
                    "memory_percent": process.memory_percent(),
                    "cpu_percent": process.cpu_percent(interval=0.0),
                    "num_threads": process.num_threads(),
                }
            )

            try:
                process_snapshot["create_time"] = datetime.fromtimestamp(process.create_time()).isoformat()
            except Exception:
                process_snapshot["create_time"] = None

            try:
                process_snapshot["num_fds"] = process.num_fds()
            except Exception:
                process_snapshot["num_fds"] = None

            try:
                process_snapshot["num_handles"] = process.num_handles()
            except Exception:
                process_snapshot["num_handles"] = None

            try:
                io_counters = process.io_counters()
                process_snapshot["io_counters"] = {
                    "read_mb": round(io_counters.read_bytes / (1024 * 1024), 2),
                    "write_mb": round(io_counters.write_bytes / (1024 * 1024), 2),
                    "read_ops": io_counters.read_count,
                    "write_ops": io_counters.write_count,
                }
            except Exception:
                process_snapshot["io_counters"] = None

            try:
                open_files = process.open_files()
                process_snapshot["open_files"] = [f.path for f in open_files]
            except Exception:
                process_snapshot["open_files"] = None

            try:
                ctx = process.num_ctx_switches()
                process_snapshot["context_switches"] = {
                    "voluntary": ctx.voluntary,
                    "involuntary": ctx.involuntary,
                }
            except Exception:
                process_snapshot["context_switches"] = None

    except Exception:  # pragma: no cover
        return {}

    return process_snapshot


def _accumulate_query_metrics(
    query_results: list[Any],
) -> tuple[int, int, list[float], list[float], list[int], list[int], list[dict[str, Any]], dict[str, int]]:
    successes = 0
    skipped = 0
    durations_all: list[float] = []
    durations_success: list[float] = []
    rows_all: list[int] = []
    rows_success: list[int] = []
    failure_samples: list[dict[str, Any]] = []
    error_breakdown: dict[str, int] = {}

    for result in query_results:
        status = str(_extract_result_field(result, "status", "UNKNOWN")).upper() or "UNKNOWN"
        time_seconds = _coerce_time_seconds(result)
        rows_returned = _extract_result_field(result, "rows_returned", 0)

        try:
            row_value = int(rows_returned) if rows_returned is not None else 0
        except (TypeError, ValueError):
            row_value = 0

        rows_all.append(row_value)
        if time_seconds is not None:
            durations_all.append(time_seconds)

        if status == "SUCCESS":
            successes += 1
            rows_success.append(row_value)
            if time_seconds is not None:
                durations_success.append(time_seconds)
        elif status == "SKIPPED":
            skipped += 1
        else:
            error_message = _extract_result_field(result, "error_message") or _extract_result_field(result, "error")
            error_key = str(error_message).strip()[:120] or status if error_message else status
            error_breakdown[error_key] = error_breakdown.get(error_key, 0) + 1

            failure_entry: dict[str, Any] = {
                "query_id": _extract_result_field(result, "query_id", "unknown"),
                "status": status,
                "rows_returned": row_value,
            }
            if time_seconds is not None:
                failure_entry["execution_time_seconds"] = time_seconds
            if error_message:
                failure_entry["error"] = str(error_message)
            failure_samples.append(failure_entry)

    return (
        successes,
        skipped,
        durations_all,
        durations_success,
        rows_all,
        rows_success,
        failure_samples,
        error_breakdown,
    )


class ResultCaptureMixin:
    logger: logging.Logger

    def _reset_plan_capture_stats(self) -> None:
        self.query_plans_captured = 0
        self.plan_capture_failures = 0
        self.plan_capture_errors: list[dict[str, Any]] = []
        self._plan_capture_phase_active = False
        self._phase_recorded_queries: dict[str, str] = {}
        self._analyze_plans_notice_printed = False
        if not hasattr(self, "_plan_capture_lock"):
            self._plan_capture_lock = threading.Lock()

    def get_normalized_result_metadata(
        self,
        *,
        connection: Any | None = None,
        platform_info: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return build_default_normalized_result_metadata(
            self,
            connection=connection,
            platform_info=platform_info,
        )

    def _collect_resource_utilization(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = {
            "available": False,
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "timestamp": datetime.now().isoformat(),
            "non_interactive": is_non_interactive(),
        }

        try:
            import psutil
        except ImportError:
            snapshot["reason"] = "psutil not installed"
            return snapshot

        snapshot["available"] = True
        snapshot["psutil_version"] = getattr(psutil, "__version__", None)

        try:
            process = psutil.Process(os.getpid())
        except Exception:  # pragma: no cover
            process = None

        cpu_percent = None
        per_cpu_percent: list[float] | None = None
        try:
            psutil.cpu_percent(interval=None)
            cpu_percent = psutil.cpu_percent(interval=0.0)
            per_cpu_percent = psutil.cpu_percent(interval=0.0, percpu=True)
        except Exception:  # pragma: no cover
            cpu_percent = None
            per_cpu_percent = None

        snapshot["cpu_percent"] = cpu_percent
        snapshot["cpu"] = {"percent": cpu_percent, "per_cpu_percent": per_cpu_percent}

        try:
            load_avg = psutil.getloadavg()
        except Exception:  # pragma: no cover
            load_avg = None
        snapshot["cpu"]["load_average"] = load_avg

        try:
            freq = psutil.cpu_freq()
            snapshot["cpu"]["frequency_mhz"] = freq.current if freq else None
        except Exception:  # pragma: no cover
            snapshot["cpu"]["frequency_mhz"] = None

        try:
            counted = psutil.cpu_count()
            if counted:
                snapshot["cpu_count"] = counted
        except Exception:  # pragma: no cover
            pass

        try:
            vm = psutil.virtual_memory()
            snapshot["memory"] = {
                "total_mb": round(vm.total / (1024 * 1024), 2),
                "available_mb": round(vm.available / (1024 * 1024), 2),
                "used_mb": round((vm.total - vm.available) / (1024 * 1024), 2),
                "percent": vm.percent,
            }
        except Exception:  # pragma: no cover
            snapshot["memory"] = None

        try:
            swap = psutil.swap_memory()
            snapshot["swap"] = {
                "total_mb": round(swap.total / (1024 * 1024), 2),
                "used_mb": round(swap.used / (1024 * 1024), 2),
                "percent": swap.percent,
            }
        except Exception:  # pragma: no cover
            snapshot["swap"] = None

        try:
            disk = psutil.disk_usage(str(Path.cwd()))
            snapshot["disk"] = {
                "mount_point": str(Path.cwd()),
                "total_mb": round(disk.total / (1024 * 1024), 2),
                "used_mb": round(disk.used / (1024 * 1024), 2),
                "free_mb": round(disk.free / (1024 * 1024), 2),
                "percent": disk.percent,
            }
        except Exception:  # pragma: no cover
            snapshot["disk"] = None

        try:
            disk_io = psutil.disk_io_counters()
            snapshot["disk_io"] = {
                "read_mb": round(disk_io.read_bytes / (1024 * 1024), 2),
                "write_mb": round(disk_io.write_bytes / (1024 * 1024), 2),
                "read_ops": disk_io.read_count,
                "write_ops": disk_io.write_count,
            }
        except Exception:  # pragma: no cover
            snapshot["disk_io"] = None

        try:
            net_io = psutil.net_io_counters()
            snapshot["network_io"] = {
                "sent_mb": round(net_io.bytes_sent / (1024 * 1024), 2),
                "received_mb": round(net_io.bytes_recv / (1024 * 1024), 2),
                "packets_sent": net_io.packets_sent,
                "packets_recv": net_io.packets_recv,
            }
        except Exception:  # pragma: no cover
            snapshot["network_io"] = None

        try:
            boot_time = datetime.fromtimestamp(psutil.boot_time()).isoformat()
        except Exception:  # pragma: no cover
            boot_time = None
        snapshot["boot_time"] = boot_time

        process_snapshot = _collect_process_metrics(process) if process is not None else {}

        if process_snapshot:
            snapshot["process_memory_mb"] = process_snapshot.get("memory_mb")
            snapshot["process_cpu_percent"] = process_snapshot.get("cpu_percent")
            snapshot["process"] = process_snapshot
        else:
            snapshot["process_memory_mb"] = None
            snapshot["process_cpu_percent"] = None
            snapshot["process"] = None

        return snapshot

    def display_query_plan_if_enabled(self, connection: Any, query: str, query_id: str) -> None:
        if not self.show_query_plans:
            return
        if self.capture_plans:
            return

        try:
            plan = self.get_query_plan(connection, query)
            if plan:
                from rich.panel import Panel

                console = quiet_console
                console.print(Panel.fit("Query Profiling Information", style="cyan"))
                console.print(f"{query}")
                console.print(plan)
                console.print()
        except Exception as e:
            self.logger.debug(f"Failed to get query plan for {query_id}: {e}")

    def get_query_plan(self, connection: Any, query: str) -> str | None:
        return None

    def get_query_plan_parser(self):
        return None

    def _record_plan_capture_failure(
        self,
        query_id: str,
        reason: str,
        message: str | None = None,
        *,
        log_warning: bool = True,
    ) -> None:
        error_record = {
            "query_id": str(query_id),
            "platform": self.platform_name,
            "reason": reason,
        }
        if message:
            error_record["message"] = message

        self.plan_capture_failures += 1
        self.plan_capture_errors.append(error_record)

        if log_warning:
            self.logger.warning(
                "Failed to capture query plan for %s (query_id=%s): %s",
                self.platform_name,
                query_id,
                message or reason,
            )

        if self.strict_plan_capture:
            raise PlanCaptureError(
                reason=reason,
                platform=self.platform_name,
                query_id=str(query_id),
                details=message,
            )

    def _resolve_raw_output_policy(self, query_id: str) -> tuple[str, int]:
        plan_config = getattr(self, "platform_config", None) or {}
        policy = plan_config.get("plan_raw_output", DEFAULT_RAW_OUTPUT_POLICY)
        configured_max_bytes = plan_config.get("plan_raw_output_max_bytes", DEFAULT_RAW_OUTPUT_MAX_BYTES)
        try:
            parsed_max_bytes = int(configured_max_bytes)
        except (TypeError, ValueError):
            parsed_max_bytes = None
        if parsed_max_bytes is not None and parsed_max_bytes > 0:
            return policy, parsed_max_bytes
        if configured_max_bytes != DEFAULT_RAW_OUTPUT_MAX_BYTES:
            self.logger.warning(
                "Invalid plan_raw_output_max_bytes %r for %s; using default %d bytes.",
                configured_max_bytes,
                query_id,
                DEFAULT_RAW_OUTPUT_MAX_BYTES,
            )
        return policy, DEFAULT_RAW_OUTPUT_MAX_BYTES

    def capture_query_plan(self, connection: Any, query: str, query_id: str) -> tuple[Any, float]:
        if not self.capture_plans:
            return None, 0.0

        if self.plan_query_filter and (
            query_id not in self.plan_query_filter and _plan_capture_public_id(query_id) not in self.plan_query_filter
        ):
            return None, 0.0

        if self.analyze_plans and not getattr(self, "_analyze_plans_notice_printed", False):
            self._analyze_plans_notice_printed = True
            quiet_console.print(
                "[bold yellow]Notice:[/bold yellow] plan capture re-executes each query "
                "(analyze_plans=True); timings are not comparable to non-capture runs."
            )

        start_time = time.perf_counter()

        try:
            explain_output, timed_out = run_with_timeout(
                self.get_query_plan,
                self.plan_capture_timeout_seconds,
                f"plan capture EXPLAIN (query_id={query_id})",
                connection,
                query,
            )
        except PlanCaptureError:
            raise
        except Exception as exc:
            capture_time_ms = (time.perf_counter() - start_time) * 1000
            self._record_plan_capture_failure(
                query_id,
                reason="explain_failed",
                message=str(exc),
            )
            return None, capture_time_ms

        if timed_out:
            capture_time_ms = (time.perf_counter() - start_time) * 1000
            self.logger.warning(
                "Query plan capture timed out for %s after %ds (%.2fms elapsed); "
                "the EXPLAIN may still be running on the connection until the database returns",
                query_id,
                self.plan_capture_timeout_seconds,
                capture_time_ms,
            )
            self._record_plan_capture_failure(
                query_id,
                reason="timeout",
                message=f"EXPLAIN query timed out after {self.plan_capture_timeout_seconds}s",
                log_warning=False,
            )
            return None, capture_time_ms

        if not explain_output:
            capture_time_ms = (time.perf_counter() - start_time) * 1000
            self._record_plan_capture_failure(
                query_id,
                reason="explain_failed",
                message="No EXPLAIN output returned",
            )
            return None, capture_time_ms

        parser = self.get_query_plan_parser()
        if not parser:
            capture_time_ms = (time.perf_counter() - start_time) * 1000
            self.logger.warning(
                "Query plan capture disabled for %s: no parser available. "
                "Plans will not be captured for this benchmark run.",
                self.platform_name,
            )
            self._record_plan_capture_failure(
                query_id,
                reason="parser_unavailable",
                message=f"No parser available for {self.platform_name}",
                log_warning=False,
            )
            return None, capture_time_ms

        try:
            plan = parser.parse_explain_output(query_id, explain_output)
        except PlanCaptureError:
            raise
        except Exception as exc:
            capture_time_ms = (time.perf_counter() - start_time) * 1000
            self._record_plan_capture_failure(
                query_id,
                reason="parse_error",
                message=str(exc),
            )
            return None, capture_time_ms

        if plan is None:
            capture_time_ms = (time.perf_counter() - start_time) * 1000
            self._record_plan_capture_failure(
                query_id,
                reason="parse_error",
                message="Parser returned no plan",
            )
            return None, capture_time_ms

        raw_output_policy, raw_output_max_bytes = self._resolve_raw_output_policy(query_id)
        plan.apply_raw_output_policy(raw_output_policy, raw_output_max_bytes)

        try:
            size_kb = plan.estimate_serialized_size(max_depth=getattr(self, "plan_max_depth", DEFAULT_PLAN_MAX_DEPTH))
            size_kb /= 1024
            if size_kb > 100:
                hint = (
                    " Consider a stricter plan_raw_output policy (full|truncated|none)."
                    if plan.raw_explain_output
                    else " The structured plan tree itself is large; consider a smaller plan_max_depth."
                )
                self.logger.warning("Large query plan for %s: %.1f KB.%s", query_id, size_kb, hint)
        except SerializationError as exc:
            capture_time_ms = (time.perf_counter() - start_time) * 1000
            self._record_plan_capture_failure(
                query_id,
                reason="serialization_error",
                message=str(exc),
            )
            return None, capture_time_ms

        capture_time_ms = (time.perf_counter() - start_time) * 1000
        self.query_plans_captured += 1
        return plan, capture_time_ms

    def _merge_plan_capture_into_result(
        self,
        result: dict[str, Any],
        connection: Any,
        query: str,
        query_id: str,
    ) -> None:
        if not getattr(self, "capture_plans", False) or result.get("status") != "SUCCESS":
            return
        if getattr(self, "_plan_capture_phase_active", False):
            recorded_id = result.get("query_id") or query_id
            if recorded_id is not None:
                capture_key = _plan_capture_key(recorded_id, query)
                result["_plan_capture_key"] = capture_key
                with self._plan_capture_lock:
                    self._phase_recorded_queries.setdefault(capture_key, query)
            return
        query_plan, plan_capture_time_ms = self.capture_query_plan(connection, query, query_id)
        if query_plan:
            result["query_plan"] = query_plan
            result["plan_fingerprint"] = query_plan.plan_fingerprint
            if getattr(self, "normalize_plan_literals", False):
                result["plan_fingerprint_normalized"] = query_plan.normalized_fingerprint
        if plan_capture_time_ms is not None:
            result["plan_capture_time_ms"] = plan_capture_time_ms

    def execute_query_with_plan_capture(
        self,
        execute: Callable[..., dict[str, Any]],
        connection: Any,
        query: str,
        query_id: str,
        benchmark_type: str | None = None,
        scale_factor: float | None = None,
        validate_row_count: bool = True,
        stream_id: int | None = None,
    ) -> dict[str, Any]:
        result = execute(
            connection=connection,
            query=query,
            query_id=query_id,
            benchmark_type=benchmark_type,
            scale_factor=scale_factor,
            validate_row_count=validate_row_count,
            stream_id=stream_id,
        )
        self._merge_plan_capture_into_result(result, connection, query, query_id)
        return result

    def validate_loaded_data(self, connection: Any, benchmark_type: str, scale_factor: float) -> ValidationResult:
        if not self.enable_validation:
            return ValidationResult(
                is_valid=True,
                errors=[],
                warnings=["Validation disabled"],
                details={
                    "benchmark_type": benchmark_type,
                    "scale_factor": scale_factor,
                    "platform": self.platform_name,
                    "validation_enabled": False,
                },
            )

        from benchbox.core.validation import ValidationService

        service = ValidationService()
        result = service.run_database(connection, benchmark_type, scale_factor)

        result.details.update({"platform": self.platform_name, "validation_enabled": True})

        return result

    def validate_row_counts(self, connection: Any, expected_counts: dict[str, int]):
        self.log_operation_start("Row count validation", f"{len(expected_counts)} tables to validate")

        try:
            from benchbox.core.validation.data import DataValidator

            validator = DataValidator(self)
            result = validator.validate_row_counts(expected_counts)

            if result.is_valid:
                self.log_operation_complete("Row count validation", details="All row counts match expected values")
            else:
                self.log_verbose(f"Row count validation failed: {len(result.issues)} issues found")

            return result

        except Exception as e:
            self.logger.error(f"Failed to validate row counts: {e}")
            from benchbox.core.validation.data import ValidationResult

            result = ValidationResult()
            result.add_error(f"Row count validation failed: {e}")
            return result

    def _summarize_performance_characteristics(
        self,
        query_results: list[Any] | None,
        total_duration: float,
        total_rows_loaded: int,
    ) -> dict[str, Any]:

        summary: dict[str, Any] = {
            "total_duration_seconds": total_duration,
            "total_rows_loaded": total_rows_loaded,
            "total_queries": 0,
            "successful_queries": 0,
            "skipped_queries": 0,
            "failed_queries": 0,
            "success_rate": None,
            "average_query_time_ms": None,
            "average_success_query_time_ms": None,
            "throughput_qps": None,
            "successful_throughput_qps": None,
            "rows_returned_total": 0,
            "rows_returned_average": None,
            "rows_returned_per_second": None,
            "execution_time_stats": None,
            "rows_returned_stats": None,
            "error_breakdown": {},
            "failure_samples": [],
        }

        if not query_results:
            return summary

        total_queries = len(query_results)
        summary["total_queries"] = total_queries

        (
            successes,
            skipped,
            durations_all,
            durations_success,
            rows_all,
            rows_success,
            failure_samples,
            error_breakdown,
        ) = _accumulate_query_metrics(query_results)

        summary["rows_returned_total"] = sum(rows_all)
        summary["successful_queries"] = successes
        summary["skipped_queries"] = skipped
        summary["failed_queries"] = total_queries - successes - skipped
        summary["error_breakdown"] = error_breakdown
        summary["failure_samples"] = failure_samples[:5]

        if total_queries:
            summary["success_rate"] = successes / total_queries

        if durations_all:
            summary["average_query_time_ms"] = statistics.fmean(durations_all) * 1000.0

        if durations_success:
            summary["average_success_query_time_ms"] = statistics.fmean(durations_success) * 1000.0

        if rows_all:
            summary["rows_returned_average"] = statistics.fmean(rows_all)

        if total_duration and total_duration > 0:
            summary["throughput_qps"] = total_queries / total_duration if total_queries else 0.0
            summary["successful_throughput_qps"] = successes / total_duration if successes else 0.0
            if summary["rows_returned_total"]:
                summary["rows_returned_per_second"] = summary["rows_returned_total"] / total_duration

        summary["execution_time_stats"] = {
            "all": _build_latency_stats(durations_all),
            "successful": _build_latency_stats(durations_success),
        }

        summary["rows_returned_stats"] = {
            "all": _build_numeric_stats([float(r) for r in rows_all]),
            "successful": _build_numeric_stats([float(r) for r in rows_success]),
        }

        return summary

    def _format_execution_time(self, execution_time_seconds: float) -> str:
        if execution_time_seconds < 0.001:
            return f"{execution_time_seconds * 1000000:.0f}μs"
        elif execution_time_seconds < 1.0:
            return f"{execution_time_seconds * 1000:.1f}ms"
        elif execution_time_seconds < 60.0:
            return f"{execution_time_seconds:.2f}s"
        else:
            minutes = int(execution_time_seconds // 60)
            seconds = execution_time_seconds % 60
            return f"{minutes}:{seconds:04.1f}"

    _KNOWN_BENCHMARK_IDS = frozenset(_BENCHMARK_FAMILY) | {"ssb", "clickbench"}

    @staticmethod
    def _normalize_known_benchmark_id(name: str) -> str | None:
        benchmark_id = normalize_benchmark_id(name)
        if benchmark_id in ResultCaptureMixin._KNOWN_BENCHMARK_IDS:
            return benchmark_id
        return None

    @staticmethod
    def _class_name_benchmark_type(class_name: str) -> str | None:
        benchmark_patterns = [
            ("amplab", "amplab"),
            ("h2odb", "h2odb"),
            ("h2o", "h2odb"),
            ("coffeeshop", "coffeeshop"),
            ("joinorder", "joinorder"),
            ("join_order", "joinorder"),
            ("tpcdi", "tpcdi"),
            ("tpc_di", "tpcdi"),
            ("star_schema", "ssb"),
        ]
        for pattern, benchmark_id in benchmark_patterns:
            if pattern in class_name:
                return benchmark_id
        return None

    def _build_query_result_with_validation(
        self,
        query_id: str,
        execution_time: float,
        actual_row_count: int,
        first_row: Any = None,
        validation_result: Any = None,
        error: str | None = None,
        result_digest: str | None = None,
        materialized_rows: MaterializedRowsSource | None = None,
    ) -> dict[str, Any]:
        from benchbox.core.expected_results.models import ValidationMode

        result_dict = {
            "query_id": str(query_id),
            "status": "FAILED" if error else "SUCCESS",
            "execution_time_seconds": execution_time,
            "rows_returned": actual_row_count,
            "first_row": first_row,
        }

        if result_digest is not None:
            result_dict["result_digest"] = result_digest

        if error:
            result_dict["error"] = error

        if validation_result:
            row_count_validation = {
                "expected": validation_result.expected_row_count,
                "actual": actual_row_count,
            }

            if validation_result.validation_mode == ValidationMode.SKIP and validation_result.is_valid:
                row_count_validation["status"] = "SKIPPED"
                if validation_result.warning_message:
                    row_count_validation["warning"] = validation_result.warning_message
            elif validation_result.is_valid:
                row_count_validation["status"] = "PASSED"
            else:
                row_count_validation["status"] = "FAILED"
                row_count_validation["error"] = validation_result.error_message
                result_dict["status"] = "FAILED"
                result_dict["error"] = validation_result.error_message

            result_dict["row_count_validation"] = row_count_validation

        apply_materialized_result_validation(result_dict, query_id, materialized_rows)

        execution = query_execution_from_legacy_dict(result_dict)
        compatibility_result = query_execution_to_legacy_dict(
            execution,
            include_milliseconds=False,
            include_seconds=True,
            error_field="error",
        )
        compatibility_result["first_row"] = first_row
        return compatibility_result

    def _build_query_failure_result(
        self,
        query_id: str,
        start_time: float,
        exception: Exception,
        log_error: bool = True,
    ) -> dict[str, Any]:
        execution_time = elapsed_seconds(start_time)

        if log_error:
            self.logger.error(
                f"Query {query_id} failed after {execution_time:.3f}s: {exception}",
                exc_info=True,
            )

        execution = QueryExecution(
            query_id=query_id,
            stream_id=None,
            execution_order=None,
            execution_time_seconds=execution_time,
            status="FAILED",
            rows_returned=0,
            error_message=str(exception),
            error_type=type(exception).__name__,
            iteration=None,
            run_type=None,
        )
        return query_execution_to_legacy_dict(
            execution,
            include_milliseconds=False,
            include_seconds=True,
            error_field="error",
        )

    def _build_dry_run_result(self, query_id: str) -> dict[str, Any]:
        self.log_very_verbose(f"Captured query {query_id} for dry-run")
        execution = QueryExecution(
            query_id=query_id,
            stream_id=None,
            execution_order=None,
            execution_time_seconds=0.0,
            status="DRY_RUN",
            rows_returned=0,
            iteration=None,
            run_type=None,
        )
        compatibility_result = query_execution_to_legacy_dict(
            execution,
            include_milliseconds=False,
            include_seconds=True,
            error_field="error",
        )
        compatibility_result.update(first_row=None, error=None, dry_run=True)
        return compatibility_result

    @staticmethod
    def _normalize_and_validate_file_paths(
        file_paths: list | Any,
    ) -> list[Path]:
        if not isinstance(file_paths, list):
            file_paths = [file_paths]

        valid_files = [Path(f) for f in file_paths if Path(f).exists() and Path(f).stat().st_size > 0]

        return valid_files

    def _create_failed_benchmark_result(
        self,
        benchmark,
        validation_phase,
        table_stats,
        loading_time,
        schema_creation_phase,
        data_loading_phase,
        tunings_applied_dict,
        tuning_validation_status,
        tuning_metadata_saved,
        requested_config_hash=None,
        per_table_timings=None,
    ):
        from datetime import datetime as _datetime

        setup_phase = SetupPhase(
            data_loading=data_loading_phase,
            schema_creation=schema_creation_phase,
            validation=validation_phase,
            post_load_maintenance=self.build_post_load_maintenance_phase(),
            merge_settle=self.build_merge_settle_phase(),
        )

        power_test_phase = PowerTestPhase(
            start_time=_datetime.now().isoformat(),
            end_time=_datetime.now().isoformat(),
            duration_ms=0,
            query_executions=[],
            geometric_mean_time=0.0,
            power_at_size=0.0,
        )

        execution_phases = ExecutionPhases(setup=setup_phase, power_test=power_test_phase)

        try:
            platform_info = self.get_platform_info(None)
        except Exception:
            platform_info = {"error": "Could not retrieve platform info"}
        normalized_metadata = self.get_normalized_result_metadata(platform_info=platform_info)

        execution_metadata = {
            "execution_timestamp": _datetime.now().isoformat(),
            "data_validation_failed": True,
            "validation_details": validation_phase.validation_details,
            "benchbox_version": "0.1.0",
            "sorted_ingestion": self.get_sorted_ingestion_metadata(),
            "post_load_maintenance": self.get_post_load_maintenance_metadata(),
        }

        total_rows_loaded = sum(table_stats.values()) if table_stats else 0
        data_size_mb = self._calculate_data_size(benchmark.output_dir) if hasattr(benchmark, "output_dir") else 0.0

        return benchmark.create_enhanced_benchmark_result(
            platform=self.platform_name,
            query_results=[],
            execution_metadata=execution_metadata,
            phases=execution_phases,
            resource_utilization={},
            performance_characteristics={},
            total_rows_loaded=total_rows_loaded,
            data_size_mb=data_size_mb,
            data_loading_time=loading_time,
            schema_creation_time=getattr(schema_creation_phase, "duration_ms", 0) / 1000.0,
            table_statistics=table_stats,
            per_table_timings=per_table_timings,
            tunings_applied=tunings_applied_dict,
            tuning_validation_status=tuning_validation_status,
            tuning_metadata_saved=tuning_metadata_saved,
            tuning_config_hash=requested_config_hash,
            tuning_source_file=getattr(self, "tuning_source_file", None),
            tuning_source=getattr(self, "tuning_source", None),
            platform_info=platform_info,
            **normalized_metadata,
            validation_status="FAILED",
            validation_details=validation_phase.validation_details,
        )

    def _create_throughput_phase(self, throughput_result) -> ThroughputTestPhase | None:
        if throughput_result is None:
            return None

        from benchbox.core.throughput.result import throughput_result_succeeded, throughput_stream_succeeded

        streams: list[ThroughputStream] = []
        total_queries_executed = 0
        persisted_order = 0

        for stream_result in getattr(throughput_result, "stream_results", []) or []:
            start_iso = self._format_timestamp(getattr(stream_result, "start_wall_time", ""))
            end_iso = self._format_timestamp(getattr(stream_result, "end_wall_time", ""))

            duration_seconds = float(getattr(stream_result, "duration", 0.0) or 0.0)
            if (
                not duration_seconds
                and isinstance(stream_result.start_time, (int, float))
                and isinstance(stream_result.end_time, (int, float))
            ):
                duration_seconds = max(stream_result.end_time - stream_result.start_time, 0.0)
            duration_ms = int(duration_seconds * 1000)

            query_executions: list[QueryExecution] = []
            for query_result in stream_result.query_results:
                persisted_order += 1
                execution_order_value = query_result.get("execution_order")
                if execution_order_value is not None:
                    execution_order = execution_order_value
                else:
                    execution_order = persisted_order
                execution_time_seconds = query_result.get("execution_time_seconds")
                execution_time_ms = None if execution_time_seconds is None else float(execution_time_seconds) * 1000

                query_executions.append(
                    QueryExecution(
                        query_id=str(query_result.get("query_id")),
                        stream_id=str(stream_result.stream_id),
                        execution_order=int(execution_order),
                        execution_time_ms=execution_time_ms,
                        status="SUCCESS" if query_result.get("success", True) else "FAILED",
                        rows_returned=query_result.get("result_count"),
                        error_message=query_result.get("error"),
                        run_type=self._infer_query_result_run_type(query_result),
                    )
                )

            stream_success = throughput_stream_succeeded(stream_result)
            stream_error = getattr(stream_result, "error", None)
            if not stream_success and not stream_error:
                stream_error = (
                    f"{getattr(stream_result, 'queries_successful', 0)}/"
                    f"{getattr(stream_result, 'queries_executed', 0)} queries succeeded"
                )

            streams.append(
                ThroughputStream(
                    stream_id=stream_result.stream_id,
                    start_time=start_iso,
                    end_time=end_iso,
                    duration_ms=duration_ms,
                    query_executions=query_executions,
                    success=stream_success,
                    error_message=stream_error,
                )
            )
            total_queries_executed += getattr(stream_result, "queries_executed", len(stream_result.query_results))

        duration_ms = int(float(getattr(throughput_result, "total_time", 0.0)) * 1000)
        end_time_iso = throughput_result.end_time or datetime.now().isoformat()
        phase_success = throughput_result_succeeded(throughput_result)
        raw_outstanding_stream_ids = getattr(throughput_result, "outstanding_stream_ids", None)
        outstanding_stream_ids = (
            list(raw_outstanding_stream_ids) if isinstance(raw_outstanding_stream_ids, (list, tuple)) else []
        )
        cleanup_state = getattr(throughput_result, "cleanup_state", "complete")
        if not isinstance(cleanup_state, str):
            cleanup_state = "complete"
        outstanding_work = None
        if outstanding_stream_ids or cleanup_state != "complete":
            outstanding_work = {
                "stream_ids": outstanding_stream_ids,
                "cleanup_state": cleanup_state,
            }

        raw_stream_numbering = getattr(throughput_result, "stream_numbering", None)
        stream_numbering = dict(raw_stream_numbering) if isinstance(raw_stream_numbering, dict) else None

        return ThroughputTestPhase(
            start_time=throughput_result.start_time,
            end_time=end_time_iso,
            duration_ms=duration_ms,
            num_streams=getattr(getattr(throughput_result, "config", None), "num_streams", len(streams)),
            streams=streams,
            total_queries_executed=total_queries_executed,
            throughput_at_size=(getattr(throughput_result, "throughput_at_size", None) if phase_success else None),
            success=phase_success,
            errors=list(getattr(throughput_result, "errors", []) or []),
            outstanding_work=outstanding_work,
            stream_numbering=stream_numbering,
        )

    @staticmethod
    def _format_timestamp(value: Any) -> str:
        if isinstance(value, str) and value:
            return value
        if isinstance(value, (int, float)) and value > 0:
            return datetime.fromtimestamp(value).isoformat()
        return datetime.now().isoformat()

    def _determine_overall_validation_status(self, validation_phase) -> str:
        if (
            validation_phase.row_count_validation == "FAILED"
            or validation_phase.schema_validation == "FAILED"
            or validation_phase.data_integrity_checks == "FAILED"
        ):
            return "FAILED"
        elif (
            validation_phase.row_count_validation == "PARTIAL"
            or validation_phase.schema_validation == "PARTIAL"
            or validation_phase.data_integrity_checks == "PARTIAL"
        ):
            return "PARTIAL"
        else:
            return "PASSED"

    def _extract_query_definitions(
        self, benchmark, queries: dict[str, str], stream_id: str = "standard"
    ) -> dict[str, dict[str, QueryDefinition]]:
        query_definitions = {stream_id: {}}

        for query_id, sql_text in queries.items():
            query_definitions[stream_id][query_id] = QueryDefinition(
                sql=sql_text,
                parameters={},
            )

        return query_definitions

    def _create_standard_execution_phase(
        self, query_results: list[dict[str, Any]], stream_id: str = "standard"
    ) -> list[QueryExecution]:
        query_executions = []

        for i, result in enumerate(query_results):
            source = dict(result)
            source.setdefault("query_id", f"Q{i + 1}")
            source.setdefault("stream_id", stream_id)
            source.setdefault("execution_order", i + 1)
            source.setdefault("run_type", QUERY_RUN_TYPE_MEASUREMENT)
            execution = query_execution_from_legacy_dict(source)
            if execution.execution_time_ms is not None:
                execution = replace(execution, execution_time_ms=round(execution.execution_time_ms, 2))
            query_executions.append(execution)

        return query_executions

    def _setup_reused_database_phases(self, benchmark, connection: Any) -> tuple:
        if self.table_mode == "external":
            return self._setup_reused_external_phases(benchmark, connection)

        quiet_console.print("✅ Database being reused - skipping schema creation and data loading")
        schema_time = 0.0
        schema_creation_phase = self._create_enhanced_schema_creation_phase(benchmark, connection, schema_time)
        loading_time = 0.0
        table_stats = {}
        if hasattr(benchmark, "get_schema"):
            schema = benchmark.get_schema()
            if isinstance(schema, dict):
                self.logger.debug(f"Collecting table stats for {len(schema)} tables from reused database")
                for table_name in schema:
                    try:
                        count = self.get_table_row_count(connection, table_name)
                        table_stats[table_name] = count
                        self.logger.debug(f"Table {table_name}: {count} rows")
                    except Exception as e:
                        self.logger.warning(f"Could not get row count for {table_name}: {e}")
                        table_stats[table_name] = 0
        data_loading_phase = self._create_enhanced_data_loading_phase(table_stats, loading_time, None)
        self._last_per_table_timings = None
        tuning_metadata_saved = False
        return schema_time, schema_creation_phase, loading_time, table_stats, data_loading_phase, tuning_metadata_saved

    def _setup_reused_external_phases(self, benchmark, connection: Any) -> tuple:
        if not self.supports_external_tables:
            raise RuntimeError(f"Platform '{self.platform_name}' does not support --table-mode external")

        validate_fn = getattr(self, "validate_external_table_requirements", None)
        if callable(validate_fn):
            validate_fn()

        data_dir = Path(benchmark.output_dir) if hasattr(benchmark, "output_dir") else Path(".")

        quiet_console.print("Creating external tables (reusing existing database)...")
        schema_time = 0.0
        schema_creation_phase = self._create_enhanced_schema_creation_phase(benchmark, connection, schema_time)
        schema_creation_phase.status = "SKIPPED"

        table_stats, loading_time, per_table_timings = self.create_external_tables(benchmark, connection, data_dir)
        _fmt_tag = f" [{self.external_format}]" if self.external_format else ""
        quiet_console.print(f"✅ External tables created in {loading_time:.2f}s{_fmt_tag}")
        data_loading_phase = self._create_enhanced_data_loading_phase(table_stats, loading_time, per_table_timings)
        self._last_per_table_timings = per_table_timings
        return schema_time, schema_creation_phase, loading_time, table_stats, data_loading_phase, False

    def _check_validation_failure(self, validation_phase) -> bool:
        if (
            validation_phase.row_count_validation == "FAILED"
            or validation_phase.schema_validation == "FAILED"
            or validation_phase.data_integrity_checks == "FAILED"
        ):
            quiet_console.print("❌ Data validation failed - benchmark execution halted")
            if validation_phase.validation_details:
                details = validation_phase.validation_details
                if "empty_tables" in details and details["empty_tables"]:
                    quiet_console.print(f"⚠️  Empty tables detected: {', '.join(details['empty_tables'])}")
                if "missing_tables" in details and details["missing_tables"]:
                    quiet_console.print(f"⚠️  Missing tables: {', '.join(details['missing_tables'])}")
                if "inaccessible_tables" in details and details["inaccessible_tables"]:
                    quiet_console.print(f"⚠️  Inaccessible tables: {', '.join(details['inaccessible_tables'])}")
            return True
        return False

    def _log_plan_capture_summary(self, query_results: list[dict[str, Any]]) -> None:
        from benchbox.core.results.schema import compute_plan_capture_stats

        plans_captured, capture_failures, _errors = compute_plan_capture_stats(
            query_results, self.capture_plans, existing_errors=list(self.plan_capture_errors)
        )
        total_queries = plans_captured + capture_failures
        summary_message = f"Query plans: {plans_captured}/{total_queries} captured"
        if capture_failures:
            summary_message = f"{summary_message}, {capture_failures} failed"
        log_fn = self.logger.warning if capture_failures else self.logger.info
        log_fn(summary_message)

    def _build_execution_phases(
        self,
        query_results,
        query_executions,
        run_config,
        setup_phase,
        *,
        power_workload_timing: tuple[str, str, int] | None = None,
    ) -> tuple:
        from datetime import datetime as _datetime

        successful_queries = len([r for r in query_results if r["status"] == "SUCCESS"])
        total_exec_time = sum(r["execution_time_seconds"] for r in query_results if r["status"] == "SUCCESS")
        avg_time = total_exec_time / max(successful_queries, 1)

        if self.capture_plans:
            self._log_plan_capture_summary(query_results)

        eet = run_config.get("_effective_execution_type")
        execution_type = eet if eet is not None else run_config.get("test_execution_type", "standard")

        power_at_size_value = 0.0
        if getattr(self, "_last_power_test_result", None) is not None:
            power_at_size_value = getattr(self._last_power_test_result, "power_at_size", 0.0) or 0.0
            self._last_power_test_result = None

        power_test_phase = None
        if execution_type not in {"throughput"}:
            start_time, end_time, duration_ms = power_workload_timing or (
                _datetime.now().isoformat(),
                _datetime.now().isoformat(),
                int(total_exec_time * 1000),
            )
            power_test_phase = PowerTestPhase(
                start_time=start_time,
                end_time=end_time,
                duration_ms=duration_ms,
                query_executions=query_executions,
                geometric_mean_time=avg_time,
                power_at_size=power_at_size_value,
            )

        throughput_test_phase = None
        if getattr(self, "_last_throughput_test_result", None) is not None:
            throughput_test_phase = self._create_throughput_phase(self._last_throughput_test_result)
            self._last_throughput_test_result = None

        execution_phases = ExecutionPhases(
            setup=setup_phase,
            power_test=power_test_phase,
            throughput_test=throughput_test_phase,
        )
        return execution_phases, total_exec_time, power_test_phase, throughput_test_phase

    def _build_execution_metadata(self, run_config: dict) -> tuple:
        try:
            from benchbox.core.results.anonymization import (
                AnonymizationConfig,
                AnonymizationManager,
            )
            from benchbox.utils.system_info import get_system_info

            anonymization_manager = AnonymizationManager(AnonymizationConfig.from_public_environ())
            system_info = get_system_info()
            system_profile = system_info.to_dict()
            anonymous_machine_id = anonymization_manager.get_anonymous_machine_id()
        except ImportError:
            system_profile = None
            anonymous_machine_id = None

        execution_metadata = {
            "benchmark_type": run_config.get("benchmark_type", "olap"),
            "query_subset": run_config.get("query_subset"),
            "categories": run_config.get("categories"),
            "connection_config_hash": self._hash_connection_config(run_config.get("connection", {})),
            "python_version": platform.python_version(),
            "benchbox_version": "0.1.0",
            "mode": "sql",
            "benchmark_id": run_config.get("benchmark_name"),
            "run_config": {
                "compression": {
                    "type": run_config.get("compression_type"),
                    "level": run_config.get("compression_level"),
                }
                if run_config.get("compression_type")
                else None,
                "seed": run_config.get("seed"),
                "phases": run_config.get("phases"),
                "query_subset": run_config.get("query_subset"),
                "platform_options": run_config.get("platform_options"),
                "platform_option_sources": run_config.get("platform_option_sources"),
                "tuning_mode": run_config.get("tuning_mode"),
                "tuning_config": run_config.get("tuning_config"),
                "table_mode": self.table_mode if self.table_mode != "native" else None,
                "external_format": self.external_format,
                "table_format": run_config.get("table_format"),
                "table_format_compression": (
                    run_config.get("table_format_compression") if run_config.get("table_format") else None
                ),
                "table_format_partition_cols": (
                    run_config.get("table_format_partition_cols") if run_config.get("table_format") else None
                ),
            },
            "sorted_ingestion": self.get_sorted_ingestion_metadata(),
            "post_load_maintenance": self.get_post_load_maintenance_metadata(),
        }
        from benchbox.core.results.builder import normalize_benchmark_id

        if normalize_benchmark_id(str(run_config.get("benchmark_name") or "")) == "tpch":
            from benchbox.core.tpch.benchmark import describe_query_parameters

            seed = run_config.get("seed")
            execution_type = run_config.get("test_execution_type", "standard")
            if run_config.get("_effective_execution_type") is not None:
                execution_type = "standard"
            execution_metadata["run_config"]["query_parameters"] = describe_query_parameters(
                None if seed is None else int(seed),
                execution_type=execution_type,
                requested_phases=set((run_config.get("options") or {}).get("requested_phases") or []),
            )
        tuning_profile_metadata = self._build_tuning_profile_metadata(run_config)
        if tuning_profile_metadata:
            execution_metadata["tuning_profile"] = tuning_profile_metadata
        return execution_metadata, system_profile, anonymous_machine_id

    def _build_tuning_profile_metadata(self, run_config: dict) -> dict[str, Any] | None:
        try:
            from benchbox.core.tuning.profile_validation import build_tuning_profile_metadata

            effective_config = self.get_effective_tuning_configuration()
            return build_tuning_profile_metadata(
                benchmark=run_config.get("benchmark_name"),
                platform=getattr(self, "platform_name", None),
                tuning_config=effective_config,
            )
        except Exception as exc:  # pragma: no cover
            logger = getattr(self, "logger", None)
            if logger is not None:
                logger.debug("Unable to build tuning profile metadata: %s", exc)
            return None
