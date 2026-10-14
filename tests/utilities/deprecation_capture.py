from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

DEPRECATION_LOGGER = "benchbox.core.deprecation_notices"


@contextmanager
def captured_deprecations(caplog: pytest.LogCaptureFixture) -> Iterator[list[str]]:
    seen: list[str] = []
    with caplog.at_level(logging.WARNING, logger=DEPRECATION_LOGGER):
        start = len(caplog.records)
        yield seen
        seen.extend(record.getMessage() for record in caplog.records[start:] if record.name == DEPRECATION_LOGGER)
