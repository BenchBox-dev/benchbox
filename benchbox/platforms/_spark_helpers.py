from __future__ import annotations

import logging
import re
import socket
from collections.abc import Callable, Mapping
from typing import Any

from benchbox.core.sql_utils import (
    extract_table_name as _extract_table_name,
    normalize_table_name_in_sql as _normalize_table_name_in_sql,
)
from benchbox.utils.sql_identifier import is_valid_sql_identifier

_SPARK_IDENTIFIER_MAX_LEN = 128

SPARK_AQE_KEYS = (
    "spark.sql.adaptive.enabled",
    "spark.sql.adaptive.coalescePartitions.enabled",
    "spark.sql.adaptive.skewJoin.enabled",
)

SPARK_CBO_KEYS = (
    "spark.sql.cbo.enabled",
    "spark.sql.cbo.joinReorder.enabled",
)


def spark_aqe_conf_entries(adaptive_enabled: bool) -> dict[str, str]:
    value = "true" if adaptive_enabled else "false"
    return dict.fromkeys(SPARK_AQE_KEYS, value)


def adaptive_enabled_from_config(config: Mapping[str, Any]) -> bool:
    options = config.get("options") or {}
    value = config.get("adaptive_enabled", options.get("adaptive_enabled", True))
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "y", "on")
    return bool(value)


def apply_spark_olap_runtime_conf(
    connection: Any,
    benchmark_type: str,
    benchmark_types: tuple[str, ...] = ("olap", "analytics", "tpch", "tpcds", "joinorder"),
    *,
    adaptive_enabled: bool = True,
    spark_config: Mapping[str, str] | None = None,
    logger: logging.Logger | None = None,
    platform_label: str = "Spark",
) -> None:
    try:
        if benchmark_type.lower() not in tuple(t.lower() for t in benchmark_types):
            return
        overrides = spark_config or {}
        aqe_value = "true" if adaptive_enabled else "false"
        for aqe_key in SPARK_AQE_KEYS:
            connection.conf.set(aqe_key, overrides.get(aqe_key, aqe_value))
        for cbo_key in SPARK_CBO_KEYS:
            connection.conf.set(cbo_key, overrides.get(cbo_key, "true"))
        if logger is not None:
            logger.debug("Applied OLAP optimizations for %s", platform_label)
    except Exception as exc:
        (logger or logging.getLogger(__name__)).warning("Failed to apply benchmark configuration: %s", exc)


def validate_spark_identifier(identifier: str) -> bool:
    return is_valid_sql_identifier(identifier, max_length=_SPARK_IDENTIFIER_MAX_LEN)


def extract_spark_table_name(statement: str) -> str | None:
    return _extract_table_name(statement)


def _unquote_spark_retry_identifier(identifier: str | None) -> str | None:
    if not identifier:
        return None
    identifier = identifier.strip()
    if len(identifier) >= 2 and identifier[0] == identifier[-1] and identifier[0] in {'"', "`"}:
        identifier = identifier[1:-1]
    return identifier


def normalize_spark_table_name_in_sql(sql: str) -> str:
    return _normalize_table_name_in_sql(sql)


def parse_spark_connect_endpoint(endpoint: str, *, default_port: int = 50051) -> tuple[str, int]:
    url = endpoint
    if url.startswith("sc://"):
        url = url[5:]
    host, _, port_str = url.partition(":")
    try:
        port = int(port_str) if port_str else default_port
    except ValueError:
        port = default_port
    return host or "localhost", port


def is_spark_connect_reachable(endpoint: str, *, timeout: float = 2.0) -> bool:
    host, port = parse_spark_connect_endpoint(endpoint)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def list_spark_tables(connection: Any) -> list[str]:
    try:
        return [t.name.lower() for t in connection.catalog.listTables()]
    except Exception:
        return []


def analyze_spark_table(connection: Any, table_name: str, *, logger: logging.Logger) -> None:
    if not validate_spark_identifier(table_name):
        logger.warning(f"Skipping ANALYZE: invalid table identifier '{table_name}'")
        return
    try:
        connection.sql(f"ANALYZE TABLE {table_name.lower()} COMPUTE STATISTICS")
        logger.debug(f"Analyzed table {table_name}")
    except Exception as e:
        logger.warning(f"Failed to analyze table {table_name}: {e}")


def get_spark_query_plan(
    connection: Any,
    query: str,
    *,
    logger: logging.Logger | None = None,
) -> str | None:
    log = logger or logging.getLogger(__name__)
    spark = connection
    from benchbox.platforms.base.sql_execution import join_explain_rows

    try:
        result_df = spark.sql(f"EXPLAIN EXTENDED {query}")
        plan_rows = result_df.collect()
        return join_explain_rows(plan_rows) or ""
    except Exception as e:
        log.warning("Could not get query plan via EXPLAIN: %s", e)
        return None


_USING_CLAUSE_RE = re.compile(r"\s+USING\s+\w+", re.IGNORECASE)
_SMALLINT_RE = re.compile(r"\bSMALLINT\b", re.IGNORECASE)
_INLINE_PK_RE = re.compile(r"\bPRIMARY\s+KEY\b(?!\s*\()", re.IGNORECASE)
_INLINE_UNIQUE_RE = re.compile(r"\bUNIQUE\b(?!\s*\()", re.IGNORECASE)
_TABLE_CONSTRAINT_KEYWORD_RE = re.compile(
    r",\s*(PRIMARY\s+KEY|UNIQUE|FOREIGN\s+KEY|CHECK)\s*\(",
    re.IGNORECASE,
)
_REFERENCES_TAIL_RE = re.compile(r"\s+REFERENCES\s+\S+\s*\(", re.IGNORECASE)
_TRAILING_COMMA_RE = re.compile(r",\s*\)")
_REPEATED_SPACE_RE = re.compile(r"[ \t]{2,}")
_LEADING_COMMENTS_RE = re.compile(r"\A(?:\s*(?:--[^\n]*(?:\n|$)|/\*.*?\*/))+\s*", re.DOTALL)
_FIXED_SIZE_ARRAY_TYPE_RE = re.compile(r"\b([A-Za-z]\w*)\s*\[\s*\d+\s*\]")
_ARRAY_FIXED_SIZE_SUFFIX_RE = re.compile(r"(>)\s*\[\s*\d+\s*\]")
_CREATE_TABLE_COLUMN_LIST_RE = re.compile(
    r"\A\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:\"[^\"]*\"|`[^`]*`|[^\s(\"`])+\s*\(",
    re.IGNORECASE,
)


def _column_list_end(statement: str) -> int:
    match = _CREATE_TABLE_COLUMN_LIST_RE.match(statement)
    if not match:
        return len(statement)
    depth = 1
    quote = ""
    cursor = match.end()
    while cursor < len(statement):
        ch = statement[cursor]
        cursor += 1
        if quote:
            if ch == "\\" and quote == "'":
                cursor += 1
            elif ch == quote:
                quote = ""
        elif ch in "'\"`":
            quote = ch
        elif ch == "-" and statement[cursor : cursor + 1] == "-":
            newline = statement.find("\n", cursor + 1)
            cursor = len(statement) if newline == -1 else newline + 1
        elif ch == "/" and statement[cursor : cursor + 1] == "*":
            close = statement.find("*/", cursor + 1)
            cursor = len(statement) if close == -1 else close + 2
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return cursor
    return len(statement)


def _strip_balanced_paren_constraints(statement: str) -> str:
    out: list[str] = []
    idx = 0
    while True:
        match = _TABLE_CONSTRAINT_KEYWORD_RE.search(statement, idx)
        if not match:
            out.append(statement[idx:])
            return "".join(out)

        out.append(statement[idx : match.start()])
        depth = 1
        cursor = match.end()
        while cursor < len(statement) and depth:
            ch = statement[cursor]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            cursor += 1
        if depth:
            out.append(statement[match.start() :])
            return "".join(out)

        ref_match = _REFERENCES_TAIL_RE.match(statement, cursor)
        if ref_match:
            ref_depth = 1
            ref_cursor = ref_match.end()
            while ref_cursor < len(statement) and ref_depth:
                ch = statement[ref_cursor]
                if ch == "(":
                    ref_depth += 1
                elif ch == ")":
                    ref_depth -= 1
                ref_cursor += 1
            if ref_depth == 0:
                cursor = ref_cursor
        idx = cursor


def optimize_spark_table_definition(
    statement: str,
    *,
    table_format: str = "parquet",
    strip_v1_constraints: bool = True,
    upcast_smallint: bool = True,
) -> str:
    statement = _LEADING_COMMENTS_RE.sub("", statement)
    if not statement.strip():
        return ""
    if not statement.upper().startswith("CREATE TABLE"):
        return statement

    statement = _USING_CLAUSE_RE.sub("", statement)
    statement = _FIXED_SIZE_ARRAY_TYPE_RE.sub(r"ARRAY<\1>", statement)
    statement = _ARRAY_FIXED_SIZE_SUFFIX_RE.sub(r"\1", statement)

    if upcast_smallint:
        statement = _SMALLINT_RE.sub("INT", statement)

    if strip_v1_constraints:
        statement = _INLINE_PK_RE.sub("", statement)
        statement = _INLINE_UNIQUE_RE.sub("", statement)
        statement = _strip_balanced_paren_constraints(statement)
        statement = _TRAILING_COMMA_RE.sub(")", statement)
        statement = _REPEATED_SPACE_RE.sub(" ", statement)

    if ")" in statement:
        fmt = (table_format or "parquet").upper()
        statement = statement.rstrip(";").rstrip()
        end = _column_list_end(statement)
        statement = f"{statement[:end]} USING {fmt}{statement[end:]}"
    return statement


def purge_orphaned_warehouse_directory(spark: Any, *, logger: logging.Logger) -> None:
    try:
        existing = list(spark.catalog.listTables())
    except Exception as exc:
        logger.debug(f"Warehouse purge skipped (catalog probe failed): {exc}")
        return
    if existing:
        return
    try:
        rows = spark.sql("SELECT current_database()").collect()
        if not rows:
            return
        current_db = rows[0][0]
        if not validate_spark_identifier(current_db):
            return
        spark.sql(f"DROP DATABASE IF EXISTS `{current_db}` CASCADE")
        spark.sql(f"CREATE DATABASE IF NOT EXISTS `{current_db}`")
        spark.sql(f"USE `{current_db}`")
        logger.info(f"Purged potentially-orphaned warehouse directory for database '{current_db}'")
    except Exception as exc:
        logger.debug(f"Warehouse purge aborted mid-cycle: {exc}")


class SparkLikeAdapterMixin:
    ledger_execute_verbs = ("sql",)

    def apply_constraint_configuration(
        self,
        primary_key_config: Any,
        foreign_key_config: Any,
        connection: Any,
    ) -> None:
        platform = self.platform_name
        if primary_key_config and primary_key_config.enabled:
            self.logger.info(f"Primary key constraints enabled for {platform} (informational only, not enforced)")
        if foreign_key_config and foreign_key_config.enabled:
            self.logger.info(f"Foreign key constraints enabled for {platform} (informational only, not enforced)")

    def apply_unified_tuning(self, unified_config: Any, connection: Any) -> None:
        from benchbox.platforms.base.tuning_config import apply_standard_unified_tuning

        apply_standard_unified_tuning(self, unified_config, connection)

    def apply_platform_optimizations(self, platform_config: Any, connection: Any) -> None:
        if not platform_config:
            return

        spark = connection
        platform = self.platform_name
        ledger = getattr(self, "_applied_tuning_ledger", None)
        if hasattr(platform_config, "spark") and platform_config.spark:
            for key, value in platform_config.spark.items():
                try:
                    spark.conf.set(f"spark.{key}", str(value))
                    self.logger.debug(f"Applied {platform} config: spark.{key} = {value}")
                except Exception as exc:
                    self.logger.warning(f"Failed to apply {platform} config spark.{key}: {exc}")
                    self._record_spark_conf_ledger(ledger, key, value, applied=False, error=exc)
                    continue
                self._record_spark_conf_ledger(ledger, key, value, applied=True)

        self.logger.info(f"{platform} platform optimizations applied")

    def apply_olap_runtime_conf(self, connection: Any, benchmark_type: str, platform_label: str) -> None:
        apply_spark_olap_runtime_conf(
            connection,
            benchmark_type,
            adaptive_enabled=self.adaptive_enabled,
            spark_config=self.spark_config,
            logger=self.logger,
            platform_label=platform_label,
        )

    @staticmethod
    def _record_spark_conf_ledger(ledger: Any, key: str, value: Any, *, applied: bool, error: Any = None) -> None:
        if ledger is None:
            return
        try:
            from benchbox.core.tuning.applied_ledger import EXECUTED, PHASE_SESSION, STATEMENT_FAILED

            ledger.record(
                f"SET spark.{key}={value}",
                PHASE_SESSION,
                status=EXECUTED if applied else STATEMENT_FAILED,
                mechanism="spark_session_config",
                error=error,
            )
        except Exception:  # pragma: no cover
            pass


def _run_schema_statement(spark: Any, statement: str) -> None:
    result = spark.sql(statement)
    collect = getattr(result, "collect", None)
    if callable(collect):
        collect()


def run_spark_schema_creation_loop(
    spark: Any,
    statements: list[str],
    optimize_statement: Callable[[str], str],
    *,
    logger: logging.Logger,
    on_pre_loop: Callable[[Any], None] | None = None,
    on_location_collision: Callable[[Any, str], None] | None = None,
) -> None:
    if on_pre_loop is not None:
        on_pre_loop(spark)

    for statement in statements:
        statement = statement.strip() if statement else ""
        if not statement:
            continue
        statement = normalize_spark_table_name_in_sql(statement)
        statement = optimize_statement(statement)
        if not statement.strip():
            continue
        try:
            _run_schema_statement(spark, statement)
            logger.debug(f"Executed schema statement: {statement[:100]}...")
        except Exception as exc:
            error_lower = str(exc).lower()
            if "already exists" not in error_lower:
                raise

            extracted_table_name = extract_spark_table_name(statement)
            table_name = _unquote_spark_retry_identifier(extracted_table_name)
            if not (table_name and validate_spark_identifier(table_name)):
                raise RuntimeError(
                    "Cannot safely retry CREATE: extracted table name "
                    f"{extracted_table_name!r} from statement {statement[:80]!r} failed "
                    "strict-ASCII identifier validation. Either rename the "
                    "table to match ^[a-zA-Z_][a-zA-Z0-9_]*$ (<=128 chars) or "
                    "drop the conflicting object manually."
                ) from exc

            _run_schema_statement(spark, f"DROP TABLE IF EXISTS {table_name}")
            if on_location_collision is not None and "location_already_exists" in error_lower:
                on_location_collision(spark, table_name)
            _run_schema_statement(spark, statement)
