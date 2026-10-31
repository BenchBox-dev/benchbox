from __future__ import annotations

import re

from benchbox.platforms.questdb_rewrites import exp

ORDER = 350

_DISTINCT_WINDOW = re.compile(r"\bdistinct\b.*\bover\s*\(", re.IGNORECASE | re.DOTALL)
_STAGE = "_qdw"


def applies(sql: str) -> bool:
    return _DISTINCT_WINDOW.search(sql) is not None


def _output_name(item: exp.Expr) -> str | None:
    return item.alias_or_name or None


def _wraps_window(select: exp.Select) -> bool:
    for item in select.expressions:
        target = item.this if isinstance(item, exp.Alias) else item
        if not isinstance(target, exp.Window) and target.find(exp.Window) is not None:
            return True
    return False


def _output_columns(select: exp.Select) -> dict[str, str] | None:
    columns: dict[str, str] = {}
    for item in select.expressions:
        name = _output_name(item)
        if name is None or isinstance(item, exp.Star):
            return None
        columns[item.this.sql() if isinstance(item, exp.Alias) else item.sql()] = name
    names = [name.lower() for name in columns.values()]
    if len(columns) != len(select.expressions) or len(set(names)) != len(names):
        return None
    return columns


def _stage(select: exp.Select) -> exp.Select | None:
    if not isinstance(select.args.get("distinct"), exp.Distinct) or select.args["distinct"].args.get("on"):
        return None
    if not _wraps_window(select):
        return None
    outputs = _output_columns(select)
    if outputs is None:
        return None

    order = select.args.get("order")
    if order is not None:
        remapped = order.copy()
        for ordered in remapped.expressions:
            for node in list(ordered.this.walk()):
                name = outputs.get(node.sql())
                if name is not None and not isinstance(node, exp.Window):
                    node.replace(exp.column(name, table=_STAGE))
            if any(column.table.lower() != _STAGE for column in ordered.this.find_all(exp.Column)):
                return None
    else:
        remapped = None

    inner = select.copy()
    for key in ("distinct", "order", "limit", "offset"):
        inner.set(key, None)
    outer = exp.Select(
        expressions=[exp.column(name, table=_STAGE) for name in outputs.values()],
        distinct=exp.Distinct(),
    ).from_(exp.Subquery(this=inner, alias=exp.TableAlias(this=exp.to_identifier(_STAGE))))
    for key, value in (("order", remapped), ("limit", select.args.get("limit")), ("offset", select.args.get("offset"))):
        if value is not None:
            outer.set(key, value.copy())
    return outer


def transform(expression: exp.Expr) -> exp.Expr:
    for select in reversed(list(expression.find_all(exp.Select))):
        replacement = _stage(select)
        if replacement is None:
            continue
        if select is expression:
            expression = replacement
        else:
            select.replace(replacement)
    return expression
