from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Iterable

from benchbox.core.benchmark_registry import CATEGORY_ORDER
from benchbox.core.platform_registry import PlatformRegistry
from tests.uat import docker_assets

PLATFORM_CLI_FLAGS: dict[str, list[str]] = {
    "velox": ["--iterations", "1"],
}

PLATFORM_UV_EXTRA: dict[str, str] = {
    "clickhouse-local": "clickhouse-local",
    "clickhouse-server": "clickhouse-server",
    "lakesail": "lakesail",
    "singlestore": "singlestore",
    "influxdb": "influxdb",
    "databend": "databend",
}

LOCAL_SQL_PLATFORMS: tuple[str, ...] = ("duckdb", "sqlite", "datafusion")
FAST_NATIVE_PLATFORMS: tuple[str, ...] = tuple(platform for platform in LOCAL_SQL_PLATFORMS if platform != "sqlite") + (
    "clickhouse-local",
)
FAST_DOCKER_PLATFORMS: tuple[str, ...] = (
    "lakesail",
    "clickhouse-server",
    "cedardb",
    "starrocks",
)
SLOW_NATIVE_PLATFORMS: tuple[str, ...] = tuple(platform for platform in LOCAL_SQL_PLATFORMS if platform == "sqlite") + (
    "spark",
)
SLOW_DOCKER_PLATFORMS: tuple[str, ...] = (
    "postgresql",
    "presto",
    "trino",
    "databend",
    "doris",
    "influxdb",
    "pg-duckdb",
    "pg-mooncake",
    "timescaledb",
    "questdb",
    "singlestore",
    "velox",
)
UAT_DATAFRAME_PLATFORM_BASES: tuple[str, ...] = (
    "polars",
    "pandas",
    "pyspark",
    "dask",
    "datafusion",
)


def _registry_platform_subset(
    group_name: str,
    candidates: tuple[str, ...],
    registry_platforms: Iterable[str],
) -> tuple[str, ...]:
    registry_set = set(registry_platforms)
    missing = tuple(platform for platform in candidates if platform not in registry_set)
    if missing:
        raise ValueError(f"UAT {group_name} platforms missing from platform registry: {missing}")
    return candidates


def _dataframe_selector_platforms() -> tuple[str, ...]:
    registry_platforms = set(PlatformRegistry.get_dataframe_platforms())
    missing = tuple(platform for platform in UAT_DATAFRAME_PLATFORM_BASES if platform not in registry_platforms)
    if missing:
        raise ValueError(f"UAT dataframe platforms missing from platform registry: {missing}")
    return tuple(f"{platform}-df" for platform in UAT_DATAFRAME_PLATFORM_BASES)


SQL_PLATFORMS = _registry_platform_subset(
    "sql",
    FAST_NATIVE_PLATFORMS + FAST_DOCKER_PLATFORMS + SLOW_NATIVE_PLATFORMS + SLOW_DOCKER_PLATFORMS,
    PlatformRegistry.get_sql_platforms(),
)
DOCKER_PLATFORMS = _registry_platform_subset(
    "docker",
    FAST_DOCKER_PLATFORMS + SLOW_DOCKER_PLATFORMS,
    PlatformRegistry.get_self_hosted_platforms(),
)
DATAFRAME_PLATFORMS = _dataframe_selector_platforms()

PLATFORM_GROUPS: dict[str, tuple[str, ...]] = {
    "fast": FAST_NATIVE_PLATFORMS + FAST_DOCKER_PLATFORMS,
    "slow": SLOW_NATIVE_PLATFORMS + SLOW_DOCKER_PLATFORMS,
    "native-sql": FAST_NATIVE_PLATFORMS + SLOW_NATIVE_PLATFORMS,
    "sql": SQL_PLATFORMS,
    "dataframe": DATAFRAME_PLATFORMS,
    "docker": DOCKER_PLATFORMS,
    "docker-fast": FAST_DOCKER_PLATFORMS,
    "docker-slow": SLOW_DOCKER_PLATFORMS,
    "all": SQL_PLATFORMS + DATAFRAME_PLATFORMS,
}


def known_platform_ids() -> frozenset[str]:
    sql = set(PlatformRegistry.get_sql_platforms())
    dataframe = set(PlatformRegistry.get_dataframe_platforms())
    df_aliases = {f"{platform}-df" for platform in dataframe if not platform.endswith("-df")}
    return frozenset(sql | dataframe | df_aliases)


def missing_platforms_from_include(
    include: Iterable[str],
    known: Iterable[str] | None = None,
) -> list[str]:
    known_set = set(known_platform_ids()) if known is None else set(known)
    seen: set[str] = set()
    missing: list[str] = []
    for platform in include:
        if platform not in known_set and platform not in seen:
            seen.add(platform)
            missing.append(platform)
    return missing


def resolve_platforms(
    groups: Iterable[str] = (),
    include: Iterable[str] = (),
    exclude: Iterable[str] = (),
    known: Iterable[str] | None = None,
) -> list[str]:
    known_set = set(known_platform_ids()) if known is None else set(known)
    seen: set[str] = set()
    resolved: list[str] = []
    for group in groups:
        if group not in PLATFORM_GROUPS:
            raise ValueError(f"Unknown platform group {group!r}; valid: {sorted(PLATFORM_GROUPS)}")
        for platform in PLATFORM_GROUPS[group]:
            if platform not in seen:
                seen.add(platform)
                resolved.append(platform)
    for platform in include:
        if platform in known_set and platform not in seen:
            seen.add(platform)
            resolved.append(platform)
    excluded = set(exclude)
    return [p for p in resolved if p not in excluded]


_REACHABILITY_CACHE: dict[str, bool] = {}


def invalidate_reachability_cache_after_lifecycle_change() -> None:
    _REACHABILITY_CACHE.clear()


def tcp_probe(host: str, port: int, timeout_s: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except (OSError, ValueError):
        return False


def probe_platform_reachability(platform: str, *, timeout_s: float = 2.0) -> bool:
    addr = docker_assets.host_reachability_endpoint(platform)
    if addr is None:
        return True
    host, _, port_s = addr.partition(":")
    try:
        port = int(port_s)
    except ValueError:
        return True
    return tcp_probe(host, port, timeout_s=timeout_s)


def platform_is_reachable(platform: str) -> bool:
    if platform in _REACHABILITY_CACHE:
        return _REACHABILITY_CACHE[platform]
    reachable = probe_platform_reachability(platform)
    _REACHABILITY_CACHE[platform] = reachable
    return reachable


@dataclass(frozen=True)
class BenchmarkInfo:
    benchmark_id: str
    category: str
    default_scale: float
    min_scale: float | None
    scale_options: tuple[float, ...]
    supports_dataframe: bool
    surface: str = "public"


def load_benchmarks() -> dict[str, BenchmarkInfo]:
    from benchbox.core.benchmark_registry import BENCHMARK_METADATA

    out: dict[str, BenchmarkInfo] = {}
    for bid, meta in BENCHMARK_METADATA.items():
        out[bid] = BenchmarkInfo(
            benchmark_id=bid,
            category=meta.get("category", ""),
            surface=str(meta.get("surface", "public")),
            default_scale=float(meta.get("default_scale", 1.0)),
            min_scale=(float(meta["min_scale"]) if meta.get("min_scale") is not None else None),
            scale_options=tuple(float(s) for s in meta.get("scale_options", ())),
            supports_dataframe=bool(meta.get("supports_dataframe", True)),
        )
    return out


def category_group_slug(category: str) -> str:
    return "".join(ch for ch in category.lower() if ch.isalnum())


CATEGORY_GROUPS: dict[str, tuple[str, ...]] = {
    category_group_slug(category): (category,) for category in CATEGORY_ORDER
}
CATEGORY_GROUPS["all"] = tuple(CATEGORY_ORDER)


def resolve_benchmarks(
    groups: Iterable[str] = (),
    include: Iterable[str] = (),
    exclude: Iterable[str] = (),
    benchmarks: dict[str, BenchmarkInfo] | None = None,
) -> list[str]:
    if benchmarks is None:
        benchmarks = load_benchmarks()
    seen: set[str] = set()
    resolved: list[str] = []
    for group in groups:
        if group not in CATEGORY_GROUPS:
            raise ValueError(f"Unknown benchmark group {group!r}; valid: {sorted(CATEGORY_GROUPS)}")
        target_categories = set(CATEGORY_GROUPS[group])
        for bid, info in benchmarks.items():
            if info.surface == "public" and info.category in target_categories and bid not in seen:
                seen.add(bid)
                resolved.append(bid)
    for bid in include:
        if bid in benchmarks and bid not in seen:
            seen.add(bid)
            resolved.append(bid)
    excluded = set(exclude)
    return [b for b in resolved if b not in excluded]


def missing_benchmarks_from_include(
    include: Iterable[str],
    benchmarks: dict[str, BenchmarkInfo] | None = None,
) -> list[str]:
    if benchmarks is None:
        benchmarks = load_benchmarks()
    seen: set[str] = set()
    missing: list[str] = []
    for bid in include:
        if bid not in benchmarks and bid not in seen:
            seen.add(bid)
            missing.append(bid)
    return missing


def smoke_scale_for(benchmark_id: str, info: BenchmarkInfo | None = None) -> float:
    if info is None:
        info = load_benchmarks()[benchmark_id]
    if benchmark_id == "tpcds":
        return 0.01
    if info.min_scale is not None:
        return info.min_scale
    return info.default_scale


def filter_scales_by_registry(
    benchmark_id: str,
    requested_scales: Iterable[float],
    info: BenchmarkInfo | None = None,
) -> list[float]:
    if info is None:
        info = load_benchmarks()[benchmark_id]
    if not info.scale_options:
        return list(requested_scales)
    allowed = set(info.scale_options)
    return [s for s in requested_scales if s in allowed]


def uv_run_argv(platform: str) -> list[str]:
    extra = PLATFORM_UV_EXTRA.get(platform)
    if extra is not None:
        return ["uv", "run", "--extra", extra, "--"]
    return ["uv", "run", "--no-sync", "--"]


def benchbox_run_argv(
    platform: str,
    benchmark: str,
    scale: float,
    *,
    phases: str = "load,power",
    compression: str | None = None,
    extra_args: Iterable[str] = (),
    local_managed_platform: bool = False,
    quiet: bool = True,
) -> list[str]:
    argv = uv_run_argv(platform)
    argv += [
        "benchbox",
        "run",
        "--platform",
        docker_assets.platform_cli_selector(platform),
        "--benchmark",
        benchmark,
        "--scale",
        str(scale),
        "--non-interactive",
    ]
    if quiet:
        argv += ["--quiet"]
    argv += [
        "--phases",
        phases,
    ]
    if compression is not None:
        argv += ["--compression", compression]
    argv += PLATFORM_CLI_FLAGS.get(platform, [])
    argv += docker_assets.platform_extra_opts(platform)
    if local_managed_platform:
        argv += docker_assets.local_managed_platform_extra_opts(platform)
    argv += list(extra_args)
    return argv


def benchbox_run_official_argv(
    platform: str,
    benchmark: str,
    scale: float,
    *,
    phases: str,
    streams: int,
    seed: int | None = None,
    extra_args: Iterable[str] = (),
    local_managed_platform: bool = False,
    quiet: bool = True,
) -> list[str]:
    argv = uv_run_argv(platform)
    argv += [
        "benchbox",
        "run-official",
        benchmark,
        "--platform",
        docker_assets.platform_cli_selector(platform),
        "--scale",
        str(scale),
        "--phases",
        phases,
        "--streams",
        str(streams),
    ]
    if seed is not None:
        argv += ["--seed", str(seed)]
    if quiet:
        argv += ["--quiet"]
    argv += PLATFORM_CLI_FLAGS.get(platform, [])
    argv += docker_assets.platform_extra_opts(platform)
    if local_managed_platform:
        argv += docker_assets.local_managed_platform_extra_opts(platform)
    argv += list(extra_args)
    return argv
