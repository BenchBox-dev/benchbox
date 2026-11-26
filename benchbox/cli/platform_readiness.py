from __future__ import annotations

import socket
from dataclasses import dataclass
from importlib import util

DEFAULT_LAKESAIL_ENDPOINT = "sc://localhost:50051"


@dataclass(frozen=True)
class LocalTcpEndpoint:
    platform: str
    display_name: str
    host: str
    port: int
    remediation: str

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"


@dataclass(frozen=True)
class PlatformReadinessResult:
    platform: str
    check: str
    status: str
    summary: str
    detail: str = ""
    remediation: str = ""
    endpoint: str | None = None

    @property
    def ready(self) -> bool:
        return self.status == "ready"


_DATAFRAME_ALIASES: dict[str, str] = {
    "polars-df": "polars",
    "pandas-df": "pandas",
    "pyspark-df": "pyspark",
    "datafusion-df": "datafusion",
    "dask-df": "dask",
    "cudf-df": "cudf",
    "lakesail-df": "lakesail",
}

_LOCAL_TCP_ENDPOINTS: dict[str, LocalTcpEndpoint] = {
    "clickhouse-server": LocalTcpEndpoint(
        "clickhouse-server",
        "ClickHouse Server",
        "localhost",
        9000,
        "Start the local ClickHouse server before running clickhouse-server benchmarks.",
    ),
    "cedardb": LocalTcpEndpoint(
        "cedardb",
        "CedarDB",
        "localhost",
        5435,
        "Start the local CedarDB service before running cedardb benchmarks.",
    ),
    "starrocks": LocalTcpEndpoint(
        "starrocks",
        "StarRocks",
        "localhost",
        19030,
        "Start the local StarRocks FE service before running starrocks benchmarks.",
    ),
    "postgresql": LocalTcpEndpoint(
        "postgresql",
        "PostgreSQL",
        "localhost",
        5432,
        "Start the local PostgreSQL service before running postgresql benchmarks.",
    ),
    "presto": LocalTcpEndpoint(
        "presto",
        "PrestoDB",
        "localhost",
        18081,
        "Start the local Presto coordinator before running presto benchmarks.",
    ),
    "trino": LocalTcpEndpoint(
        "trino",
        "Trino",
        "localhost",
        18080,
        "Start the local Trino coordinator before running trino benchmarks.",
    ),
    "databend": LocalTcpEndpoint(
        "databend",
        "Databend",
        "localhost",
        8000,
        "Start the local Databend service before running databend benchmarks.",
    ),
    "doris": LocalTcpEndpoint(
        "doris",
        "Doris",
        "localhost",
        19031,
        "Start the local Doris FE service before running doris benchmarks.",
    ),
    "influxdb": LocalTcpEndpoint(
        "influxdb",
        "InfluxDB",
        "localhost",
        8181,
        "Start the local InfluxDB service before running influxdb benchmarks.",
    ),
    "pg-duckdb": LocalTcpEndpoint(
        "pg-duckdb",
        "pg_duckdb",
        "localhost",
        5432,
        "Start the local PostgreSQL-compatible pg_duckdb service before running pg-duckdb benchmarks.",
    ),
    "pg-mooncake": LocalTcpEndpoint(
        "pg-mooncake",
        "pg_mooncake",
        "localhost",
        5432,
        "Start the local PostgreSQL-compatible pg_mooncake service before running pg-mooncake benchmarks.",
    ),
    "timescaledb": LocalTcpEndpoint(
        "timescaledb",
        "TimescaleDB",
        "localhost",
        5432,
        "Start the local TimescaleDB service before running timescaledb benchmarks.",
    ),
    "questdb": LocalTcpEndpoint(
        "questdb",
        "QuestDB",
        "localhost",
        8812,
        "Start the local QuestDB PostgreSQL-wire service before running questdb benchmarks.",
    ),
    "singlestore": LocalTcpEndpoint(
        "singlestore",
        "SingleStore",
        "localhost",
        13306,
        "Start the local SingleStore service before running singlestore benchmarks.",
    ),
    "velox": LocalTcpEndpoint(
        "velox",
        "Velox Spark Connect",
        "localhost",
        50051,
        "Start the local Velox Spark Connect service before running remote velox benchmarks.",
    ),
}


def normalize_readiness_platform(platform: str) -> str:
    normalized = platform.lower()
    return _DATAFRAME_ALIASES.get(normalized, normalized)


def check_platform_readiness(
    platform: str,
    *,
    timeout_seconds: float = 1.0,
) -> tuple[PlatformReadinessResult, ...]:
    canonical_platform = normalize_readiness_platform(platform)

    if canonical_platform in _LOCAL_TCP_ENDPOINTS:
        return (_check_local_tcp_endpoint(_LOCAL_TCP_ENDPOINTS[canonical_platform], timeout_seconds),)
    if canonical_platform == "lakesail":
        return _check_lakesail(platform, timeout_seconds)
    return ()


def has_readiness_failures(results: tuple[PlatformReadinessResult, ...]) -> bool:
    return any(not result.ready for result in results)


def _check_local_tcp_endpoint(endpoint: LocalTcpEndpoint, timeout_seconds: float) -> PlatformReadinessResult:
    if _tcp_reachable(endpoint.host, endpoint.port, timeout_seconds):
        return PlatformReadinessResult(
            platform=endpoint.platform,
            check="local_tcp_endpoint",
            status="ready",
            summary=f"{endpoint.display_name} endpoint is reachable at {endpoint.address}.",
            endpoint=endpoint.address,
        )

    return PlatformReadinessResult(
        platform=endpoint.platform,
        check="local_tcp_endpoint",
        status="environment_skip",
        summary=f"{endpoint.display_name} endpoint is not reachable at {endpoint.address}.",
        detail="Benchmarks for this platform should be skipped until the local service is provisioned.",
        remediation=endpoint.remediation,
        endpoint=endpoint.address,
    )


def _check_lakesail(platform: str, timeout_seconds: float) -> tuple[PlatformReadinessResult, ...]:
    endpoint = _configured_lakesail_endpoint()
    results: list[PlatformReadinessResult] = []

    if _module_available("pyspark"):
        results.append(
            PlatformReadinessResult(
                platform=platform,
                check="pyspark_client",
                status="ready",
                summary="PySpark Spark Connect client is importable.",
            )
        )
    else:
        results.append(
            PlatformReadinessResult(
                platform=platform,
                check="pyspark_client",
                status="environment_skip",
                summary="PySpark Spark Connect client is not importable.",
                remediation="Install the LakeSail client dependency with: uv add 'pyspark>=3.4.0,<4.3'",
            )
        )

    host, port = _parse_spark_connect_endpoint(endpoint)
    if _tcp_reachable(host, port, timeout_seconds):
        results.append(
            PlatformReadinessResult(
                platform=platform,
                check="spark_connect_endpoint",
                status="ready",
                summary=f"LakeSail Spark Connect endpoint is reachable at {endpoint}.",
                endpoint=endpoint,
            )
        )
        return tuple(results)

    pysail_available = _module_available("pysail")
    is_sql_adapter = platform == "lakesail"
    if pysail_available and is_sql_adapter:
        results.append(
            PlatformReadinessResult(
                platform=platform,
                check="spark_connect_endpoint",
                status="ready",
                summary=(
                    f"LakeSail Spark Connect endpoint is not reachable at {endpoint}, "
                    "but the SQL adapter can auto-start a local pysail server at run time."
                ),
                detail=(
                    "pysail is importable; LakeSailAdapter._ensure_server_ready will start a local Sail server "
                    "when the benchmark runs. A run selected with --platform lakesail:distributed does not auto-start."
                ),
                endpoint=endpoint,
            )
        )
        return tuple(results)

    if pysail_available:
        auto_start_detail = (
            "The SQL adapter can auto-start a local pysail server at run time, but this readiness check does not "
            "start it. LakeSail DataFrame mode still needs an already-running endpoint."
        )
    else:
        auto_start_detail = (
            "The SQL adapter also needs pysail for local auto-start; DataFrame mode needs an already-running endpoint."
        )
    results.append(
        PlatformReadinessResult(
            platform=platform,
            check="spark_connect_endpoint",
            status="environment_skip",
            summary=f"LakeSail Spark Connect endpoint is not reachable at {endpoint}.",
            detail=auto_start_detail,
            remediation=(
                "Start a Sail Spark Connect server, save LakeSail credentials with endpoint=sc://host:port, "
                "or install pysail with uv add pysail for SQL-mode local auto-start."
            ),
            endpoint=endpoint,
        )
    )
    return tuple(results)


def _configured_lakesail_endpoint() -> str:
    try:
        from benchbox.security.credentials import CredentialManager

        credentials = CredentialManager().get_platform_credentials("lakesail") or {}
    except Exception:
        credentials = {}

    return str(credentials.get("endpoint") or DEFAULT_LAKESAIL_ENDPOINT)


def _module_available(module_name: str) -> bool:
    try:
        return util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def _parse_spark_connect_endpoint(endpoint: str) -> tuple[str, int]:
    address = endpoint[5:] if endpoint.startswith("sc://") else endpoint
    host, _, port_text = address.partition(":")
    try:
        port = int(port_text) if port_text else 50051
    except ValueError:
        port = 50051
    return host or "localhost", port


def _tcp_reachable(host: str, port: int, timeout_seconds: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False
