from __future__ import annotations

from benchbox.utils.dialect_utils import sql_expressions

exp = sql_expressions()


def _flatten(node: exp.Expr, kind: type[exp.Connector]) -> list[exp.Expr]:
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, kind):
        return _flatten(node.left, kind) + _flatten(node.right, kind)
    return [node]


def _is_column_equality(node: exp.Expr) -> bool:
    return isinstance(node, exp.EQ) and isinstance(node.left, exp.Column) and isinstance(node.right, exp.Column)


def _factor(disjunction: exp.Or) -> list[exp.Expr] | None:
    branches = [_flatten(branch, exp.And) for branch in _flatten(disjunction, exp.Or)]
    shared = [
        conjunct
        for conjunct in branches[0]
        if _is_column_equality(conjunct) and all(any(conjunct == other for other in branch) for branch in branches[1:])
    ]
    if not shared:
        return None
    residual = []
    for branch in branches:
        rest = [conjunct for conjunct in branch if not any(conjunct == key for key in shared)]
        if not rest:
            return [key.copy() for key in shared]
        residual.append(exp.and_(*[conjunct.copy() for conjunct in rest]))
    combined = exp.or_(*residual)
    return [key.copy() for key in shared] + [exp.paren(combined)]


def factor_shared_or_conjuncts(expression: exp.Expr) -> exp.Expr:
    for where in list(expression.find_all(exp.Where)):
        conjuncts = _flatten(where.this, exp.And)
        rebuilt: list[exp.Expr] = []
        changed = False
        for conjunct in conjuncts:
            factored = _factor(conjunct) if isinstance(conjunct, exp.Or) else None
            if factored is None:
                rebuilt.append(conjunct)
            else:
                rebuilt.extend(factored)
                changed = True
        if changed:
            where.set("this", exp.and_(*[part.copy() for part in rebuilt]))
    return expression
