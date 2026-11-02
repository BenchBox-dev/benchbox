from __future__ import annotations

import re

from benchbox.platforms.questdb_rewrites import exp

from ._select_walk import replace_selects
from .grouping_sets import _own_nodes, _scope_roots, has_grouping_syntax, resolve_ordinals, stage_aggregation

ORDER = 210

_GROUP_BY = re.compile(r"\bgroup\s+by\b", re.IGNORECASE)
_WINDOW = re.compile(r"\bover\s*\(", re.IGNORECASE)


def applies(sql: str) -> bool:
    return _GROUP_BY.search(sql) is not None and _WINDOW.search(sql) is not None


def _rewrite_select(select: exp.Select) -> exp.Expr | None:
    group = select.args.get("group")
    if group is None or not group.expressions or has_grouping_syntax(group):
        return None
    if not any(_own_nodes(root, select, exp.Window) for root in _scope_roots(select)):
        return None
    resolved = resolve_ordinals(select, group)
    return stage_aggregation(select, [list(resolved.expressions)]) if resolved is not None else None


def transform(expression: exp.Expr) -> exp.Expr:
    return replace_selects(expression, _rewrite_select)
