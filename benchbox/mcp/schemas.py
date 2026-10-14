# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import logging
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from benchbox.core.constants import MCP_DATA_ONLY_ALIASES, MCP_MODE_CHOICES, VALID_PHASES

if TYPE_CHECKING:
    from benchbox.core.deployment import PlatformSelector

logger = logging.getLogger(__name__)

MAX_QUERY_IDS = 100
MAX_QUERY_ID_LENGTH = 64
QUERY_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
PLATFORM_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+(?::[a-zA-Z0-9_-]+)?$")
BENCHMARK_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
FILENAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]+$")
PLATFORM_OPTION_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MEMORY_LIMIT_PATTERN = re.compile(r"^(?:[1-9]\d{0,5}(?:\.\d{1,2})?)(?:B|KB|MB|GB|TB)$", re.IGNORECASE)
IDENTIFIER_LIST_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(?:,[a-zA-Z_][a-zA-Z0-9_]*)*$")
CLICKHOUSE_PROFILE_NAME_PATTERN = PLATFORM_OPTION_NAME_PATTERN

MCP_CLICKHOUSE_PROFILE_ENV = "BENCHBOX_MCP_CLICKHOUSE_PROFILES"

MCP_REJECTED_DEPLOYMENT_SELECTORS: dict[str, frozenset[str]] = {
    "velox": frozenset({"remote"}),
}

MAX_PLATFORM_OPTIONS = 16
MAX_PLATFORM_OPTION_VALUE_LENGTH = 256
MAX_MEMORY_LIMIT_BYTES = 1 << 40

MIN_SCALE_FACTOR = 0.001
MAX_SCALE_FACTOR = 10000


class MCPValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MCPPlatformOptionSpec:
    kind: Literal["bool", "int", "float", "string"]
    choices: tuple[str, ...] = ()
    minimum: int | float | None = None
    maximum: int | float | None = None

    def parse(self, name: str, value: object) -> bool | int | float | str:
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise MCPValidationError(f"Platform option '{name}' must be a boolean")
            return value
        if self.kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise MCPValidationError(f"Platform option '{name}' must be an integer")
            normalized: int | float | str = value
        elif self.kind == "float":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise MCPValidationError(f"Platform option '{name}' must be a number")
            normalized = float(value)
            if not math.isfinite(normalized):
                raise MCPValidationError(f"Platform option '{name}' must be finite")
        else:
            if not isinstance(value, str):
                raise MCPValidationError(f"Platform option '{name}' must be a string")
            normalized = value.strip()
            if not normalized or len(normalized) > MAX_PLATFORM_OPTION_VALUE_LENGTH:
                raise MCPValidationError(f"Platform option '{name}' has an invalid length")

        if self.choices and normalized not in self.choices:
            raise MCPValidationError(f"Platform option '{name}' has an unsupported value")
        if self.minimum is not None and normalized < self.minimum:
            raise MCPValidationError(f"Platform option '{name}' is below the permitted minimum")
        if self.maximum is not None and normalized > self.maximum:
            raise MCPValidationError(f"Platform option '{name}' exceeds the permitted maximum")
        return normalized


_MCP_OPTION = MCPPlatformOptionSpec


@dataclass(frozen=True, slots=True)
class MCPPlatformOptionContract:
    consumer: str
    security_class: Literal["execution", "resource", "device", "layout", "connection"]
    aliases: tuple[str, ...] = ()
    rejected_alternatives: tuple[str, ...] = ()


MCP_PLATFORM_OPTION_ALLOWLIST: dict[str, dict[str, MCPPlatformOptionSpec]] = {
    "clickhouse": {
        "connection_profile": _MCP_OPTION("string"),
        "deployment_mode": _MCP_OPTION("string", choices=("local", "server")),
    },
    "clickhouse-server": {
        "connection_profile": _MCP_OPTION("string"),
    },
    "cudf": {
        "device_id": _MCP_OPTION("int", minimum=0, maximum=255),
        "spill_to_host": _MCP_OPTION("bool"),
    },
    "dask": {
        "memory_limit": _MCP_OPTION("string"),
        "n_workers": _MCP_OPTION("int", minimum=1, maximum=256),
        "threads_per_worker": _MCP_OPTION("int", minimum=1, maximum=256),
        "use_distributed": _MCP_OPTION("bool"),
    },
    "datafusion": {
        "batch_size": _MCP_OPTION("int", minimum=1, maximum=1_000_000),
        "memory_limit": _MCP_OPTION("string"),
        "parquet_pushdown": _MCP_OPTION("bool"),
        "repartition_joins": _MCP_OPTION("bool"),
        "target_partitions": _MCP_OPTION("int", minimum=1, maximum=1_024),
    },
    "duckdb": {
        "memory_limit": _MCP_OPTION("string"),
        "threads": _MCP_OPTION("int", minimum=1, maximum=256),
    },
    "firebolt": {
        "disable_result_cache": _MCP_OPTION("bool"),
        "strict_validation": _MCP_OPTION("bool"),
    },
    "databricks": {
        "databricks_clustering_strategy": _MCP_OPTION(
            "string", choices=("z_order", "liquid_clustering", "liquid_clustering_auto", "none")
        ),
        "liquid_clustering_columns": _MCP_OPTION("string"),
    },
    "pandas": {"dtype_backend": _MCP_OPTION("string", choices=("numpy_nullable", "pyarrow"))},
    "polars": {
        "n_rows": _MCP_OPTION("int", minimum=1, maximum=10_000_000),
        "rechunk": _MCP_OPTION("bool"),
        "streaming": _MCP_OPTION("bool"),
    },
    "spark": {"adaptive_enabled": _MCP_OPTION("bool")},
    "sqlite": {
        "check_same_thread": _MCP_OPTION("bool"),
        "timeout": _MCP_OPTION("float", minimum=0, maximum=300),
    },
    "velox": {
        "adaptive_enabled": _MCP_OPTION("bool"),
        "driver_memory": _MCP_OPTION("string"),
        "offheap_size": _MCP_OPTION("string"),
        "shuffle_partitions": _MCP_OPTION("int", minimum=1, maximum=1_024),
    },
}


def _contract(
    consumer: str,
    security_class: Literal["execution", "resource", "device", "layout", "connection"],
    *,
    aliases: tuple[str, ...] = (),
    rejected_alternatives: tuple[str, ...] = (),
) -> MCPPlatformOptionContract:
    return MCPPlatformOptionContract(
        consumer=consumer,
        security_class=security_class,
        aliases=aliases,
        rejected_alternatives=rejected_alternatives,
    )


_CLICKHOUSE_PROFILE_CONTRACT = _contract(
    "ClickHouseAdapter.from_config(port, secure) resolved from server configuration",
    "connection",
    rejected_alternatives=(
        "caller-supplied port",
        "caller-supplied secure/TLS policy",
        "arbitrary destination selection",
        "transport downgrade",
    ),
)


MCP_PLATFORM_OPTION_CONTRACT: dict[str, dict[str, MCPPlatformOptionContract]] = {
    "clickhouse": {
        "connection_profile": _CLICKHOUSE_PROFILE_CONTRACT,
        "deployment_mode": _contract(
            "Deprecated alias of the clickhouse:local|server platform name, resolved through the core deployment selector",
            "connection",
        ),
    },
    "clickhouse-server": {
        "connection_profile": _CLICKHOUSE_PROFILE_CONTRACT,
    },
    "cudf": {
        "device_id": _contract("cuDF runtime device selection", "device"),
        "spill_to_host": _contract("cuDF memory policy", "resource"),
    },
    "dask": {
        "memory_limit": _contract("Dask worker resource envelope", "resource"),
        "n_workers": _contract("Dask LocalCluster worker count", "resource"),
        "threads_per_worker": _contract("Dask LocalCluster thread count", "resource"),
        "use_distributed": _contract("Dask execution-mode selector", "execution"),
    },
    "datafusion": {
        "batch_size": _contract("DataFusion execution configuration", "resource"),
        "memory_limit": _contract("DataFusion memory policy", "resource"),
        "parquet_pushdown": _contract("DataFusion parquet execution options", "execution"),
        "repartition_joins": _contract("DataFusion join execution options", "execution"),
        "target_partitions": _contract("DataFusion partition configuration", "resource"),
    },
    "duckdb": {
        "memory_limit": _contract("DuckDB thread/memory adapter settings", "resource"),
        "threads": _contract(
            "DuckDBAdapter.thread_limit",
            "resource",
            aliases=("thread_limit",),
        ),
    },
    "firebolt": {
        "disable_result_cache": _contract("Firebolt query execution options", "execution"),
        "strict_validation": _contract("Firebolt validation options", "execution"),
    },
    "databricks": {
        "databricks_clustering_strategy": _contract(
            "Databricks PlatformOptimizationConfiguration clustering strategy", "layout"
        ),
        "liquid_clustering_columns": _contract(
            "Databricks PlatformOptimizationConfiguration clustering columns", "layout"
        ),
    },
    "pandas": {"dtype_backend": _contract("pandas dataframe dtype backend", "execution")},
    "polars": {
        "n_rows": _contract("Polars input row limit", "resource"),
        "rechunk": _contract("Polars dataframe memory layout", "resource"),
        "streaming": _contract("Polars dataframe execution mode", "execution"),
    },
    "spark": {"adaptive_enabled": _contract("Spark adaptive execution setting", "execution")},
    "sqlite": {
        "check_same_thread": _contract("SQLite connection safety setting", "execution"),
        "timeout": _contract("SQLite connection timeout", "resource"),
    },
    "velox": {
        "adaptive_enabled": _contract("Velox execution options", "execution"),
        "driver_memory": _contract("Velox driver resource envelope", "resource"),
        "offheap_size": _contract("Velox off-heap resource envelope", "resource"),
        "shuffle_partitions": _contract("Velox shuffle resource envelope", "resource"),
    },
}


MCP_CORE_PARAMETER_CONTRACT: dict[str, MCPPlatformOptionContract] = {
    "execution_engine": _contract(
        "Core execution engine validated against the platform manifest",
        "execution",
        rejected_alternatives=("platform-specific engine option names",),
    ),
}


_CLICKHOUSE_PROFILES_CACHE: dict[str, dict[str, dict[str, object]]] = {}
_CACHED_CLICKHOUSE_RAW: str | None = None


def _load_clickhouse_connection_profiles() -> dict[str, dict[str, object]]:
    global _CACHED_CLICKHOUSE_RAW
    raw = os.environ.get(MCP_CLICKHOUSE_PROFILE_ENV, "").strip()
    if not raw:
        _CACHED_CLICKHOUSE_RAW = raw
        return {}
    if raw == _CACHED_CLICKHOUSE_RAW and raw in _CLICKHOUSE_PROFILES_CACHE:
        return dict(_CLICKHOUSE_PROFILES_CACHE[raw])
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Ignoring malformed %s: value is not valid JSON", MCP_CLICKHOUSE_PROFILE_ENV)
        _CLICKHOUSE_PROFILES_CACHE[raw] = {}
        _CACHED_CLICKHOUSE_RAW = raw
        return {}
    if not isinstance(decoded, dict):
        logger.error("Ignoring malformed %s: value is not a JSON object", MCP_CLICKHOUSE_PROFILE_ENV)
        _CLICKHOUSE_PROFILES_CACHE[raw] = {}
        _CACHED_CLICKHOUSE_RAW = raw
        return {}

    profiles: dict[str, dict[str, object]] = {}
    for name, entry in decoded.items():
        if not isinstance(name, str) or not CLICKHOUSE_PROFILE_NAME_PATTERN.fullmatch(name):
            logger.error("Ignoring a %s entry whose profile name is not snake_case", MCP_CLICKHOUSE_PROFILE_ENV)
            continue
        if not isinstance(entry, Mapping):
            logger.error("Ignoring %s profile '%s': value is not an object", MCP_CLICKHOUSE_PROFILE_ENV, name)
            continue
        unknown = set(entry) - {"port", "secure"}
        if unknown:
            logger.error(
                "Ignoring %s profile '%s': only 'port' and 'secure' are supported", MCP_CLICKHOUSE_PROFILE_ENV, name
            )
            continue
        port = entry.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65_535:
            logger.error(
                "Ignoring %s profile '%s': 'port' must be an integer in 1-65535", MCP_CLICKHOUSE_PROFILE_ENV, name
            )
            continue
        secure = entry.get("secure", False)
        if not isinstance(secure, bool):
            logger.error("Ignoring %s profile '%s': 'secure' must be a boolean", MCP_CLICKHOUSE_PROFILE_ENV, name)
            continue
        profiles[name] = {"port": port, "secure": secure}
    _CLICKHOUSE_PROFILES_CACHE[raw] = dict(profiles)
    _CACHED_CLICKHOUSE_RAW = raw
    return profiles


def resolve_clickhouse_connection_profile(name: str) -> dict[str, object]:
    profile = _load_clickhouse_connection_profiles().get(name)
    if profile is None:
        raise MCPValidationError("Platform option 'connection_profile' is not configured on this server")
    return dict(profile)


def _resolved_clickhouse_deployment(normalized: Mapping[str, object]) -> str | None:
    from benchbox.core.deployment import normalize_deployment_value
    from benchbox.core.platform_registry import PlatformRegistry

    requested = normalized.get("deployment_mode")
    if requested is None:
        return PlatformRegistry.get_default_deployment("clickhouse")
    return normalize_deployment_value("clickhouse", requested)


def _validate_clickhouse_options(platform: str, normalized: Mapping[str, object]) -> None:
    profile = normalized.get("connection_profile")
    if profile is None:
        return
    if not CLICKHOUSE_PROFILE_NAME_PATTERN.fullmatch(str(profile)):
        raise MCPValidationError("Platform option 'connection_profile' must be a lowercase snake_case profile name")
    if platform == "clickhouse" and _resolved_clickhouse_deployment(normalized) != "server":
        raise MCPValidationError(
            "Platform option 'connection_profile' requires the server deployment: "
            "use platform 'clickhouse:server' or 'clickhouse-server'"
        )
    resolve_clickhouse_connection_profile(str(profile))


def _memory_size_bytes(value: str) -> float | None:
    if not MEMORY_LIMIT_PATTERN.fullmatch(value):
        return None
    number, unit = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([A-Za-z]+)", value).groups()
    multipliers = {"B": 1, "KB": 1 << 10, "MB": 1 << 20, "GB": 1 << 30, "TB": 1 << 40}
    return float(number) * multipliers[unit.upper()]


def _validate_memory_limit(name: str, value: str) -> str:
    size = _memory_size_bytes(value)
    if size is None:
        raise MCPValidationError(f"Platform option '{name}' must use a bounded memory size")
    if size > MAX_MEMORY_LIMIT_BYTES:
        raise MCPValidationError(f"Platform option '{name}' exceeds the permitted memory limit")
    return value


@dataclass(frozen=True, slots=True)
class DaskResourceEnvelope:
    max_workers: int
    max_total_threads: int
    max_total_memory_bytes: float


_DASK_DEFAULT_WORKERS = 2
_DASK_DEFAULT_THREADS_PER_WORKER = 2
_DASK_DEFAULT_MEMORY_PER_WORKER_BYTES = float(2 << 30)

MCP_DASK_MAX_WORKERS_ENV = "BENCHBOX_MCP_DASK_MAX_WORKERS"
MCP_DASK_MAX_TOTAL_THREADS_ENV = "BENCHBOX_MCP_DASK_MAX_TOTAL_THREADS"
MCP_DASK_MAX_TOTAL_MEMORY_ENV = "BENCHBOX_MCP_DASK_MAX_TOTAL_MEMORY"
_DASK_DEFAULT_MAX_WORKERS = 16
_DASK_DEFAULT_MAX_TOTAL_THREADS = 64
_DASK_DEFAULT_MAX_TOTAL_MEMORY = "64GB"
_DASK_MAX_TOTAL_MEMORY_CEILING_BYTES = float(16 << 40)


def _envelope_int(env_name: str, default: int, *, maximum: int) -> int:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.error("Ignoring malformed %s: value is not an integer", env_name)
        return default
    if not 1 <= value <= maximum:
        logger.error("Ignoring out-of-range %s: value must be in 1-%d", env_name, maximum)
        return default
    return value


def load_dask_resource_envelope() -> DaskResourceEnvelope:
    raw_memory = os.environ.get(MCP_DASK_MAX_TOTAL_MEMORY_ENV, "").strip()
    memory_bytes = _memory_size_bytes(raw_memory) if raw_memory else None
    if raw_memory and memory_bytes is None:
        logger.error("Ignoring malformed %s: value is not a bounded memory size", MCP_DASK_MAX_TOTAL_MEMORY_ENV)
    if memory_bytes is not None and memory_bytes > _DASK_MAX_TOTAL_MEMORY_CEILING_BYTES:
        logger.error(
            "Ignoring out-of-range %s: value must not exceed %dTB",
            MCP_DASK_MAX_TOTAL_MEMORY_ENV,
            int(_DASK_MAX_TOTAL_MEMORY_CEILING_BYTES) >> 40,
        )
        memory_bytes = None
    if memory_bytes is None:
        memory_bytes = _memory_size_bytes(_DASK_DEFAULT_MAX_TOTAL_MEMORY)
    assert memory_bytes is not None
    return DaskResourceEnvelope(
        max_workers=_envelope_int(MCP_DASK_MAX_WORKERS_ENV, _DASK_DEFAULT_MAX_WORKERS, maximum=256),
        max_total_threads=_envelope_int(
            MCP_DASK_MAX_TOTAL_THREADS_ENV, _DASK_DEFAULT_MAX_TOTAL_THREADS, maximum=65_536
        ),
        max_total_memory_bytes=memory_bytes,
    )


def _validate_dask_options(normalized: Mapping[str, object]) -> None:
    envelope = load_dask_resource_envelope()

    workers = normalized.get("n_workers", _DASK_DEFAULT_WORKERS)
    threads_per_worker = normalized.get("threads_per_worker", _DASK_DEFAULT_THREADS_PER_WORKER)
    if not isinstance(workers, int) or not isinstance(threads_per_worker, int):
        raise MCPValidationError("Dask 'n_workers' and 'threads_per_worker' must be integers")

    if workers > envelope.max_workers:
        raise MCPValidationError("Dask 'n_workers' exceeds the server's worker budget")

    total_threads = workers * threads_per_worker
    if total_threads > envelope.max_total_threads:
        raise MCPValidationError("Dask 'n_workers' x 'threads_per_worker' exceeds the server's total thread budget")

    memory_limit = normalized.get("memory_limit")
    per_worker_bytes = (
        _memory_size_bytes(str(memory_limit)) if memory_limit is not None else _DASK_DEFAULT_MEMORY_PER_WORKER_BYTES
    )
    if per_worker_bytes is None:
        raise MCPValidationError("Platform option 'memory_limit' must use a bounded memory size")
    if workers * per_worker_bytes > envelope.max_total_memory_bytes:
        raise MCPValidationError("Dask 'n_workers' x 'memory_limit' exceeds the server's total memory budget")


def resolve_platform_policy_key(platform: str) -> str:
    name = validate_platform_selector(platform).platform
    if name not in MCP_PLATFORM_OPTION_ALLOWLIST and name.endswith("-df"):
        return name[:-3]
    return name


def is_dataframe_alias(platform: str) -> bool:
    return validate_platform_name(platform).endswith("-df")


def validate_platform_options(platform: str, options: Mapping[str, object] | None) -> dict[str, object]:
    if options is None:
        options = {}
    if not isinstance(options, Mapping):
        raise MCPValidationError("platform_options must be an object")
    if len(options) > MAX_PLATFORM_OPTIONS:
        raise MCPValidationError(f"Too many platform options (maximum {MAX_PLATFORM_OPTIONS})")

    policy_name = resolve_platform_policy_key(platform)
    specs = MCP_PLATFORM_OPTION_ALLOWLIST.get(policy_name) or {}
    contracts = MCP_PLATFORM_OPTION_CONTRACT.get(policy_name) or {}
    normalized: dict[str, object] = {}
    for raw_name, raw_value in options.items():
        if not isinstance(raw_name, str) or not PLATFORM_OPTION_NAME_PATTERN.fullmatch(raw_name):
            raise MCPValidationError("Platform option names must use lowercase snake_case")
        name = raw_name.lower()
        spec = specs.get(name)
        contract = contracts.get(name)
        if spec is None or contract is None:
            raise MCPValidationError(f"Platform option '{name}' is not authorized for MCP")
        parsed = spec.parse(name, raw_value)
        if spec.kind == "string" and name in {"memory_limit", "driver_memory", "offheap_size"}:
            parsed = _validate_memory_limit(name, parsed)
        if name == "liquid_clustering_columns" and not IDENTIFIER_LIST_PATTERN.fullmatch(str(parsed)):
            raise MCPValidationError(f"Platform option '{name}' contains invalid identifiers")
        normalized[name] = parsed

    _validate_cross_field_policy(policy_name, normalized)
    return normalized


def validate_execution_engine(platform: str, value: object | None) -> str:
    from benchbox.core.execution_engine import UnsupportedExecutionEngineError, resolve_requested
    from benchbox.core.platform_manifest import DEFAULT_EXECUTION_ENGINE

    if value is None:
        return DEFAULT_EXECUTION_ENGINE
    if not isinstance(value, str) or not value.strip():
        raise MCPValidationError("execution_engine must be a non-empty string")
    try:
        return resolve_requested(validate_platform_selector(platform).platform, value.strip())
    except UnsupportedExecutionEngineError as exc:
        raise MCPValidationError(str(exc)) from exc


def _validate_cross_field_policy(platform_name: str, normalized: Mapping[str, object]) -> None:
    if platform_name in {"clickhouse", "clickhouse-server"}:
        _validate_clickhouse_options(platform_name, normalized)
    elif platform_name == "dask":
        _validate_dask_options(normalized)
    elif platform_name == "databricks":
        build_databricks_clustering_intent(normalized)


def build_databricks_clustering_intent(normalized: Mapping[str, object]):
    from benchbox.core.run_service import build_databricks_clustering_intent as build_core_intent

    try:
        return build_core_intent(dict(normalized))
    except ValueError as exc:
        raise MCPValidationError(f"Databricks clustering options conflict: {exc}") from exc


def validate_query_id(query_id: str) -> str:
    query_id = query_id.strip()

    if not query_id:
        raise MCPValidationError("Query ID cannot be empty")

    if len(query_id) > MAX_QUERY_ID_LENGTH:
        raise MCPValidationError(f"Query ID too long (max {MAX_QUERY_ID_LENGTH} chars): {query_id[:20]}...")

    if not QUERY_ID_PATTERN.match(query_id):
        raise MCPValidationError(f"Query ID contains invalid characters: {query_id}")

    return query_id


def validate_query_list(queries: str | None) -> list[str] | None:
    if not queries:
        return None

    query_list = [q.strip() for q in queries.split(",") if q.strip()]

    if not query_list:
        return None

    if len(query_list) > MAX_QUERY_IDS:
        raise MCPValidationError(f"Too many query IDs (max {MAX_QUERY_IDS}): got {len(query_list)}")

    return [validate_query_id(q) for q in query_list]


def validate_platform_name(platform: str) -> str:
    platform = platform.strip().lower()

    if not platform:
        raise MCPValidationError("Platform name cannot be empty")

    if len(platform) > 50:
        raise MCPValidationError(f"Platform name too long (max 50 chars): {platform[:20]}...")

    if not PLATFORM_PATTERN.match(platform):
        raise MCPValidationError(f"Platform name contains invalid characters: {platform}")

    return platform


def validate_platform_selector(platform: str) -> PlatformSelector:
    from benchbox.core.deployment import parse_platform_selector

    name = validate_platform_name(platform)
    try:
        selector = parse_platform_selector(name)
    except ValueError as exc:
        raise MCPValidationError(str(exc)) from exc
    if selector.deployment in MCP_REJECTED_DEPLOYMENT_SELECTORS.get(selector.platform, frozenset()):
        raise MCPValidationError(
            f"Deployment '{selector.deployment}' for platform '{selector.platform}' is not authorized for MCP"
        )
    return selector


def validate_benchmark_name(benchmark: str) -> str:
    benchmark = benchmark.strip().lower()

    if not benchmark:
        raise MCPValidationError("Benchmark name cannot be empty")

    if len(benchmark) > 50:
        raise MCPValidationError(f"Benchmark name too long (max 50 chars): {benchmark[:20]}...")

    if not BENCHMARK_PATTERN.match(benchmark):
        raise MCPValidationError(f"Benchmark name contains invalid characters: {benchmark}")

    return benchmark


def validate_filename(filename: str) -> str:
    filename = filename.strip()

    if not filename:
        raise MCPValidationError("Filename cannot be empty")

    if len(filename) > 255:
        raise MCPValidationError(f"Filename too long (max 255 chars): {filename[:20]}...")

    if ".." in filename or "/" in filename or "\\" in filename:
        raise MCPValidationError(f"Filename cannot contain path components: {filename}")

    if not FILENAME_PATTERN.match(filename):
        raise MCPValidationError(f"Filename contains invalid characters: {filename}")

    return filename


MODE_CHOICES = MCP_MODE_CHOICES


def validate_mode(mode: str | None) -> str | None:
    if mode is None:
        return None
    mode_lower = mode.strip().lower()
    if mode_lower in MCP_DATA_ONLY_ALIASES:
        mode_lower = "data_only"
    if mode_lower not in MODE_CHOICES:
        raise MCPValidationError(f"Invalid mode: {mode}. Must be one of: {', '.join(MODE_CHOICES)}")
    return mode_lower


def validate_phases(phases: str | None) -> str | None:
    if phases is None:
        return None

    requested = [phase.strip().lower() for phase in phases.split(",") if phase.strip()]
    if not requested:
        return None

    unknown = [phase for phase in requested if phase not in VALID_PHASES]
    if unknown:
        raise MCPValidationError(
            f"Invalid phases: {', '.join(sorted(set(unknown)))}. Valid phases: {', '.join(VALID_PHASES)}"
        )

    return ",".join(requested)


def validate_scale_factor(scale_factor: float) -> float:
    if scale_factor <= 0:
        raise MCPValidationError(f"Scale factor must be positive: {scale_factor}")

    if scale_factor < MIN_SCALE_FACTOR:
        raise MCPValidationError(f"Scale factor too small (min {MIN_SCALE_FACTOR}): {scale_factor}")

    if scale_factor > MAX_SCALE_FACTOR:
        raise MCPValidationError(f"Scale factor too large (max {MAX_SCALE_FACTOR}): {scale_factor}")

    return scale_factor
