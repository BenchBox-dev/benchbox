from __future__ import annotations

import importlib
import pkgutil
from functools import cache
from types import ModuleType

from benchbox._vendor import sqlglot
from benchbox._vendor.sqlglot import exp

QUESTDB_DIALECT = "questdb"


@cache
def _modules() -> tuple[ModuleType, ...]:
    modules = [
        importlib.import_module(f"{__name__}.{module.name}")
        for module in pkgutil.iter_modules(__path__)
        if not module.name.startswith("_")
    ]
    return tuple(sorted(modules, key=lambda module: module.ORDER))


def rewrite_query(sql: str) -> str:
    try:
        statements = [statement for statement in sqlglot.parse(sql, read=QUESTDB_DIALECT) if statement is not None]
    except sqlglot.errors.SqlglotError:
        return sql
    if len(statements) != 1:
        return sql
    original = statements[0]
    rewritten = original.copy()
    for module in _modules():
        applies = getattr(module, "applies", None)
        if applies is None or applies(sql):
            rewritten = module.transform(rewritten)
    if rewritten == original:
        return sql
    for module in _modules():
        finalize = getattr(module, "finalize", None)
        if finalize is not None:
            rewritten = finalize(rewritten)
    return rewritten.sql(dialect=QUESTDB_DIALECT, identify=False)
