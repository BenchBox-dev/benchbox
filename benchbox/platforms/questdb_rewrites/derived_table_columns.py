from __future__ import annotations

import re

from benchbox.platforms.questdb_rewrites import exp

ORDER = 80

_COLUMN_LIST = re.compile(r"\)\s*(?:AS\s+)?\w+\s*\(", re.IGNORECASE)


def applies(sql: str) -> bool:
    return _COLUMN_LIST.search(sql) is not None


def _alias_projections(subquery: exp.Subquery) -> None:
    alias = subquery.args.get("alias")
    select = subquery.this
    if alias is None or not alias.args.get("columns") or not isinstance(select, exp.Select):
        return
    names = alias.args["columns"]
    projections = select.expressions
    if len(projections) != len(names) or any(isinstance(item, exp.Star) for item in projections):
        return
    select.set(
        "expressions",
        [
            exp.alias_(item.this if isinstance(item, exp.Alias) else item, name.copy(), quoted=name.args.get("quoted"))
            for item, name in zip(projections, names, strict=True)
        ],
    )
    alias.set("columns", None)


def transform(expression: exp.Expr) -> exp.Expr:
    for subquery in list(expression.find_all(exp.Subquery)):
        _alias_projections(subquery)
    return expression
