# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from benchbox.core.benchmark_registry import (
    get_all_benchmarks,
    get_benchmark_surface,
    get_public_benchmark_class,
)
from benchbox.core.constants import VALID_PHASES
from benchbox.core.hooks.platform_hooks import PlatformHookRegistry
from benchbox.core.results.exporter import ResultExporter
from benchbox.core.results.schema import build_result_payload
from benchbox.mcp.errors import (
    ErrorCode,
    make_error,
    make_execution_error,
    make_not_found_error,
    make_unsupported_mode_error,
)
from benchbox.mcp.schemas import (
    MCPValidationError,
    resolve_clickhouse_connection_profile,
    validate_execution_engine,
    validate_phases,
    validate_platform_options,
)
from benchbox.mcp.security import PathProvider, resolve_path_provider
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.path_utils import get_benchmark_runs_datagen_path
from benchbox.utils.printing import get_quiet_console, silence_output

logger = logging.getLogger(__name__)

RUN_BENCHMARK_ANNOTATIONS = ToolAnnotations(
    title="Execute benchmark",
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)

QUERY_DETAILS_ANNOTATIONS = ToolAnnotations(
    title="Get query details",
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


def _build_query_details_benchmark_info(benchmark: str, meta: dict[str, Any]) -> dict[str, Any]:
    info = {
        "display_name": meta.get("display_name", benchmark),
        "category": meta.get("category", "unknown"),
    }
    if get_benchmark_surface(benchmark) == "public":
        info["support_status"] = meta["support_status"]
    return info


def _get_platform_adapter(platform: str, mode: str | None = None, **config):
    from benchbox.platforms import get_dataframe_adapter, get_platform_adapter, is_dataframe_platform

    platform_lower = platform.lower()
    use_dataframe = mode == "dataframe" or is_dataframe_platform(platform_lower)

    if use_dataframe:
        df_config = {k: v for k, v in config.items() if k not in ("benchmark", "scale_factor")}
        return get_dataframe_adapter(platform_lower, **df_config)
    else:
        return get_platform_adapter(platform_lower, **config)


def register_benchmark_tools(
    mcp: MCPServer,
    *,
    results_dir: PathProvider,
    allow_synchronous_execution: bool = True,
    anonymize_results: bool = False,
) -> None:

    @mcp.tool(
        description="Run a benchmark on a database platform.\n\n        Args:\n            platform: Target platform (duckdb, polars-df, snowflake, etc.)\n            benchmark: Benchmark to run (tpch, tpcds, tpcds_obt, ssb, joinorder, clickbench, nyctaxi, tsbs_devops, h2odb, amplab, coffeeshop, tpch_skew, datavault, tpcdi, write_primitives, read_primitives, and more)\n            scale_factor: Data scale factor (0.01 for testing, 1+ for production; joinorder uses canonical IMDb 2013 data and only accepts 1.0)\n            queries: Comma-separated query IDs to run (e.g., \"1,3,6\")\n            phases: Comma-separated phases (default: \"load,power\")\n            mode: Execution mode: 'sql', 'dataframe', or 'data_only'\n            capture_plans: Capture query execution plans (3-8%% overhead). Supported: DuckDB, PostgreSQL, DataFusion.\n            dry_run: Preview execution plan without running\n            validate_only: Validate configuration without running\n            link_probe: Measure post-benchmark statement overhead (6 metered SELECT 1 statements on billable warehouses). Set false to skip; equivalent to the CLI --no-link-probe flag.\n            platform_options: Bounded, non-secret platform settings approved for the selected platform.\n\n        Returns:\n            Benchmark results, dry-run preview, or validation status.\n\n        Platform options are a deliberately smaller MCP contract than the CLI\n        ``--platform-option`` surface. Only bounded, non-secret execution\n        settings are accepted; credentials, endpoints, paths, and package\n        installation controls must remain server configuration.\n\n        JoinOrder note:\n            The public joinorder benchmark downloads and verifies the canonical IMDb 2013\n            Parquet archive on first use, then reuses BENCHBOX_OUTPUT_DIR/benchmark_runs/datagen/joinorder_sf1/.\n        ",
        annotations=RUN_BENCHMARK_ANNOTATIONS,
    )
    def run_benchmark(
        platform: str,
        benchmark: str,
        scale_factor: float = 0.01,
        queries: str | None = None,
        phases: str | None = None,
        mode: str | None = None,
        execution_engine: str = "default",
        capture_plans: bool = False,
        dry_run: bool = False,
        validate_only: bool = False,
        link_probe: bool = True,
        platform_options: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        try:
            normalized_platform_options = validate_platform_options(platform, platform_options)
            resolved_execution_engine = validate_execution_engine(platform, execution_engine)
        except MCPValidationError as exc:
            response = make_error(ErrorCode.VALIDATION_ERROR, str(exc), details={"platform": platform})
            response["status"] = "failed"
            return response

        try:
            phases = validate_phases(phases)
        except MCPValidationError as exc:
            response = make_error(
                ErrorCode.VALIDATION_INVALID_PHASE, str(exc), details={"valid_phases": list(VALID_PHASES)}
            )
            response["status"] = "failed"
            return response

        if validate_only:
            return _validate_config_impl(platform, benchmark, scale_factor, mode)

        if dry_run:
            return _dry_run_impl(platform, benchmark, scale_factor, queries, mode)

        if not allow_synchronous_execution:
            response = make_error(
                ErrorCode.VALIDATION_ERROR,
                "Remote benchmark execution requires start_benchmark",
                details={"replacement_tool": "start_benchmark"},
            )
            response["status"] = "failed"
            return response

        return _run_benchmark_impl(
            platform,
            benchmark,
            scale_factor,
            queries,
            phases,
            mode,
            capture_plans,
            link_probe=link_probe,
            execution_engine=resolved_execution_engine,
            platform_options=normalized_platform_options,
            results_dir=resolve_path_provider(results_dir),
            anonymize=anonymize_results,
        )

    @mcp.tool(
        description="Get detailed information about a specific query.\n\n        Args:\n            benchmark: Benchmark name (tpch, tpcds, ssb, clickbench, nyctaxi, tsbs_devops, h2odb, amplab, coffeeshop, tpch_skew, datavault, and more)\n            query_id: Query identifier (e.g., '1', 'Q1', '17')\n            platform: Target platform for dialect translation\n            mode: Execution mode: 'sql' or 'dataframe'\n\n        Returns:\n            Query details including SQL text or DataFrame source code.\n        ",
        annotations=QUERY_DETAILS_ANNOTATIONS,
    )
    def get_query_details(
        benchmark: str,
        query_id: str,
        platform: str | None = None,
        mode: str | None = None,
    ) -> dict[str, Any]:
        benchmark_lower = benchmark.lower()
        all_benchmarks = get_all_benchmarks()

        if benchmark_lower not in all_benchmarks:
            return make_not_found_error("benchmark", benchmark, available=list(all_benchmarks.keys()))

        try:
            resolved_mode = _resolve_query_details_mode(platform, mode)
            normalized_id = query_id.upper().lstrip("Q")
            if not normalized_id.isdigit():
                normalized_id = query_id

            meta = all_benchmarks[benchmark_lower]

            response: dict[str, Any] = {
                "benchmark": benchmark_lower,
                "query_id": query_id,
                "normalized_id": normalized_id,
                "execution_mode": resolved_mode,
            }

            if platform:
                response["platform"] = platform.lower()

            if resolved_mode == "dataframe":
                _populate_dataframe_query_details(response, benchmark_lower, normalized_id, platform)
            else:
                _populate_sql_query_details(response, benchmark_lower, normalized_id, platform)

            from benchbox.core.query_hints import get_query_complexity_hints as _core_hints

            response["complexity_hints"] = _core_hints(benchmark_lower, normalized_id)
            response["benchmark_info"] = _build_query_details_benchmark_info(benchmark_lower, meta)

            return response

        except Exception as e:
            return make_error(
                ErrorCode.INTERNAL_ERROR,
                f"Failed to get query details: {e}",
                details={"benchmark": benchmark, "query_id": query_id, "exception_type": type(e).__name__},
            )


def _make_failed_response(error_response: dict[str, Any], execution_id: str) -> dict[str, Any]:
    error_response["execution_id"] = execution_id
    error_response["status"] = "failed"
    return error_response


def _resolve_mcp_mode_with_registry(platform: str, mode: str | None):
    from benchbox.core.run_service import resolve_mode_with_registry

    resolved_mode, mode_error = resolve_mode_with_registry(platform, mode)
    if mode_error is None:
        return resolved_mode, None
    return resolved_mode, make_unsupported_mode_error(platform, mode_error.mode, list(mode_error.supported))


def _export_and_build_payload(
    result: Any,
    execution_id: str,
    results_dir: Path,
    *,
    anonymize: bool,
) -> tuple[str | None, dict[str, Any] | None]:
    result_file_path = None
    result_payload: dict[str, Any] | None = None
    try:
        result.execution_id = execution_id
        exporter = ResultExporter(
            output_dir=results_dir,
            anonymize=anonymize,
            console=get_quiet_console(),
        )
        exported_files = exporter.export_result(result, formats=["json"])
        if "json" in exported_files:
            result_file_path = str(exported_files["json"])
            with open(result_file_path, encoding="utf-8") as handle:
                result_payload = json.load(handle)
    except Exception as export_err:
        logger.warning("Failed to export benchmark results (%s)", type(export_err).__name__)
        try:
            result_payload = build_result_payload(result)
        except Exception as payload_err:
            logger.warning("Failed to build benchmark payload (%s)", type(payload_err).__name__)
    return result_file_path, result_payload


def _attach_summary_charts(response: dict[str, Any], result: Any) -> None:
    if not result or not result.query_results:
        return
    try:
        from benchbox.core.visualization.post_run_summary import generate_post_run_summary

        summary = generate_post_run_summary(result)
        if summary.charts:
            response["summary_charts"] = {
                "summary_box": summary.summary_box,
                "query_histogram": summary.query_histogram,
            }
    except Exception:
        logger.debug("Failed to generate post-run summary charts", exc_info=True)


def _execute_mcp_run_via_core(
    *,
    platform: str,
    benchmark: str,
    benchmark_class: Any,
    scale_factor: float,
    queries: str | None,
    phases: list[str],
    resolved_mode: str,
    capture_plans: bool,
    execution_engine: str = "default",
    link_probe: bool = True,
    normalized_platform_options: Mapping[str, object],
    results_dir: Path,
    execution_id: str,
    start_time: float,
    anonymize: bool,
) -> dict[str, Any]:
    from benchbox.core.run_service import (
        SilentVerbosity,
        begin_run,
        execute_run,
        map_phases_to_execution_type,
        translate_platform_options_for_adapter,
    )
    from benchbox.core.schemas import BenchmarkConfig, DatabaseConfig, ExecutionContext
    from benchbox.core.system import SystemProfiler

    begin_run()
    try:
        resolved_execution_engine = validate_execution_engine(platform, execution_engine)
    except MCPValidationError as exc:
        return _make_failed_response(
            make_error(ErrorCode.VALIDATION_ERROR, str(exc), details={"platform": platform}),
            execution_id,
        )

    data_only = resolved_mode == "data_only"
    data_dir = get_benchmark_runs_datagen_path(benchmark, scale_factor, results_dir / "datagen") if data_only else None
    benchmark_instance = (
        benchmark_class(scale_factor=scale_factor, output_dir=data_dir)
        if data_only
        else benchmark_class(scale_factor=scale_factor)
    )

    adapter_options: dict[str, object] = {}
    if not data_only:
        try:
            adapter_input = dict(normalized_platform_options)
            platform_name = platform.lower().removesuffix("-df")
            if (
                platform_name in {"clickhouse", "clickhouse-server"}
                and "connection_profile" in normalized_platform_options
            ):
                profile = resolve_clickhouse_connection_profile(str(normalized_platform_options["connection_profile"]))
                adapter_input["port"] = profile["port"]
                adapter_input["secure"] = profile["secure"]
                adapter_input.pop("connection_profile", None)
            adapter_options = translate_platform_options_for_adapter(platform, adapter_input)
        except MCPValidationError as exc:
            return _make_failed_response(
                make_error(ErrorCode.VALIDATION_ERROR, str(exc), details={"platform": platform}),
                execution_id,
            )

    query_subset = [query.strip() for query in queries.split(",")] if queries else None
    execution_context = ExecutionContext(
        entry_point="mcp",
        phases=phases,
        query_subset=query_subset,
        mode=resolved_mode if resolved_mode in ("sql", "dataframe") else "sql",
    )
    all_benchmarks = get_all_benchmarks()
    metadata = all_benchmarks[benchmark]
    benchmark_config = BenchmarkConfig(
        name=benchmark,
        display_name=metadata.get("display_name", benchmark.upper()),
        scale_factor=scale_factor,
        queries=query_subset,
        capture_plans=capture_plans,
        link_probe=link_probe,
    )
    benchmark_config.test_execution_type = map_phases_to_execution_type(phases)
    if "statistics" in phases:
        benchmark_config.options = dict(benchmark_config.options or {})
        benchmark_config.options["gather_statistics"] = True
        benchmark_config.options["statistics_benchmark_name"] = benchmark

    database_config = None
    if not data_only:
        database_config = DatabaseConfig(
            type=platform.lower(),
            name=f"mcp_{platform.lower()}",
            execution_mode=resolved_mode,
            execution_engine=resolved_execution_engine,
            options=dict(adapter_options),
        )
    profiler = SystemProfiler()
    system_profile = profiler.get_system_profile()
    from benchbox.core.platform_config import get_platform_config

    platform_config = None
    if database_config is not None:
        platform_config = get_platform_config(
            database_config,
            system_profile,
            benchmark_name=benchmark,
            scale_factor=scale_factor,
            tuning_config=benchmark_config.options.get("unified_tuning_configuration")
            if benchmark_config.options
            else None,
        )

    def adapter_factory(*, execution_mode, output_root, phases):
        if database_config is None:
            return None
        if not (phases.load or phases.execute):
            return None
        if execution_mode == "dataframe":
            registered = PlatformHookRegistry.list_option_specs(database_config.type)
            dataframe_options = (
                {key: value for key, value in database_config.options.items() if key in registered}
                if registered
                else dict(database_config.options)
            )
            from benchbox.platforms import get_adapter

            return get_adapter(
                database_config.type,
                mode="dataframe",
                working_dir=output_root,
                verbose=False,
                very_verbose=False,
                tuning_config=benchmark_config.options.get("df_tuning_config") if benchmark_config.options else None,
                execution_engine=database_config.execution_engine,
                **dataframe_options,
            )

        adapter_kwargs = dict(platform_config or {})
        adapter_kwargs.update(adapter_options)
        adapter_kwargs.pop("benchmark", None)
        adapter_kwargs.pop("scale_factor", None)
        adapter = _get_platform_adapter(
            database_config.type,
            mode=execution_mode,
            benchmark=benchmark,
            scale_factor=scale_factor,
            **adapter_kwargs,
        )
        if adapter is not None:
            adapter.benchmark_instance = benchmark_instance
            adapter.scale_factor = scale_factor
        return adapter

    with silence_output(enabled=True):
        result = execute_run(
            config=benchmark_config,
            benchmark_instance=benchmark_instance,
            database_config=database_config,
            system_profile=system_profile,
            platform_config=platform_config,
            output_root=benchmark_instance.output_dir,
            phases_to_run=phases,
            adapter_factory=adapter_factory,
            verbosity=SilentVerbosity(),
            monitor=None,
            execution_context=execution_context,
        )
    if result is not None:
        result.execution_context = execution_context.model_dump()

    if data_only and result is not None and data_dir is not None:
        return _build_data_only_response(
            benchmark=benchmark,
            scale_factor=scale_factor,
            execution_id=execution_id,
            start_time=start_time,
            data_dir=data_dir,
        )

    return _build_run_response(
        result,
        execution_id=execution_id,
        results_dir=results_dir,
        anonymize=anonymize,
        start_time=start_time,
        platform=platform,
        benchmark=benchmark,
        scale_factor=scale_factor,
        resolved_mode=resolved_mode,
    )


def _result_outstanding_work(result: Any) -> dict[str, Any] | None:
    throughput = getattr(getattr(result, "execution_phases", None), "throughput_test", None)
    outstanding_work = getattr(throughput, "outstanding_work", None)
    return dict(outstanding_work) if isinstance(outstanding_work, Mapping) and outstanding_work else None


def _build_run_response(
    result: Any,
    *,
    execution_id: str,
    results_dir: Path,
    anonymize: bool,
    start_time: float,
    platform: str,
    benchmark: str,
    scale_factor: float,
    resolved_mode: str,
) -> dict[str, Any]:
    execution_time = elapsed_seconds(start_time)
    result_file_path, result_payload = (
        _export_and_build_payload(result, execution_id, results_dir, anonymize=anonymize) if result else (None, None)
    )
    response: dict[str, Any] = result_payload or {}
    export_failed = bool(result) and result_file_path is None
    if export_failed:
        outstanding_work = _result_outstanding_work(result)
        if outstanding_work is not None:
            response["phases"] = {"throughput_test": {"outstanding_work": outstanding_work}}
    response["mcp_metadata"] = {
        "execution_id": execution_id,
        "status": "incomplete" if export_failed else ("completed" if result else "no_results"),
        "platform_requested": platform,
        "benchmark_requested": benchmark,
        "scale_factor_requested": scale_factor,
        "execution_mode": resolved_mode,
        "execution_time_seconds": round(execution_time, 2),
        "result_file": result_file_path,
    }
    _attach_summary_charts(response, result)
    return response


def _run_benchmark_impl(
    platform: str,
    benchmark: str,
    scale_factor: float,
    queries: str | None,
    phases: str | None,
    mode: str | None,
    capture_plans: bool = False,
    link_probe: bool = True,
    *,
    execution_engine: str = "default",
    platform_options: Mapping[str, object] | None = None,
    results_dir: Path,
    execution_id: str | None = None,
    anonymize: bool,
) -> dict[str, Any]:
    execution_id = execution_id or f"mcp_{uuid.uuid4().hex[:8]}"
    start_time = mono_time()

    try:
        normalized_platform_options = validate_platform_options(platform, platform_options)
        resolved_execution_engine = validate_execution_engine(platform, execution_engine)
        benchmark_lower = benchmark.lower()
        all_benchmarks = get_all_benchmarks()

        if benchmark_lower not in all_benchmarks:
            return _make_failed_response(
                make_not_found_error("benchmark", benchmark, available=list(all_benchmarks.keys())), execution_id
            )

        benchmark_class = get_public_benchmark_class(benchmark_lower)
        if benchmark_class is None:
            return _make_failed_response(
                make_error(
                    ErrorCode.DEPENDENCY_MISSING,
                    f"Benchmark '{benchmark}' requires additional dependencies",
                    details={"benchmark": benchmark},
                ),
                execution_id,
            )

        resolved_mode, mode_error = _resolve_mcp_mode_with_registry(platform, mode)
        if mode_error:
            return _make_failed_response(mode_error, execution_id)

        phases_list = (
            ["generate"]
            if resolved_mode == "data_only"
            else [phase.strip() for phase in (phases or "load,power").split(",")]
        )
        return _execute_mcp_run_via_core(
            platform=platform,
            benchmark=benchmark_lower,
            benchmark_class=benchmark_class,
            scale_factor=scale_factor,
            queries=queries,
            phases=phases_list,
            resolved_mode=resolved_mode,
            capture_plans=capture_plans,
            execution_engine=resolved_execution_engine,
            link_probe=link_probe,
            normalized_platform_options=normalized_platform_options,
            results_dir=results_dir,
            execution_id=execution_id,
            start_time=start_time,
            anonymize=anonymize,
        )

    except Exception as e:
        logger.error("Benchmark execution failed (%s)", type(e).__name__)
        error_response = make_execution_error(
            f"Benchmark execution failed: {e}",
            execution_id=execution_id,
            exception=e,
            retry_hint=False,
        )
        error_response["status"] = "failed"
        error_response["platform"] = platform
        error_response["benchmark"] = benchmark
        error_response["execution_time_seconds"] = round(elapsed_seconds(start_time), 2)
        return error_response


def _build_data_only_response(
    *,
    benchmark: str,
    scale_factor: float,
    execution_id: str,
    start_time: float,
    data_dir: Path,
) -> dict[str, Any]:
    generated_files = list(data_dir.glob("*.parquet"))
    if not generated_files:
        generated_files = list(data_dir.glob("*.*"))

    total_size = sum(f.stat().st_size for f in generated_files if f.is_file())
    execution_time = elapsed_seconds(start_time)

    return {
        "mcp_metadata": {
            "execution_id": execution_id,
            "status": "completed",
            "execution_mode": "data_only",
            "execution_time_seconds": round(execution_time, 2),
            "result_file": None,
        },
        "data_generation": {
            "status": "generated",
            "benchmark": benchmark,
            "scale_factor": scale_factor,
            "data_path": str(data_dir),
            "file_count": len(generated_files),
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "files": [f.name for f in generated_files[:20]],
            "files_truncated": len(generated_files) > 20,
        },
    }


def _dry_run_impl(
    platform: str,
    benchmark: str,
    scale_factor: float,
    queries: str | None,
    mode: str | None,
) -> dict[str, Any]:
    from benchbox.core.dryrun import preview_benchmark_run

    result = preview_benchmark_run(platform, benchmark, scale_factor, queries, mode)

    if result.get("status") == "error" and "not found" in str(result.get("error", "")).lower():
        benchmark_lower = benchmark.lower()
        all_benchmarks = get_all_benchmarks()
        if benchmark_lower not in all_benchmarks:
            error_response = make_not_found_error("benchmark", benchmark, available=list(all_benchmarks.keys()))
            error_response["status"] = "error"
            return error_response

    if result.get("error_code") == "VALIDATION_UNSUPPORTED_MODE":
        details = result.get("details", {})
        error_response = make_unsupported_mode_error(
            details.get("platform", platform),
            details.get("requested_mode", mode or "unknown"),
            details.get("supported_modes", []),
        )
        error_response["status"] = "error"
        return error_response

    if result.get("status") == "error" and result.get("error_code") == "INTERNAL_ERROR":
        if "error" in result and "not found" not in str(result["error"]).lower():
            error_response = make_error(
                ErrorCode.INTERNAL_ERROR,
                result.get("error", "Dry run failed"),
                details=result.get("details", {}),
            )
            error_response["status"] = "error"
            return error_response

    return result


def _validate_config_impl(
    platform: str,
    benchmark: str,
    scale_factor: float,
    mode: str | None,
) -> dict[str, Any]:
    from benchbox.core.validation.config import validate_config as _core_validate

    return _core_validate(platform, benchmark, scale_factor, mode)


def _resolve_query_details_mode(platform: str | None, mode: str | None) -> str:
    if mode is not None:
        return mode.lower()

    if platform is not None:
        from benchbox.platforms import is_dataframe_platform

        if is_dataframe_platform(platform.lower()):
            return "dataframe"

    return "sql"


def _get_dataframe_family_for_platform(platform: str | None) -> str | None:
    if platform is None:
        return None

    from benchbox import DATAFRAME_PLATFORMS

    base = platform.lower().replace("-df", "")
    info = DATAFRAME_PLATFORMS.get(base)
    if info is not None:
        return info.family.value
    return None


def _resolve_dataframe_impl(df_query: Any, family: str | None) -> tuple[Any | None, str | None]:
    if family == "expression" and df_query.expression_impl is not None:
        return df_query.expression_impl, None
    if family == "pandas" and df_query.pandas_impl is not None:
        return df_query.pandas_impl, None
    if df_query.expression_impl is not None:
        return df_query.expression_impl, "expression" if family is None else None
    if df_query.pandas_impl is not None:
        return df_query.pandas_impl, "pandas" if family is None else None
    return None, None


def _populate_dataframe_query_details(
    response: dict[str, Any],
    benchmark: str,
    normalized_id: str,
    platform: str | None,
) -> None:
    import inspect

    from benchbox.core.query_catalog import get_dataframe_query

    family = _get_dataframe_family_for_platform(platform)
    if family:
        response["dataframe_family"] = family

    df_query = get_dataframe_query(benchmark, normalized_id)
    if df_query is None:
        response["error"] = f"No DataFrame query found for {benchmark} Q{normalized_id}"
        return

    response["query_name"] = df_query.query_name
    response["description"] = df_query.description
    response["has_expression_impl"] = df_query.has_expression_impl()
    response["has_pandas_impl"] = df_query.has_pandas_impl()

    impl, resolved_family = _resolve_dataframe_impl(df_query, family)
    if resolved_family:
        response["dataframe_family"] = resolved_family

    if impl is not None:
        try:
            source = inspect.getsource(impl)
            if len(source) > 5000:
                response["source_code"] = source[:5000]
                response["source_truncated"] = True
            else:
                response["source_code"] = source
                response["source_truncated"] = False
        except OSError:
            response["source_code"] = None
            response["source_truncated"] = False


def _populate_sql_query_details(
    response: dict[str, Any],
    benchmark: str,
    normalized_id: str,
    platform: str | None,
) -> None:
    from benchbox.core.query_catalog import get_sql_render

    if get_public_benchmark_class(benchmark) is None:
        response["error"] = f"Benchmark '{benchmark}' requires additional dependencies"
        return

    dialect = None
    if platform is not None:
        from benchbox.core.config_inheritance import get_platform_family_dialect

        platform_lower = platform.lower().replace("-df", "")
        try:
            dialect = get_platform_family_dialect(platform_lower)
        except Exception:
            pass

    render = get_sql_render(benchmark, normalized_id, dialect=dialect)
    if render is None:
        return

    if len(render.sql) > 2000:
        response["sql"] = render.sql[:2000]
        response["sql_truncated"] = True
    else:
        response["sql"] = render.sql
        response["sql_truncated"] = False
