from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import CompatibilityDecision, FailureMode, SkipQueryPayload, SupportLevel
from benchbox.sql_compat.registry import REGISTRY

_VERSION = "ClickHouse 25.8.33.6"

_NO_MERGE = (
    f"{_VERSION} has no MERGE statement: `MERGE INTO t AS target USING ...` fails with Code 62 SYNTAX_ERROR "
    "(`Syntax error: failed at position 1 (MERGE)`)."
)
_NO_COPY = (
    f"{_VERSION} has no COPY statement: `COPY INTO t FROM '<file>' WITH (FORMAT ...)` fails with Code 62 "
    "SYNTAX_ERROR (`Syntax error: failed at position 11`), and the catalog's host-side bulk-load files are not "
    "readable by the server's file() table function."
)
_NO_READ_CSV_AUTO = (
    f"{_VERSION} has no read_csv_auto table function: the catalog's INSERT ... SELECT FROM read_csv_auto(...) "
    "fails with Code 46 UNKNOWN_FUNCTION (`Unknown table function read_csv_auto`)."
)
_NO_RETURNING = f"{_VERSION} has no RETURNING clause: the statement fails with Code {{code}} ({{detail}})."
_NO_CORRELATED_MUTATION = (
    f"{_VERSION} cannot resolve a correlated subquery that references the {{verb}} target: the statement fails "
    "with Code 47 UNKNOWN_IDENTIFIER (`Missing columns: '<target>.<column>' while processing`)."
)

CLICKHOUSE_WRITE_PRIMITIVES_OPERATION_SKIPS: dict[str, str] = {
    "insert_on_conflict_ignore": (
        f"{_VERSION} has no INSERT ... ON CONFLICT: the statement fails with Code 62 SYNTAX_ERROR "
        "(`Syntax error: failed at position 86 (ON): ON CONFLICT(o_orderkey) DO NOTHING`)."
    ),
    "insert_returning_clause": _NO_RETURNING.format(
        code="27 CANNOT_PARSE_INPUT_ASSERTION_FAILED",
        detail="`Cannot parse input: expected '(' before: 'RETURNING ...'`",
    ),
    "update_returning": _NO_RETURNING.format(
        code="62 SYNTAX_ERROR", detail="`Syntax error: failed at position 164 (RETURNING)`"
    ),
    "delete_returning": _NO_RETURNING.format(
        code="62 SYNTAX_ERROR", detail="`Syntax error: failed at position 98 (RETURNING)`"
    ),
    "update_with_subquery": _NO_CORRELATED_MUTATION.format(verb="UPDATE"),
    "update_from_select": _NO_CORRELATED_MUTATION.format(verb="UPDATE"),
    "update_with_aggregate": _NO_CORRELATED_MUTATION.format(verb="UPDATE"),
    "delete_with_join": _NO_CORRELATED_MUTATION.format(verb="DELETE"),
    "delete_with_not_exists": _NO_CORRELATED_MUTATION.format(verb="DELETE"),
    "delete_with_aggregation": _NO_CORRELATED_MUTATION.format(verb="DELETE"),
    "merge_scd_type2_basic": _NO_CORRELATED_MUTATION.format(verb="UPDATE"),
    "merge_scd_type2_no_change": _NO_CORRELATED_MUTATION.format(verb="UPDATE"),
    "update_with_join": (
        f"{_VERSION} has no UPDATE ... FROM: the statement fails with Code 62 SYNTAX_ERROR "
        "(`Syntax error: failed at position 58 (FROM)`)."
    ),
    "update_conditional_multi_column": (
        f"{_VERSION} cannot unify a CASE whose branches mix Float64 literals with a Decimal(15, 2) column: the "
        "statement fails with Code 386 NO_COMMON_TYPE (`There is no supertype for types Float64, Float64, "
        "Decimal(15, 2)`)."
    ),
    "ddl_create_table_with_constraints": (
        f"{_VERSION} rejects a NOT NULL modifier on a Nullable column: the statement fails with Code 377 "
        "(`Can't use [NOT] NULL modifier with Nullable type`)."
    ),
    **dict.fromkeys(
        ("ddl_create_table_with_index", "ddl_create_index_on_existing", "ddl_drop_index"),
        (
            f"{_VERSION} requires a data-skipping index TYPE: `CREATE INDEX name ON t(col)` fails with Code 80 "
            "(`CREATE INDEX without TYPE is forbidden`)."
        ),
    ),
    "bulk_load_upsert_mode": _NO_MERGE,
    **dict.fromkeys(("bulk_load_column_subset", "bulk_load_with_transformation"), _NO_READ_CSV_AUTO),
    **dict.fromkeys(
        (
            "merge_simple_upsert_small",
            "merge_upsert_with_delete",
            "merge_overlap_10pct",
            "merge_overlap_50pct",
            "merge_overlap_90pct",
            "merge_no_overlap_all_insert",
            "merge_full_overlap_all_update",
            "merge_with_join_condition",
            "merge_from_subquery_aggregated",
            "merge_conditional_update",
            "merge_conditional_insert",
            "merge_multi_column_update",
            "merge_computed_values",
            "merge_string_operations",
            "merge_date_arithmetic",
            "merge_with_cte_source",
            "merge_returning_clause",
            "merge_error_handling",
            "merge_etl_aggregation_pattern",
            "merge_deduplication_window_function",
        ),
        _NO_MERGE,
    ),
    **dict.fromkeys(
        (
            "bulk_load_csv_small_uncompressed",
            "bulk_load_csv_small_gzip",
            "bulk_load_csv_small_zstd",
            "bulk_load_csv_small_bzip2",
            "bulk_load_csv_medium_uncompressed",
            "bulk_load_csv_medium_gzip",
            "bulk_load_csv_medium_zstd",
            "bulk_load_csv_medium_bzip2",
            "bulk_load_csv_large_uncompressed",
            "bulk_load_csv_large_gzip",
            "bulk_load_csv_large_zstd",
            "bulk_load_csv_large_bzip2",
            "bulk_load_parquet_small_uncompressed",
            "bulk_load_parquet_small_snappy",
            "bulk_load_parquet_small_gzip",
            "bulk_load_parquet_small_zstd",
            "bulk_load_parquet_medium_uncompressed",
            "bulk_load_parquet_medium_snappy",
            "bulk_load_parquet_medium_gzip",
            "bulk_load_parquet_medium_zstd",
            "bulk_load_parquet_large_uncompressed",
            "bulk_load_parquet_large_snappy",
            "bulk_load_parquet_large_gzip",
            "bulk_load_parquet_large_zstd",
            "bulk_load_error_handling_skip_bad_rows",
            "bulk_load_parallel_multi_file",
            "bulk_load_append_mode",
            "bulk_load_replace_mode",
            "bulk_load_delimited_custom",
            "bulk_load_quoted_fields",
            "bulk_load_null_handling",
            "bulk_load_encoding_utf8",
            "bulk_load_date_format_custom",
        ),
        _NO_COPY,
    ),
}


def clickhouse_write_primitive_skip_reason(operation) -> str | None:
    return CLICKHOUSE_WRITE_PRIMITIVES_OPERATION_SKIPS.get(operation.id)


for _query_id, _reason in CLICKHOUSE_WRITE_PRIMITIVES_OPERATION_SKIPS.items():
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"execution_filter.clickhouse.write_primitives.{_query_id}",
            action=CompatAction.SKIP_QUERY,
            support_level=SupportLevel.SKIPPED_QUERY,
            failure_mode=FailureMode.UNSUPPORTED_FEATURE,
            payload=SkipQueryPayload(reason=_reason, query_id=_query_id),
            reason=_reason,
        ),
        Phase.EXECUTION_FILTER,
        "clickhouse",
        benchmark="write_primitives",
        query_id=_query_id,
    )
