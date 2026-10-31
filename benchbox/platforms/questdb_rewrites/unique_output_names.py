from __future__ import annotations

from benchbox.platforms.questdb_rewrites import exp

ORDER = 302


def _unique_aliases(select: exp.Select) -> None:
    seen = {projection.alias_or_name.lower() for projection in select.expressions if projection.alias_or_name}
    taken: set[str] = set()
    for projection in select.expressions:
        if not isinstance(projection, exp.Alias):
            continue
        name = projection.alias.lower()
        if name not in taken:
            taken.add(name)
            continue
        suffix = 2
        while f"{name}_{suffix}" in seen or f"{name}_{suffix}" in taken:
            suffix += 1
        renamed = f"{projection.alias}_{suffix}"
        taken.add(renamed.lower())
        projection.set("alias", exp.to_identifier(renamed))


def _rewrite(node: exp.Expr) -> exp.Expr:
    if isinstance(node, exp.Select):
        _unique_aliases(node)
    return node


def transform(expression: exp.Expr) -> exp.Expr:
    return expression.transform(_rewrite)
