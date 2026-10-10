#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Sequence

CLI_DESCRIPTION = (
    "Validate Results Explorer compatibility against the current read model.\n"
    "\n"
    "This CLI tool verifies that the Results Explorer SPA and its artifacts\n"
    "maintain compatibility with the current corpus DuckDB read-model schema\n"
    "(v13). It also validates hermetic, content-addressed Explorer application\n"
    "artifact bundles.\n"
    "\n"
    "Usage:\n"
    "    # Run schema compatibility checks only (v13 only):\n"
    "    uv run -- python scripts/publication/check_explorer_compat.py --schema-only\n"
    "\n"
    "    # Validate an Explorer build artifact directory or archive:\n"
    "    uv run -- python scripts/publication/check_explorer_compat.py --artifact results-explorer/dist\n"
    "\n"
    "    # Validate with required manifest (fail-closed):\n"
    "    uv run -- python scripts/publication/check_explorer_compat.py --artifact results-explorer/dist --require-manifest\n"
    "\n"
    "    # Generate content-addressed manifest and checksums in artifact directory:\n"
    "    uv run -- python scripts/publication/check_explorer_compat.py --artifact results-explorer/dist --generate-manifest\n"
    "\n"
    "    # Validate a specific DuckDB database snapshot file:\n"
    "    uv run -- python scripts/publication/check_explorer_compat.py --db-path results-explorer/public/data/results.duckdb\n"
    "\n"
    "    # Check specific schema versions (only 13 is supported):\n"
    "    uv run -- python scripts/publication/check_explorer_compat.py --schema-only --schema-versions 13\n"
    "\n"
    "    # Output machine-readable JSON:\n"
    "    uv run -- python scripts/publication/check_explorer_compat.py --schema-only --json\n"
    "\n"
    "Exit codes:\n"
    "    0 - All compatibility and artifact checks passed\n"
    "    1 - Compatibility or artifact validation failed\n"
    "    2 - CLI argument or environment error\n"
    ""
)

try:
    from _project.scripts.explorer_pipeline.contract import (
        EXPLORER_BUILD_CONTRACT_VERSION as _CONTRACT_VERSION,
        EXPLORER_READ_MODEL_VERSION as _READ_MODEL_VERSION,
    )

    SUPPORTED_SCHEMA_VERSIONS: tuple[int, ...] = (_READ_MODEL_VERSION,)
    CURRENT_SCHEMA_VERSION: int = _READ_MODEL_VERSION
    CONTRACT_VERSION: str = _CONTRACT_VERSION
except ImportError:
    SUPPORTED_SCHEMA_VERSIONS: tuple[int, ...] = (15,)
    CURRENT_SCHEMA_VERSION: int = 15
    CONTRACT_VERSION: str = "6"

_TYPE_ALIASES: dict[str, str] = {
    "varchar": "VARCHAR",
    "text": "VARCHAR",
    "string": "VARCHAR",
    "int": "INTEGER",
    "integer": "INTEGER",
    "int4": "INTEGER",
    "int8": "BIGINT",
    "bigint": "BIGINT",
    "float": "DOUBLE",
    "double": "DOUBLE",
    "float8": "DOUBLE",
    "bool": "BOOLEAN",
    "boolean": "BOOLEAN",
}


def normalize_type(type_str: str) -> str:
    clean = type_str.strip().lower()
    return _TYPE_ALIASES.get(clean, clean.upper())


TABLE_COLUMNS_V9: dict[str, dict[str, str]] = {
    "metadata": {
        "read_model_version": "INTEGER",
    },
    "result_environment": {
        "result_id": "VARCHAR",
        "os": "VARCHAR",
        "arch": "VARCHAR",
        "cpu_count": "INTEGER",
        "memory_gb": "DOUBLE",
        "python": "VARCHAR",
        "cpu_model": "VARCHAR",
        "cpu_family": "VARCHAR",
        "cpu_identity_provenance": "VARCHAR",
    },
    "result_phase_durations": {
        "result_id": "VARCHAR",
        "phase": "VARCHAR",
        "duration_s": "DOUBLE",
    },
    "result_basis_availability": {
        "result_id": "VARCHAR",
        "has_warmup": "BOOLEAN",
        "measurement_pass_count": "INTEGER",
        "warmup_status": "VARCHAR",
        "available_bases": "VARCHAR",
        "varying_pass_queries": "VARCHAR",
    },
    "results": {
        "result_id": "VARCHAR",
        "benchmark": "VARCHAR",
        "scale_factor": "DOUBLE",
        "platform": "VARCHAR",
        "platform_id": "VARCHAR",
        "driver_version": "VARCHAR",
        "run_date": "VARCHAR",
        "power_score": "DOUBLE",
        "total_duration_s": "DOUBLE",
        "geomean_ms": "DOUBLE",
        "display_geomean_ms": "DOUBLE",
        "query_count": "INTEGER",
        "logical_query_count": "INTEGER",
        "has_display_timing": "BOOLEAN",
        "valid_query_count": "INTEGER",
        "missing_query_count": "INTEGER",
        "zero_timing_count": "INTEGER",
        "display_exclusion_reason": "VARCHAR",
        "comparison_exclusion_reason": "VARCHAR",
        "ranking_exclusion_reason": "VARCHAR",
        "trust_label": "VARCHAR",
        "visibility": "VARCHAR",
        "funding": "VARCHAR",
        "platform_version": "VARCHAR",
        "execution_mode": "VARCHAR",
        "tuning_mode": "VARCHAR",
        "tuning_hash": "VARCHAR",
        "requested_config_hash": "VARCHAR",
        "applied_ledger_hash": "VARCHAR",
        "tuning_validation_status": "VARCHAR",
        "applied_receipt": "VARCHAR",
        "tuning_policy_generation": "VARCHAR",
        "test_type": "VARCHAR",
        "validation_status": "VARCHAR",
        "cost_usd": "DOUBLE",
        "normalized_cost_usd": "DOUBLE",
        "cost_model_version": "VARCHAR",
        "cost_model_source": "VARCHAR",
        "cost_scope": "VARCHAR",
        "cost_status": "VARCHAR",
        "billing_unit": "VARCHAR",
        "pricing_region": "VARCHAR",
        "deployment_class": "VARCHAR",
        "cloud_provider": "VARCHAR",
        "cloud_region": "VARCHAR",
        "instance_or_warehouse": "VARCHAR",
        "storage_format": "VARCHAR",
        "instance_type": "VARCHAR",
        "warehouse_size": "VARCHAR",
        "node_count": "INTEGER",
        "cluster_size": "VARCHAR",
        "storage_tier": "VARCHAR",
        "compliance_class": "VARCHAR",
        "is_ranking_eligible": "BOOLEAN",
        "has_plans": "BOOLEAN",
        "plans_published": "BOOLEAN",
        "has_tuning": "BOOLEAN",
        "bundle_download_url": "VARCHAR",
        "physical_mechanisms": "VARCHAR",
        "physical_rendering_id": "VARCHAR",
        "benchmark_support_status": "VARCHAR",
    },
    "query_display_timings": {
        "result_id": "VARCHAR",
        "query_id": "VARCHAR",
        "display_ms": "DOUBLE",
        "sample_count": "INTEGER",
        "is_valid_display_timing": "BOOLEAN",
        "timing_exclusion_reason": "VARCHAR",
    },
    "query_executions": {
        "result_id": "VARCHAR",
        "query_id": "VARCHAR",
        "duration_ms": "DOUBLE",
        "status": "VARCHAR",
        "run_type": "VARCHAR",
        "iter": "INTEGER",
        "stream": "INTEGER",
    },
    "benchmark_matrix_cells": {
        "benchmark": "VARCHAR",
        "scale_factor": "DOUBLE",
        "phase": "VARCHAR",
        "result_id": "VARCHAR",
        "platform_id": "VARCHAR",
        "query_id": "VARCHAR",
        "display_ms": "DOUBLE",
        "is_valid_display_timing": "BOOLEAN",
        "timing_exclusion_reason": "VARCHAR",
    },
    "benchmark_rankings": {
        "benchmark": "VARCHAR",
        "scale_factor": "DOUBLE",
        "phase": "VARCHAR",
        "result_id": "VARCHAR",
        "platform_id": "VARCHAR",
        "platform": "VARCHAR",
        "short_id": "VARCHAR",
        "trust_label": "VARCHAR",
        "funding": "VARCHAR",
        "tuning_mode": "VARCHAR",
        "tuning_hash": "VARCHAR",
        "execution_mode": "VARCHAR",
        "compliance_class": "VARCHAR",
        "run_date": "VARCHAR",
        "is_ranking_eligible": "BOOLEAN",
        "has_display_timing": "BOOLEAN",
        "logical_query_count": "INTEGER",
        "valid_query_count": "INTEGER",
        "missing_query_count": "INTEGER",
        "zero_timing_count": "INTEGER",
        "display_exclusion_reason": "VARCHAR",
        "comparison_exclusion_reason": "VARCHAR",
        "ranking_exclusion_reason": "VARCHAR",
        "power_score": "DOUBLE",
        "display_geomean_ms": "DOUBLE",
        "sample_geomean_ms": "DOUBLE",
        "cost_usd": "DOUBLE",
        "primary_metric": "VARCHAR",
        "primary_order": "VARCHAR",
        "rank": "INTEGER",
        "total_in_cohort": "INTEGER",
        "cohort_ranked_count": "INTEGER",
        "cohort_ranking_exclusion_reason": "VARCHAR",
        "percentile_p50": "DOUBLE",
        "percentile_p90": "DOUBLE",
        "percentile_p95": "DOUBLE",
        "percentile_p99": "DOUBLE",
        "speedup_vs_best": "DOUBLE",
        "speedup_vs_slowest_in_cohort": "DOUBLE",
    },
    "cohort_metadata": {
        "cohort_key": "VARCHAR",
        "benchmark": "VARCHAR",
        "scale_factor": "DOUBLE",
        "phase": "VARCHAR",
        "cohort_label": "VARCHAR",
        "cohort_href": "VARCHAR",
        "platform_count": "INTEGER",
        "cohort_ranked_count": "INTEGER",
        "cohort_ranking_exclusion_reason": "VARCHAR",
        "primary_metric": "VARCHAR",
        "primary_order": "VARCHAR",
        "platform_id": "VARCHAR",
        "platform": "VARCHAR",
        "result_id": "VARCHAR",
        "short_id": "VARCHAR",
        "tuning_mode": "VARCHAR",
        "trust_label": "VARCHAR",
        "has_display_timing": "BOOLEAN",
        "logical_query_count": "INTEGER",
        "valid_query_count": "INTEGER",
        "missing_query_count": "INTEGER",
        "zero_timing_count": "INTEGER",
        "display_exclusion_reason": "VARCHAR",
        "comparison_exclusion_reason": "VARCHAR",
        "ranking_exclusion_reason": "VARCHAR",
        "rank": "INTEGER",
        "metric_value": "DOUBLE",
        "speedup_vs_best": "DOUBLE",
    },
    "meta_leaderboard": {
        "platform_id": "VARCHAR",
        "platform": "VARCHAR",
        "avg_rank": "DOUBLE",
        "n_cohorts": "INTEGER",
    },
    "short_ids": {
        "short_id": "VARCHAR",
        "result_id": "VARCHAR",
    },
}

TABLE_COLUMNS_V10: dict[str, dict[str, str]] = {
    **TABLE_COLUMNS_V9,
    "result_environment": {
        **TABLE_COLUMNS_V9["result_environment"],
        "client_region": "VARCHAR",
        "client_cloud": "VARCHAR",
        "statement_overhead_min_ms": "DOUBLE",
        "statement_overhead_median_ms": "DOUBLE",
        "link_status": "VARCHAR",
    },
}

TABLE_COLUMNS_V11: dict[str, dict[str, str]] = {
    **TABLE_COLUMNS_V10,
    "results": {
        **TABLE_COLUMNS_V10["results"],
        "override_rules": "VARCHAR",
        "override_evidence": "VARCHAR",
        "override_approver": "VARCHAR",
        "override_expires": "VARCHAR",
    },
}

TABLE_COLUMNS_V12: dict[str, dict[str, str]] = {**TABLE_COLUMNS_V11}

TABLE_COLUMNS_V13: dict[str, dict[str, str]] = {
    **TABLE_COLUMNS_V12,
    "results": {
        **TABLE_COLUMNS_V12["results"],
        "benchmark_support_status": "VARCHAR",
    },
}

TABLE_COLUMNS_V14: dict[str, dict[str, str]] = {
    **TABLE_COLUMNS_V13,
    "results": {
        **TABLE_COLUMNS_V13["results"],
        "throughput_at_size": "DOUBLE",
        "stream_count": "INTEGER",
    },
    "benchmark_matrix_cells": {
        **TABLE_COLUMNS_V13["benchmark_matrix_cells"],
        "stream_count": "INTEGER",
    },
    "benchmark_rankings": {
        **TABLE_COLUMNS_V13["benchmark_rankings"],
        "throughput_at_size": "DOUBLE",
        "stream_count": "INTEGER",
    },
    "cohort_metadata": {
        **TABLE_COLUMNS_V13["cohort_metadata"],
        "stream_count": "INTEGER",
    },
}
_V15_RESULTS_COLUMNS: dict[str, str] = {}
for _column_name, _column_type in TABLE_COLUMNS_V14["results"].items():
    _V15_RESULTS_COLUMNS[_column_name] = _column_type
    if _column_name == "tuning_policy_generation":
        _V15_RESULTS_COLUMNS["template_evidence"] = "VARCHAR"

TABLE_COLUMNS_V15: dict[str, dict[str, str]] = {
    **TABLE_COLUMNS_V14,
    "results": _V15_RESULTS_COLUMNS,
}


SCHEMA_REGISTRY: dict[int, dict[str, dict[str, str]]] = {
    9: TABLE_COLUMNS_V9,
    10: TABLE_COLUMNS_V10,
    11: TABLE_COLUMNS_V11,
    12: TABLE_COLUMNS_V12,
    13: TABLE_COLUMNS_V13,
    14: TABLE_COLUMNS_V14,
    15: TABLE_COLUMNS_V15,
}

REQUIRED_INDEXES_V9: list[tuple[str, str, list[str]]] = [
    ("idx_query_executions_result", "query_executions", ["result_id"]),
    ("idx_matrix_cells_cohort", "benchmark_matrix_cells", ["benchmark", "scale_factor", "phase"]),
    ("idx_benchmark_rankings_cohort", "benchmark_rankings", ["benchmark", "scale_factor", "phase"]),
    ("idx_cohort_metadata_key", "cohort_metadata", ["cohort_key"]),
    ("idx_cohort_metadata_platform", "cohort_metadata", ["cohort_key", "platform_id"]),
]

REQUIRED_INDEXES_V10: list[tuple[str, str, list[str]]] = list(REQUIRED_INDEXES_V9)

REQUIRED_VIEWS_V9: list[str] = [
    "result_detail_metrics",
    "platform_index_rows",
]

REQUIRED_VIEWS_V10: list[str] = list(REQUIRED_VIEWS_V9)

REQUIRED_VIEWS_V11: list[str] = list(REQUIRED_VIEWS_V10)

REQUIRED_VIEWS_V12: list[str] = list(REQUIRED_VIEWS_V11)

REQUIRED_VIEW_COLUMNS_V11: dict[str, list[str]] = {
    "result_detail_metrics": [
        "override_rules",
        "override_evidence",
        "override_approver",
        "override_expires",
    ],
}

REQUIRED_VIEW_COLUMNS_V12: dict[str, list[str]] = {
    "result_detail_metrics": list(REQUIRED_VIEW_COLUMNS_V11["result_detail_metrics"]),
}

REQUIRED_VIEW_COLUMNS_V13: dict[str, list[str]] = {
    "result_detail_metrics": [
        *REQUIRED_VIEW_COLUMNS_V11["result_detail_metrics"],
        "benchmark_support_status",
    ],
}

REQUIRED_VIEW_COLUMNS_V14: dict[str, list[str]] = {
    "result_detail_metrics": [
        *REQUIRED_VIEW_COLUMNS_V13["result_detail_metrics"],
        "throughput_at_size",
        "stream_count",
    ],
}
REQUIRED_VIEW_COLUMNS_V15: dict[str, list[str]] = {
    "result_detail_metrics": [
        *REQUIRED_VIEW_COLUMNS_V14["result_detail_metrics"],
        "template_evidence",
    ],
}


def get_table_columns_for_version(version: int) -> dict[str, dict[str, str]]:
    if version in SUPPORTED_SCHEMA_VERSIONS and version in SCHEMA_REGISTRY:
        return SCHEMA_REGISTRY[version]
    raise ValueError(f"Unsupported schema version: {version}")


def get_views_for_version(version: int) -> list[str]:
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(f"Unsupported schema version: {version}")
    if version >= 12:
        return list(REQUIRED_VIEWS_V12)
    if version >= 11:
        return list(REQUIRED_VIEWS_V11)
    return list(REQUIRED_VIEWS_V10)


def get_indexes_for_version(version: int) -> list[tuple[str, str, list[str]]]:
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(f"Unsupported schema version: {version}")
    return list(REQUIRED_INDEXES_V10)


def generate_schema_ddl(version: int) -> list[str]:
    columns_map = get_table_columns_for_version(version)
    ddl_statements: list[str] = []

    ddl_statements.append("CREATE TABLE metadata (read_model_version INTEGER NOT NULL);")

    env_cols = ", ".join(f"{c} {t}" for c, t in columns_map["result_environment"].items())
    ddl_statements.append(f"CREATE TABLE result_environment ({env_cols}, PRIMARY KEY (result_id));")

    ddl_statements.append(
        "CREATE TABLE result_phase_durations (result_id VARCHAR NOT NULL, phase VARCHAR NOT NULL, "
        "duration_s DOUBLE NOT NULL, PRIMARY KEY (result_id, phase));"
    )

    if "result_basis_availability" in columns_map:
        rba_cols = ", ".join(f"{c} {t}" for c, t in columns_map["result_basis_availability"].items())
        ddl_statements.append(f"CREATE TABLE result_basis_availability ({rba_cols}, PRIMARY KEY (result_id));")

    res_cols = ", ".join(f"{c} {t}" for c, t in columns_map["results"].items())
    ddl_statements.append(f"CREATE TABLE results ({res_cols}, PRIMARY KEY (result_id));")

    qdt_cols = ", ".join(f"{c} {t}" for c, t in columns_map["query_display_timings"].items())
    ddl_statements.append(f"CREATE TABLE query_display_timings ({qdt_cols}, PRIMARY KEY (result_id, query_id));")

    qe_cols = ", ".join(f"{c} {t}" for c, t in columns_map["query_executions"].items())
    ddl_statements.append(f"CREATE TABLE query_executions ({qe_cols});")
    ddl_statements.append("CREATE INDEX idx_query_executions_result ON query_executions (result_id);")

    bmc_cols = ", ".join(f"{c} {t}" for c, t in columns_map["benchmark_matrix_cells"].items())
    ddl_statements.append(
        f"CREATE TABLE benchmark_matrix_cells ({bmc_cols}, PRIMARY KEY (benchmark, scale_factor, phase, result_id, query_id));"
    )
    ddl_statements.append(
        "CREATE INDEX idx_matrix_cells_cohort ON benchmark_matrix_cells (benchmark, scale_factor, phase);"
    )

    br_cols = ", ".join(f"{c} {t}" for c, t in columns_map["benchmark_rankings"].items())
    ddl_statements.append(
        f"CREATE TABLE benchmark_rankings ({br_cols}, PRIMARY KEY (benchmark, scale_factor, phase, result_id));"
    )
    ddl_statements.append(
        "CREATE INDEX idx_benchmark_rankings_cohort ON benchmark_rankings (benchmark, scale_factor, phase);"
    )

    cm_cols = ", ".join(f"{c} {t}" for c, t in columns_map["cohort_metadata"].items())
    ddl_statements.append(f"CREATE TABLE cohort_metadata ({cm_cols}, PRIMARY KEY (cohort_key, result_id));")
    ddl_statements.append("CREATE INDEX idx_cohort_metadata_key ON cohort_metadata (cohort_key);")
    ddl_statements.append("CREATE INDEX idx_cohort_metadata_platform ON cohort_metadata (cohort_key, platform_id);")

    ddl_statements.append(
        "CREATE TABLE meta_leaderboard (platform_id VARCHAR PRIMARY KEY, platform VARCHAR NOT NULL, "
        "avg_rank DOUBLE, n_cohorts INTEGER NOT NULL);"
    )

    ddl_statements.append("CREATE TABLE short_ids (short_id VARCHAR PRIMARY KEY, result_id VARCHAR NOT NULL UNIQUE);")

    env_select_cols = [f"e.{col}" for col in columns_map["result_environment"] if col != "result_id"]
    env_proj = ", ".join(env_select_cols)
    if env_proj:
        view_sql = f"CREATE VIEW result_detail_metrics AS SELECT r.*, {env_proj} FROM results r LEFT JOIN result_environment e USING (result_id);"
    else:
        view_sql = "CREATE VIEW result_detail_metrics AS SELECT r.* FROM results r LEFT JOIN result_environment e USING (result_id);"
    ddl_statements.append(view_sql)

    ddl_statements.append("CREATE VIEW platform_index_rows AS SELECT * FROM results;")

    return ddl_statements


CORE_EXPLORER_QUERIES: list[tuple[str, str]] = [
    ("Read model version probe", "SELECT read_model_version FROM metadata"),
    (
        "Results query page",
        "SELECT result_id, benchmark, scale_factor, platform, platform_id, run_date, power_score, "
        "total_duration_s, geomean_ms, display_geomean_ms, query_count, logical_query_count, "
        "has_display_timing, valid_query_count, missing_query_count, zero_timing_count, "
        "display_exclusion_reason, comparison_exclusion_reason, ranking_exclusion_reason, "
        "trust_label, visibility, funding, validation_status, cost_usd, benchmark_support_status, "
        "throughput_at_size, stream_count "
        "FROM results ORDER BY run_date DESC LIMIT 24",
    ),
    (
        "Platform index view browse",
        "SELECT result_id, benchmark, scale_factor, platform, platform_id, run_date, power_score "
        "FROM platform_index_rows LIMIT 50",
    ),
    (
        "Result detail metrics view",
        "SELECT result_id, benchmark, scale_factor, platform, validation_status, override_rules, "
        "override_evidence, override_approver, override_expires, throughput_at_size, stream_count "
        "FROM result_detail_metrics LIMIT 10",
    ),
    (
        "Benchmark matrix cells scan",
        "SELECT benchmark, scale_factor, phase, result_id, platform_id, query_id, display_ms, "
        "is_valid_display_timing, timing_exclusion_reason, stream_count FROM benchmark_matrix_cells LIMIT 100",
    ),
    (
        "Benchmark rankings query",
        "SELECT benchmark, scale_factor, phase, result_id, platform_id, platform, short_id, "
        "trust_label, funding, is_ranking_eligible, has_display_timing, power_score, throughput_at_size, "
        "stream_count, display_geomean_ms, "
        "primary_metric, primary_order, rank, total_in_cohort, cohort_ranked_count, "
        "speedup_vs_best, speedup_vs_slowest_in_cohort FROM benchmark_rankings LIMIT 50",
    ),
    (
        "Cohort metadata query",
        "SELECT cohort_key, benchmark, scale_factor, phase, cohort_label, cohort_href, "
        "platform_count, cohort_ranked_count, primary_metric, primary_order, platform_id, "
        "platform, result_id, short_id, rank, metric_value, speedup_vs_best, stream_count "
        "FROM cohort_metadata LIMIT 50",
    ),
    (
        "Meta leaderboard summary",
        "SELECT platform_id, platform, avg_rank, n_cohorts FROM meta_leaderboard",
    ),
    (
        "Short ID resolution",
        "SELECT result_id FROM short_ids WHERE short_id = 'test_short_id'",
    ),
    (
        "Query display timings for result",
        "SELECT result_id, query_id, display_ms, sample_count, is_valid_display_timing, "
        "timing_exclusion_reason FROM query_display_timings WHERE result_id = 'test_res'",
    ),
    (
        "Query executions scan",
        "SELECT result_id, query_id, duration_ms, status FROM query_executions WHERE result_id = 'test_res'",
    ),
]


def create_in_memory_schema(version: int) -> Any:
    import duckdb

    con = duckdb.connect(":memory:")
    for stmt in generate_schema_ddl(version):
        con.execute(stmt)

    con.execute("INSERT INTO metadata VALUES (?)", [version])
    return con


def validate_database_schema(con: Any, expected_version: int | None = None) -> list[str]:
    errors: list[str] = []

    meta_row = None
    try:
        meta_row = con.execute("SELECT read_model_version FROM metadata").fetchone()
    except Exception as exc:
        errors.append(f"metadata table missing or unreadable: {exc}")

    actual_version = int(meta_row[0]) if meta_row else None

    if expected_version is not None and actual_version != expected_version:
        errors.append(f"read_model_version mismatch: expected {expected_version}, got {actual_version}")

    version_to_check = expected_version or actual_version or CURRENT_SCHEMA_VERSION
    if version_to_check not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append(
            f"unsupported read_model_version {version_to_check} (supported: {list(SUPPORTED_SCHEMA_VERSIONS)})"
        )
        return errors

    expected_tables = get_table_columns_for_version(version_to_check)

    existing_tables_rows = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
    ).fetchall()
    existing_tables = {row[0] for row in existing_tables_rows}

    missing_tables = sorted(set(expected_tables.keys()) - existing_tables)
    if missing_tables:
        errors.append(f"missing required tables for v{version_to_check}: {', '.join(missing_tables)}")

    for table, expected_cols in expected_tables.items():
        if table not in existing_tables:
            continue
        col_rows = con.execute(
            f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{table}'"
        ).fetchall()
        actual_cols = {row[0]: normalize_type(row[1]) for row in col_rows}

        missing_cols = sorted(set(expected_cols.keys()) - set(actual_cols.keys()))
        if missing_cols:
            errors.append(f"table '{table}' missing required columns: {', '.join(missing_cols)}")

    expected_views = get_views_for_version(version_to_check)
    existing_views_rows = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' AND table_type = 'VIEW'"
    ).fetchall()
    existing_views = {row[0] for row in existing_views_rows}
    missing_views = sorted(set(expected_views) - existing_views)
    if missing_views:
        errors.append(f"missing required views for v{version_to_check}: {', '.join(missing_views)}")

    if version_to_check >= 15:
        view_column_requirements = REQUIRED_VIEW_COLUMNS_V15
    elif version_to_check >= 14:
        view_column_requirements = REQUIRED_VIEW_COLUMNS_V14
    elif version_to_check >= 13:
        view_column_requirements = REQUIRED_VIEW_COLUMNS_V13
    elif version_to_check >= 12:
        view_column_requirements = REQUIRED_VIEW_COLUMNS_V12
    else:
        view_column_requirements = REQUIRED_VIEW_COLUMNS_V11
    if version_to_check >= 11:
        for view, required_cols in view_column_requirements.items():
            if view not in existing_views:
                continue
            view_col_rows = con.execute(
                f"SELECT column_name FROM information_schema.columns WHERE table_name = '{view}'"
            ).fetchall()
            actual_view_cols = {row[0] for row in view_col_rows}
            missing_view_cols = sorted(set(required_cols) - actual_view_cols)
            if missing_view_cols:
                errors.append(f"view '{view}' missing required columns: {', '.join(missing_view_cols)}")

    return errors


def validate_database_queries(con: Any, version: int) -> list[str]:
    errors: list[str] = []
    for label, sql in CORE_EXPLORER_QUERIES:
        try:
            con.execute(sql).fetchall()
        except Exception as exc:
            errors.append(f"query '{label}' failed under v{version}: {exc}")
    return errors


def check_schema_compatibility(versions: Sequence[int] | None = None) -> dict[int, list[str]]:
    versions_to_test = versions or SUPPORTED_SCHEMA_VERSIONS
    results: dict[int, list[str]] = {}

    for version in versions_to_test:
        version_errors: list[str] = []
        try:
            con = create_in_memory_schema(version)
            try:
                schema_errors = validate_database_schema(con, expected_version=version)
                version_errors.extend(schema_errors)

                query_errors = validate_database_queries(con, version=version)
                version_errors.extend(query_errors)
            finally:
                con.close()
        except Exception as exc:
            version_errors.append(f"failed to initialize in-memory schema v{version}: {exc}")

        results[version] = version_errors

    return results


def compute_file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_directory_checksums(
    directory: Path,
    exclude_names: set[str] | None = None,
) -> dict[str, str]:
    excludes = exclude_names or {"manifest.json", "SHA256SUMS"}
    checksums: dict[str, str] = {}
    for p in sorted(directory.rglob("*")):
        if p.is_file():
            rel = p.relative_to(directory).as_posix()
            if rel in excludes:
                continue
            checksums[rel] = compute_file_sha256(p)
    return checksums


def compute_content_address(checksums: dict[str, str]) -> str:
    hasher = hashlib.sha256()
    for rel_path in sorted(checksums.keys()):
        hasher.update(f"{rel_path}:{checksums[rel_path]}\n".encode())
    return hasher.hexdigest()


def generate_artifact_manifest(
    artifact_dir: Path,
    write: bool = False,
) -> dict[str, Any]:
    checksums = compute_directory_checksums(artifact_dir)
    content_addr = compute_content_address(checksums)

    manifest = {
        "bundle": "explorer_app",
        "content_address": content_addr,
        "file_count": len(checksums),
        "files": checksums,
        "read_model_version": CURRENT_SCHEMA_VERSION,
        "supported_versions": list(SUPPORTED_SCHEMA_VERSIONS),
        "contract_version": CONTRACT_VERSION,
        "github_sha": os.environ.get("GITHUB_SHA", ""),
        "github_ref": os.environ.get("GITHUB_REF", ""),
        "github_event_name": os.environ.get("GITHUB_EVENT_NAME", ""),
    }

    if write:
        manifest_path = artifact_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

        sha256sums_path = artifact_dir / "SHA256SUMS"
        sums_text = "\n".join(f"{h}  {rel}" for rel, h in sorted(checksums.items())) + "\n"
        sha256sums_path.write_text(sums_text, encoding="utf-8")

    return manifest


def _extract_artifact_archive(artifact_path: Path) -> tuple[Path | None, list[str]]:
    temp_dir = Path(tempfile.mkdtemp(prefix="explorer_artifact_"))
    if zipfile.is_zipfile(artifact_path):
        with zipfile.ZipFile(artifact_path) as zf:
            zf.extractall(temp_dir)
        return temp_dir, []
    if tarfile.is_tarfile(artifact_path):
        with tarfile.open(artifact_path) as tf:
            tf.extractall(temp_dir, filter="data")
        return temp_dir, []
    shutil.rmtree(temp_dir, ignore_errors=True)
    return None, [f"artifact file is neither a zip nor tar archive: {artifact_path}"]


def _validate_index_html(target_dir: Path) -> list[str]:
    errors: list[str] = []
    index_html = target_dir / "index.html"
    if not index_html.is_file():
        errors.append("artifact is missing 'index.html'")
    elif index_html.stat().st_size == 0:
        errors.append("artifact 'index.html' is empty")
    else:
        html_content = index_html.read_text(encoding="utf-8", errors="replace")
        if "<html" not in html_content.lower() and "<!doctype html" not in html_content.lower():
            errors.append("'index.html' does not contain valid HTML document root")
    return errors


def _validate_bundle_files(all_files: list[Path], target_dir: Path) -> tuple[list[str], list[Path], list[Path]]:
    errors: list[str] = []
    js_files = [p for p in all_files if p.is_file() and p.suffix == ".js"]
    css_files = [p for p in all_files if p.is_file() and p.suffix == ".css"]

    if not js_files:
        errors.append("artifact contains no JavaScript bundles (.js files)")
    if not css_files:
        errors.append("artifact contains no stylesheet bundles (.css files)")

    for p in all_files:
        if p.is_file() and p.stat().st_size == 0 and p.name != ".gitkeep":
            errors.append(f"empty file in artifact bundle: {p.relative_to(target_dir)}")

    return errors, js_files, css_files


def _validate_manifest_json(
    target_dir: Path,
    content_addr: str,
    require_manifest: bool = False,
) -> list[str]:
    errors: list[str] = []
    manifest_file = target_dir / "manifest.json"
    if not manifest_file.is_file():
        if require_manifest:
            errors.append("manifest.json is missing (required for verification)")
        return errors

    try:
        manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
        if manifest_data.get("bundle") != "explorer_app":
            errors.append(f"manifest 'bundle' field expected 'explorer_app', got {manifest_data.get('bundle')!r}")
        if manifest_data.get("content_address") != content_addr:
            errors.append(
                f"manifest content_address mismatch: recorded {manifest_data.get('content_address')}, "
                f"computed {content_addr}"
            )
        if "read_model_version" not in manifest_data:
            errors.append("manifest missing 'read_model_version' field")
        elif manifest_data.get("read_model_version") != CURRENT_SCHEMA_VERSION:
            errors.append(
                f"manifest read_model_version mismatch: expected {CURRENT_SCHEMA_VERSION}, "
                f"got {manifest_data.get('read_model_version')!r}"
            )
        recorded_files = manifest_data.get("files", {})
        for rel_path, expected_hash in recorded_files.items():
            actual_file = target_dir / rel_path
            if not actual_file.is_file():
                errors.append(f"file in manifest missing on disk: {rel_path}")
            elif compute_file_sha256(actual_file) != expected_hash:
                errors.append(f"file checksum mismatch for {rel_path}")
    except Exception as exc:
        errors.append(f"failed to parse or validate manifest.json: {exc}")

    return errors


def _validate_sha256sums(
    target_dir: Path,
    require_manifest: bool = False,
) -> list[str]:
    errors: list[str] = []
    sha256sums_file = target_dir / "SHA256SUMS"
    if not sha256sums_file.is_file():
        if require_manifest:
            errors.append("SHA256SUMS is missing (required for verification)")
        return errors

    for idx, line in enumerate(sha256sums_file.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2:
            errors.append(f"malformed SHA256SUMS line {idx}: {line!r}")
            continue
        expected_h, rel_p = parts[0], parts[1].strip("* ")
        if len(expected_h) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected_h):
            errors.append(f"malformed SHA256SUMS hash at line {idx}: {expected_h!r}")
            continue
        target_f = target_dir / rel_p
        if not target_f.is_file():
            errors.append(f"file in SHA256SUMS missing on disk: {rel_p}")
        elif compute_file_sha256(target_f) != expected_h:
            errors.append(f"SHA256SUMS hash mismatch for {rel_p}")

    return errors


def validate_artifact_bundle(
    artifact_path: Path,
    require_manifest: bool = False,
) -> tuple[list[str], dict[str, Any] | None]:
    errors: list[str] = []
    bundle_metadata: dict[str, Any] | None = None

    if not artifact_path.exists():
        return [f"artifact path does not exist: {artifact_path}"], None

    temp_dir: Path | None = None
    target_dir = artifact_path

    try:
        if artifact_path.is_file():
            temp_dir, archive_errors = _extract_artifact_archive(artifact_path)
            if temp_dir is None:
                return archive_errors, None
            target_dir = temp_dir

        if not target_dir.is_dir():
            return [f"target artifact is not a directory: {target_dir}"], None

        errors.extend(_validate_index_html(target_dir))

        all_files = list(target_dir.rglob("*"))
        file_errors, js_files, css_files = _validate_bundle_files(all_files, target_dir)
        errors.extend(file_errors)

        checksums = compute_directory_checksums(target_dir)
        content_addr = compute_content_address(checksums)

        errors.extend(_validate_manifest_json(target_dir, content_addr, require_manifest=require_manifest))
        errors.extend(_validate_sha256sums(target_dir, require_manifest=require_manifest))

        bundle_metadata = {
            "path": str(artifact_path),
            "content_address": content_addr,
            "file_count": len(checksums),
            "js_bundles": len(js_files),
            "css_bundles": len(css_files),
            "total_bytes": sum(p.stat().st_size for p in all_files if p.is_file()),
        }

    finally:
        if temp_dir and temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

    return errors, bundle_metadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=CLI_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--artifact",
        "-a",
        type=Path,
        help="Path to Results Explorer build directory (dist/) or archive.",
    )
    parser.add_argument(
        "--db-path",
        "-d",
        type=Path,
        help="Path to a DuckDB database snapshot file (results.duckdb) to validate.",
    )
    parser.add_argument(
        "--schema-versions",
        "-s",
        type=str,
        default=",".join(str(v) for v in SUPPORTED_SCHEMA_VERSIONS),
        help=f"Comma-separated list of schema versions to validate (default: {','.join(str(v) for v in SUPPORTED_SCHEMA_VERSIONS)}).",
    )
    parser.add_argument(
        "--generate-manifest",
        action="store_true",
        help="Compute and write manifest.json and SHA256SUMS in the --artifact directory.",
    )
    parser.add_argument(
        "--require-manifest",
        action="store_true",
        help="Require manifest.json and SHA256SUMS to be present and valid (fail-closed).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output validation results in JSON format.",
    )
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="Run in-memory schema compatibility checks without --artifact or --db-path.",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Quiet mode: only output on error.",
    )
    return parser


def _parse_schema_versions_arg(raw: str) -> list[int] | None:
    try:
        if raw.strip().lower() == "all":
            return list(SUPPORTED_SCHEMA_VERSIONS)
        if raw.strip() == "":
            return None
        return [int(v.strip()) for v in raw.split(",") if v.strip()]
    except ValueError:
        return None


def _run_database_check(db_path: Path) -> dict[str, Any] | None:
    if not db_path.is_file():
        return None

    import duckdb

    db_errors: list[str] = []
    try:
        with duckdb.connect(str(db_path), read_only=True) as con:
            db_errors.extend(validate_database_schema(con))
            row = con.execute("SELECT read_model_version FROM metadata").fetchone()
            db_version = int(row[0]) if row else CURRENT_SCHEMA_VERSION
            db_errors.extend(validate_database_queries(con, version=db_version))
    except Exception as exc:
        db_errors.append(f"failed to open/validate DuckDB snapshot: {exc}")

    return {"path": str(db_path), "passed": len(db_errors) == 0, "errors": db_errors}


def _run_artifact_check(
    artifact_path: Path,
    generate_manifest: bool,
    require_manifest: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    manifest_info = None
    if generate_manifest:
        manifest_info = generate_artifact_manifest(artifact_path, write=True)
        artifact_errors, bundle_info = validate_artifact_bundle(artifact_path, require_manifest=False)
        artifact_check = {
            "path": str(artifact_path),
            "passed": len(artifact_errors) == 0,
            "metadata": bundle_info,
            "errors": artifact_errors,
        }
        return artifact_check, manifest_info

    artifact_errors, bundle_info = validate_artifact_bundle(artifact_path, require_manifest=require_manifest)
    artifact_check = {
        "path": str(artifact_path),
        "passed": len(artifact_errors) == 0,
        "metadata": bundle_info,
        "errors": artifact_errors,
    }
    return artifact_check, manifest_info


def _print_text_report(output_data: dict[str, Any], schema_versions: Sequence[int], overall_passed: bool) -> None:
    print("=== Results Explorer Compatibility & Artifact Verification ===")
    print(f"Current read-model version: v{CURRENT_SCHEMA_VERSION}")
    print(f"Supported versions: {', '.join(f'v{v}' for v in SUPPORTED_SCHEMA_VERSIONS)}")
    print()

    print("Schema Compatibility:")
    for v in schema_versions:
        v_info = output_data["schema_checks"][f"v{v}"]
        mark = "✓ PASS" if v_info["passed"] else "✗ FAIL"
        print(f"  [{mark}] Schema v{v}")
        for err in v_info["errors"]:
            print(f"      - {err}")

    if "database_check" in output_data:
        db_info = output_data["database_check"]
        mark = "✓ PASS" if db_info["passed"] else "✗ FAIL"
        print()
        print(f"Database Snapshot: {db_info['path']}")
        print(f"  [{mark}] Snapshot integrity")
        for err in db_info["errors"]:
            print(f"      - {err}")

    if "artifact_check" in output_data:
        art_info = output_data["artifact_check"]
        mark = "✓ PASS" if art_info["passed"] else "✗ FAIL"
        print()
        print(f"Explorer Application Artifact: {art_info['path']}")
        if art_info.get("metadata"):
            meta = art_info["metadata"]
            print(f"  Content address: {meta.get('content_address')}")
            print(f"  Total files:     {meta.get('file_count')} ({meta.get('total_bytes')} bytes)")
            print(f"  JS / CSS bundles: {meta.get('js_bundles')} JS, {meta.get('css_bundles')} CSS")
        print(f"  [{mark}] Artifact bundle integrity")
        for err in art_info["errors"]:
            print(f"      - {err}")

    print()
    if overall_passed:
        print("All Results Explorer compatibility checks PASSED.")
    else:
        print("Results Explorer compatibility checks FAILED.", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:  # noqa: C901
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.generate_manifest and args.require_manifest:
        print("Error: --generate-manifest and --require-manifest are mutually exclusive", file=sys.stderr)
        return 2

    if args.artifact is None and args.db_path is None and not args.schema_only:
        print(
            "Error: provide --artifact and/or --db-path, or pass --schema-only for in-memory schema checks",
            file=sys.stderr,
        )
        return 2

    schema_versions = _parse_schema_versions_arg(args.schema_versions)
    if schema_versions is None or len(schema_versions) == 0:
        print(f"Error: Invalid --schema-versions format: {args.schema_versions!r}", file=sys.stderr)
        return 2

    for v in schema_versions:
        if v not in SUPPORTED_SCHEMA_VERSIONS:
            print(
                f"Error: Unsupported schema version {v}. Supported versions: {list(SUPPORTED_SCHEMA_VERSIONS)}",
                file=sys.stderr,
            )
            return 2

    overall_passed = True
    output_data: dict[str, Any] = {
        "status": "passed",
        "current_version": CURRENT_SCHEMA_VERSION,
        "supported_versions": list(SUPPORTED_SCHEMA_VERSIONS),
        "schema_checks": {},
    }

    schema_results = check_schema_compatibility(schema_versions)
    for v, errors in schema_results.items():
        passed = len(errors) == 0
        if not passed:
            overall_passed = False
        output_data["schema_checks"][f"v{v}"] = {
            "passed": passed,
            "errors": errors,
        }

    if args.db_path:
        db_check = _run_database_check(args.db_path)
        if db_check is None:
            print(f"Error: DuckDB database file not found: {args.db_path}", file=sys.stderr)
            return 2
        if not db_check["passed"]:
            overall_passed = False
        output_data["database_check"] = db_check

    if args.artifact:
        if not args.artifact.exists():
            print(f"Error: Artifact path not found: {args.artifact}", file=sys.stderr)
            return 2
        if args.generate_manifest and not args.artifact.is_dir():
            print("Error: --generate-manifest requires a directory for --artifact", file=sys.stderr)
            return 2

        if args.generate_manifest:
            artifact_check, manifest_info = _run_artifact_check(
                args.artifact, generate_manifest=True, require_manifest=False
            )
            output_data["manifest_generated"] = manifest_info
            if not artifact_check["passed"]:
                overall_passed = False
            output_data["artifact_check"] = artifact_check
        else:
            artifact_check, manifest_info = _run_artifact_check(
                args.artifact, generate_manifest=False, require_manifest=args.require_manifest
            )
            if manifest_info is not None:
                output_data["manifest_generated"] = manifest_info
            if not artifact_check["passed"]:
                overall_passed = False
            output_data["artifact_check"] = artifact_check

    output_data["status"] = "passed" if overall_passed else "failed"

    if args.json:
        print(json.dumps(output_data, indent=2))
    elif not args.quiet or not overall_passed:
        _print_text_report(output_data, schema_versions, overall_passed)

    return 0 if overall_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
