from __future__ import annotations

from benchbox.platforms.questdb_rewrites import exp

ORDER = 60

_DIRECT_FUNCTIONS = {
    "YEAR": "year",
    "MONTH": "month",
    "DAY": "day",
    "HOUR": "hour",
    "MINUTE": "minute",
    "SECOND": "second",
    "WEEK": "week_of_year",
    "ISODOW": "day_of_week",
}


def applies(sql: str) -> bool:
    return "EXTRACT" in sql.upper()


def _extract_to_function(node: exp.Expr) -> exp.Expr:
    if not isinstance(node, exp.Extract):
        return node
    part = node.this.name.upper()
    operand = node.expression
    if part in _DIRECT_FUNCTIONS:
        return exp.Anonymous(this=_DIRECT_FUNCTIONS[part], expressions=[operand])
    if part == "DOW":
        sunday_first = exp.Anonymous(this="day_of_week_sunday_first", expressions=[operand])
        return exp.Paren(this=exp.Sub(this=sunday_first, expression=exp.Literal.number(1)))
    return node


def _expand_group_by_aliases(expression: exp.Expr) -> None:
    for select in list(expression.find_all(exp.Select)):
        group = select.args.get("group")
        if group is None:
            continue
        aliased = {
            item.alias.lower(): item.this
            for item in select.expressions
            if isinstance(item, exp.Alias) and item.this.find(exp.Extract) is not None
        }
        for key in list(group.expressions):
            if isinstance(key, exp.Column) and not key.table and key.name.lower() in aliased:
                key.replace(aliased[key.name.lower()].copy())


def transform(expression: exp.Expr) -> exp.Expr:
    _expand_group_by_aliases(expression)
    return expression.transform(_extract_to_function)
