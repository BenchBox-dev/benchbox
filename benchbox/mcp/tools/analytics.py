# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from benchbox.core.query_plans.insights import compute_complexity_score
from benchbox.core.query_plans.visualization import render_plan
from benchbox.core.results import analytics as _core_analytics
from benchbox.core.results.loader import _index_plan_entries
from benchbox.core.results.query_normalizer import normalize_query_id
from benchbox.core.results.query_plan_models import QueryPlanDAG
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
        stream: str | None = None,
        test_type: str | None = None,
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
            return _get_query_plan_impl(file_path, result_file, query_id, format_lower, stream, test_type)
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


def _prefer_measurement(rows: list[dict[str, Any]]) -> dict | None:
    for row in rows:
        if row.get("run_type") == "measurement":
            return row
    return rows[0] if rows else None


def _find_query_execution(data: dict[str, Any], normalized_id: str) -> dict | None:
    return _prefer_measurement(
        [q for q in data.get("queries", []) if normalize_query_id(q.get("id", "")) == normalized_id]
    )


def _resolve_plans_path(file_path: Path) -> Path | None:
    plans_path = file_path.with_suffix("").with_suffix(".plans.json")
    if not plans_path.exists():
        plans_path = Path(str(file_path).replace(".json", ".plans.json"))
    return plans_path if plans_path.exists() else None


def _rehydrate_plan(query_plan: dict) -> QueryPlanDAG:
    return QueryPlanDAG.from_dict(query_plan, verify_fingerprint=False)


def _summarize_plan(plan: QueryPlanDAG) -> dict[str, Any]:
    score = compute_complexity_score(plan)
    summary: dict[str, Any] = {
        "operator_count": score.total_operators,
        "join_count": score.join_count,
        "scan_count": score.scan_count,
        "aggregate_count": score.aggregation_count,
        "filter_count": score.filter_count,
        "sort_count": score.sort_count,
        "max_depth": score.max_tree_depth,
        "estimated_rows": plan.estimated_rows,
        "estimated_cost": plan.estimated_cost,
    }
    if plan.truncated_at_depth is not None:
        summary["truncated_at_depth"] = plan.truncated_at_depth
    return summary


def _format_plan_response(query_plan: dict, format_lower: str, normalized_id: str, runtime_ms: Any) -> dict[str, Any]:
    response: dict[str, Any] = {
        "status": "success",
        "query_id": normalized_id,
        "runtime_ms": runtime_ms,
    }

    if format_lower == "json":
        response["plan"] = query_plan
        return response

    try:
        plan = _rehydrate_plan(query_plan)
        if format_lower == "summary":
            response["summary"] = _summarize_plan(plan)
        else:
            tree = render_plan(plan)
            if plan.truncated_at_depth is not None:
                tree += (
                    f"\n\n[plan truncated at depth {plan.truncated_at_depth}: "
                    "operators at that depth have omitted subtrees; request format='json' for the stored markers]"
                )
                response["truncated_at_depth"] = plan.truncated_at_depth
            response["plan_tree"] = tree
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return make_error(
            ErrorCode.VALIDATION_ERROR,
            f"Stored query plan for {normalized_id} is not a canonical plan: {exc!r}",
            details={"query_id": normalized_id},
            suggestion="Request format='json' to inspect the stored plan",
        )

    return response


def _split_variant_key(key: str) -> tuple[str, str | None]:
    stream_id, sep, test_type = key.partition(":")
    return stream_id, (test_type if sep else None)


def _variant_test_types(data: dict[str, Any], normalized_id: str, key_stream: str) -> set[str]:
    return {
        str(q.get("test_type", "")).lower()
        for q in data.get("queries", [])
        if normalize_query_id(q.get("id", "")) == normalized_id and str(q.get("stream", 0)) == key_stream
    }


def _find_variant_execution(
    data: dict[str, Any], normalized_id: str, key_stream: str, key_test_type: str | None
) -> dict | None:
    rows = [
        q
        for q in data.get("queries", [])
        if normalize_query_id(q.get("id", "")) == normalized_id
        and str(q.get("stream", 0)) == key_stream
        and (not key_test_type or str(q.get("test_type", "")).lower() == key_test_type.lower())
    ]
    return _prefer_measurement(rows)


def _select_plan_variant(
    bucket: dict[str, Any],
    stream: str | None,
    test_type: str | None,
    data: dict[str, Any],
    normalized_id: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    matches = []
    for key in bucket:
        key_stream, key_test_type = _split_variant_key(key)
        if stream is not None and key_stream != stream:
            continue
        if test_type is not None:
            known = {key_test_type.lower()} if key_test_type else _variant_test_types(data, normalized_id, key_stream)
            if test_type.lower() not in known:
                continue
        matches.append(key)
    if len(matches) == 1:
        return bucket[matches[0]], matches
    return None, matches


def _get_query_plan_impl(
    file_path: Path,
    result_file: str,
    query_id: str,
    format_lower: str,
    stream: str | int | None = None,
    test_type: str | None = None,
) -> dict[str, Any]:
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

    stream_filter = None if stream is None else str(stream)
    runtime_ms = query_exec.get("ms")
    query_plan_entry = _index_plan_entries(plans_data).get(normalized_id)
    if query_plan_entry is not None and "plan" not in query_plan_entry:
        bucket = query_plan_entry
        query_plan_entry, matches = _select_plan_variant(bucket, stream_filter, test_type, data, normalized_id)
        if query_plan_entry is not None:
            variant_exec = _find_variant_execution(data, normalized_id, *_split_variant_key(matches[0]))
            if variant_exec is not None:
                runtime_ms = variant_exec.get("ms")
        if query_plan_entry is None and matches:
            return make_error(
                ErrorCode.VALIDATION_ERROR,
                f"Query {normalized_id} has {len(matches)} plan variants; specify stream and/or test_type",
                details={"query_id": normalized_id, "available_variants": sorted(matches)},
                suggestion="Pass stream and/or test_type matching one of `details.available_variants` (format 'stream' or 'stream:test_type')",
            )
        if query_plan_entry is None and (stream_filter is not None or test_type is not None):
            return make_not_found_error(
                "query plan variant",
                f"{normalized_id} (stream={stream_filter}, test_type={test_type})",
                available=sorted(bucket),
                suggestion="Use a stream/test_type matching one of `details.available`",
            )
    if not query_plan_entry or "plan" not in query_plan_entry:
        return {
            "status": "no_plan",
            "query_id": normalized_id,
            "message": "No query plan captured for this query",
            "query_info": query_info,
        }

    return _format_plan_response(query_plan_entry["plan"], format_lower, normalized_id, runtime_ms)


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
