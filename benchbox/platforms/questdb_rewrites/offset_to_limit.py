from __future__ import annotations

import re

from benchbox.platforms.questdb_rewrites import exp

ORDER = 40

_UNBOUNDED_UPPER = 9223372036854775807

_OFFSET_KEYWORD = re.compile(r"\boffset\b", re.IGNORECASE)


def applies(sql: str) -> bool:
    return _OFFSET_KEYWORD.search(sql) is not None


def _integer(node: exp.Expr | None) -> int | None:
    if isinstance(node, exp.Literal) and not node.is_string and node.name.isdigit():
        return int(node.name)
    return None


def _rewrite(node: exp.Expr) -> exp.Expr:
    offset_node = node.args.get("offset")
    if not isinstance(offset_node, exp.Offset):
        return node
    lower = _integer(offset_node.expression)
    if lower is None:
        return node
    limit_node = node.args.get("limit")
    if limit_node is None:
        upper = _UNBOUNDED_UPPER
    elif isinstance(limit_node, exp.Limit) and not limit_node.args.get("expressions"):
        count = _integer(limit_node.expression)
        if count is None:
            return node
        upper = lower + count
    else:
        return node
    node.set("offset", None)
    node.set("limit", exp.Limit(expression=exp.Var(this=f"{lower}, {upper}")))
    return node


def transform(expression: exp.Expr) -> exp.Expr:
    return expression.transform(_rewrite)
