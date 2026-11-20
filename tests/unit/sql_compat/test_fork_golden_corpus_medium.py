from __future__ import annotations

import pytest

from tests.unit.sql_compat.golden_corpus import (
    METADATA,
    area_names,
    differences,
    recorded,
)

pytestmark = [pytest.mark.unit, pytest.mark.medium]

AREAS = area_names()


@pytest.mark.parametrize("area", AREAS)
def test_legacy_and_fork_differences_equal_the_recorded_set(area: str):
    stored = {
        case_id: {k: v for k, v in entry.items() if k not in METADATA} for case_id, entry in recorded(area).items()
    }
    assert differences(area) == stored, (
        f"{area}: legacy and fork output differ from the owner-accepted set. "
        "Review the change, then run `uv run -- python -m tests.unit.sql_compat.golden_corpus "
        f"{area}` to record it."
    )
