# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

import hashlib
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import Callable

SCHEMA_DDL_SCOPE = "schema_ddl"
WORKLOAD_QUERY_SCOPE = "workload_query"

NO_IDENTIFY_DIALECTS = frozenset(
    {"clickhouse", "postgres", "snowflake", "exasol", "questdb", "netezza", "greenplum", "vertica", "datafusion"}
)


def _resolve_translation_scope(scope: str | None) -> str:
    if scope is None:
        return WORKLOAD_QUERY_SCOPE
    if scope not in (SCHEMA_DDL_SCOPE, WORKLOAD_QUERY_SCOPE):
        raise ValueError(f"Unknown SQL translation scope: {scope!r}")
    return scope


def _fingerprint_sql(sql: str) -> str:
    normalized: list[str] = []
    quote: str | None = None
    pending_space = False
    index = 0
    while index < len(sql):
        char = sql[index]
        if quote is not None:
            normalized.append(char)
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    normalized.append(sql[index + 1])
                    index += 1
                else:
                    quote = None
            index += 1
            continue
        if char in "'\"`":
            if pending_space and normalized:
                normalized.append(" ")
            pending_space = False
            normalized.append(char)
            quote = char
        elif char == "[":
            if pending_space and normalized:
                normalized.append(" ")
            pending_space = False
            normalized.append(char)
            quote = "]"
        elif char.isspace():
            pending_space = True
        else:
            if pending_space and normalized:
                normalized.append(" ")
            pending_space = False
            normalized.append(char)
        index += 1
    return hashlib.sha1("".join(normalized).strip().encode()).hexdigest()


def translation_collection_active() -> bool:
    return _SQL_TRANSLATION_OUTCOMES.get() is not None


@dataclass(frozen=True)
class SqlTranslationOutcome:
    source_dialect: str
    target_dialect: str
    translator: str
    status: str
    strict_mode: bool
    normalized_source_dialect: str | None = None
    normalized_target_dialect: str | None = None
    warning_category: str | None = None
    error_category: str | None = None
    message: str | None = None
    scope: str | None = None
    query_fingerprint: str | None = None

    def __post_init__(self) -> None:
        _resolve_translation_scope(self.scope)

    def to_dict(self) -> dict[str, object]:
        return {key: value for key, value in asdict(self).items() if value is not None and key != "query_fingerprint"}


class SQLTranslationError(RuntimeError):
    def __init__(self, message: str, outcome: SqlTranslationOutcome):
        super().__init__(message)
        self.outcome = outcome


_SQL_TRANSLATION_STRICT: ContextVar[bool] = ContextVar("benchbox_sql_translation_strict", default=False)
_SQL_TRANSLATION_OUTCOMES: ContextVar[list[SqlTranslationOutcome] | None] = ContextVar(
    "benchbox_sql_translation_outcomes",
    default=None,
)


@contextmanager
def sql_translation_context(strict: bool = False) -> Iterator[list[SqlTranslationOutcome]]:
    outcomes: list[SqlTranslationOutcome] = []
    strict_token = _SQL_TRANSLATION_STRICT.set(strict)
    outcomes_token = _SQL_TRANSLATION_OUTCOMES.set(outcomes)
    try:
        yield outcomes
    finally:
        _SQL_TRANSLATION_OUTCOMES.reset(outcomes_token)
        _SQL_TRANSLATION_STRICT.reset(strict_token)


def current_sql_translation_strict_mode() -> bool:
    return _SQL_TRANSLATION_STRICT.get()


def record_sql_translation_outcome(outcome: SqlTranslationOutcome) -> None:
    outcomes = _SQL_TRANSLATION_OUTCOMES.get()
    if outcomes is not None:
        outcomes.append(outcome)


def summarize_sql_translation_outcomes(
    outcomes: Sequence[SqlTranslationOutcome],
    *,
    strict_mode: bool | None = None,
) -> dict[str, object] | None:
    if not outcomes:
        return None

    counts = Counter(outcome.status for outcome in outcomes)
    if counts.get("failed"):
        status = "failed"
    elif counts.get("fallback"):
        status = "fallback"
    else:
        status = "success"

    grouped: dict[tuple[object, ...], dict[str, object]] = {}
    group_fingerprints: dict[tuple[object, ...], set[str]] = {}
    for outcome in outcomes:
        scope = _resolve_translation_scope(outcome.scope)
        key = (
            scope,
            outcome.source_dialect,
            outcome.target_dialect,
            outcome.normalized_source_dialect,
            outcome.normalized_target_dialect,
            outcome.translator,
            outcome.status,
            outcome.warning_category,
            outcome.error_category,
            outcome.strict_mode,
        )
        entry = grouped.get(key)
        if entry is None:
            entry = outcome.to_dict()
            entry["scope"] = scope
            if outcome.normalized_source_dialect is not None:
                entry["parser_grammar"] = outcome.normalized_source_dialect
            entry["count"] = 0
            grouped[key] = entry
            group_fingerprints[key] = set()
        entry["count"] = int(entry["count"]) + 1
        entry["calls"] = entry["count"]
        if outcome.query_fingerprint is not None:
            group_fingerprints[key].add(outcome.query_fingerprint)
    for key, entry in grouped.items():
        if group_fingerprints[key]:
            entry["unique_queries"] = len(group_fingerprints[key])

    warning_categories = sorted({outcome.warning_category for outcome in outcomes if outcome.warning_category})
    error_categories = sorted({outcome.error_category for outcome in outcomes if outcome.error_category})
    workload_fingerprints = {
        outcome.query_fingerprint
        for outcome in outcomes
        if _resolve_translation_scope(outcome.scope) == WORKLOAD_QUERY_SCOPE and outcome.query_fingerprint is not None
    }
    schema_fingerprints = {
        outcome.query_fingerprint
        for outcome in outcomes
        if _resolve_translation_scope(outcome.scope) == SCHEMA_DDL_SCOPE and outcome.query_fingerprint is not None
    }

    summary: dict[str, object] = {
        "status": status,
        "strict_mode": bool(strict_mode) if strict_mode is not None else any(o.strict_mode for o in outcomes),
        "attempt_count": len(outcomes),
        "success_count": counts.get("success", 0),
        "fallback_count": counts.get("fallback", 0),
        "failed_count": counts.get("failed", 0),
        "total_transpilation_calls": len(outcomes),
        "translators": sorted({outcome.translator for outcome in outcomes if outcome.translator}),
        "source_dialects": sorted({outcome.source_dialect for outcome in outcomes if outcome.source_dialect}),
        "target_dialects": sorted({outcome.target_dialect for outcome in outcomes if outcome.target_dialect}),
        "outcomes": sorted(
            grouped.values(),
            key=lambda item: (
                str(item.get("status", "")),
                str(item.get("source_dialect", "")),
                str(item.get("target_dialect", "")),
                str(item.get("warning_category", "")),
                str(item.get("error_category", "")),
                str(item.get("scope", "")),
            ),
        ),
    }
    if workload_fingerprints:
        summary["unique_queries_translated"] = len(workload_fingerprints)
    if schema_fingerprints:
        summary["schema_statements_translated"] = len(schema_fingerprints)
    if warning_categories:
        summary["warning_categories"] = warning_categories
    if error_categories:
        summary["error_categories"] = error_categories
    return summary


class UnknownSqlDialectError(ValueError):
    pass


SQLITE_DISCOUNT_BOUNDS = {"columns": ("l_discount",), "tables": ("lineitem",)}

PLATFORM_DIALECTS: dict[str, str] = {
    "ansi": "postgres",
    "standard": "postgres",
    "postgresql": "postgres",
    "timescaledb": "postgres",
    "pg_mooncake": "postgres",
    "pg_duckdb": "postgres",
    "cedardb": "postgres",
    "paradedb": "postgres",
    "citus": "postgres",
    "firebolt": "postgres",
    "influxdb": "datafusion",
    "motherduck": "duckdb",
    "ducklake": "duckdb",
    "clickhouse_local": "clickhouse",
    "clickhouse_server": "clickhouse",
    "clickhouse_cloud": "clickhouse",
    "starburst": "trino",
    "databricks_df": "databricks",
    "databend": "snowflake",
    "snowpark_connect": "snowflake",
    "synapse": "tsql",
    "fabric_dw": "tsql",
    "fabric_lakehouse": "tsql",
    "glue": "spark",
    "emr_serverless": "spark",
    "athena_spark": "spark",
    "dataproc": "spark",
    "dataproc_serverless": "spark",
    "fabric_spark": "spark",
    "synapse_spark": "spark",
    "velox": "spark",
    "lakesail": "spark",
    "quanton": "spark",
    "pyspark": "spark",
}


def resolve_sql_dialect(platform_or_dialect: str) -> str:
    from benchbox._vendor.sqlglot.dialects import DIALECT_MODULE_NAMES
    from benchbox._vendor.sqlglot.dialects.dialect import Dialect

    key = (platform_or_dialect or "").strip().lower().replace("-", "_")
    resolved = PLATFORM_DIALECTS.get(key, key)
    if resolved not in DIALECT_MODULE_NAMES or Dialect.get(resolved) is None:
        raise UnknownSqlDialectError(
            f"No fork SQL dialect for {platform_or_dialect!r}; register the platform in PLATFORM_DIALECTS"
        )
    return resolved


def _resolve_or_none(platform_or_dialect: str) -> str | None:
    try:
        return resolve_sql_dialect(platform_or_dialect)
    except UnknownSqlDialectError:
        return None


def split_sql_statements(sql: str, source_dialect: str) -> list[str]:
    try:
        resolved = resolve_sql_dialect(source_dialect)
        from benchbox._vendor import sqlglot

        tokens = sqlglot.Dialect.get_or_raise(resolved).tokenize(sql)
    except Exception:
        return [sql]

    statements: list[str] = []
    statement_start = 0
    for token in tokens:
        if token.token_type == sqlglot.TokenType.SEMICOLON:
            statements.append(sql[statement_start : token.start])
            statement_start = token.end + 1
    statements.append(sql[statement_start:])
    non_empty = [statement.strip() for statement in statements if statement.strip()]
    return non_empty or [sql]


def translate_sql_query(
    query: str,
    target_dialect: str,
    source_dialect: str = "netezza",
    identify: bool = True,
    pre_processors: list[Callable[[str], str]] | None = None,
    post_processors: list[Callable[[str], str]] | None = None,
    strict: bool | None = None,
    scope: str | None = None,
) -> str:
    import logging

    logger = logging.getLogger(__name__)
    strict_mode = current_sql_translation_strict_mode() if strict is None else strict
    fingerprint = _fingerprint_sql(query) if translation_collection_active() else None

    try:
        from benchbox._vendor import sqlglot
    except ImportError:
        outcome = SqlTranslationOutcome(
            source_dialect=source_dialect,
            target_dialect=target_dialect,
            translator="sqlglot",
            status="failed" if strict_mode else "fallback",
            strict_mode=strict_mode,
            warning_category=None if strict_mode else "translator_unavailable",
            error_category="translator_unavailable" if strict_mode else None,
            message="SQLGlot not available",
            scope=scope,
            query_fingerprint=fingerprint,
        )
        record_sql_translation_outcome(outcome)
        if strict_mode:
            raise SQLTranslationError("SQLGlot not available for strict SQL translation", outcome) from None
        logger.warning("SQLGlot not available, returning original query")
        return query

    try:
        src = resolve_sql_dialect(source_dialect)
        tgt = resolve_sql_dialect(target_dialect)
        processed_query = query
        if pre_processors:
            for pre_proc in pre_processors:
                processed_query = pre_proc(processed_query)

        should_identify = identify and (tgt not in NO_IDENTIFY_DIALECTS)
        parsed = sqlglot.parse(processed_query, read=src)
        expression = parsed[0] if parsed else None
        if expression is None:
            translated = ""
        else:
            if tgt == "sqlite":
                expression = apply_rewrite("fold_between_literal_arithmetic", expression, tgt, **SQLITE_DISCOUNT_BOUNDS)
            translated = expression.sql(dialect=tgt, identify=should_identify)

        if post_processors:
            for post_proc in post_processors:
                translated = post_proc(translated)

        record_sql_translation_outcome(
            SqlTranslationOutcome(
                source_dialect=source_dialect,
                target_dialect=target_dialect,
                normalized_source_dialect=src,
                normalized_target_dialect=tgt,
                translator="sqlglot",
                status="success",
                strict_mode=strict_mode,
                scope=scope,
                query_fingerprint=fingerprint,
            )
        )
        return translated

    except Exception as e:
        outcome = SqlTranslationOutcome(
            source_dialect=source_dialect,
            target_dialect=target_dialect,
            normalized_source_dialect=_resolve_or_none(source_dialect),
            normalized_target_dialect=_resolve_or_none(target_dialect),
            translator="sqlglot",
            status="failed" if strict_mode else "fallback",
            strict_mode=strict_mode,
            warning_category=None if strict_mode else "translation_failed",
            error_category="translation_failed" if strict_mode else None,
            message=str(e),
            scope=scope,
            query_fingerprint=fingerprint,
        )
        record_sql_translation_outcome(outcome)
        if strict_mode:
            raise SQLTranslationError(
                f"SQLGlot translation failed from {source_dialect} to {target_dialect}: {e}",
                outcome,
            ) from e
        logger.warning(
            f"SQLGlot translation failed from {source_dialect} to {target_dialect}: {e}. Returning original query."
        )
        return query


FORK_REWRITE_MODULES = ("rewrites", "engine_rewrites", "compat", "subqueries")


def _fork_rewrite(name: str) -> Callable[..., object]:
    import importlib
    import inspect

    if not name.startswith("_"):
        for module_name in FORK_REWRITE_MODULES:
            module = importlib.import_module(f"benchbox._vendor.sqlglot.benchbox.{module_name}")
            function = getattr(module, name, None)
            if (
                inspect.isfunction(function)
                and function.__module__ == module.__name__
                and "copy" in inspect.signature(function).parameters
            ):
                return function
    raise ValueError(f"Unknown fork rewrite: {name!r}")


def apply_rewrite(name: str, sql_or_expression, dialect: str, **args):
    import inspect

    function = _fork_rewrite(name)
    resolved = resolve_sql_dialect(dialect)
    if "dialect" in inspect.signature(function).parameters:
        args.setdefault("dialect", resolved)
    if isinstance(sql_or_expression, str):
        from benchbox._vendor import sqlglot

        expression = sqlglot.parse_one(sql_or_expression, read=resolved)
        return function(expression, **args).sql(dialect=resolved)
    return function(sql_or_expression, **args)


def translate_ddl(
    ddl: str,
    target_dialect: str,
    source_dialect: str = "standard",
    identify: bool = True,
    **options,
) -> list[str]:
    import importlib
    from dataclasses import fields

    ddl_module = importlib.import_module("benchbox._vendor.sqlglot.benchbox.ddl")
    layout_class = importlib.import_module("benchbox._vendor.sqlglot.benchbox.layout").TableLayout

    def layout(value):
        return layout_class(**value) if isinstance(value, dict) else value

    unknown = set(options) - {field.name for field in fields(ddl_module.DdlOptions)}
    if unknown:
        raise ValueError(f"Unknown DDL options: {sorted(unknown)}")
    if options.get("default_layout") is not None:
        options["default_layout"] = layout(options["default_layout"])
    if options.get("layouts"):
        options["layouts"] = {name: layout(value) for name, value in options["layouts"].items()}
    if isinstance(options.get("singlestore"), dict):
        options["singlestore"] = ddl_module.SingleStoreKeys(**options["singlestore"])
    if isinstance(options.get("type_map"), str):
        engines = importlib.import_module("benchbox._vendor.sqlglot.benchbox.ddl_engines")
        name = f"{options['type_map'].upper()}_TYPE_MAP"
        if not hasattr(engines, name):
            raise ValueError(f"Unknown DDL type map: {options['type_map']!r}")
        options["type_map"] = getattr(engines, name)

    read = resolve_sql_dialect(source_dialect)
    write = resolve_sql_dialect(target_dialect)
    return ddl_module.transpile_ddl(
        ddl,
        read=read,
        write=write,
        options=ddl_module.DdlOptions(**options),
        identify=identify and write not in NO_IDENTIFY_DIALECTS,
    )


def contains_division(sql: str, dialect: str) -> bool:
    from benchbox._vendor import sqlglot
    from benchbox._vendor.sqlglot import exp

    resolved = resolve_sql_dialect(dialect)
    return sqlglot.parse_one(sql, read=resolved).find(exp.Div) is not None
