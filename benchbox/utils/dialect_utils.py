# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

import hashlib
import math
import re
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from decimal import Context, Decimal, DecimalException, Inexact
from typing import Callable

SCHEMA_DDL_SCOPE = "schema_ddl"
WORKLOAD_QUERY_SCOPE = "workload_query"

NO_IDENTIFY_DIALECTS = frozenset({"clickhouse", "postgres", "snowflake", "exasol"})


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


def _query_has_group_or_order_by_all(query: str) -> bool:

    return bool(re.search(r"(?i)\b(?:GROUP|ORDER)\s+BY\s+ALL\b", query))


def _restore_group_order_by_all_keyword(query: str) -> str:

    return re.sub(
        r"(?i)\b((?:GROUP|ORDER)\s+BY)\s+(\"ALL\"|`ALL`|\[ALL\])",
        r"\1 ALL",
        query,
    )


def _fold_sqlite_discount_bounds(query: str) -> str:
    if not re.search(r"\bl_discount\b", query, re.IGNORECASE) or not re.search(r"\bBETWEEN\b", query, re.IGNORECASE):
        return query

    from benchbox._vendor import sqlglot
    from benchbox._vendor.sqlglot import exp

    context = Context(prec=60, traps=[Inexact])
    max_leaves = 4
    noise = 1e-12
    leaves = 0

    def evaluate(node: "exp.Expression") -> "tuple[Decimal, float] | None":
        nonlocal leaves
        node = node.unnest()
        if isinstance(node, exp.Literal):
            leaves += 1
            if leaves > max_leaves or not node.is_number or not re.fullmatch(r"\d{1,2}\.\d{1,6}", node.name):
                return None
            return Decimal(node.name), float(node.name)
        if isinstance(node, (exp.Add, exp.Sub)):
            left, right = evaluate(node.this), evaluate(node.expression)
            if left is None or right is None:
                return None
            try:
                if isinstance(node, exp.Add):
                    return context.add(left[0], right[0]), left[1] + right[1]
                return context.subtract(left[0], right[0]), left[1] - right[1]
            except DecimalException:
                return None
        return None

    tree = sqlglot.parse_one(query, read="sqlite")
    if not any(table.name.lower() == "lineitem" for table in tree.find_all(exp.Table)):
        return query
    changed = False
    for between in tree.find_all(exp.Between):
        if not isinstance(between.this, exp.Column) or between.this.name.lower() != "l_discount":
            continue
        folded: list[Decimal] = []
        for bound in (between.args["low"], between.args["high"]):
            leaves = 0
            value = evaluate(bound) if isinstance(bound.unnest(), (exp.Add, exp.Sub)) else None
            if value is None:
                break
            exact, real = value
            if exact < 0 or not math.isfinite(real) or abs(real - float(exact)) > noise:
                break
            folded.append(exact)
        else:
            between.set("low", exp.Literal.number(format(folded[0], "f")))
            between.set("high", exp.Literal.number(format(folded[1], "f")))
            changed = True
    return tree.sql(dialect="sqlite") if changed else query


def _fix_sqlite_unsupported_syntax(query: str) -> str:

    def replace_date_interval(match: re.Match[str]) -> str:
        date_literal = match.group("date")
        sign = match.group("op")
        value = match.group("value")
        unit = match.group("unit").lower()
        return f"DATE('{date_literal}', '{sign}{value} {unit}')"

    query = re.sub(
        r"\bDATE\s*\(\s*'(?P<date>\d{4}-\d{2}-\d{2})'\s*\)\s*"
        r"(?P<op>[+-])\s*INTERVAL\s+'(?P<value>\d+)'\s+(?P<unit>DAY|MONTH|YEAR)\b",
        replace_date_interval,
        query,
        flags=re.IGNORECASE,
    )

    date_parts = {
        "DAY": "%d",
        "MONTH": "%m",
        "YEAR": "%Y",
        "HOUR": "%H",
        "MINUTE": "%M",
        "SECOND": "%S",
    }

    def replace_extract(match: re.Match[str]) -> str:
        part = match.group("part").upper()
        expression = match.group("expression").strip()
        return f"CAST(STRFTIME('{date_parts[part]}', {expression}) AS INTEGER)"

    query = re.sub(
        r"\bEXTRACT\s*\(\s*(?P<part>DAY|MONTH|YEAR|HOUR|MINUTE|SECOND)\s+FROM\s+"
        r"(?P<expression>[^()]+?)\s*\)",
        replace_extract,
        query,
        flags=re.IGNORECASE,
    )

    trunc_formats = {
        "YEAR": "%Y-01-01 00:00:00",
        "MONTH": "%Y-%m-01 00:00:00",
        "DAY": "%Y-%m-%d 00:00:00",
        "HOUR": "%Y-%m-%d %H:00:00",
        "MINUTE": "%Y-%m-%d %H:%M:00",
        "SECOND": "%Y-%m-%d %H:%M:%S",
    }

    def replace_date_trunc(match: re.Match[str]) -> str:
        unit = match.group("unit").upper()
        expression = match.group("expression").strip()
        return f"STRFTIME('{trunc_formats[unit]}', {expression})"

    query = re.sub(
        r"\bDATE_TRUNC\s*\(\s*['\"](?P<unit>YEAR|MONTH|DAY|HOUR|MINUTE|SECOND)['\"]\s*,\s*"
        r"(?P<expression>[^(),]+?)\s*\)",
        replace_date_trunc,
        query,
        flags=re.IGNORECASE,
    )
    return _fold_sqlite_discount_bounds(query)


def normalize_dialect_for_sqlglot(dialect: str) -> str:
    dialect_lower = dialect.lower() if dialect else ""

    dialect_mapping = {
        "netezza": "postgres",
        "greenplum": "postgres",
        "vertica": "postgres",
        "datafusion": "postgres",
        "ansi": "postgres",
        "standard": "postgres",
    }

    return dialect_mapping.get(dialect_lower, dialect_lower)


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
        src = normalize_dialect_for_sqlglot(source_dialect.lower())
        tgt = normalize_dialect_for_sqlglot(target_dialect.lower())

        processed_query = query
        if pre_processors:
            for pre_proc in pre_processors:
                processed_query = pre_proc(processed_query)

        should_identify = identify and (tgt not in NO_IDENTIFY_DIALECTS)
        translated = sqlglot.transpile(processed_query, read=src, write=tgt, identify=should_identify)[0]

        if tgt == "duckdb" and _query_has_group_or_order_by_all(query):
            translated = _restore_group_order_by_all_keyword(translated)
        elif tgt == "sqlite":
            translated = _fix_sqlite_unsupported_syntax(translated)

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
            normalized_source_dialect=normalize_dialect_for_sqlglot(source_dialect.lower()),
            normalized_target_dialect=normalize_dialect_for_sqlglot(target_dialect.lower()),
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


def fix_postgres_date_arithmetic(query: str) -> str:
    import re

    pattern = r"(\b\w*\.?\w*d_date\w*)\s*([+-])\s*(\d+)"

    def replace_with_interval(match: re.Match) -> str:
        col = match.group(1)
        op = match.group(2)
        num = match.group(3)
        return f"{col} {op} INTERVAL '{num}' DAY"

    return re.sub(pattern, replace_with_interval, query, flags=re.IGNORECASE)
