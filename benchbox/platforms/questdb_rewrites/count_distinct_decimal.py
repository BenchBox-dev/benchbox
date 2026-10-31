from __future__ import annotations

from functools import cache

from benchbox.platforms.questdb_rewrites import exp

ORDER = 304


@cache
def _decimal_columns() -> frozenset[str]:
    from benchbox.core.tpcds.schema import TABLES, DataType
    from benchbox.core.tpcds_obt.schema import get_obt_columns

    names = {column.name for table in TABLES for column in table.columns if column.data_type is DataType.DECIMAL}
    names.update(column.name for column in get_obt_columns() if column.data_type is DataType.DECIMAL)
    return frozenset(name.lower() for name in names)


def _rewrite(node: exp.Expr) -> exp.Expr:
    if not isinstance(node, exp.Count) or not isinstance(node.this, exp.Distinct):
        return node
    arguments = node.this.expressions
    if len(arguments) != 1 or not isinstance(arguments[0], exp.Column):
        return node
    if arguments[0].name.lower() not in _decimal_columns():
        return node
    text = exp.Cast(this=arguments[0].copy(), to=exp.DataType.build("VARCHAR"))
    return exp.Anonymous(this="count_distinct", expressions=[text])


def transform(expression: exp.Expr) -> exp.Expr:
    return expression.transform(_rewrite)


def applies(sql: str) -> bool:
    return "distinct" in sql.lower()
