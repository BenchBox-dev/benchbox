from __future__ import annotations

from benchbox.platforms.questdb_rewrites import exp

ORDER = 180

_DECORRELATED_PREFIX = "_u_"


def applies(sql: str) -> bool:
    return _DECORRELATED_PREFIX in sql and "GROUP BY" in sql.upper()


def _decorrelated_aliases(select: exp.Select) -> set[str]:
    aliases: set[str] = set()
    for join in select.args.get("joins") or []:
        source = join.this
        if isinstance(source, exp.Subquery) and source.alias.startswith(_DECORRELATED_PREFIX):
            aliases.add(source.alias.lower())
    return aliases


def _own_columns(item: exp.Expr, select: exp.Select) -> list[exp.Column]:
    return [column for column in item.find_all(exp.Column) if column.find_ancestor(exp.Select) is select]


def _grouped(select: exp.Select) -> set[str]:
    group = select.args.get("group")
    return {key.sql() for key in group.expressions} if group is not None else set()


def _wrap(select: exp.Select, aliases: set[str]) -> None:
    grouped = _grouped(select)
    items = list(select.expressions)
    order = select.args.get("order")
    if order is not None:
        items.append(order)
    for item in items:
        for column in _own_columns(item, select):
            if column.table.lower() not in aliases or column.sql() in grouped:
                continue
            if column.find_ancestor(exp.AggFunc) is not None:
                continue
            column.replace(exp.Max(this=column.copy()))


def transform(expression: exp.Expr) -> exp.Expr:
    for select in list(expression.find_all(exp.Select)):
        if select.args.get("group") is None:
            continue
        aliases = _decorrelated_aliases(select)
        if aliases:
            _wrap(select, aliases)
    return expression
