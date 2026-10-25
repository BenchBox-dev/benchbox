from __future__ import annotations

import gc
import os
import sys
import tempfile
from functools import lru_cache
from types import ModuleType

import pytest

_MAX_REASON_CHARS = 240


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return text if len(text) <= _MAX_REASON_CHARS else text[: _MAX_REASON_CHARS - 3] + "..."


@lru_cache(maxsize=1)
def chdb_skip_reason() -> str | None:
    from benchbox.platforms.clickhouse._dependencies import import_chdb

    try:
        module = import_chdb()
    except ModuleNotFoundError as exc:
        if exc.name == "chdb":
            return "chDB not installed"
        return f"chDB is installed but a dependency is missing: {_first_line(exc)}"
    except (ImportError, OSError) as exc:
        if "mis-aligned LINKEDIT" in str(exc):
            return (
                "chDB is installed but its native library cannot be loaded: "
                "this macOS release's loader rejects the published chdb wheel "
                "build (mis-aligned LINKEDIT string pool); run these tests on "
                "Linux CI or use a ClickHouse server backend on this host"
            )
        return f"chDB is installed but its native library cannot be loaded: {_first_line(exc)}"

    if not hasattr(module, "connect"):
        return "chDB is installed but its import is incomplete (no connect attribute)"
    try:
        connection = module.connect(":memory:")
        try:
            if str(connection.query("SELECT 1", "CSV")).strip() != "1":
                return "chDB is installed but its SQL probe returned an unexpected result"
            connection.query("CREATE TABLE benchbox_usability_probe (value UInt8) ENGINE=Memory", "CSV")
            connection.query("INSERT INTO benchbox_usability_probe VALUES (1)", "CSV")
            if str(connection.query("SELECT value FROM benchbox_usability_probe", "CSV")).strip() != "1":
                return "chDB is installed but its table probe returned an unexpected result"
        finally:
            connection.close()
    except (ImportError, OSError, RuntimeError, getattr(module, "ChdbError", RuntimeError)) as exc:
        return f"chDB is installed but its connection or SQL runtime is unusable: {_first_line(exc)}"
    return None


def chdb_usable() -> bool:
    return chdb_skip_reason() is None


def require_chdb() -> ModuleType:
    from benchbox.platforms.clickhouse._dependencies import import_chdb

    reason = chdb_skip_reason()
    if reason is not None:
        pytest.skip(reason, allow_module_level=True)
    return import_chdb()


_CHDB_OPEN_HOOK_TARGETS = (("chdb.session", "Session"), ("chdb.state.sqlitelike", "Connection"))
_chdb_open_count = 0
_chdb_hooked: set[type] = set()


def _real_chdb_module(name: str) -> ModuleType | None:
    module = sys.modules.get(name)
    return module if getattr(module, "__file__", None) is not None else None


def chdb_loaded() -> bool:
    return _real_chdb_module("chdb.session") is not None


def chdb_open_count() -> int:
    return _chdb_open_count


def install_chdb_open_counter() -> None:
    for module_name, class_name in _CHDB_OPEN_HOOK_TARGETS:
        module = _real_chdb_module(module_name)
        cls = getattr(module, class_name, None)
        if not isinstance(cls, type) or cls in _chdb_hooked:
            continue
        original = cls.__init__

        def counted_init(self, *args, _original=original, **kwargs):
            global _chdb_open_count
            _chdb_open_count += 1
            _original(self, *args, **kwargs)

        cls.__init__ = counted_init
        _chdb_hooked.add(cls)


def chdb_open_connection_error() -> str | None:
    session_module = _real_chdb_module("chdb.session")
    if session_module is None:
        return None

    gc.collect()
    with tempfile.TemporaryDirectory(suffix=".db.chdb") as probe_path:
        try:
            session_module.Session(path=probe_path).close()
        except RuntimeError as exc:
            if "EmbeddedServer already initialized" not in str(exc):
                raise
            return _first_line(exc)
    return None


@lru_cache(maxsize=1)
def pyspark_skip_reason() -> str | None:
    if sys.platform == "win32":
        return "PySpark tests skipped on Windows - Hadoop requires winutils.exe setup"

    from benchbox.platforms.pyspark import PYSPARK_AVAILABLE, get_java_skip_reason

    if not PYSPARK_AVAILABLE:
        return "PySpark not installed"

    java_home = os.environ.get("JAVA_HOME")
    try:
        java_reason = get_java_skip_reason()
    finally:
        if java_home is None:
            os.environ.pop("JAVA_HOME", None)
        else:
            os.environ["JAVA_HOME"] = java_home
    if java_reason:
        return java_reason
    return None


def pyspark_usable() -> bool:
    return pyspark_skip_reason() is None


def require_pyspark() -> None:
    reason = pyspark_skip_reason()
    if reason is not None:
        pytest.skip(reason)
