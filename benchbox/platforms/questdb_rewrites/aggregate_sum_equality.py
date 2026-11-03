from __future__ import annotations

from benchbox.platforms.questdb_rewrites import exp

ORDER = 240

TOLERANCE = "0.000001"

_SUM = "sum"
_MAX = "max"
_FIXED_SCALE = "fixed-scale"
_REVENUE_SQL = "l_extendedprice * (1 - l_discount)"
_SCALE_PRESERVING = (exp.Alias, exp.Paren, exp.Max, exp.Sum, exp.Window, exp.Subquery, exp.Select)
_MAX_DEPTH = 12


def _is_zero(node: exp.Expr | None) -> bool:
    return node is None or (isinstance(node.unnest(), exp.Literal) and node.unnest().this == "0")


def _is_revenue(node: exp.Expr | None) -> bool:
    if node is None:
        return False
    node = node.unnest()
    if isinstance(node, exp.Case):
        return (
            bool(node.args.get("ifs"))
            and all(_is_revenue(branch.args.get("true")) for branch in node.args["ifs"])
            and _is_zero(node.args.get("default"))
        )
    unqualified = node.copy()
    for column in unqualified.find_all(exp.Column):
        column.set("table", None)
    return unqualified.sql().lower() == _REVENUE_SQL


def _zero_fallback(inner: exp.Expr, parent: exp.Expr) -> bool:
    if isinstance(parent, exp.Coalesce):
        return all(_is_zero(other) for other in [parent.this, *parent.expressions] if other is not inner)
    if isinstance(parent, exp.If):
        return inner is parent.args.get("true")
    if isinstance(parent, exp.Case):
        branches = [branch.args.get("true") for branch in parent.args.get("ifs") or []]
        others = [branch for branch in [*branches, parent.args.get("default")] if branch is not inner.args.get("true")]
        return isinstance(inner, exp.If) and all(_is_zero(other) for other in others)
    return False


def _preserves_scale(inner: exp.Expr, root: exp.Expr) -> bool:
    parent = inner.parent
    while parent is not None and inner is not root:
        if not isinstance(parent, _SCALE_PRESERVING) and not _zero_fallback(inner, parent):
            return False
        inner, parent = parent, parent.parent
    return True


def applies(sql: str) -> bool:
    compact = sql.upper().replace(" ", "")
    return "SUM(" in compact and "MAX(" in compact


class _Scope:
    def __init__(self, root: exp.Expr):
        self._definitions: dict[str, list[exp.Select]] = {}
        for cte in root.find_all(exp.CTE):
            if isinstance(cte.this, exp.Select):
                self._definitions.setdefault(cte.alias.lower(), []).append(cte.this)
        for subquery in root.find_all(exp.Subquery):
            if subquery.alias and isinstance(subquery.this, exp.Select):
                self._definitions.setdefault(subquery.alias.lower(), []).append(subquery.this)
        for table in root.find_all(exp.Table):
            if table.alias and table.name.lower() in self._definitions:
                self._definitions.setdefault(table.alias.lower(), []).extend(self._definitions[table.name.lower()])

    def _sources(self, select: exp.Select) -> list[str]:
        sources = [select.args.get("from_")] + list(select.args.get("joins") or [])
        names: list[str] = []
        for source in sources:
            if source is None:
                continue
            target = source.this
            if isinstance(target, exp.Table):
                names.append(target.alias_or_name.lower())
            elif isinstance(target, exp.Subquery) and target.alias:
                names.append(target.alias.lower())
        return names

    def _column_flags(self, column: exp.Column, depth: int) -> frozenset[str]:
        if column.table:
            candidates = [column.table.lower()]
        else:
            owner = column.find_ancestor(exp.Select)
            candidates = self._sources(owner) if owner is not None else []
        flags: set[str] = set()
        for name in candidates:
            for definition in self._definitions.get(name, ()):
                for projection in definition.expressions:
                    if projection.alias_or_name.lower() == column.name.lower():
                        flags |= self.flags(projection, depth + 1)
        return frozenset(flags)

    def flags(self, node: exp.Expr, depth: int = 0) -> frozenset[str]:
        if depth > _MAX_DEPTH:
            return frozenset()
        if isinstance(node, exp.Subquery) and isinstance(node.this, exp.Select) and len(node.this.expressions) == 1:
            return self.flags(node.this.expressions[0], depth + 1)
        flags: set[str] = set()
        for sum_node in node.find_all(exp.Sum):
            flags.add(_SUM)
            if _is_revenue(sum_node.this) and _preserves_scale(sum_node, node):
                flags.add(_FIXED_SCALE)
        if node.find(exp.Max) is not None:
            flags.add(_MAX)
        for column in node.find_all(exp.Column):
            column_flags = self._column_flags(column, depth)
            if not _preserves_scale(column, node):
                column_flags -= {_FIXED_SCALE}
            flags |= column_flags
        return frozenset(flags)

    def is_revenue_max(self, node: exp.Expr) -> bool:
        return self.flags(node) >= {_SUM, _MAX, _FIXED_SCALE}

    def is_revenue(self, node: exp.Expr) -> bool:
        return self.flags(node) >= {_SUM, _FIXED_SCALE}


def _tolerance() -> exp.Expr:
    return exp.Literal.number(TOLERANCE)


def _rewrite(node: exp.Expr, scope: _Scope) -> exp.Expr:
    if isinstance(node, exp.EQ):
        left, right = node.this, node.expression
        if not (scope.is_revenue_max(left) or scope.is_revenue_max(right)):
            return node
        if not (scope.is_revenue(left) and scope.is_revenue(right)):
            return node
        difference = exp.Abs(this=exp.Sub(this=left.copy(), expression=right.copy()))
        return exp.Paren(this=exp.LTE(this=difference, expression=_tolerance()))
    if isinstance(node, exp.GTE):
        left, right = node.this, node.expression
        if scope.is_revenue_max(right) and scope.is_revenue(left):
            lowered = exp.Sub(this=right.copy(), expression=_tolerance())
            return exp.GTE(this=left.copy(), expression=exp.Paren(this=lowered))
    if isinstance(node, exp.LTE):
        left, right = node.this, node.expression
        if scope.is_revenue_max(left) and scope.is_revenue(right):
            lowered = exp.Sub(this=left.copy(), expression=_tolerance())
            return exp.LTE(this=exp.Paren(this=lowered), expression=right.copy())
    return node


def transform(expression: exp.Expr) -> exp.Expr:
    scope = _Scope(expression)
    candidates = [node for node in expression.find_all(exp.EQ, exp.GTE, exp.LTE) if not _is_tolerance_bound(node)]
    for node in reversed(candidates):
        replacement = _rewrite(node, scope)
        if replacement is node:
            continue
        if node is expression:
            return replacement
        node.replace(replacement)
    return expression


def _is_tolerance_bound(node: exp.Expr) -> bool:
    return isinstance(node, exp.LTE) and isinstance(node.this, exp.Abs)
