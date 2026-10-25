from __future__ import annotations

import importlib
import os
from types import ModuleType

try:
    from clickhouse_driver import Client as ClickHouseClient
    from clickhouse_driver.errors import Error as ClickHouseError
except ImportError:
    ClickHouseClient = None
    ClickHouseError = Exception

try:
    import clickhouse_connect
except ImportError:
    clickhouse_connect = None

chdb: ModuleType | None = None


def _import_with_cwd_restore(module_name: str) -> ModuleType:
    original_cwd = os.getcwd()
    try:
        return importlib.import_module(module_name)
    finally:
        os.chdir(original_cwd)


def import_chdb() -> ModuleType:
    return _import_with_cwd_restore("chdb")


def import_chdb_session() -> ModuleType:
    import_chdb()
    return _import_with_cwd_restore("chdb.session")


__all__ = [
    "ClickHouseClient",
    "ClickHouseError",
    "clickhouse_connect",
    "chdb",
    "import_chdb",
    "import_chdb_session",
]
