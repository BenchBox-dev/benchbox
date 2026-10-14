# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from benchbox.core.results import analytics as _core_analytics
from benchbox.core.results.query_normalizer import normalize_query_id
from benchbox.mcp.errors import ErrorCode, make_error, make_not_found_error
from benchbox.mcp.security import PathProvider, resolve_path_provider
from benchbox.mcp.tools.path_utils import resolve_result_file_path

logger = logging.getLogger(__name__)

_classify_query_changes = _core_analytics._classify_query_changes
_compute_group_stats = _core_analytics._compute_group_stats
_extract_keyed_timings = _core_analytics._extract_keyed_timings
_extract_measurement_timings = _core_analytics._extract_measurement_timings
_extract_run_identity = _core_analytics._extract_run_identity
_list_result_files = _core_analytics._list_result_files
_load_regression_runs = _core_analytics._load_regression_runs
_load_trend_data_point = _core_analytics._load_trend_data_point
_matches_filters = _core_analytics._matches_filters
_resolve_date_group_key = _core_analytics._resolve_date_group_key
_resolve_group_key = _core_analytics._resolve_group_key
_resolve_timestamp_str = _core_analytics._resolve_timestamp_str

ANALYTICS_READONLY_ANNOTATIONS = ToolAnnotations(
    title="Read-only analytics tool",
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


def _resolve_validation_directory(directory: str, results_dir: Path, *, tenant_scoped: bool) -> Path | dict[str, Any]:
    candidate = Path(directory)
    if tenant_scoped and candidate.is_absolute():
        return make_error(ErrorCode.VALIDATION_ERROR, "Absolute validation directories are not allowed")
    if not candidate.is_absolute():
        candidate = results_dir / candidate
    if tenant_scoped:
        try:
            candidate.resolve().relative_to(results_dir.resolve())
        except ValueError:
            return make_error(ErrorCode.VALIDATION_ERROR, "Validation directory escapes tenant workspace")
    return candidate


def register_analytics_tools(
    mcp: MCPServer,
    *,
    results_dir: PathProvider,
    anonymize_results: bool = False,
) -> None:
    tenant_scoped = not isinstance(results_dir, Path)

    @mcp.tool(
        description="Analyze benchmark results.\n\n        Args:\n            analysis: Analysis type: 'compare', 'regressions', 'trends', 'aggregate'\n            file1: Baseline result file (for 'compare')\n            file2: Comparison result file (for 'compare')\n            platform: Filter by platform name\n            benchmark: Filter by benchmark name\n            threshold_percent: Change threshold for regressions (default: 10%)\n            metric: Metric for trends: geometric_mean, p50, p95, p99, total_time\n            group_by: Grouping for aggregate: platform, benchmark, date\n            limit: Max runs to analyze (default: 10)\n\n        Returns:\n            Analysis results based on the selected type.\n        ",
        annotations=ANALYTICS_READONLY_ANNOTATIONS,
    )
    def analyze_results(
        analysis: str = "compare",
        file1: str | None = None,
        file2: str | None = None,
        platform: str | None = None,
        benchmark: str | None = None,
        threshold_percent: float = 10.0,
        metric: str = "geometric_mean",
        group_by: str = "platform",
        limit: int = 10,
    ) -> dict[str, Any]:
        configured_results_dir = resolve_path_provider(results_dir)
        analysis_lower = analysis.lower()

        if analysis_lower == "compare":
            if not file1 or not file2:
                return make_error(
                    ErrorCode.VALIDATION_ERROR,
                    "compare analysis requires file1 and file2 parameters",
                    suggestion="Provide both file1 and file2 for comparison",
                )
            return _compare_results_impl(
                file1, file2, threshold_percent, configured_results_dir, anonymize=anonymize_results
            )

        elif analysis_lower == "regressions":
            return _detect_regressions_impl(platform, benchmark, threshold_percent, limit, configured_results_dir)

        elif analysis_lower == "trends":
            return _get_performance_trends_impl(platform, benchmark, metric, limit, configured_results_dir)

        elif analysis_lower == "aggregate":
            return _aggregate_results_impl(platform, benchmark, group_by, configured_results_dir)

        else:
            return make_error(
                ErrorCode.VALIDATION_ERROR,
                f"Invalid analysis type: {analysis}",
                details={"valid_types": ["compare", "regressions", "trends", "aggregate"]},
            )

    @mcp.tool(
        description="Get query execution plan from benchmark results.\n\n        Args:\n            result_file: Result file containing query plans\n            query_id: Query identifier (e.g., '1', 'Q1', 'q05')\n            format: Output format: 'tree', 'json', 'summary'\n\n        Returns:\n            Query plan in the requested format.\n        ",
        annotations=ANALYTICS_READONLY_ANNOTATIONS,
    )
    def get_query_plan(
        result_file: str,
        query_id: str,
        format: str = "tree",
    ) -> dict[str, Any]:
        valid_formats = ["tree", "json", "summary"]
        format_lower = format.lower()
        if format_lower not in valid_formats:
            return make_error(
                ErrorCode.VALIDATION_INVALID_FORMAT,
                f"Invalid format: {format}",
                details={"valid_formats": valid_formats},
            )

        configured_results_dir = resolve_path_provider(results_dir)
        file_path = resolve_result_file_path(result_file, configured_results_dir)
        if file_path is None:
            return make_error(
                ErrorCode.RESOURCE_NOT_FOUND,
                f"Result file not found: {result_file}",
                details={"requested_file": result_file},
            )

        try:
            return _get_query_plan_impl(file_path, result_file, query_id, format_lower)
        except json.JSONDecodeError as e:
            return make_error(
                ErrorCode.RESOURCE_INVALID_FORMAT,
                f"Invalid JSON in result file: {e}",
                details={"file": result_file, "parse_error": str(e)},
            )
        except Exception as e:
            logger.error("Failed to get query plan (%s)", type(e).__name__)
            return make_error(
                ErrorCode.INTERNAL_ERROR,
                f"Failed to get query plan: {e}",
                details={"exception_type": type(e).__name__},
            )

    @mcp.tool(
        description="Validate integrity, completeness, and believability of result JSON files.\n\n        Provide result_file (single file path) or directory (batch mode).\n        Returns structured check results with PASS/WARN/FAIL status per check.\n\n        Args:\n            result_file: Path to a single result JSON file\n            directory: Path to a directory of result JSON files\n            verbose: Include PASS checks in output (default: WARN+FAIL only)\n\n        Returns:\n            Validation report with per-check status and overall result.\n        ",
        annotations=ANALYTICS_READONLY_ANNOTATIONS,
    )
    def validate_results(
        result_file: str = "",
        directory: str = "",
        verbose: bool = False,
    ) -> dict[str, Any]:
        from benchbox.core.results.integrity_validator import (
            validate_directory as _validate_directory,
            validate_file as _validate_file,
        )

        configured_results_dir = resolve_path_provider(results_dir)
        if result_file:
            file_path = resolve_result_file_path(result_file, configured_results_dir)
            if file_path is None or not file_path.exists():
                return make_not_found_error(
                    "result_file",
                    result_file,
                    suggestion='Use get_results(format="list") to see available result files',
                )
            report = _validate_file(file_path)
            checks = [
                {
                    "category": c.category.value,
                    "name": c.name,
                    "status": c.status.value,
                    "message": c.message,
                    "details": c.details,
                }
                for c in report.checks
                if verbose or c.status.value != "PASS"
            ]
            return {
                "file": report.file,
                "benchmark_id": report.benchmark_id,
                "platform": report.platform,
                "scale_factor": report.scale_factor,
                "overall_status": report.overall_status.value,
                "summary": report.summary,
                "checks": checks,
            }
        elif directory:
            resolved_directory = _resolve_validation_directory(
                directory,
                configured_results_dir,
                tenant_scoped=tenant_scoped,
            )
            if isinstance(resolved_directory, dict):
                return resolved_directory
            dir_path = resolved_directory
            if not dir_path.is_dir():
                return make_error(
                    ErrorCode.RESOURCE_NOT_FOUND,
                    f"Directory not found: {directory}",
                )
            reports = _validate_directory(dir_path)
            return {
                "total": len(reports),
                "pass": sum(1 for r in reports if r.overall_status.value == "PASS"),
                "warn": sum(1 for r in reports if r.overall_status.value == "WARN"),
                "fail": sum(1 for r in reports if r.overall_status.value == "FAIL"),
                "files": [
                    {
                        "file": r.file,
                        "benchmark_id": r.benchmark_id,
                        "platform": r.platform,
                        "overall_status": r.overall_status.value,
                    }
                    for r in reports
                ],
            }
        else:
            return make_error(
                ErrorCode.VALIDATION_ERROR,
                "Provide either result_file or directory parameter",
            )


def _find_query_execution(data: dict[str, Any], normalized_id: str) -> dict | None:
    for query_result in data.get("queries", []):
        qid = query_result.get("id", "")
        if normalize_query_id(qid) == normalized_id:
            return query_result
    return None


def _resolve_plans_path(file_path: Path) -> Path | None:
    plans_path = file_path.with_suffix("").with_suffix(".plans.json")
    if not plans_path.exists():
        plans_path = Path(str(file_path).replace(".json", ".plans.json"))
    return plans_path if plans_path.exists() else None


def _format_plan_response(query_plan: dict, format_lower: str, normalized_id: str, runtime_ms: Any) -> dict[str, Any]:
    response: dict[str, Any] = {
        "status": "success",
        "query_id": normalized_id,
        "runtime_ms": runtime_ms,
    }

    if format_lower == "json":
        response["plan"] = query_plan
    elif format_lower == "summary":
        response["summary"] = _extract_plan_summary(query_plan)
    else:
        response["plan_tree"] = _format_plan_tree(query_plan)

    return response


def _get_query_plan_impl(file_path: Path, result_file: str, query_id: str, format_lower: str) -> dict[str, Any]:
    with open(file_path, encoding="utf-8") as f:
        data = json.load(f)

    normalized_id = normalize_query_id(query_id)
    query_exec = _find_query_execution(data, normalized_id)

    if not query_exec:
        available_ids = [str(q.get("id", "")) for q in data.get("queries", []) if q.get("id")]
        return make_not_found_error(
            "query",
            query_id,
            available=sorted(set(available_ids))[:20],
            suggestion="Use get_query_details() with a query ID from `details.available`",
        )

    plans_path = _resolve_plans_path(file_path)
    query_info = {"runtime_ms": query_exec.get("ms"), "status": query_exec.get("status")}

    if plans_path is None:
        return {
            "status": "no_plan",
            "query_id": normalized_id,
            "message": "No query plan captured for this query",
            "suggestion": "Run benchmark with --capture-plans flag",
            "query_info": query_info,
        }

    with open(plans_path, encoding="utf-8") as plans_handle:
        plans_data = json.load(plans_handle)

    query_plan_entry = plans_data.get("queries", {}).get(normalized_id)
    if not query_plan_entry or "plan" not in query_plan_entry:
        return {
            "status": "no_plan",
            "query_id": normalized_id,
            "message": "No query plan captured for this query",
            "query_info": query_info,
        }

    return _format_plan_response(query_plan_entry["plan"], format_lower, normalized_id, query_exec.get("ms"))


def _compare_results_impl(
    file1: str,
    file2: str,
    threshold_percent: float,
    results_dir: Path,
    *,
    anonymize: bool,
) -> dict[str, Any]:
    from benchbox.core.results.analytics import compare_results as _core_compare

    path1 = resolve_result_file_path(file1, results_dir)
    path2 = resolve_result_file_path(file2, results_dir)

    if path1 is None:
        return make_error(
            ErrorCode.RESOURCE_NOT_FOUND,
            f"Baseline file not found: {file1}",
            details={"file_type": "baseline", "requested_file": file1},
        )
    if path2 is None:
        return make_error(
            ErrorCode.RESOURCE_NOT_FOUND,
            f"Comparison file not found: {file2}",
            details={"file_type": "comparison", "requested_file": file2},
        )

    comparison = _core_compare(path1, path2, threshold_percent, anonymize=anonymize)
    if comparison.get("error_code") == "VALIDATION_ERROR":
        return make_error(ErrorCode.VALIDATION_ERROR, comparison["error"], details=comparison.get("details", {}))
    if "error" in comparison:
        return make_error(
            ErrorCode.INTERNAL_ERROR,
            comparison.get("error", "Failed to compare results"),
            details={
                "baseline_loaded": comparison.get("baseline_loaded"),
                "current_loaded": comparison.get("current_loaded"),
            },
        )

    return comparison


def _detect_regressions_impl(
    platform: str | None,
    benchmark: str | None,
    threshold_percent: float,
    lookback_runs: int,
    results_dir: Path,
) -> dict[str, Any]:
    from benchbox.core.results.analytics import detect_regressions as _core_detect

    return _core_detect(results_dir, platform, benchmark, threshold_percent, lookback_runs)


def _get_performance_trends_impl(
    platform: str | None,
    benchmark: str | None,
    metric: str,
    limit: int,
    results_dir: Path,
) -> dict[str, Any]:
    from benchbox.core.results.analytics import get_performance_trends as _core_trends

    result = _core_trends(results_dir, platform, benchmark, metric, limit)
    if "error" in result and result.get("error_code") == "VALIDATION_ERROR":
        return make_error(
            ErrorCode.VALIDATION_ERROR,
            result["error"],
            details=result.get("details", {}),
        )
    return result


def _aggregate_results_impl(
    platform: str | None,
    benchmark: str | None,
    group_by: str,
    results_dir: Path,
) -> dict[str, Any]:
    from benchbox.core.results.analytics import aggregate_results as _core_aggregate

    result = _core_aggregate(results_dir, platform, benchmark, group_by)
    if "error" in result and result.get("error_code") == "VALIDATION_ERROR":
        return make_error(
            ErrorCode.VALIDATION_ERROR,
            result["error"],
            details=result.get("details", {}),
        )
    return result


def _extract_plan_summary(plan: dict) -> dict[str, Any]:
    summary = {
        "operator_count": 0,
        "estimated_rows": None,
        "estimated_cost": None,
        "join_count": 0,
        "scan_count": 0,
    }

    def count_operators(node: dict | list) -> None:
        if isinstance(node, dict):
            _update_plan_summary(summary, node)
            for value in node.values():
                count_operators(value)
            return
        if isinstance(node, list):
            for item in node:
                count_operators(item)

    count_operators(plan)
    return summary


def _update_plan_summary(summary: dict[str, Any], node: dict[str, Any]) -> None:
    summary["operator_count"] += 1
    op_type = node.get("type", node.get("operator", "")).lower()
    if "join" in op_type:
        summary["join_count"] += 1
    if "scan" in op_type or "read" in op_type:
        summary["scan_count"] += 1
    if summary["estimated_rows"] is None and "rows" in node:
        summary["estimated_rows"] = node["rows"]
    if summary["estimated_cost"] is None and "cost" in node:
        summary["estimated_cost"] = node["cost"]


def _format_plan_tree(plan: dict, indent: int = 0) -> str:
    lines = []
    prefix = "  " * indent

    if isinstance(plan, dict):
        op_type = plan.get("type") or plan.get("operator") or plan.get("name") or "Node"
        lines.append(f"{prefix}├── {op_type}")

        for key in ["table", "alias", "condition", "rows", "cost"]:
            if key in plan:
                lines.append(f"{prefix}│   {key}: {plan[key]}")

        children = plan.get("children") or plan.get("inputs") or plan.get("plans") or []
        if isinstance(children, list):
            for child in children:
                lines.append(_format_plan_tree(child, indent + 1))
        elif isinstance(children, dict):
            lines.append(_format_plan_tree(children, indent + 1))

    return "\n".join(lines)
