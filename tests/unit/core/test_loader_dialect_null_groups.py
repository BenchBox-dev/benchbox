# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from pathlib import Path
from unittest.mock import Mock

import pytest

from benchbox.core.dataframe.data_loader import DataFrameDataLoader
from benchbox.core.runner.runner import _ensure_data_generated
from benchbox.core.schemas import BenchmarkConfig

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def benchmark_config() -> BenchmarkConfig:
    return BenchmarkConfig(
        name="tpcds",
        display_name="TPC-DS",
        scale_factor=0.01,
        compress_data=True,
        compression_type="zstd",
        compression_level=None,
        options={},
    )


class _AttrBenchmark:
    name = "attr_fixture"
    csv_delimiter = ";"
    csv_has_header = False


def _write_csv(path: Path, rows: str) -> Path:
    path.write_text(rows, encoding="utf-8")
    return path


class TestSingleDialectSource:
    def test_manifest_metadata_wins_over_benchmark_attributes(self, tmp_path: Path):
        csv_path = _write_csv(tmp_path / "t.csv", "a;b\n1;2\n")
        loader = DataFrameDataLoader(platform="polars")
        dialects = loader._resolve_table_dialects(
            _AttrBenchmark(),
            {"t": csv_path},
            {"t": {"csv_delimiter": ",", "csv_has_header": True, "csv_null_marker": ""}},
        )
        assert dialects["t"].delimiter == ","
        assert dialects["t"].has_header is True
        assert dialects["t"].null_marker == ""

    def test_attribute_fallback_preserved_without_metadata(self, tmp_path: Path):
        csv_path = _write_csv(tmp_path / "t.csv", "a;b\n1;2\n")
        loader = DataFrameDataLoader(platform="polars")
        dialects = loader._resolve_table_dialects(_AttrBenchmark(), {"t": csv_path})
        assert dialects["t"].delimiter == ";"
        assert dialects["t"].has_header is False

    def test_null_markers_derive_from_resolved_dialect(self, tmp_path: Path):
        csv_path = _write_csv(tmp_path / "t.csv", "a;b\n1;2\n")
        loader = DataFrameDataLoader(platform="polars")
        markers = loader._get_null_markers(
            _AttrBenchmark(),
            {"t": csv_path},
            {"t": {"csv_null_marker": ""}},
        )
        assert markers["t"] == ""

    def test_manifest_hints_empty_without_manifest(self, tmp_path: Path):
        assert DataFrameDataLoader._read_manifest_dialect_hints(tmp_path, ["t"]) == {}
        assert DataFrameDataLoader._read_manifest_dialect_hints(None, ["t"]) == {}


class TestNullGroupKeys:
    def test_unmatched_left_join_key_kept_as_group(self):
        pd = pytest.importorskip("pandas")
        from benchbox.core.nyctaxi.dataframe_queries.queries import _pandas_top_zone

        trips = pd.DataFrame(
            {
                "pickup_datetime": pd.to_datetime(["2019-01-05", "2019-01-06", "2019-01-07"]),
                "pickup_location_id": [1, 2, 999],
            }
        )
        zones = pd.DataFrame(
            {
                "location_id": [1, 2],
                "zone": ["Midtown", "Uptown"],
                "borough": ["Manhattan", "Manhattan"],
            }
        )
        tables = {"trips": trips, "taxi_zones": zones}

        class SimplePandasContext:
            def get_table(self, name: str):
                return tables[name]

        result = _pandas_top_zone(SimplePandasContext(), "Q1", "pickup_location_id")
        assert len(result) == 3
        assert bool(result["zone"].isna().any())

    def test_dask_groupby_dropna_keeps_null_group(self):
        pytest.importorskip("pandas")
        dd = pytest.importorskip("dask.dataframe")
        import pandas as pd

        df = pd.DataFrame({"k": ["a", None, "a", None], "v": [1, 2, 3, 4]})
        result = dd.from_pandas(df, npartitions=2).groupby("k", dropna=False).sum().compute()
        assert len(result) == 2


class TestPopulatedTablesAuxiliaryHeal:
    def test_populated_tables_reuse_runs_auxiliary_hook(self, tmp_path: Path, benchmark_config: BenchmarkConfig):
        data_file = tmp_path / "customer.dat"
        data_file.write_text("1|sample\n")
        hook = Mock()

        class DummyBenchmark:
            def __init__(self) -> None:
                self.output_dir = tmp_path
                self.tables = {"customer": data_file}
                self.generate_data = Mock()
                self.ensure_auxiliary_data_files = hook

        dummy = DummyBenchmark()
        benchmark_config.options["table_mode"] = "external"

        result = _ensure_data_generated(dummy, benchmark_config)

        assert result == (False, False)
        dummy.generate_data.assert_not_called()
        hook.assert_called_once_with()


