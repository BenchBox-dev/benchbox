from __future__ import annotations

import pytest

from tests.unit.sql_compat.golden_corpus import (
    CLASSES,
    METADATA,
    area_cases,
    area_names,
    classified,
    differences,
    recorded,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

AREAS = area_names()


def test_every_moved_area_has_a_corpus():
    assert AREAS


@pytest.mark.parametrize("area", AREAS)
def test_area_has_cases(area: str):
    assert area_cases(area)


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


@pytest.mark.parametrize("area", AREAS)
def test_recorded_differences_name_existing_cases(area: str):
    assert set(recorded(area)) <= {case.id for case in area_cases(area)}


@pytest.mark.parametrize("area", [name for name in AREAS if classified(name)])
def test_every_recorded_difference_is_classified_and_none_is_a_fork_gap(area: str):
    entries = recorded(area)
    unclassified = sorted(case_id for case_id, entry in entries.items() if entry.get("classification") not in CLASSES)
    assert not unclassified, f"{area}: unclassified differences {unclassified[:5]}"
    gaps = sorted(case_id for case_id, entry in entries.items() if entry["classification"] == "fork-gap")
    assert not gaps, f"{area}: fork gaps awaiting a fork fix or owner approval {gaps[:5]}"
    assert all(entry.get("reason") for entry in entries.values())
