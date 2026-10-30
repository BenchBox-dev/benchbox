from __future__ import annotations

from collections.abc import Callable

from benchbox.utils.dialect_utils import sql_expressions

exp = sql_expressions()

ColumnsOf = Callable[[str], "frozenset[str] | None"]

_INNER_KINDS = {"", "INNER", "CROSS"}


class _Unresolved(Exception):
    pass


def _flatten_and(node: exp.Expr) -> list[exp.Expr]:
    if isinstance(node, exp.And):
        return _flatten_and(node.left) + _flatten_and(node.right)
    return [node]


def _ref(node: exp.Expr) -> str:
    return node.alias_or_name.lower()


def _is_inner(join: exp.Join) -> bool:
    if join.args.get("side") or join.args.get("using") or join.args.get("method"):
        return False
    if not isinstance(join.this, (exp.Table, exp.Subquery)):
        return False
    return (join.args.get("kind") or "").upper() in _INNER_KINDS


def _derived_columns(node: exp.Subquery) -> frozenset[str] | None:
    select = node.this
    if not isinstance(select, exp.Select):
        return None
    if any(isinstance(item, exp.Star) or item.find(exp.Star) for item in select.expressions):
        return None
    return frozenset(item.alias_or_name.lower() for item in select.expressions)


class _Scope:
    def __init__(self, sources: list[exp.Expr], columns_of: ColumnsOf):
        self.refs = [_ref(source) for source in sources]
        self._sources = dict(zip(self.refs, sources))
        self._columns_of = columns_of
        self._columns: dict[str, frozenset[str] | None] = {}

    def _columns_for(self, ref: str) -> frozenset[str] | None:
        if ref not in self._columns:
            source = self._sources[ref]
            if isinstance(source, exp.Subquery):
                self._columns[ref] = _derived_columns(source)
            else:
                self._columns[ref] = self._columns_of(source.name.lower())
        return self._columns[ref]

    def tables_of(self, predicate: exp.Expr) -> set[str]:
        tables: set[str] = set()
        for column in predicate.find_all(exp.Column):
            qualifier = column.table.lower()
            if qualifier:
                if qualifier in self._sources:
                    tables.add(qualifier)
                continue
            owners = []
            for ref in self.refs:
                columns = self._columns_for(ref)
                if columns is None:
                    raise _Unresolved
                if column.name.lower() in columns:
                    owners.append(ref)
            if len(owners) > 1:
                raise _Unresolved
            tables.update(owners)
        return tables


def _is_column_equality(predicate: exp.Expr) -> bool:
    return (
        isinstance(predicate, exp.EQ)
        and isinstance(predicate.left, exp.Column)
        and isinstance(predicate.right, exp.Column)
    )


def _misplaced(joins: list[exp.Join], scope: _Scope, first: str, where_parts: list[exp.Expr]) -> bool:
    in_scope = {first}
    where_links = [scope.tables_of(predicate) for predicate in where_parts if _is_column_equality(predicate)]
    for join in joins:
        current = _ref(join.this)
        on = join.args.get("on")
        if on is None:
            if any(current in tables and tables <= in_scope | {current} for tables in where_links):
                return True
        else:
            for predicate in _flatten_and(on):
                tables = scope.tables_of(predicate)
                if not tables <= in_scope | {current} or current not in tables:
                    return True
        in_scope.add(current)
    return False


def _reassign(select: exp.Select, columns_of: ColumnsOf) -> None:
    from_ = select.args.get("from_")
    joins = select.args.get("joins") or []
    if not from_ or not joins or not isinstance(from_.this, (exp.Table, exp.Subquery)):
        return
    if not all(_is_inner(join) for join in joins):
        return

    sources = [from_.this] + [join.this for join in joins]
    scope = _Scope(sources, columns_of)
    first = _ref(from_.this)
    where = select.args.get("where")
    where_parts = _flatten_and(where.this) if where else []
    if not _misplaced(joins, scope, first, where_parts):
        return

    candidates: list[tuple[exp.Expr, set[str]]] = []
    kept_where: list[exp.Expr] = []
    for predicate in where_parts:
        tables: set[str] = set()
        if _is_column_equality(predicate):
            try:
                tables = scope.tables_of(predicate)
            except _Unresolved:
                tables = set()
        if len(tables) == 2:
            candidates.append((predicate, tables))
        else:
            kept_where.append(predicate)
    for join in joins:
        on = join.args.get("on")
        if on is None:
            continue
        for predicate in _flatten_and(on):
            tables = scope.tables_of(predicate)
            if len(tables) >= 2:
                candidates.append((predicate, tables))
            else:
                kept_where.append(predicate)

    pending = list(joins)
    in_scope = {first}
    ordered: list[exp.Join] = []
    while pending:
        chosen = next(
            (
                join
                for join in pending
                if any(_ref(join.this) in tables and tables <= in_scope | {_ref(join.this)} for _, tables in candidates)
            ),
            pending[0],
        )
        pending.remove(chosen)
        current = _ref(chosen.this)
        attached = [
            predicate for predicate, tables in candidates if current in tables and tables <= in_scope | {current}
        ]
        candidates = [(p, t) for p, t in candidates if not any(p is a for a in attached)]
        chosen.set("on", exp.and_(*[predicate.copy() for predicate in attached]) if attached else None)
        chosen.set("kind", None if attached else "CROSS")
        ordered.append(chosen)
        in_scope.add(current)

    kept_where.extend(predicate for predicate, _ in candidates)
    select.set("joins", ordered)
    select.set("where", exp.Where(this=exp.and_(*kept_where)) if kept_where else None)


def reassign_join_predicates(expression: exp.Expr, columns_of: ColumnsOf) -> exp.Expr:
    for select in reversed(list(expression.find_all(exp.Select))):
        try:
            _reassign(select, columns_of)
        except _Unresolved:
            continue
    return expression
