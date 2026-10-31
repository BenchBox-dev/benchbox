from __future__ import annotations

from benchbox.platforms.questdb_rewrites import exp

ORDER = 180

_DECORRELATED_PREFIX = "_u_"


def applies(sql: str) -> bool:
    return _DECORRELATED_PREFIX in sql and "GROUP BY" in sql.upper()


def _conjuncts(node: exp.Expr) -> list[exp.Expr]:
    if isinstance(node, exp.And):
        return _conjuncts(node.left) + _conjuncts(node.right)
    return [node]


def _keyed_by_group(join: exp.Join, alias: str, grouped: set[str]) -> bool:
    on = join.args.get("on")
    if on is None:
        return False
    for conjunct in _conjuncts(on):
        if not isinstance(conjunct, exp.EQ):
            return False
        sides = [conjunct.left, conjunct.right]
        derived = [side for side in sides if isinstance(side, exp.Column) and side.table.lower() == alias]
        other = [side for side in sides if side not in derived]
        if len(derived) != 1 or len(other) != 1 or other[0].sql() not in grouped:
            return False
    return True


def _decorrelated_aliases(select: exp.Select, grouped: set[str]) -> set[str]:
    aliases: set[str] = set()
    for join in select.args.get("joins") or []:
        source = join.this
        if not isinstance(source, exp.Subquery) or not source.alias.startswith(_DECORRELATED_PREFIX):
            continue
        alias = source.alias.lower()
        if _keyed_by_group(join, alias, grouped):
            aliases.add(alias)
    return aliases


def _own_columns(item: exp.Expr, select: exp.Select) -> list[exp.Column]:
    return [column for column in item.find_all(exp.Column) if column.find_ancestor(exp.Select) is select]


def _grouped(select: exp.Select) -> set[str]:
    group = select.args.get("group")
    return {key.sql() for key in group.expressions} if group is not None else set()


def _wrap(select: exp.Select, aliases: set[str], grouped: set[str]) -> None:
    items = list(select.expressions)
    for key in ("having", "order"):
        clause = select.args.get(key)
        if clause is not None:
            items.append(clause)
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
        grouped = _grouped(select)
        aliases = _decorrelated_aliases(select, grouped)
        if aliases:
            _wrap(select, aliases, grouped)
    return expression
