# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

import hashlib
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import Any, Callable

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


def bundled_sqlglot_runtime_identity() -> dict[str, str] | None:
    try:
        from benchbox._vendor import _runtime_pin as pin

        identity = {
            "distribution": pin["distribution"],
            "version": pin["version"],
            "fork_sha": pin["fork_sha"],
            "upstream_sha": pin["upstream_sha"],
            "wheel_sha256": pin["sha256"],
        }
    except (ImportError, KeyError):
        return None
    return identity


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
    runtime = bundled_sqlglot_runtime_identity()
    if runtime is not None:
        summary["runtime"] = runtime
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


GENERIC_DIALECT_ALIASES: dict[str, str] = {"ansi": "postgres", "standard": "postgres"}


def resolve_sql_dialect(platform_or_dialect: str) -> str:
    from benchbox._vendor.sqlglot.dialects import DIALECT_MODULE_NAMES
    from benchbox._vendor.sqlglot.dialects.dialect import Dialect
    from benchbox.core.platform_manifest import get_platform_sql_dialect

    key = (platform_or_dialect or "").strip().lower().replace("-", "_")
    resolved = GENERIC_DIALECT_ALIASES.get(key) or get_platform_sql_dialect(key) or key
    if resolved not in DIALECT_MODULE_NAMES or Dialect.get(resolved) is None:
        raise UnknownSqlDialectError(
            f"No fork SQL dialect for {platform_or_dialect!r}; declare capabilities.sql_dialect for the platform in "
            "the platform manifest"
        )
    return resolved


def split_sql_statements(sql: str, source_dialect: str) -> list[str]:
    resolved = resolve_sql_dialect(source_dialect)
    try:
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


def statement_variant_id(query_id: str, index: int) -> str:
    return f"{query_id}{chr(ord('a') + index)}"


def expand_multi_statement_queries(queries: Mapping[Any, Any], source_dialect: str) -> dict[Any, Any]:
    try:
        resolve_sql_dialect(source_dialect)
    except UnknownSqlDialectError:
        source_dialect = "duckdb"
    expanded: dict[Any, Any] = {}
    for query_id, sql in queries.items():
        statements = split_sql_statements(sql, source_dialect) if isinstance(sql, str) and ";" in sql else [sql]
        if len(statements) < 2:
            expanded[query_id] = sql
            continue
        for index, statement in enumerate(statements):
            expanded[statement_variant_id(str(query_id), index)] = statement
    return expanded


STATEMENT_SEPARATOR = ";\n"


def translate_sql_query(
    query: str,
    target_dialect: str,
    source_dialect: str = "netezza",
    identify: bool = True,
    strict: bool | None = None,
    scope: str | None = None,
    date_columns: Iterable[str] | None = None,
    rewrites: Sequence[tuple[str, dict]] | None = None,
    pretty: bool = False,
) -> str:
    import logging

    logger = logging.getLogger(__name__)
    src = resolve_sql_dialect(source_dialect)
    tgt = resolve_sql_dialect(target_dialect)
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
        should_identify = identify and (tgt not in NO_IDENTIFY_DIALECTS)
        translated_statements: list[str] = []
        for expression in sqlglot.parse(query, read=src):
            if expression is None:
                continue
            for rewrite_name, rewrite_args in rewrites or ():
                rewrite_args = dict(rewrite_args)
                expression = apply_rewrite(rewrite_name, expression, rewrite_args.pop("dialect", src), **rewrite_args)
            if tgt == "questdb":
                from benchbox.utils.questdb_subqueries import rewrite_subqueries

                expression = rewrite_subqueries(expression.copy())
            if tgt == "sqlite":
                expression = apply_rewrite("fold_between_literal_arithmetic", expression, tgt)
                if date_columns:
                    expression = apply_rewrite("mark_date_columns", expression, tgt, date_columns=date_columns)
            statement_sql = expression.sql(dialect=tgt, identify=should_identify, pretty=pretty)
            if tgt == "starrocks":
                statement_sql = _starrocks_order_by_projection_aliases(statement_sql, should_identify, pretty)
            translated_statements.append(statement_sql)
        translated = STATEMENT_SEPARATOR.join(translated_statements)

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
            normalized_source_dialect=src,
            normalized_target_dialect=tgt,
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


_STARROCKS_ORDER_BY_ALIAS_GATE = re.compile(
    r"\bORDER\s+BY\b.*\b(?:GROUPING|RANK|ROW_NUMBER|DENSE_RANK)\s*\(", re.I | re.S
)


def _starrocks_order_by_projection_aliases(sql: str, identify: bool, pretty: bool) -> str:
    if not _STARROCKS_ORDER_BY_ALIAS_GATE.search(sql):
        return sql

    from benchbox._vendor import sqlglot
    from benchbox._vendor.sqlglot import exp

    tree = sqlglot.parse_one(sql, read="starrocks")
    changed = False
    for select in tree.find_all(exp.Select):
        order = select.args.get("order")
        if order is None:
            continue
        targets = {}
        for projection in select.expressions:
            if isinstance(projection, exp.Alias) and (
                projection.this.find(exp.Grouping) or projection.this.find(exp.Window)
            ):
                targets.setdefault(projection.this, projection.alias)
        if not targets:
            continue

        def replace(node, targets=targets):
            alias = targets.get(node)
            return exp.column(alias, quoted=True) if alias else node

        rewritten = order.transform(replace)
        if rewritten != order:
            order.replace(rewritten)
            changed = True
    if not changed:
        return sql
    return tree.sql(dialect="starrocks", identify=identify, pretty=pretty)


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


def alias_outer_table_references(expression, table: str, alias: str, *, copy: bool = True):
    from benchbox._vendor.sqlglot import exp

    tree = expression.copy() if copy else expression
    outer_tables = [
        node
        for node in tree.find_all(exp.Table)
        if node.name == table and not node.alias and node.find_ancestor(exp.Select) is tree
    ]
    for node in outer_tables:
        node.set("alias", exp.TableAlias(this=exp.to_identifier(alias)))
    if outer_tables:
        for column in tree.find_all(exp.Column):
            if column.table == table:
                column.set("table", exp.to_identifier(alias))
    return tree


def coalesce_count_subqueries(expression, *, copy: bool = True):
    from benchbox._vendor.sqlglot import exp

    tree = expression.copy() if copy else expression
    for subquery in list(tree.find_all(exp.Subquery)):
        inner = subquery.this
        if not isinstance(inner, exp.Select) or inner.args.get("group") or len(inner.expressions) != 1:
            continue
        if not isinstance(subquery.parent, (exp.Alias, exp.Select)):
            continue
        if isinstance(inner.expressions[0], exp.Count):
            subquery.replace(exp.Coalesce(this=subquery.copy(), expressions=[exp.Literal.number(0)]))
    return tree


_LOCAL_REWRITES: dict[str, Callable[..., object]] = {
    "alias_outer_table_references": alias_outer_table_references,
    "coalesce_count_subqueries": coalesce_count_subqueries,
}


def _rewrite_tree(name: str, expression, resolved: str, **args):
    import inspect

    function = _LOCAL_REWRITES.get(name) or _fork_rewrite(name)
    if "dialect" in inspect.signature(function).parameters:
        args.setdefault("dialect", resolved)
    return function(expression, **args)


def apply_rewrite(name: str, sql_or_expression, dialect: str, **args):
    if isinstance(sql_or_expression, str):
        return apply_rewrites(sql_or_expression, dialect, [(name, args)], keep_unchanged=False)
    return _rewrite_tree(name, sql_or_expression, resolve_sql_dialect(dialect), **args)


def apply_rewrites(sql: str, dialect: str, rewrites: Sequence[tuple[str, dict]], *, keep_unchanged: bool = True) -> str:
    from benchbox._vendor import sqlglot

    resolved = resolve_sql_dialect(dialect)
    strict_mode = current_sql_translation_strict_mode()

    def record(status: str, error: Exception | None = None) -> None:
        record_sql_translation_outcome(
            SqlTranslationOutcome(
                source_dialect=dialect,
                target_dialect=dialect,
                normalized_source_dialect=resolved,
                normalized_target_dialect=resolved,
                translator="sqlglot_rewrite",
                status=status,
                strict_mode=strict_mode,
                warning_category=None if error is None or strict_mode else "rewrite_failed",
                error_category="rewrite_failed" if error is not None and strict_mode else None,
                message=None if error is None else str(error),
                scope=WORKLOAD_QUERY_SCOPE,
            )
        )

    try:
        originals = [statement for statement in sqlglot.parse(sql, read=resolved) if statement is not None]
        if not originals:
            raise ValueError("No SQL statement to rewrite")
        rewritten_statements = []
        for original in originals:
            rewritten = original
            for name, args in rewrites:
                rewritten = _rewrite_tree(name, rewritten, resolved, **args)
            rewritten_statements.append(rewritten)
        if keep_unchanged and rewritten_statements == originals:
            result = sql
        else:
            result = STATEMENT_SEPARATOR.join(
                statement.sql(dialect=resolved, identify=False) for statement in rewritten_statements
            )
    except Exception as error:
        record("failed" if strict_mode else "fallback", error)
        raise
    record("success")
    return result


def sql_expressions():
    from benchbox._vendor.sqlglot import exp

    return exp


def rewrite_sql_tree(
    sql: str,
    dialect: str,
    transform: Callable[[Any], Any],
    finalize: Callable[[Any], Any] | None = None,
) -> str:
    from benchbox._vendor import sqlglot

    resolved = resolve_sql_dialect(dialect)
    try:
        statements = [statement for statement in sqlglot.parse(sql, read=resolved) if statement is not None]
    except sqlglot.errors.SqlglotError:
        return sql
    if len(statements) != 1:
        return sql
    original = statements[0]
    rewritten = transform(original.copy())
    if rewritten == original:
        return sql
    if finalize is not None:
        rewritten = finalize(rewritten)
    return rewritten.sql(dialect=resolved, identify=False)


def unqualified_table_names(sql: str, dialect: str) -> list[str] | None:
    import importlib

    from benchbox._vendor import sqlglot

    resolved = resolve_sql_dialect(dialect)
    try:
        statements = sqlglot.parse(sql, read=resolved)
    except Exception:
        return None
    if not statements or any(statement is None for statement in statements):
        return None
    unqualified_tables = importlib.import_module("benchbox._vendor.sqlglot.benchbox.rewrites").unqualified_tables
    names: list[str] = []
    for statement in statements:
        for table in unqualified_tables(statement):
            name = table.name.upper()
            if name not in names:
                names.append(name)
    return names


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
    strict_mode = current_sql_translation_strict_mode()
    fingerprint = _fingerprint_sql(ddl) if translation_collection_active() else None

    def record(status: str, error: Exception | None = None) -> None:
        record_sql_translation_outcome(
            SqlTranslationOutcome(
                source_dialect=source_dialect,
                target_dialect=target_dialect,
                normalized_source_dialect=read,
                normalized_target_dialect=write,
                translator="sqlglot",
                status=status,
                strict_mode=strict_mode,
                error_category=None if error is None else "translation_failed",
                message=None if error is None else str(error),
                scope=SCHEMA_DDL_SCOPE,
                query_fingerprint=fingerprint,
            )
        )

    try:
        statements = ddl_module.transpile_ddl(
            ddl,
            read=read,
            write=write,
            options=ddl_module.DdlOptions(**options),
            identify=identify and write not in NO_IDENTIFY_DIALECTS,
        )
    except Exception as error:
        record("failed", error)
        raise
    record("success")
    return statements


def mooncake_mirror_statements(table: str, staging_table: str, schema: str | None = None) -> list[str]:
    import importlib

    engines = importlib.import_module("benchbox._vendor.sqlglot.benchbox.ddl_engines")
    return engines.mooncake_mirror_statements(table, staging_table, schema)


def schema_ddl_read_dialect(ddl: str, target_dialect: str) -> str:
    from benchbox._vendor import sqlglot
    from benchbox._vendor.sqlglot import exp

    trees = sqlglot.parse(ddl, read=resolve_sql_dialect("standard"))
    if any(isinstance(tree, exp.Command) for tree in trees):
        return target_dialect
    return "standard"


def lift_ddl_table_layouts(schema_sql: str, read: str, table_format: str) -> tuple[str, dict[str, dict[str, Any]]]:
    from benchbox._vendor import sqlglot
    from benchbox._vendor.sqlglot import exp

    dialect = resolve_sql_dialect(read)
    trees = [tree for tree in sqlglot.parse(schema_sql, read=dialect) if tree is not None]
    layouts: dict[str, dict[str, Any]] = {}
    lifted = False
    for tree in trees:
        properties = tree.args.get("properties") if isinstance(tree, exp.Create) else None
        if not properties or not isinstance(tree.this, exp.Schema) or not isinstance(tree.this.this, exp.Table):
            continue
        partition = next((p for p in properties.expressions if isinstance(p, exp.PartitionedByProperty)), None)
        cluster = next((p for p in properties.expressions if isinstance(p, exp.ClusterProperty)), None)
        if partition is None and cluster is None:
            continue
        partition_nodes = partition.this.expressions if partition is not None else []
        cluster_nodes = cluster.expressions if cluster is not None else []
        simple = (exp.Identifier, exp.Column, exp.ColumnDef)
        if not all(isinstance(node, simple) for node in (*partition_nodes, *cluster_nodes)):
            continue
        layout: dict[str, Any] = {"format": table_format}
        if partition_nodes:
            layout["partition_by"] = [node.name for node in partition_nodes]
        if cluster_nodes:
            layout["cluster_by"] = [node.name for node in cluster_nodes]
        layouts[tree.this.this.name.lower()] = layout
        remaining = [p for p in properties.expressions if p is not partition and p is not cluster]
        if remaining:
            properties.set("expressions", remaining)
        else:
            tree.set("properties", None)
        lifted = True
    if not lifted:
        return schema_sql, {}
    return ";\n".join(tree.sql(dialect=dialect) for tree in trees), layouts


def ddl_column_names(ddl: str, source_dialect: str = "standard") -> dict[str, list[str]]:
    from benchbox._vendor import sqlglot
    from benchbox._vendor.sqlglot import exp

    columns: dict[str, list[str]] = {}
    for tree in sqlglot.parse(ddl, read=resolve_sql_dialect(source_dialect)):
        if not isinstance(tree, exp.Create) or not isinstance(tree.this, exp.Schema):
            continue
        table = tree.this.this
        if isinstance(table, exp.Table):
            columns[table.name.lower()] = [column.name for column in tree.this.find_all(exp.ColumnDef)]
    return columns


def contains_division(sql: str, dialect: str) -> bool:
    from benchbox._vendor import sqlglot
    from benchbox._vendor.sqlglot import exp

    resolved = resolve_sql_dialect(dialect)
    return sqlglot.parse_one(sql, read=resolved).find(exp.Div) is not None


def is_create_table(ddl: str) -> bool:
    body = "\n".join(line for line in ddl.splitlines() if not line.lstrip().startswith("--"))
    return body.lstrip().upper().startswith("CREATE TABLE")


def create_table_shape(ddl: str, source_dialect: str = "standard") -> tuple[str, list[str], list[str]]:
    from benchbox._vendor import sqlglot
    from benchbox._vendor.sqlglot import exp

    tree = sqlglot.parse_one(ddl, read=resolve_sql_dialect(source_dialect))
    if not isinstance(tree, exp.Create) or not isinstance(tree.this, exp.Schema):
        return "", [], []
    columns = [column.name.lower() for column in tree.find_all(exp.ColumnDef)]
    key = [
        identifier.name.lower()
        for primary in tree.find_all(exp.PrimaryKey)
        for identifier in primary.find_all(exp.Identifier)
    ]
    key += [
        column.name.lower()
        for column in tree.find_all(exp.ColumnDef)
        if any(
            isinstance(c.args.get("kind"), exp.PrimaryKeyColumnConstraint) for c in column.args.get("constraints") or []
        )
    ]
    return tree.this.this.name.lower(), columns, key
