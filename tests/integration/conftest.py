from __future__ import annotations

import pytest

AMPLE_AVAILABLE_MEMORY_GB = 64.0


@pytest.fixture
def ample_available_memory(monkeypatch: pytest.MonkeyPatch) -> float:
    monkeypatch.setattr(
        "benchbox.core.dataframe.capabilities.get_available_memory_gb", lambda: AMPLE_AVAILABLE_MEMORY_GB
    )
    return AMPLE_AVAILABLE_MEMORY_GB
