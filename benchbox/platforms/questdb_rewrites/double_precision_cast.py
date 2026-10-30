from __future__ import annotations

import re

from benchbox.platforms.questdb_rewrites import exp

ORDER = 90

_FLOAT_TYPE = re.compile(r"(?i)\bdouble\s+precision\b")


def applies(sql: str) -> bool:
    return _FLOAT_TYPE.search(sql) is not None


def _plain_double(node: exp.Expr) -> exp.Expr:
    if isinstance(node, (exp.Cast, exp.TryCast)):
        target = node.args["to"]
        if target.this == exp.DataType.Type.DOUBLE and not target.expressions:
            node.set("to", exp.DataType(this=exp.DataType.Type.USERDEFINED, kind="DOUBLE"))
    return node


def transform(expression: exp.Expr) -> exp.Expr:
    return expression.transform(_plain_double)


finalize = transform
