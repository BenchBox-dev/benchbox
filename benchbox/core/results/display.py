# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from typing import Any

from benchbox.utils import format_duration
from benchbox.utils.printing import emit


def display_results(result_data: dict[str, Any], verbosity: int = 0) -> None:
    benchmark_name = result_data.get("benchmark", "unknown").upper()
    emit(f"Benchmark: {benchmark_name}")
    emit(f"Scale Factor: {result_data.get('scale_factor', 'unknown')}")
    emit(f"Platform: {result_data.get('platform', 'unknown')}")

    success = result_data.get("success", False)
    emit(f"\nBenchmark Status: {'PASSED' if success else 'FAILED'}")

    if verbosity > 0 and "total_duration" in result_data:
        emit(f"Total Duration: {format_duration(result_data['total_duration'])}")
        if "schema_creation_time" in result_data:
            emit(f"Schema Creation: {format_duration(result_data['schema_creation_time'])}")
        if "data_loading_time" in result_data:
            emit(f"Data Loading: {format_duration(result_data['data_loading_time'])}")

    if "successful_queries" in result_data:
        emit(f"Queries: {result_data['successful_queries']}/{result_data['total_queries']} successful")

    if success and "total_execution_time" in result_data:
        emit(f"Query Execution Time: {format_duration(result_data['total_execution_time'])}")
        if "average_query_time" in result_data:
            emit(f"Average Query Time: {format_duration(result_data['average_query_time'])}")

    if success:
        emit(f"\n✅ {benchmark_name} benchmark completed!")
    else:
        emit(f"\n❌ {benchmark_name} benchmark failed.")


def display_platform_list(platforms: dict[str, bool], get_requirements_func=None) -> None:
    emit("Available platforms:")
    for platform, available in platforms.items():
        status = "✅ Available" if available else "❌ Not available"
        if not available and get_requirements_func:
            requirements = get_requirements_func(platform)
            status += f" ({requirements})"
        emit(f"  {platform}: {status}")


def display_benchmark_list(benchmark_classes: dict[str, Any]) -> None:
    from benchbox.core.benchmark_registry import get_benchmark_metadata

    emit("Available benchmarks:")
    for benchmark_name in sorted(benchmark_classes.keys()):
        benchmark_class = benchmark_classes[benchmark_name]
        metadata = get_benchmark_metadata(benchmark_name)
        description = metadata.get("description") if metadata else None
        if not isinstance(description, str) or not description:
            description = getattr(benchmark_class, "description", None)
        if not isinstance(description, str) or not description:
            description = "No description available"
        emit(f"  {benchmark_name}: {description}")


def display_configuration_summary(config: dict[str, Any], verbosity: int = 0) -> None:
    if verbosity == 0:
        return

    emit(f"Benchmark: {config['benchmark'].upper()} @ SF{config['scale_factor']} on {config['platform'].title()}")
    emit(f"Phases: {config.get('phases', 'unknown')}")

    if verbosity > 0:
        tuning_mode = config.get("tuning_mode", "unknown")
        emit(f"Tuning mode: {tuning_mode}")

        if config.get("tuning_config"):
            tuning_type = config["tuning_config"].get("_metadata", {}).get("configuration_type", "unknown")
            emit(f"Tuning type: {tuning_type}")


def display_verbose_config_feedback(config: dict[str, Any], platform: str) -> None:
    if platform in ["duckdb", "sqlite"]:
        db_path = config.get("database_path", "unknown")
        emit(f"Database file: {db_path}")
    elif platform == "databricks":
        schema_name = config.get("schema", "unknown")
        catalog_name = config.get("catalog", "unknown")
        emit(f"Database schema: {catalog_name}.{schema_name}")
    elif platform == "bigquery":
        dataset_id = config.get("dataset_id", "unknown")
        project_id = config.get("project_id", "unknown")
        emit(f"BigQuery dataset: {project_id}.{dataset_id}")
    elif platform in ["redshift", "snowflake"]:
        database_name = config.get("database", "unknown")
        emit(f"Database name: {database_name}")
    elif platform in {"clickhouse", "clickhouse-local"}:
        data_path = config.get("data_path", "unknown")
        emit(f"Data path: {data_path}")


def print_phase_header(phase: str) -> None:
    emit(f"\n--- Running Phase: {phase.title()} ---")


def print_completion_message(phase: str, output_location: str | None = None) -> None:
    if output_location:
        emit(f"✅ {phase.title()} phase complete in {output_location}")
    else:
        emit(f"✅ {phase.title()} phase complete.")


def _format_metric(value, suffix):
    try:
        return f"{float(value):.2f} {suffix}"
    except (TypeError, ValueError):
        return f"{value} {suffix}" if value is not None else None


def _print_dry_run_artifacts(saved_files) -> None:
    if saved_files:
        emit("Artifacts:")
        for label, path in sorted(saved_files.items()):
            emit(f"  • {label}: {path}")
    else:
        emit("Artifacts: JSON, YAML, and per-query SQL files emitted.")


def _print_benchmark_overview(benchmark_info: dict, preview: dict, query_count: int) -> None:
    emit("\nBenchmark Overview:")
    display_name = benchmark_info.get("display_name") or benchmark_info.get("name", "unknown").upper()
    emit(f"  • Benchmark: {display_name}")
    if "scale_factor" in benchmark_info:
        emit(f"  • Scale factor: {benchmark_info['scale_factor']}")
    execution_context = (
        preview.get("execution_context") or benchmark_info.get("test_execution_type") or "standard execution"
    )
    emit(f"  • Execution mode: {execution_context}")
    emit(f"  • Queries prepared: {query_count}")
    if preview.get("estimated_time"):
        emit(f"  • Estimated run time: {preview['estimated_time']}")
    if preview.get("data_size_mb"):
        emit(f"  • Estimated data size: {preview['data_size_mb']} MB")


def _print_platform_summary(platform_info: dict) -> None:
    if not platform_info:
        return
    emit("\nPlatform Summary:")
    platform_name = platform_info.get("platform_name") or platform_info.get("platform_type")
    if platform_name:
        emit(f"  • Platform: {platform_name}")
    connection_mode = platform_info.get("connection_mode") or platform_info.get("connection_type")
    if connection_mode:
        emit(f"  • Connection: {connection_mode}")
    configuration = platform_info.get("configuration") or {}
    interesting_keys = ["database_path", "memory_limit", "schema", "catalog", "staging_root"]
    for key in interesting_keys:
        if key in configuration and configuration[key] is not None:
            emit(f"  • {key.replace('_', ' ').title()}: {configuration[key]}")


def _print_resource_estimates(resources: dict) -> None:
    if not resources:
        return
    emit("\nResource Estimates:")
    data_size = _format_metric(resources.get("estimated_data_size_mb"), "MB")
    if data_size:
        emit(f"  • Estimated data size: {data_size}")
    memory_usage = _format_metric(resources.get("estimated_memory_usage_mb"), "MB")
    if memory_usage:
        emit(f"  • Estimated memory usage: {memory_usage}")
    runtime = _format_metric(resources.get("estimated_runtime_minutes"), "minutes")
    if runtime:
        emit(f"  • Estimated runtime: {runtime}")
    if resources.get("cpu_cores_available") is not None:
        emit(f"  • Cores available: {resources['cpu_cores_available']}")


def print_dry_run_summary(result, output_dir, saved_files=None) -> None:
    benchmark_info = result.benchmark_config or {}
    preview = result.query_preview or {}
    platform_info = result.platform_config or {}
    resources = result.estimated_resources or {}
    query_count = preview.get("query_count") or len(result.queries) or 0

    emit("✅ Dry run completed successfully!")
    emit(f"Artifacts directory: {output_dir}")

    _print_dry_run_artifacts(saved_files)
    _print_benchmark_overview(benchmark_info, preview, query_count)
    _print_platform_summary(platform_info)
    deployment = getattr(result, "deployment", None)
    if deployment:
        emit(f"  • Deployment: {deployment}")
    _print_resource_estimates(resources)

    if result.warnings:
        emit("\nWarnings:")
        for warning in result.warnings:
            emit(f"  • {warning}")
