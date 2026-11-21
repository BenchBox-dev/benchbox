from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from benchbox.core.tpcds.c_tools import tpcds_option
from benchbox.core.tpcds.refresh_data import RefreshSet, generate_refresh_sets

pytestmark = [pytest.mark.unit, pytest.mark.medium, pytest.mark.tpcds]

NIGHTLY_SEED = 20260709


def _rows(path: Path) -> list[list[str]]:
    return [line.split("|") for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="module")
def sf1_refresh_sets(tmp_path_factory: pytest.TempPathFactory) -> dict[int, RefreshSet]:
    try:
        return generate_refresh_sets(1.0, 1, 2, tmp_path_factory.mktemp("sf1-refresh"), seed=NIGHTLY_SEED)
    except RuntimeError as exc:
        pytest.skip(f"dsdgen unavailable: {exc}")


@pytest.fixture(scope="module")
def sf1_item_ids(tmp_path_factory: pytest.TempPathFactory) -> set[str]:
    from benchbox.core.tpcds.generator import TPCDSDataGenerator

    output_dir = tmp_path_factory.mktemp("sf1-item")
    generator = TPCDSDataGenerator(scale_factor=1.0, output_dir=output_dir, quiet=True)
    if generator.dsdgen_exe is None:
        pytest.skip("dsdgen unavailable")
    generator._run_file_based_dsdgen(output_dir, [tpcds_option("table"), "item"])
    return {row[1] for row in _rows(output_dir / "item.dat")}


@pytest.mark.parametrize("set_id", [1, 2])
def test_web_order_lineitems_reference_distinct_real_items(
    sf1_refresh_sets: dict[int, RefreshSet], sf1_item_ids: set[str], set_id: int
):
    lineitems = _rows(sf1_refresh_sets[set_id].files["s_web_order_lineitem"])

    unknown = sorted({row[2] for row in lineitems} - sf1_item_ids)
    duplicates = sorted(pair for pair, count in Counter((row[0], row[2]) for row in lineitems).items() if count > 1)

    assert unknown == []
    assert duplicates == []


@pytest.mark.parametrize("set_id", [1, 2])
def test_web_returns_reference_ordered_items(sf1_refresh_sets: dict[int, RefreshSet], set_id: int):
    files = sf1_refresh_sets[set_id].files
    ordered = {(row[0], row[2]) for row in _rows(files["s_web_order_lineitem"])}
    returns = _rows(files["s_web_returns"])

    assert returns
    assert sorted({(row[1], row[3]) for row in returns} - ordered) == []
