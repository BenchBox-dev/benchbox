from __future__ import annotations

from collections import Counter

import pytest

from benchbox.core.tpcds.refresh_data import RefreshSet, generate_refresh_sets

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]

SF1_ITEM_ROWS = 18000
NIGHTLY_SEED = 20260709


def _business_key_value(key: str) -> int:
    assert len(key) == 16 and set(key) <= set("ABCDEFGHIJKLMNOP"), f"malformed business key {key!r}"
    return sum((ord(char) - ord("A")) << (4 * index) for index, char in enumerate(key[8:] + key[:8]))


def _rows(refresh_set: RefreshSet, table: str) -> list[list[str]]:
    return [line.split("|") for line in refresh_set.files[table].read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="module")
def sf1_refresh_sets(tmp_path_factory: pytest.TempPathFactory) -> dict[int, RefreshSet]:
    try:
        return generate_refresh_sets(1.0, 1, 2, tmp_path_factory.mktemp("sf1-refresh"), seed=NIGHTLY_SEED)
    except RuntimeError as exc:
        pytest.skip(f"dsdgen unavailable: {exc}")


@pytest.mark.parametrize("set_id", [1, 2])
def test_web_order_lineitems_reference_distinct_real_items(sf1_refresh_sets: dict[int, RefreshSet], set_id: int):
    lineitems = _rows(sf1_refresh_sets[set_id], "s_web_order_lineitem")

    out_of_range = sorted({row[2] for row in lineitems if not 1 <= _business_key_value(row[2]) <= SF1_ITEM_ROWS})
    duplicates = sorted(pair for pair, count in Counter((row[0], row[2]) for row in lineitems).items() if count > 1)

    assert out_of_range == []
    assert duplicates == []


@pytest.mark.parametrize("set_id", [1, 2])
def test_web_returns_reference_ordered_items(sf1_refresh_sets: dict[int, RefreshSet], set_id: int):
    ordered = {(row[0], row[2]) for row in _rows(sf1_refresh_sets[set_id], "s_web_order_lineitem")}
    returns = _rows(sf1_refresh_sets[set_id], "s_web_returns")

    assert returns
    assert sorted({(row[1], row[3]) for row in returns} - ordered) == []
