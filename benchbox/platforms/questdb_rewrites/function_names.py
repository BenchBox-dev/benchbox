from __future__ import annotations

from benchbox.platforms.questdb_rewrites import exp

ORDER = 130

_RENAMES = {
    exp.AnyValue: "first",
    exp.ApproxDistinct: "approx_count_distinct",
}


def _rename(node: exp.Expr) -> exp.Expr:
    name = _RENAMES.get(type(node))
    if name is not None:
        return exp.Anonymous(this=name, expressions=[node.this.copy()])
    if isinstance(node, exp.ApproxQuantile) and not node.args.get("accuracy") and not node.args.get("weight"):
        return exp.Anonymous(this="approx_percentile", expressions=[node.this.copy(), node.args["quantile"].copy()])
    return node


def transform(expression: exp.Expr) -> exp.Expr:
    return expression.transform(_rename)
