from __future__ import annotations

import re
from typing import Any, Callable

from benchbox.core.connection import DatabaseConnection
from benchbox.core.tpch.schema import get_create_all_tables_sql as get_tpch_ddl, get_table as get_tpch_table


def summarize_validation_failures(validation_results: list[dict[str, Any]]) -> str:
    parts = []
    for vr in validation_results:
        if vr.get("skipped") or vr.get("passed", True):
            continue
        parts.append(f"{vr.get('query_id', '?')} ({vr.get('actual_rows', '?')} rows)")
    if not parts:
        return "validation failed"
    return "validation failed: " + ", ".join(parts)


def quote_identifier(identifier: str) -> str:
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", identifier):
        raise ValueError(
            f"Invalid SQL identifier: {identifier}. "
            "Only alphanumeric and underscore allowed, must start with letter or underscore."
        )

    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


UPPERCASE_IDENTIFIER_DIALECTS = frozenset({"snowflake", "bigquery"})


def quote_identifier_for_dialect(identifier: str, dialect: str | None) -> str:
    normalized = (dialect or "standard").lower()
    if normalized == "bigquery":
        quote_identifier(identifier)
        return f"`{identifier.upper()}`"
    if normalized in ("databricks", "doris", "starrocks"):
        quote_identifier(identifier)
        escaped = identifier.replace("`", "``")
        return f"`{escaped}`"
    if normalized in UPPERCASE_IDENTIFIER_DIALECTS:
        return quote_identifier(identifier.upper())
    return quote_identifier(identifier)


_REPLACE_IN_PLACE_DIALECTS = frozenset({"databricks"})


def replaces_tables_in_place(dialect: str | None) -> bool:
    return (dialect or "").lower() in _REPLACE_IN_PLACE_DIALECTS


def replace_table_sql(create_sql: str) -> str:
    head, sep, rest = create_sql.partition("CREATE TABLE")
    if not sep or "IF NOT EXISTS" in rest.split("(", 1)[0].upper():
        raise ValueError("replace_table_sql expects a plain CREATE TABLE statement")
    return f"{head}CREATE OR REPLACE TABLE{rest}"


def failed_platform_error(cursor: Any) -> str | None:
    platform_result = getattr(cursor, "platform_result", None)
    if isinstance(platform_result, dict) and platform_result.get("status") == "FAILED":
        return str(platform_result.get("error", "unknown error"))
    if isinstance(cursor, dict) and cursor.get("status") == "FAILED":
        return str(cursor.get("error", "unknown error"))
    return None


def fetch_count_probe(connection: DatabaseConnection, sql: str) -> int:
    cursor = connection.execute(sql)
    if (probe_error := failed_platform_error(cursor)) is not None:
        raise RuntimeError(f"Count probe failed: {probe_error}")
    row = cursor.fetchone()
    return row[0] if row else 0


_CATALOG_PROBE_DIALECTS = frozenset({"postgres", "postgresql"})


def table_exists(
    connection: DatabaseConnection,
    table_name: str,
    log_verbose: Callable[[str], None],
    dialect: str | None = None,
) -> bool:
    try:
        quoted_table = quote_identifier_for_dialect(table_name, dialect)
        if (dialect or "").lower() in _CATALOG_PROBE_DIALECTS:
            cursor = connection.execute(f"SELECT to_regclass('{quoted_table}') IS NOT NULL")
            if (error := failed_platform_error(cursor)) is not None:
                log_verbose(f"Unexpected error checking table '{table_name}': {error}")
                return False
            row = cursor.fetchone()
            return bool(row and row[0])
        cursor = connection.execute(f"SELECT 1 FROM {quoted_table} LIMIT 0")
        if (error := failed_platform_error(cursor)) is not None:
            error_msg = error.lower()
            if any(
                phrase in error_msg
                for phrase in ["does not exist", "doesn't exist", "no such table", "unknown table", "not found"]
            ):
                return False
            log_verbose(f"Unexpected error checking table '{table_name}': {error}")
            return False
        return True
    except ValueError as exc:
        log_verbose(f"Invalid table name '{table_name}': {exc}")
        return False
    except Exception as exc:
        error_msg = str(exc).lower()
        if any(
            phrase in error_msg
            for phrase in ["does not exist", "doesn't exist", "no such table", "unknown table", "not found"]
        ):
            return False

        log_verbose(f"Unexpected error checking table '{table_name}': {type(exc).__name__}: {exc}")
        return False


def build_tpch_staging_tables_sql(
    *,
    dialect: str,
    tuning_config: Any,
    staging_heading: str,
    get_staging_tables_sql: Callable[[str], str],
) -> str:
    enable_primary_keys = False
    enable_foreign_keys = False

    if tuning_config is not None:
        pk_config = getattr(tuning_config, "primary_keys", None)
        fk_config = getattr(tuning_config, "foreign_keys", None)
        enable_primary_keys = getattr(pk_config, "enabled", False)
        enable_foreign_keys = getattr(fk_config, "enabled", False)

    tpch_ddl = get_tpch_ddl(
        enable_primary_keys=enable_primary_keys,
        enable_foreign_keys=enable_foreign_keys,
    )
    staging_ddl = get_staging_tables_sql(dialect)

    return f"""{tpch_ddl}

-- ============================================================
-- Generated staging load tables
-- ============================================================
-- These tables receive generator-emitted orders_stage.tbl and
-- lineitem_stage.tbl files during the generic load phase.
-- ============================================================

{_get_generated_stage_load_tables_sql()}

-- ============================================================
-- {staging_heading}
-- ============================================================
-- These tables are created empty and populated via setup()
-- after TPC-H base data is loaded
-- ============================================================

{staging_ddl}"""


def _get_generated_stage_load_tables_sql() -> str:
    statements = []
    for source_name, stage_name in (("orders", "orders_stage"), ("lineitem", "lineitem_stage")):
        source_sql = get_tpch_table(source_name).get_create_table_sql(
            enable_primary_keys=False,
            enable_foreign_keys=False,
        )
        statements.append(source_sql.replace(f"CREATE TABLE {source_name} (", f"CREATE TABLE {stage_name} (", 1))
    return "\n\n".join(statements)
