from __future__ import annotations

import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from benchbox.core.results.metrics import TPCMetricsCalculator
from benchbox.core.tpch.generator import TPCHDataGenerator
from benchbox.core.tpch.refresh import require_refresh_support
from tests.integration._tpch_refresh_utils import _check_rf1_rf2_with_expected_counts

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.tpch]

DBGEN_PATH_BUFFER_SIZE = 128
LONGEST_UPDATE_FILE_NAME = "lineitem.tbl.u5"
DBGEN_OVERFLOWING_DIR_LENGTH = DBGEN_PATH_BUFFER_SIZE - len(LONGEST_UPDATE_FILE_NAME)


def test_sqlite_sf001_applies_rf1_and_rf2_with_expected_counts_and_deleted_keys(tmp_path: Path) -> None:
    _check_rf1_rf2_with_expected_counts(sqlite3.connect(":memory:"), tmp_path)


def test_duckdb_sf001_applies_rf1_and_rf2_with_expected_counts_and_deleted_keys(tmp_path: Path) -> None:
    import duckdb

    _check_rf1_rf2_with_expected_counts(duckdb.connect(":memory:"), tmp_path)


@pytest.mark.parametrize("platform_name", ["unsupported-engine", "dataframe"])
def test_refresh_functions_reject_unsupported_adapters(platform_name: str) -> None:
    class Adapter:
        supports_refresh_functions = False

    adapter = Adapter()
    adapter.platform_name = platform_name

    with pytest.raises(NotImplementedError, match=platform_name):
        require_refresh_support(adapter)


def test_dbgen_update_sets_use_generated_files_with_distinct_order_keys(tmp_path: Path) -> None:
    from benchbox.core.tpch.refresh import generate_refresh_update_sets

    update_sets = generate_refresh_update_sets(0.01, 5, 2, tmp_path / "dbgen-output")
    order_keys = {
        set_id: {int(line.split("|", 1)[0]) for line in update_set.orders_path.read_text(encoding="utf-8").splitlines()}
        for set_id, update_set in update_sets.items()
    }

    assert set(update_sets) == {5, 6}
    assert all(
        path.is_file()
        for update_set in update_sets.values()
        for path in (update_set.orders_path, update_set.lineitem_path, update_set.delete_path)
    )
    assert order_keys[5]
    assert order_keys[6]
    assert order_keys[5].isdisjoint(order_keys[6])


@pytest.fixture(params=["short-base", "long-base"])
def base_dir(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Path]:
    if request.param == "long-base":
        yield tmp_path / ("b" * 100)
        return
    with tempfile.TemporaryDirectory(dir="/tmp") as short_base:
        yield Path(short_base)


def _output_dir_beyond_dbgen_path_buffer(base: Path) -> Path:
    output_dir = base / "dbgen-output"
    padding = DBGEN_OVERFLOWING_DIR_LENGTH - len(str(output_dir))
    if padding <= 0:
        return output_dir
    return base / ("p" * padding) / output_dir.name


def test_dbgen_update_sets_succeed_with_long_output_paths(base_dir: Path) -> None:
    from benchbox.core.tpch.refresh import generate_refresh_update_sets

    output_dir = _output_dir_beyond_dbgen_path_buffer(base_dir)
    assert len(str(output_dir)) >= DBGEN_OVERFLOWING_DIR_LENGTH
    update_sets = generate_refresh_update_sets(0.01, 5, 1, output_dir)

    assert set(update_sets) == {5}
    assert update_sets[5].orders_path.is_file()
    assert update_sets[5].lineitem_path.is_file()
    assert update_sets[5].delete_path.is_file()


def test_dbgen_base_tables_succeed_with_long_output_paths(tmp_path: Path) -> None:
    output_dir = tmp_path / ("a" * 80) / ("b" * 80) / ("c" * 80)
    assert len(str(output_dir.resolve())) >= 250

    generator = TPCHDataGenerator(scale_factor=0.01, output_dir=output_dir, quiet=True)
    generated = generator._run_dbgen_native(output_dir)

    assert generated is None or generated
    assert (output_dir / "lineitem.tbl").is_file() or any(output_dir.glob("lineitem.tbl.*"))


def test_qphh_metric_uses_24_power_terms_and_rounds_to_one_decimal_place() -> None:
    power_terms = [1.0, *([1.0] * 22), 4.0]
    power = TPCMetricsCalculator.calculate_power_at_size(power_terms, scale_factor=1.0)
    throughput = 123.456

    qphh = TPCMetricsCalculator.calculate_qphh_at_size(power, throughput)

    assert len(power_terms) == 24
    assert power == pytest.approx(3600.0 / 4.0 ** (1.0 / 24.0))
    assert qphh == round((power * throughput) ** 0.5, 1)
