from __future__ import annotations

import re

from benchbox.platforms.questdb_rewrites import exp

ORDER = 340

_WHERE = re.compile(r"\bwhere\b", re.IGNORECASE)


def applies(sql: str) -> bool:
    return _WHERE.search(sql) is not None


def _is_fromless_filter(select: exp.Expr) -> bool:
    return (
        isinstance(select, exp.Select)
        and select.args.get("from_") is None
        and select.args.get("where") is not None
        and not select.args.get("joins")
    )


def _inline_exists(expression: exp.Expr) -> None:
    for node in list(expression.find_all(exp.Exists)):
        inner = node.this
        if not _is_fromless_filter(inner) or inner.args.get("group") or inner.args.get("having"):
            continue
        condition = inner.args["where"].this
        node.replace(exp.Coalesce(this=condition.copy(), expressions=[exp.false()]))


def _add_source(expression: exp.Expr) -> None:
    for select in expression.find_all(exp.Select):
        if _is_fromless_filter(select):
            select.set("from_", exp.From(this=exp.Table(this=exp.func("long_sequence", exp.Literal.number(1)))))


def transform(expression: exp.Expr) -> exp.Expr:
    _inline_exists(expression)
    _add_source(expression)
    return expression
