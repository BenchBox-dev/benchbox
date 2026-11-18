from __future__ import annotations

import pytest

from benchbox.core.dataframe import MemoryCheckResult


@pytest.fixture
def sufficient_dataframe_memory(monkeypatch: pytest.MonkeyPatch) -> None:

    def _sufficient_memory(
        _benchmark: str, scale_factor: float, platform: str, queries: object = None, **_options: object
    ) -> MemoryCheckResult:
        return MemoryCheckResult(
            is_safe=True,
            message="sufficient memory supplied by test fixture",
            estimated_memory_gb=0.0,
            available_memory_gb=float("inf"),
            scale_factor=scale_factor,
            platform=platform,
        )

    monkeypatch.setattr(
        "benchbox.platforms.dataframe.benchmark_mixin.check_sufficient_memory",
        _sufficient_memory,
    )
