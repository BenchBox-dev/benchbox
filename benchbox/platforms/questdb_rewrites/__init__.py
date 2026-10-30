from __future__ import annotations

import importlib
import pkgutil
from functools import cache
from types import ModuleType

from benchbox.utils.dialect_utils import rewrite_sql_tree, sql_expressions

QUESTDB_DIALECT = "questdb"

exp = sql_expressions()


@cache
def _modules() -> tuple[ModuleType, ...]:
    modules = [
        importlib.import_module(f"{__name__}.{module.name}")
        for module in pkgutil.iter_modules(__path__)
        if not module.name.startswith("_")
    ]
    return tuple(sorted(modules, key=lambda module: module.ORDER))


def rewrite_query(sql: str) -> str:
    modules = _modules()
    active = [module for module in modules if getattr(module, "applies", None) is None or module.applies(sql)]

    def transform(expression):
        for module in active:
            expression = module.transform(expression)
        return expression

    def finalize(expression):
        for module in modules:
            finalizer = getattr(module, "finalize", None)
            if finalizer is not None:
                expression = finalizer(expression)
        return expression

    return rewrite_sql_tree(sql, QUESTDB_DIALECT, transform, finalize)
