from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from benchbox.utils.query_shape_rewrites import sql_expressions

_ISO_LITERAL = re.compile(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?))?\s*")
_GROUP_REFERENCE = re.compile(r"\\(\d)")


def _normalized_literal(value: str) -> tuple[str, bool] | None:
    match = _ISO_LITERAL.fullmatch(value)
    if match is None:
        return None
    year, month, day, time = match.groups()
    date = f"{year}-{int(month):02d}-{int(day):02d}"
    return (f"{date} {time}", True) if time else (date, False)


def _unwrap(node):
    exp = sql_expressions()
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _temporal_type(node, types: Mapping[str, str]) -> str | None:
    exp = sql_expressions()
    node = _unwrap(node)
    if isinstance(node, exp.Column):
        kind = types.get(node.name.lower(), "")
        return kind if kind in {"date", "timestamp"} else None
    if isinstance(node, (exp.Min, exp.Max)):
        return _temporal_type(node.this, types)
    return None


def _cast_literal(node, kind: str) -> None:
    exp = sql_expressions()
    literal = _unwrap(node)
    if not (isinstance(literal, exp.Literal) and literal.is_string):
        return
    normalized = _normalized_literal(literal.name)
    if normalized is None:
        return
    value, has_time = normalized
    target = "timestamp" if has_time else kind
    literal.replace(exp.cast(exp.Literal.string(value), target, copy=False))


def cast_temporal_literals(expression, *, column_types: Mapping[str, str], copy: bool = True):
    exp = sql_expressions()
    tree = expression.copy() if copy else expression
    types = {name.lower(): kind.lower() for name, kind in column_types.items()}
    for node in tree.find_all(exp.Cast):
        normalized = _normalized_literal(node.this.name) if isinstance(node.this, exp.Literal) else None
        if normalized is not None and node.to.is_type("date"):
            node.this.set("this", normalized[0])
    for node in list(tree.find_all(exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
        for operand, other in ((node.this, node.expression), (node.expression, node.this)):
            kind = _temporal_type(operand, types)
            if kind:
                _cast_literal(other, kind)
    for node in list(tree.find_all(exp.Between)):
        kind = _temporal_type(node.this, types)
        if kind:
            _cast_literal(node.args["low"], kind)
            _cast_literal(node.args["high"], kind)
    for node in list(tree.find_all(exp.In)):
        kind = _temporal_type(node.this, types)
        if kind:
            for item in list(node.expressions):
                _cast_literal(item, kind)
    return tree


def _derived_source_columns(select, ctes: Mapping[str, list[str]]) -> set[str]:
    exp = sql_expressions()
    names: set[str] = set()
    from_clause = select.args.get("from_")
    sources = [from_clause.this] if from_clause is not None else []
    sources.extend(join.this for join in select.args.get("joins") or [])
    for source in sources:
        if isinstance(source, exp.Subquery):
            names.update(name.lower() for name in source.this.named_selects)
        elif isinstance(source, exp.Table):
            names.update(name.lower() for name in ctes.get(source.name.lower(), []))
    return names


def inline_group_by_aliases(expression, *, columns: Iterable[str], copy: bool = True):
    exp = sql_expressions()
    tree = expression.copy() if copy else expression
    physical_columns = {name.lower() for name in columns}
    ctes = {cte.alias.lower(): cte.this.named_selects for cte in tree.find_all(exp.CTE)}
    for select in list(tree.find_all(exp.Select)):
        group = select.args.get("group")
        if group is None:
            continue
        source_columns = physical_columns | _derived_source_columns(select, ctes)
        if "*" in source_columns:
            continue
        aliases = {
            projection.alias.lower(): projection.this
            for projection in select.expressions
            if isinstance(projection, exp.Alias) and projection.alias
        }
        for column in list(group.find_all(exp.Column)):
            name = column.name.lower()
            if column.table or name not in aliases or name in source_columns:
                continue
            if column.find_ancestor(exp.Select) is select:
                column.replace(aliases[name].copy())
    return tree


def regexp_group_references_to_dollar(expression, *, copy: bool = True):
    exp = sql_expressions()
    tree = expression.copy() if copy else expression
    for node in tree.find_all(exp.RegexpReplace):
        replacement = node.args.get("replacement")
        if isinstance(replacement, exp.Literal) and replacement.is_string:
            replacement.set("this", _GROUP_REFERENCE.sub(r"$\1", replacement.name))
    return tree


def rename_functions(expression, *, names: Mapping[str, str], copy: bool = True):
    exp = sql_expressions()
    tree = expression.copy() if copy else expression
    renamed = {source.lower(): target for source, target in names.items()}
    for node in tree.find_all(exp.Anonymous):
        target = renamed.get(node.name.lower())
        if target is not None:
            node.set("this", target)
    return tree
