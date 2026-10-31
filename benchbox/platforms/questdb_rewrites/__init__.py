from __future__ import annotations

import importlib
import pkgutil
from functools import cache
from types import ModuleType

from benchbox.utils.dialect_utils import rewrite_sql_tree, sql_expressions

from ._join_scope import ColumnsOf, reassign_join_predicates
from ._or_factor import factor_shared_or_conjuncts

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


def rewrite_query(sql: str, columns_of: ColumnsOf | None = None) -> str:
    modules = _modules()
    active = [module for module in modules if getattr(module, "applies", None) is None or module.applies(sql)]

    def transform(expression):
        expression = factor_shared_or_conjuncts(expression)
        if columns_of is not None:
            expression = reassign_join_predicates(expression, columns_of)
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


def rewrite_with_modules(sql: str, *names: str) -> str:
    by_name = {module.__name__.rpartition(".")[2]: module for module in _modules()}
    unknown = [name for name in names if name not in by_name]
    if unknown:
        raise ValueError(f"unknown QuestDB rewrite modules: {', '.join(unknown)}")
    selected = [module for module in _modules() if module.__name__.rpartition(".")[2] in names]
    active = [module for module in selected if getattr(module, "applies", None) is None or module.applies(sql)]

    def transform(expression):
        for module in active:
            expression = module.transform(expression)
        return expression

    def finalize(expression):
        for module in selected:
            finalizer = getattr(module, "finalize", None)
            if finalizer is not None:
                expression = finalizer(expression)
        return expression

    return rewrite_sql_tree(sql, QUESTDB_DIALECT, transform, finalize)
