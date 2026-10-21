from __future__ import annotations

from typing import Any

import pytest
import pytest_timeout

COVERAGE_TIMEOUT_FACTOR = 8


def scaled(settings: Any) -> Any:
    if not settings.timeout:
        return settings
    return settings._replace(timeout=settings.timeout * COVERAGE_TIMEOUT_FACTOR)


@pytest.hookimpl(tryfirst=True)
def pytest_timeout_set_timer(item: pytest.Item, settings: Any) -> object:
    return pytest_timeout.pytest_timeout_set_timer(item, scaled(settings))
