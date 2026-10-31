from __future__ import annotations

from benchbox.platforms.questdb_rewrites import exp

ORDER = 170


def applies(sql: str) -> bool:
    upper = sql.upper()
    return "DATEADD" in upper and ("CASE" in upper or "COALESCE" in upper)


def _is_date_add(node: exp.Expr) -> bool:
    return isinstance(node, exp.Anonymous) and node.name.lower() == "dateadd"


def _is_date_cast(node: exp.Expr) -> bool:
    return isinstance(node, exp.Cast) and node.to.is_type(exp.DType.DATE)


def _as_timestamp(node: exp.Expr) -> exp.Expr:
    return exp.Cast(this=node.copy(), to=exp.DataType.build("TIMESTAMP"))


def _align(branches: list[exp.Expr]) -> None:
    if not any(_is_date_add(branch) for branch in branches):
        return
    for branch in branches:
        if _is_date_cast(branch):
            branch.replace(_as_timestamp(branch))


def transform(expression: exp.Expr) -> exp.Expr:
    for case in list(expression.find_all(exp.Case)):
        branches = [item.args["true"] for item in case.args.get("ifs") or []]
        default = case.args.get("default")
        if default is not None:
            branches.append(default)
        _align(branches)
    for coalesce in list(expression.find_all(exp.Coalesce)):
        _align([coalesce.this, *coalesce.expressions])
    return expression
