from __future__ import annotations

from benchbox.platforms.questdb_rewrites import exp

ORDER = 301


def _is_star_column(node: exp.Expr) -> bool:
    return (
        isinstance(node, exp.Column)
        and bool(node.table)
        and isinstance(node.this, exp.Identifier)
        and node.this.name == "*"
    )


def _star_source(select: exp.Select, alias: str) -> str | None:
    sources = [select.args["from_"].this] if select.args.get("from_") else []
    sources += [join.this for join in select.args.get("joins") or []]
    for source in sources:
        if not (isinstance(source, exp.Subquery) and source.alias == alias):
            continue
        inner = source.this
        if not (isinstance(inner, exp.Select) and len(inner.expressions) == 1):
            return None
        if not isinstance(inner.expressions[0], exp.Star):
            return None
        table = inner.args["from_"].this if inner.args.get("from_") else None
        return table.name if isinstance(table, exp.Table) and not table.db else None
    return None


def _single_projection(node: exp.Expr, cte_name: str) -> str | None:
    scope = node
    while scope is not None:
        with_ = scope.args.get("with_") if isinstance(scope, exp.Query) else None
        for cte in with_.expressions if with_ else []:
            if cte.alias == cte_name:
                body = cte.this
                if isinstance(body, exp.Select) and len(body.expressions) == 1:
                    name = body.expressions[0].alias_or_name
                    return name if name and name != "*" else None
                return None
        scope = scope.parent
    return None


def _resolve(node: exp.Expr) -> exp.Expr:
    if not _is_star_column(node):
        return node
    select = node.find_ancestor(exp.Select)
    if select is None:
        return node
    cte_name = _star_source(select, node.table)
    if cte_name is None:
        return node
    name = _single_projection(select, cte_name)
    if name is None:
        return node
    return exp.column(name, table=node.table)


def transform(expression: exp.Expr) -> exp.Expr:
    return expression.transform(_resolve)


def applies(sql: str) -> bool:
    return '."*"' in sql
