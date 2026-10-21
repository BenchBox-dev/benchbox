from __future__ import annotations

import pytest

from tests.unit.sql_compat.golden_corpus import area_cases, area_names, differences, recorded

pytestmark = [pytest.mark.unit, pytest.mark.fast]

AREAS = area_names()


def test_every_moved_area_has_a_corpus():
    assert AREAS


@pytest.mark.parametrize("area", AREAS)
def test_area_has_cases(area: str):
    assert area_cases(area)


@pytest.mark.parametrize("area", AREAS)
def test_legacy_and_fork_differences_equal_the_recorded_set(area: str):
    assert differences(area) == recorded(area), (
        f"{area}: legacy and fork output differ from the owner-accepted set. "
        "Review the change, then run `uv run -- python -m tests.unit.sql_compat.golden_corpus "
        f"{area}` to record it."
    )


@pytest.mark.parametrize("area", AREAS)
def test_recorded_differences_name_existing_cases(area: str):
    assert set(recorded(area)) <= {case.id for case in area_cases(area)}
