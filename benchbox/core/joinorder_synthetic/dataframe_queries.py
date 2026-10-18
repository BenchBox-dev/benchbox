# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from csv import reader
from typing import TYPE_CHECKING, Any

from benchbox._vendor.sqlglot import exp, parse_one
from benchbox.core.dataframe.context import DataFrameContext
from benchbox.core.dataframe.query import DataFrameQuery, QueryCategory, QueryRegistry
from benchbox.core.joinorder.dataframe_queries import (
    _aliases_in,
    _column_ref,
    _empty_safe_min,
    _expression_condition,
    _filter_pandas,
    _flatten_and,
    _is_join_equality,
    _join_key,
    _plan_join_sequence,
    _prefixed_pandas_frame,
    _qualified,
    _select_min_columns,
    _sql_tables,
)
from benchbox.core.joinorder_synthetic.queries import JoinOrderQueryManager

if TYPE_CHECKING:
    import pandas as pd

_QUERY_MANAGER = JoinOrderQueryManager()


def _query_sql(query_id: str, query_manager: JoinOrderQueryManager | None = None) -> str:
    return (query_manager or _QUERY_MANAGER).get_query(query_id)


def _join_predicates(tree: exp.Select) -> tuple[list[tuple[str, str]], list[exp.Expression], list[exp.Expression]]:
    tables = _sql_tables(tree)
    predicates = _flatten_and(tree.args["where"].this)
    return tables, predicates, [predicate for predicate in predicates if _is_join_equality(predicate)]


def _referenced_columns(alias: str, tree: exp.Select, predicates: list[exp.Expression]) -> set[str]:
    columns = {
        column.name
        for node in [*predicates, *tree.expressions]
        for column in node.find_all(exp.Column)
        if str(column.table) == alias
    }
    return columns


def _prefixed_expression_frame(
    ctx: DataFrameContext,
    table_name: str,
    alias: str,
    predicates: list[exp.Expression],
    join_key_columns: list[tuple[str, int]],
    referenced_columns: set[str],
) -> Any:
    frame = ctx.get_table(table_name)
    frame = frame.select(
        [ctx.col(column).alias(_qualified(alias, column)) for column in sorted(referenced_columns)]
        + [ctx.col(column).alias(_join_key(alias, column, index)) for column, index in join_key_columns]
    )
    for predicate in predicates:
        frame = frame.filter(_expression_condition(ctx, predicate))
    return frame


def _execute_joinorder_expression_query(
    ctx: DataFrameContext, query_id: str, query_manager: JoinOrderQueryManager | None = None
) -> Any:
    tree = parse_one(_query_sql(query_id, query_manager), read="duckdb")
    tables, predicates, join_predicates = _join_predicates(tree)
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
        alias: _prefixed_expression_frame(
            ctx,
            table_name,
            alias,
            local_predicates[alias],
            join_key_columns[alias],
            _referenced_columns(alias, tree, predicates),
        )
        for alias, table_name in tables
    }

    result = frames[tables[0][0]]
    used_join_predicates: set[int] = set()
    for step in _plan_join_sequence(tables, join_predicates):
        if step.predicate_index < 0:
            result = result.join(frames[step.new_alias], how="cross")
            continue
        result = result.join(
            frames[step.new_alias],
            left_on=_join_key(step.existing_alias, step.existing_column, step.predicate_index),
            right_on=_join_key(step.new_alias, step.new_column, step.predicate_index),
        )
        used_join_predicates.add(id(join_predicates[step.predicate_index]))

    for predicate in predicates:
        if id(predicate) not in used_join_predicates:
            result = result.filter(_expression_condition(ctx, predicate))

    return result.select(
        [
            ctx.col(_qualified(alias, column)).min().alias(output_alias)
            for alias, column, output_alias in _select_min_columns(tree)
        ]
    )


def _execute_joinorder_pandas_query(
    ctx: DataFrameContext, query_id: str, query_manager: JoinOrderQueryManager | None = None
) -> pd.DataFrame:
    import pandas as pd

    tree = parse_one(_query_sql(query_id, query_manager), read="duckdb")
    tables, predicates, join_predicates = _join_predicates(tree)
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
    used_join_predicates: set[int] = set()
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
        used_join_predicates.add(id(join_predicates[step.predicate_index]))

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


def _make_expression_impl(query_id: str, query_manager: JoinOrderQueryManager | None = None) -> Any:
    def _impl(ctx: DataFrameContext) -> Any:
        return _execute_joinorder_expression_query(ctx, query_id, query_manager)

    _impl.__name__ = f"q{query_id}_expression_impl"
    return _impl


def _make_pandas_impl(query_id: str, query_manager: JoinOrderQueryManager | None = None) -> Any:
    def _impl(ctx: DataFrameContext) -> Any:
        return _execute_joinorder_pandas_query(ctx, query_id, query_manager)

    _impl.__name__ = f"q{query_id}_pandas_impl"
    return _impl


_IMPLS: dict[str, Any] = {}


def __getattr__(name: str) -> Any:
    if name in _IMPLS:
        return _IMPLS[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


try:
    from benchbox.core.joinorder.queries import CANONICAL_JOINORDER_QUERIES as _CANONICAL_IDS

    _ALL_QUERY_IDS = tuple(_CANONICAL_IDS)
except ImportError:
    _ALL_QUERY_IDS = ()

for _query_id in _ALL_QUERY_IDS:
    _IMPLS[f"q{_query_id}_expression_impl"] = _make_expression_impl(_query_id)
    _IMPLS[f"q{_query_id}_pandas_impl"] = _make_pandas_impl(_query_id)


JOINORDER_DATAFRAME_QUERIES = QueryRegistry("JoinOrder DataFrame")

_CATEGORY_CODES = {
    "AG": QueryCategory.AGGREGATE,
    "FI": QueryCategory.FILTER,
    "JO": QueryCategory.JOIN,
    "MJ": QueryCategory.MULTI_JOIN,
}

_QUERY_METADATA = """\
1a|Production Companies Top 250|Production companies with top 250 ranked movies (5 tables)|JO,AG,FI
1b|Production Companies Bottom 10|Variant of 1a with bottom 10 rank predicate (5 tables)|JO,AG,FI
2a|German Company Character Name Movies|Movies from German companies with character-name-in-title keyword (5 tables)|JO,AG,FI
3a|Scandinavian/German Sequel Movies|Sequel movies from Scandinavian/German countries after 1990 (4 tables)|JO,AG,FI
4a|Sequel Movies with High Rating|Sequel movies with rating > 2.0 after 1990 (5 tables)|JO,AG,FI
5a|European Theatrical Movies|European theatrical movies from France production companies after 2005 (5 tables)|JO,AG,FI
6a|Superhero Movies with Robert Downey Jr.|Superhero/action movies featuring Robert Downey Jr. after 2000 (5 tables)|JO,AG,FI
7a|Biography Movies with Person Criteria|Biography movies with specific person criteria (8 tables)|MJ,AG,FI
8a|Japanese Dubbed Movies|Japanese dubbed movies by specific actress criteria (7 tables)|MJ,AG,FI
9a|American Voice Actress Movies|American voice actress movies (8 tables)|MJ,AG,FI
10a|Russian Actor Movies|Movies with Russian actors after 2005 (7 tables)|MJ,AG,FI
11a|Non-Polish Sequel Movies with Follow Links|Sequel movies from Film/Warner companies with follow links (9 tables)|MJ,AG,FI
12a|US Movies with High Ratings|US movies with high ratings using double info_type join (8 tables)|MJ,AG,FI
"""


_QUERIES = [
    DataFrameQuery(
        query_id=query_id,
        query_name=query_name,
        description=description,
        categories=[_CATEGORY_CODES[code] for code in category_codes.split(",")],
        expression_impl=_IMPLS[f"q{query_id}_expression_impl"],
        pandas_impl=_IMPLS[f"q{query_id}_pandas_impl"],
    )
    for query_id, query_name, description, category_codes in reader(_QUERY_METADATA.splitlines(), delimiter="|")
]


def _categories_for(query_id: str, query_sql: str) -> list[QueryCategory]:
    tables = len(_sql_tables(parse_one(query_sql, read="duckdb")))
    join_category = QueryCategory.MULTI_JOIN if tables >= 6 else QueryCategory.JOIN
    return ([join_category] if tables > 1 else []) + [QueryCategory.AGGREGATE, QueryCategory.FILTER]


_QUERIES.extend(
    DataFrameQuery(
        query_id=query_id,
        query_name=f"JOB {query_id}",
        description=f"Canonical JOB query {query_id}; generated synthetic DataFrame translation.",
        categories=_categories_for(query_id, _QUERY_MANAGER.get_query(query_id)),
        expression_impl=_IMPLS[f"q{query_id}_expression_impl"],
        pandas_impl=_IMPLS[f"q{query_id}_pandas_impl"],
    )
    for query_id in _ALL_QUERY_IDS
    if query_id not in {query.query_id for query in _QUERIES}
)

for _query in _QUERIES:
    JOINORDER_DATAFRAME_QUERIES.register(_query)


def get_dataframe_queries(query_manager: JoinOrderQueryManager | None = None) -> QueryRegistry:
    if query_manager is None:
        return JOINORDER_DATAFRAME_QUERIES
    registry = QueryRegistry("JoinOrder DataFrame")
    for query_id, sql in query_manager.get_all_queries().items():
        registry.register(
            DataFrameQuery(
                query_id=query_id,
                query_name=f"JOB {query_id}",
                description=f"JoinOrder query {query_id} from the benchmark's query directory.",
                categories=_categories_for(query_id, sql),
                expression_impl=_make_expression_impl(query_id, query_manager),
                pandas_impl=_make_pandas_impl(query_id, query_manager),
                sql_equivalent=sql,
            )
        )
    return registry
