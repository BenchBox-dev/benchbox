from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from functools import cache

from benchbox._vendor import sqlglot
from benchbox._vendor.sqlglot import exp

QUESTDB_DIALECT = "questdb"

Transform = Callable[[exp.Expr], exp.Expr]


@cache
def _transforms() -> tuple[Transform, ...]:
    modules = [
        importlib.import_module(f"{__name__}.{module.name}")
        for module in pkgutil.iter_modules(__path__)
        if not module.name.startswith("_")
    ]
    return tuple(module.transform for module in sorted(modules, key=lambda module: module.ORDER))


def rewrite_query(sql: str) -> str:
    try:
        statements = [statement for statement in sqlglot.parse(sql, read=QUESTDB_DIALECT) if statement is not None]
    except sqlglot.errors.SqlglotError:
        return sql
    if len(statements) != 1:
        return sql
    original = statements[0]
    rewritten = original
    for transform in _transforms():
        rewritten = transform(rewritten)
    if rewritten == original:
        return sql
    return rewritten.sql(dialect=QUESTDB_DIALECT, identify=False)
