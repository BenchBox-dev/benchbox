# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import re
from csv import reader
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol

from benchbox._vendor.sqlglot import exp, parse_one
from benchbox.core.dataframe.context import DataFrameContext
from benchbox.core.dataframe.query import DataFrameQuery, QueryCategory, QueryRegistry
from benchbox.core.joinorder.queries import CANONICAL_JOINORDER_QUERIES, JoinOrderQueryManager
from benchbox.core.joinorder.schema import JoinOrderSchema

if TYPE_CHECKING:
    import pandas as pd


class QueryImpl(Protocol):
    def __call__(self, ctx: DataFrameContext) -> Any: ...


_IMPLS: dict[str, QueryImpl] = {}


def __getattr__(name: str) -> QueryImpl:
    if name in _IMPLS:
        return _IMPLS[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


IMPLEMENTED_DATAFRAME_QUERY_IDS = list(CANONICAL_JOINORDER_QUERIES)
UNTRANSLATED_DATAFRAME_QUERY_IDS: list[str] = []
_HAND_TRANSLATED_DATAFRAME_QUERY_IDS: set[str] = set()
_QUERY_MANAGER = JoinOrderQueryManager()


def _schema_columns() -> dict[str, list[str]]:
    schema = JoinOrderSchema()
    tables = schema._tables
    return {
        table: [column_def.split()[0] for column_def in table_def["columns"]] for table, table_def in tables.items()
    }


_TABLE_COLUMNS = _schema_columns()


def _qualified(alias: str, column: str) -> str:
    return f"{alias}__{column}"


def _join_key(alias: str, column: str, predicate_index: int) -> str:
    return f"{_qualified(alias, column)}__join_key_{predicate_index}"


def _column_ref(node: exp.Expression) -> tuple[str, str]:
    if not isinstance(node, exp.Column) or not node.table:
        raise NotImplementedError(f"Unsupported JoinOrder DataFrame SQL column expression: {node}")
    return str(node.table), node.name


def _literal_value(node: exp.Expression) -> Any:
    if isinstance(node, exp.Null):
        return None
    if not isinstance(node, exp.Literal):
        raise NotImplementedError(f"Unsupported JoinOrder DataFrame SQL literal expression: {node}")
    if node.is_string:
        return str(node.this)
    raw = str(node.this)
    return int(raw) if raw.isdigit() else float(raw)


def _like_pattern_to_regex(pattern: str) -> str:
    parts: list[str] = ["^"]
    for char in pattern:
        if char == "%":
            parts.append(".*")
        elif char == "_":
            parts.append(".")
        else:
            parts.append(re.escape(char))
    parts.append("$")
    return "".join(parts)


def _flatten_and(node: exp.Expression) -> list[exp.Expression]:
    if isinstance(node, exp.And):
        return _flatten_and(node.this) + _flatten_and(node.expression)
    if isinstance(node, exp.Paren):
        return _flatten_and(node.this)
    return [node]


def _aliases_in(node: exp.Expression) -> set[str]:
    return {str(column.table) for column in node.find_all(exp.Column) if column.table}


def _is_join_equality(node: exp.Expression) -> bool:
    if not isinstance(node, exp.EQ):
        return False
    left = node.this
    right = node.expression
    return isinstance(left, exp.Column) and isinstance(right, exp.Column) and str(left.table) != str(right.table)


def _sql_tables(tree: exp.Select) -> list[tuple[str, str]]:
    tables: list[tuple[str, str]] = []
    from_expr = tree.args.get("from_")
    if from_expr is not None and isinstance(from_expr.this, exp.Table):
        table = from_expr.this
        tables.append((table.alias_or_name, table.name))
    for join in tree.args.get("joins") or []:
        if isinstance(join.this, exp.Table):
            table = join.this
            tables.append((table.alias_or_name, table.name))
    if not tables:
        raise NotImplementedError("JoinOrder DataFrame SQL query has no FROM tables")
    return tables


def _select_min_columns(tree: exp.Select) -> list[tuple[str, str, str]]:
    columns: list[tuple[str, str, str]] = []
    for select_expr in tree.expressions:
        if not isinstance(select_expr, exp.Alias) or not isinstance(select_expr.this, exp.Min):
            raise NotImplementedError(f"JoinOrder DataFrame only supports MIN(...) projections: {select_expr}")
        alias, column = _column_ref(select_expr.this.this)
        columns.append((alias, column, select_expr.alias))
    return columns


class _JoinStep(NamedTuple):
    existing_alias: str
    existing_column: str
    new_alias: str
    new_column: str
    predicate_index: int


def _plan_join_sequence(tables: list[tuple[str, str]], join_predicates: list[exp.Expression]) -> list[_JoinStep]:
    anchor = tables[0][0]
    joined = {anchor}
    remaining = [alias for alias, _table in tables[1:]]
    steps: list[_JoinStep] = []
    while remaining:
        for index, predicate in enumerate(join_predicates):
            left_alias, left_column = _column_ref(predicate.this)
            right_alias, right_column = _column_ref(predicate.expression)
            if left_alias in joined and right_alias in remaining:
                steps.append(_JoinStep(left_alias, left_column, right_alias, right_column, index))
                joined.add(right_alias)
                remaining.remove(right_alias)
                break
            if right_alias in joined and left_alias in remaining:
                steps.append(_JoinStep(right_alias, right_column, left_alias, left_column, index))
                joined.add(left_alias)
                remaining.remove(left_alias)
                break
        else:
            new_alias = remaining[0]
            steps.append(_JoinStep(anchor, "", new_alias, "", -1))
            joined.add(new_alias)
            remaining.remove(new_alias)
    return steps


def _expr_value(ctx: DataFrameContext, node: exp.Expression) -> Any:
    if isinstance(node, exp.Column):
        alias, column = _column_ref(node)
        return ctx.col(_qualified(alias, column))
    return ctx.lit(_literal_value(node))


def _expression_condition(ctx: DataFrameContext, node: exp.Expression) -> Any:
    if isinstance(node, exp.Paren):
        return _expression_condition(ctx, node.this)
    if isinstance(node, exp.And):
        return _expression_condition(ctx, node.this) & _expression_condition(ctx, node.expression)
    if isinstance(node, exp.Or):
        return _expression_condition(ctx, node.this) | _expression_condition(ctx, node.expression)
    if isinstance(node, exp.Not):
        return ~_expression_condition(ctx, node.this)
    if isinstance(node, exp.EQ):
        return _expr_value(ctx, node.this) == _expr_value(ctx, node.expression)
    if isinstance(node, exp.NEQ):
        return _expr_value(ctx, node.this) != _expr_value(ctx, node.expression)
    if isinstance(node, exp.GT):
        return _expr_value(ctx, node.this) > _expr_value(ctx, node.expression)
    if isinstance(node, exp.GTE):
        return _expr_value(ctx, node.this) >= _expr_value(ctx, node.expression)
    if isinstance(node, exp.LT):
        return _expr_value(ctx, node.this) < _expr_value(ctx, node.expression)
    if isinstance(node, exp.Between):
        return _expr_value(ctx, node.this).is_between(
            _expr_value(ctx, node.args["low"]), _expr_value(ctx, node.args["high"])
        )
    if isinstance(node, exp.In):
        return _expr_value(ctx, node.this).is_in([_literal_value(value) for value in node.expressions])
    if isinstance(node, exp.Like):
        matched = _expr_value(ctx, node.this).str.contains(_like_pattern_to_regex(_literal_value(node.expression)))
        return ~matched if node.args.get("negate") else matched
    if isinstance(node, exp.Is):
        expr = _expr_value(ctx, node.this)
        return expr.is_null() if isinstance(node.expression, exp.Null) else expr == _expr_value(ctx, node.expression)
    raise NotImplementedError(f"Unsupported JoinOrder DataFrame SQL predicate: {node}")


def _prefixed_expression_frame(
    ctx: DataFrameContext,
    table_name: str,
    alias: str,
    predicates: list[exp.Expression],
    join_key_columns: list[tuple[str, int]],
) -> Any:
    frame = ctx.get_table(table_name)
    frame = frame.select(
        [ctx.col(column).alias(_qualified(alias, column)) for column in _TABLE_COLUMNS[table_name]]
        + [ctx.col(column).alias(_join_key(alias, column, index)) for column, index in join_key_columns]
    )
    for predicate in predicates:
        frame = frame.filter(_expression_condition(ctx, predicate))
    return frame


def _execute_joinorder_expression_query(ctx: DataFrameContext, query_id: str) -> Any:
    tree = parse_one(_QUERY_MANAGER.get_query(query_id), read="duckdb")
    tables = _sql_tables(tree)
    predicates = _flatten_and(tree.args["where"].this)
    join_predicates = [predicate for predicate in predicates if _is_join_equality(predicate)]
    join_key_columns: dict[str, list[tuple[str, int]]] = {alias: [] for alias, _table in tables}
    for index, predicate in enumerate(join_predicates):
        left_alias, left_column = _column_ref(predicate.this)
        right_alias, right_column = _column_ref(predicate.expression)
        join_key_columns[left_alias].append((left_column, index))
        join_key_columns[right_alias].append((right_column, index))
    local_predicates = {
        alias: [
            predicate
            for predicate in predicates
            if not _is_join_equality(predicate) and _aliases_in(predicate) == {alias}
        ]
        for alias, _table in tables
    }
    frames = {
        alias: _prefixed_expression_frame(ctx, table_name, alias, local_predicates[alias], join_key_columns[alias])
        for alias, table_name in tables
    }

    result = frames[tables[0][0]]
    used_indices: set[int] = set()
    for step in _plan_join_sequence(tables, join_predicates):
        if step.predicate_index < 0:
            result = result.join(frames[step.new_alias], how="cross")
            continue
        result = result.join(
            frames[step.new_alias],
            left_on=_join_key(step.existing_alias, step.existing_column, step.predicate_index),
            right_on=_join_key(step.new_alias, step.new_column, step.predicate_index),
        )
        used_indices.add(step.predicate_index)

    used_join_predicates = {id(join_predicates[index]) for index in used_indices}
    for predicate in predicates:
        if id(predicate) not in used_join_predicates:
            result = result.filter(_expression_condition(ctx, predicate))

    return result.select(
        [
            ctx.col(_qualified(alias, column)).min().alias(output_alias)
            for alias, column, output_alias in _select_min_columns(tree)
        ]
    )


def _native_pandas_frame(frame: Any) -> Any:
    return frame.native if hasattr(frame, "native") else frame


def _pandas_value(frame: pd.DataFrame, node: exp.Expression) -> Any:
    if isinstance(node, exp.Column):
        alias, column = _column_ref(node)
        return frame[_qualified(alias, column)]
    return _literal_value(node)


def _pandas_not_null_mask(*values: Any) -> Any:
    mask = True
    for value in values:
        if hasattr(value, "notnull"):
            mask = mask & value.notnull()
    return mask


def _pandas_sql_compare(left: Any, right: Any, operator: Any) -> Any:
    return operator(left, right) & _pandas_not_null_mask(left, right)


def _pandas_condition(frame: pd.DataFrame, node: exp.Expression) -> Any:
    import pandas as pd

    if isinstance(node, exp.Paren):
        return _pandas_condition(frame, node.this)
    if isinstance(node, exp.And):
        return _pandas_condition(frame, node.this) & _pandas_condition(frame, node.expression)
    if isinstance(node, exp.Or):
        return _pandas_condition(frame, node.this) | _pandas_condition(frame, node.expression)
    if isinstance(node, exp.Not):
        return ~_pandas_condition(frame, node.this)
    if isinstance(node, exp.EQ):
        return _pandas_sql_compare(
            _pandas_value(frame, node.this), _pandas_value(frame, node.expression), lambda a, b: a == b
        )
    if isinstance(node, exp.NEQ):
        return _pandas_sql_compare(
            _pandas_value(frame, node.this), _pandas_value(frame, node.expression), lambda a, b: a != b
        )
    if isinstance(node, exp.GT):
        return _pandas_sql_compare(
            _pandas_value(frame, node.this), _pandas_value(frame, node.expression), lambda a, b: a > b
        )
    if isinstance(node, exp.GTE):
        return _pandas_sql_compare(
            _pandas_value(frame, node.this), _pandas_value(frame, node.expression), lambda a, b: a >= b
        )
    if isinstance(node, exp.LT):
        return _pandas_sql_compare(
            _pandas_value(frame, node.this), _pandas_value(frame, node.expression), lambda a, b: a < b
        )
    if isinstance(node, exp.Between):
        return _pandas_value(frame, node.this).between(
            _literal_value(node.args["low"]), _literal_value(node.args["high"])
        )
    if isinstance(node, exp.In):
        return _pandas_value(frame, node.this).isin([_literal_value(value) for value in node.expressions])
    if isinstance(node, exp.Like):
        matched = (
            _pandas_value(frame, node.this)
            .astype("string")
            .str.contains(_like_pattern_to_regex(_literal_value(node.expression)), regex=True, na=pd.NA)
        )
        return ~matched if node.args.get("negate") else matched
    if isinstance(node, exp.Is):
        value = _pandas_value(frame, node.this)
        return value.isna() if isinstance(node.expression, exp.Null) else value == _pandas_value(frame, node.expression)
    raise NotImplementedError(f"Unsupported JoinOrder DataFrame SQL predicate: {node}")


def _filter_pandas(frame: pd.DataFrame, predicate: exp.Expression) -> pd.DataFrame:
    return frame[_pandas_condition(frame, predicate).fillna(False)]


def _prefixed_pandas_frame(
    ctx: DataFrameContext, table_name: str, alias: str, predicates: list[exp.Expression]
) -> pd.DataFrame:
    frame = _native_pandas_frame(ctx.get_table(table_name))
    frame = frame.rename(columns={column: _qualified(alias, column) for column in _TABLE_COLUMNS[table_name]})
    for predicate in predicates:
        frame = _filter_pandas(frame, predicate)
    return frame


def _empty_safe_min(frame: Any, column: str) -> Any:
    import pandas as pd

    value = frame[column].min()
    if hasattr(value, "compute"):
        value = value.compute()
    return None if pd.isna(value) else value


def _execute_joinorder_pandas_query(ctx: DataFrameContext, query_id: str) -> pd.DataFrame:
    import pandas as pd

    tree = parse_one(_QUERY_MANAGER.get_query(query_id), read="duckdb")
    tables = _sql_tables(tree)
    predicates = _flatten_and(tree.args["where"].this)
    join_predicates = [predicate for predicate in predicates if _is_join_equality(predicate)]
    local_predicates = {
        alias: [
            predicate
            for predicate in predicates
            if not _is_join_equality(predicate) and _aliases_in(predicate) == {alias}
        ]
        for alias, _table in tables
    }
    frames = {
        alias: _prefixed_pandas_frame(ctx, table_name, alias, local_predicates[alias]) for alias, table_name in tables
    }

    result = frames[tables[0][0]]
    used_indices: set[int] = set()
    for step in _plan_join_sequence(tables, join_predicates):
        if step.predicate_index < 0:
            result = result.merge(frames[step.new_alias], how="cross")
            continue
        result = result.merge(
            frames[step.new_alias],
            left_on=_qualified(step.existing_alias, step.existing_column),
            right_on=_qualified(step.new_alias, step.new_column),
            how="inner",
        )
        used_indices.add(step.predicate_index)

    used_join_predicates = {id(join_predicates[index]) for index in used_indices}
    for predicate in predicates:
        if id(predicate) not in used_join_predicates:
            result = _filter_pandas(result, predicate)

    return pd.DataFrame(
        [
            {
                output_alias: _empty_safe_min(result, _qualified(alias, column))
                for alias, column, output_alias in _select_min_columns(tree)
            }
        ]
    )


def _make_generated_expression_impl(query_id: str) -> QueryImpl:
    def _impl(ctx: DataFrameContext) -> Any:
        return _execute_joinorder_expression_query(ctx, query_id)

    _impl.__name__ = f"q{query_id}_expression_impl"
    return _impl


def _make_generated_pandas_impl(query_id: str) -> QueryImpl:
    def _impl(ctx: DataFrameContext) -> Any:
        return _execute_joinorder_pandas_query(ctx, query_id)

    _impl.__name__ = f"q{query_id}_pandas_impl"
    return _impl


for _query_id in CANONICAL_JOINORDER_QUERIES:
    _IMPLS[f"q{_query_id}_expression_impl"] = _make_generated_expression_impl(_query_id)
    _IMPLS[f"q{_query_id}_pandas_impl"] = _make_generated_pandas_impl(_query_id)


JOINORDER_DATAFRAME_QUERIES = QueryRegistry("JoinOrder DataFrame")

_CATEGORY_CODES = {
    "A": QueryCategory.AGGREGATE,
    "F": QueryCategory.FILTER,
    "J": QueryCategory.JOIN,
    "M": QueryCategory.MULTI_JOIN,
}
_DEFAULT_GENERATED_CATEGORIES = (QueryCategory.MULTI_JOIN, QueryCategory.AGGREGATE, QueryCategory.FILTER)

_HAND_QUERY_METADATA = """\
1a|Production Companies Top 250|Production companies with top 250 ranked movies (5 tables)|J,A,F
1b|Production Companies Bottom 10|Variant of 1a with bottom 10 rank predicate (5 tables)|J,A,F
2a|German Company Character Name Movies|Movies from German companies with character-name-in-title keyword (5 tables)|J,A,F
3a|Scandinavian/German Sequel Movies|Sequel movies from Scandinavian/German countries after 1990 (4 tables)|J,A,F
4a|Sequel Movies with High Rating|Sequel movies with rating > 2.0 after 1990 (5 tables)|J,A,F
5a|European Theatrical Movies|European theatrical movies from France production companies after 2005 (5 tables)|J,A,F
6a|Superhero Movies with Robert Downey Jr.|Superhero/action movies featuring Robert Downey Jr. after 2000 (5 tables)|J,A,F
7a|Biography Movies with Person Criteria|Biography movies with specific person criteria (8 tables)|M,A,F
8a|Japanese Dubbed Movies|Japanese dubbed movies by specific actress criteria (7 tables)|M,A,F
9a|American Voice Actress Movies|American voice actress movies (8 tables)|M,A,F
10a|American Producer Movies|Movies with American producers after 1990 (7 tables)|M,A,F
11a|Non-Polish Sequel Movies with Follow Links|Sequel movies from Film/Warner companies with follow links (9 tables)|M,A,F
12a|Drama/Horror US Movies with High Ratings|US drama/horror movies with high ratings using double info_type join (8 tables)|M,A,F
"""


def _category_list(codes: str) -> list[QueryCategory]:
    return [_CATEGORY_CODES[code] for code in codes.split(",")]


_HAND_QUERY_METADATA_BY_ID = {
    query_id: (query_name, description, tuple(_category_list(category_codes)))
    for query_id, query_name, description, category_codes in reader(_HAND_QUERY_METADATA.splitlines(), delimiter="|")
}


def _make_query(query_id: str) -> DataFrameQuery:
    if query_id in _HAND_QUERY_METADATA_BY_ID:
        query_name, description, categories = _HAND_QUERY_METADATA_BY_ID[query_id]
    else:
        query_name = f"Canonical JoinOrder {query_id}"
        description = f"Canonical JoinOrder SQL query {query_id}; generated DataFrame translation"
        categories = _DEFAULT_GENERATED_CATEGORIES
    return DataFrameQuery(
        query_id=query_id,
        query_name=query_name,
        description=description,
        categories=list(categories),
        expression_impl=_IMPLS[f"q{query_id}_expression_impl"],
        pandas_impl=_IMPLS[f"q{query_id}_pandas_impl"],
    )


_QUERIES = [_make_query(query_id) for query_id in CANONICAL_JOINORDER_QUERIES]

for _query in _QUERIES:
    JOINORDER_DATAFRAME_QUERIES.register(_query)


def get_dataframe_queries() -> QueryRegistry:
    return JOINORDER_DATAFRAME_QUERIES


def get_implemented_dataframe_query_ids() -> list[str]:
    return list(IMPLEMENTED_DATAFRAME_QUERY_IDS)


def get_untranslated_dataframe_query_ids() -> list[str]:
    return list(UNTRANSLATED_DATAFRAME_QUERY_IDS)


__all__ = sorted({name for name in globals() if not name.startswith("_")} | set(_IMPLS))
