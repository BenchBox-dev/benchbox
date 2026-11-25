from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

PINNED_SQLITE_ENGINE_VERSION = "3.50.0"
MINIMUM_PINNED_SQLITE_VERSION = (3, 46, 0)


def default_sqlite_engine_version() -> str:
    if sqlite3.sqlite_version_info >= MINIMUM_PINNED_SQLITE_VERSION:
        return PINNED_SQLITE_ENGINE_VERSION
    return sqlite3.sqlite_version


@contextmanager
def pinned_sqlite_engine_version(version: str | None = None) -> Iterator[None]:
    with patch(
        "benchbox.utils.dialect_utils.sqlite_engine_version", lambda: version or default_sqlite_engine_version()
    ):
        yield
