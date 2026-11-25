from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

PINNED_SQLITE_ENGINE_VERSION = "3.50.0"


@contextmanager
def pinned_sqlite_engine_version(version: str = PINNED_SQLITE_ENGINE_VERSION) -> Iterator[None]:
    with patch("benchbox.utils.dialect_utils.sqlite_engine_version", lambda: version):
        yield
