# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from importlib.metadata import version
from pathlib import Path
from typing import Any, cast

from mcp.server.caching import CacheHint
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel.server import RequestHandler
from mcp.server.mcpserver import MCPServer
from mcp.types import DiscoverResult, RequestParams

from benchbox.core.runtime_paths import resolve_runtime_paths
from benchbox.mcp.prompts import register_all_prompts
from benchbox.mcp.resources import register_all_resources
from benchbox.mcp.security import RemoteSecurityRuntime, configure_transport_security_logging
from benchbox.mcp.telemetry import RedactedTelemetryMiddleware, TelemetrySettings, configure_telemetry
from benchbox.mcp.tools.analytics import register_analytics_tools
from benchbox.mcp.tools.benchmark import register_benchmark_tools
from benchbox.mcp.tools.discovery import register_discovery_tools
from benchbox.mcp.tools.results import register_results_tools
from benchbox.mcp.tools.visualization import register_visualization_tools

logger = logging.getLogger(__name__)

MCP_CACHE_HINTS = {
    "server/discover": CacheHint(ttl_ms=300_000, scope="public"),
    "tools/list": CacheHint(ttl_ms=300_000, scope="public"),
    "prompts/list": CacheHint(ttl_ms=300_000, scope="public"),
    "resources/list": CacheHint(ttl_ms=300_000, scope="public"),
    "resources/templates/list": CacheHint(ttl_ms=300_000, scope="public"),
    "resources/read": CacheHint(ttl_ms=0, scope="private"),
}


def _install_static_registry_capability_policy(mcp: MCPServer) -> None:
    low_level_server = mcp._lowlevel_server
    discover_entry = low_level_server.get_request_handler("server/discover")
    if discover_entry is None:  # pragma: no cover
        raise RuntimeError("MCP SDK did not register the server/discover handler")

    original_handler = cast(RequestHandler[Any, RequestParams], discover_entry.handler)

    async def static_registry_discover(
        context: ServerRequestContext[Any, Any], params: RequestParams
    ) -> DiscoverResult:
        result = cast(DiscoverResult, await original_handler(context, params))
        for capability in (result.capabilities.prompts, result.capabilities.resources, result.capabilities.tools):
            if capability is not None:
                capability.list_changed = False
        return result

    low_level_server.add_request_handler("server/discover", discover_entry.params_type, static_registry_discover)


def _resolve_log_level(
    explicit_log_level: str | int | None,
    *,
    env: Mapping[str, str] | None = None,
) -> int:
    if isinstance(explicit_log_level, int):
        return explicit_log_level

    source = explicit_log_level
    if source is None:
        env_map = env if env is not None else os.environ
        source = env_map.get("BENCHBOX_LOG_LEVEL")

    if source is None:
        return logging.INFO

    if isinstance(source, str):
        stripped = source.strip()
        if stripped.isdigit():
            return int(stripped)
        resolved = logging.getLevelName(stripped.upper())
        if isinstance(resolved, int):
            return resolved

    return logging.INFO


def _configure_benchbox_logging(level: int) -> None:
    benchbox_logger = logging.getLogger("benchbox")
    benchbox_logger.setLevel(level)
    if not benchbox_logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        benchbox_logger.addHandler(handler)


def create_benchbox_server(
    *,
    results_dir: str | Path | None = None,
    charts_dir: str | Path | None = None,
    log_level: str | int | None = None,
    env: Mapping[str, str] | None = None,
    remote_security: RemoteSecurityRuntime | None = None,
) -> MCPServer:
    from benchbox.utils.printing import set_quiet

    set_quiet(True)
    _configure_benchbox_logging(_resolve_log_level(log_level, env=env))

    if results_dir is not None and charts_dir is not None:
        resolved_results_dir = Path(results_dir)
        resolved_charts_dir = Path(charts_dir)
    else:
        runtime_paths = resolve_runtime_paths(
            results_dir=results_dir,
            charts_dir=charts_dir,
            env=env,
        )
        resolved_results_dir = runtime_paths.results_dir
        resolved_charts_dir = runtime_paths.charts_dir

    environment = env if env is not None else os.environ
    configure_telemetry(TelemetrySettings.from_env(environment))
    middleware: list[Any] = [RedactedTelemetryMiddleware()]
    server_kwargs: dict[str, object] = {"cache_hints": MCP_CACHE_HINTS}
    job_runtime = None
    if remote_security is not None:
        from benchbox.mcp.jobs import DurableJobRuntime

        job_runtime = DurableJobRuntime.create(
            remote_security.config.state_db,
            remote_security.config.jobs,
            remote_security.workspaces,
        )
        configure_transport_security_logging()
        middleware.append(remote_security.middleware)
        server_kwargs.update(
            auth=remote_security.auth_settings(),
            token_verifier=remote_security.verifier,
            lifespan=job_runtime.lifespan,
        )
    server_kwargs["middleware"] = middleware

    mcp = MCPServer(
        "benchbox",
        version=version("benchbox"),
        instructions="""BenchBox is a SQL benchmarking framework for OLAP databases.

Use these tools to:
1. **Discover** available platforms and benchmarks (list_available, get_benchmark_info)
2. **Run** benchmarks with specific configurations (run_benchmark with dry_run/validate_only flags)
3. **Analyze** results and compare runs (get_results, analyze_results)
4. **Visualize** results with BenchBox semantic charts (generate_chart, suggest_charts)

Start by listing available benchmarks or platforms to see what's possible.
For a quick test, try: run_benchmark(platform="duckdb", benchmark="tpch", scale_factor=0.01)
Then visualize: generate_chart(result_files="<result_file>", chart_type="performance_bar")
BenchBox chart IDs are result-aware semantic IDs such as performance_bar,
power_bar, and query_heatmap. They are separate from raw textcharts primitive
tool names such as textcharts_bar or textcharts_heatmap, which belong to the
external textcharts MCP server if that server is configured separately.

To capture query execution plans, use the capture_plans parameter:
  run_benchmark(platform="datafusion", benchmark="tpch", capture_plans=True)
Then inspect plans: get_query_plan(result_file="...", query_id="19")
""",
        **server_kwargs,
    )

    results_provider = (
        remote_security.workspaces.current_results_dir if remote_security is not None else resolved_results_dir
    )
    charts_provider = (
        remote_security.workspaces.current_charts_dir if remote_security is not None else resolved_charts_dir
    )

    logger.info("Registering discovery tools...")
    register_discovery_tools(mcp)

    logger.info("Registering benchmark execution tools...")
    register_benchmark_tools(
        mcp,
        results_dir=results_provider,
        allow_synchronous_execution=remote_security is None,
        anonymize_results=remote_security is not None,
    )
    if job_runtime is not None:
        from benchbox.mcp.jobs import register_durable_job_tools

        register_durable_job_tools(mcp, job_runtime)

    logger.info("Registering results tools...")
    register_results_tools(mcp, results_dir=results_provider)

    logger.info("Registering analytics tools...")
    register_analytics_tools(
        mcp,
        results_dir=results_provider,
        anonymize_results=remote_security is not None,
    )

    logger.info("Registering visualization tools...")
    register_visualization_tools(
        mcp,
        results_dir=results_provider,
        charts_dir=charts_provider,
    )

    logger.info("Registering resources...")
    register_all_resources(mcp, results_dir=results_provider)

    logger.info("Registering prompts...")
    register_all_prompts(mcp)
    _install_static_registry_capability_policy(mcp)

    logger.info(
        "MCP path configuration: results_dir=%s charts_dir=%s",
        "tenant-scoped" if remote_security is not None else resolved_results_dir,
        "tenant-scoped" if remote_security is not None else resolved_charts_dir,
    )
    logger.info("BenchBox MCP server configured successfully")

    return mcp
