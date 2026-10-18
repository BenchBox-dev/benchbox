# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from benchbox._vendor import sqlglot
from benchbox._vendor.sqlglot import exp
from benchbox._vendor.sqlglot.optimizer.scope import Scope, build_scope

CLI_DESCRIPTION = (
    "Static detector for correlated-subquery self-binding in benchmark SQL.\n"
    "\n"
    "PR #756 fixed three TPC-Havoc SQL variants where a correlated subquery's\n"
    "UNQUALIFIED correlation column silently bound to the INNER relation instead of\n"
    "the intended outer table, degenerating a per-row correlation into an\n"
    'uncorrelated scan (wrong results, but execution stays "green"). Example::\n'
    "\n"
    "    -- intended: correlate inner ps2 to the OUTER partsupp row\n"
    "    where ps2.ps_partkey = ps_partkey      -- BUG: ps_partkey binds to inner ps2\n"
    "    where ps2.ps_partkey = partsupp.ps_partkey   -- FIX: qualified to the outer\n"
    "\n"
    "This module is a conservative, catalog-aware lint that surfaces such cases as\n"
    "*candidates for human review* (it is advisory, not a hard gate - see\n"
    "``_project/TODO/main/planning/correlated-subquery-self-binding-cross-surface-audit.yaml``).\n"
    "\n"
    "Heuristic\n"
    "---------\n"
    "Inside a subquery, an unqualified column in a comparison predicate is flagged\n"
    "when ALL of the following hold:\n"
    "\n"
    "* the predicate's *other* operand is a column qualified by one of the subquery's\n"
    "  own (inner) source aliases - i.e. the predicate is correlation-shaped, pairing\n"
    "  an inner column with a bare column (a bare-column-vs-literal filter is ignored);\n"
    "* the bare column is provided by an inner source (so it silently binds inner);\n"
    "* the bare column is also provided by an enclosing (outer) source (so the author\n"
    "  plausibly meant the outer relation - the binding is *ambiguous*, which is what\n"
    "  makes the defect silent rather than an error).\n"
    "\n"
    'Inner/outer "provided" columns include base-table columns (from the benchmark\'s\n'
    "own DDL) AND the output columns of derived-table / CTE sources, so a correlation\n"
    "target that is a CTE (as in the #756 Q17 fix) is covered. Plain filters,\n"
    "fully-qualified correlations, and unambiguous (inner-only or outer-only) columns\n"
    "are not flagged, which keeps false positives low.\n"
    "\n"
    "The column->tables index is built by parsing the benchmark's own\n"
    "``CREATE TABLE`` DDL, so the detector is benchmark-agnostic: hand it any DDL and\n"
    "any SQL.\n"
    "\n"
    "Copyright 2026 Joe Harris / BenchBox Project\n"
    "\n"
    "Licensed under the MIT License. See LICENSE file in the project root for details.\n"
)


@dataclass(frozen=True)
class SelfBindingCandidate:
    column: str
    predicate: str
    inner_tables: tuple[str, ...]
    ambiguous_tables: tuple[str, ...]

    def describe(self) -> str:
        return (
            f"unqualified `{self.column}` in `{self.predicate}` silently binds to an "
            f"inner source {list(self.inner_tables)} though `{self.column}` is also "
            f"provided by an outer relation (defined in {list(self.ambiguous_tables)}) "
            f"- qualify it to the intended table"
        )


def build_column_table_index(create_table_sql: str, *, dialect: str = "duckdb") -> dict[str, set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for statement in sqlglot.parse(create_table_sql, read=dialect):
        if not isinstance(statement, exp.Create):
            continue
        table = statement.find(exp.Table)
        schema = statement.find(exp.Schema)
        if table is None or schema is None:
            continue
        table_name = table.name.lower()
        for column_def in schema.find_all(exp.ColumnDef):
            index[column_def.name.lower()].add(table_name)
    return dict(index)


def _provided_columns(scope: Scope, column_index: dict[str, set[str]]) -> set[str]:
    provided: set[str] = set()
    for source in scope.sources.values():
        if isinstance(source, exp.Table):
            table = source.name.lower()
            provided |= {col for col, tables in column_index.items() if table in tables}
        elif isinstance(source, Scope) and isinstance(source.expression, exp.Select):
            provided |= {select.alias_or_name.lower() for select in source.expression.selects if select.alias_or_name}
    return provided


def _inner_alias_qualified(operand: exp.Expression | None, inner_aliases: set[str]) -> bool:
    return isinstance(operand, exp.Column) and bool(operand.table) and operand.table.lower() in inner_aliases


def _comparison_predicates(scope: Scope) -> Iterable[exp.Binary]:
    select = scope.expression
    if not isinstance(select, exp.Select):
        return []
    roots: list[exp.Expression] = []
    for clause in (select.args.get("where"), select.args.get("having")):
        if clause is not None:
            roots.append(clause)
    for join in select.args.get("joins") or []:
        on = join.args.get("on")
        if on is not None:
            roots.append(on)
    predicates: list[exp.Binary] = []
    comparisons = (exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE)
    for root in roots:
        predicates.extend(root.find_all(*comparisons))
    return predicates


def _dml_outer_columns(tree: exp.Expression, column_index: dict[str, set[str]]) -> set[str]:
    if not isinstance(tree, (exp.Update, exp.Delete)):
        return set()
    outer_tables: set[str] = set()
    for table in tree.find_all(exp.Table):
        if table.find_ancestor(exp.Select) is None:
            outer_tables.add(table.name.lower())
    if not outer_tables:
        return set()
    return {col for col, tables in column_index.items() if tables & outer_tables}


def find_self_binding_candidates(
    sql: str, column_index: dict[str, set[str]], *, dialect: str = "duckdb"
) -> list[SelfBindingCandidate]:
    tree = sqlglot.parse_one(sql, read=dialect)
    root = build_scope(tree)
    if root is None:
        return []

    dml_outer_provided = _dml_outer_columns(tree, column_index)

    candidates: list[SelfBindingCandidate] = []
    seen: set[tuple[str, str]] = set()
    for scope in root.traverse():
        inner_aliases = {alias.lower() for alias in scope.sources}
        inner_provided = _provided_columns(scope, column_index)
        if not inner_provided:
            continue
        outer_provided: set[str] = set(dml_outer_provided)
        ancestor = scope.parent
        while ancestor is not None:
            outer_provided |= _provided_columns(ancestor, column_index)
            ancestor = ancestor.parent

        for predicate in _comparison_predicates(scope):
            left, right = predicate.left, predicate.right
            for bare, other in ((left, right), (right, left)):
                if not isinstance(bare, exp.Column) or bare.table:
                    continue
                name = bare.name.lower()
                if name not in inner_provided or name not in outer_provided:
                    continue
                if not _inner_alias_qualified(other, inner_aliases):
                    continue
                text = predicate.sql(dialect=dialect)
                key = (name, text)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    SelfBindingCandidate(
                        column=bare.name,
                        predicate=text,
                        inner_tables=tuple(sorted(inner_aliases)),
                        ambiguous_tables=tuple(sorted(column_index.get(name, set()))),
                    )
                )
    return candidates


def _scan_tpchavoc() -> int:
    from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark

    benchmark = TPCHavocBenchmark(scale_factor=1.0)
    index = build_column_table_index(benchmark.get_create_tables_sql(dialect="duckdb"))

    flagged = 0
    for query_id in benchmark.get_implemented_queries():
        for variant_id in range(1, 11):
            key = f"{query_id}_v{variant_id}"
            try:
                sql = benchmark.get_query(key)
                found = find_self_binding_candidates(sql, index)
            except Exception as exc:
                print(f"  {key}: SKIP ({type(exc).__name__}: {exc})")
                continue
            if found:
                flagged += 1
                print(f"  {key}:")
                for candidate in found:
                    print(f"    - {candidate.describe()}")
    print(f"\nTPC-Havoc SQL variants flagged: {flagged}")
    return flagged


def main() -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--benchmark", default="tpchavoc", choices=["tpchavoc"], help="benchmark to scan")
    parser.parse_args()
    _scan_tpchavoc()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
