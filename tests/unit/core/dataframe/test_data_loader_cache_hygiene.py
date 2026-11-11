from pathlib import Path
from unittest.mock import patch

import pytest

from benchbox.core.dataframe.capabilities import DataFormat
from benchbox.core.dataframe.data_loader import ConversionStatus, DataFrameDataLoader

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _BenchmarkStub:
    name = "tpcds"

    @staticmethod
    def get_schema() -> dict[str, dict[str, list[dict[str, str]]]]:
        return {
            "store_sales": {
                "columns": [
                    {"name": "ss_sold_date_sk", "type": "INTEGER"},
                ]
            }
        }


class _SchemaLessBenchmarkStub:
    name = "unknown_benchmark"


def test_filter_source_files_for_benchmark_drops_non_schema_tables(tmp_path: Path) -> None:
    loader = DataFrameDataLoader(cache_dir=tmp_path)
    benchmark = _BenchmarkStub()

    source_files = {
        "store_sales": [tmp_path / "store_sales.dat"],
        "tpcds_sales_returns_obt": [tmp_path / "tpcds_sales_returns_obt.dat"],
    }

    filtered = loader._filter_source_files_for_benchmark(benchmark, source_files)

    assert set(filtered.keys()) == {"store_sales"}


def test_filter_source_files_for_benchmark_warns_when_expected_tables_unavailable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    loader = DataFrameDataLoader(cache_dir=tmp_path)
    benchmark = _SchemaLessBenchmarkStub()
    source_files = {"table_a": [tmp_path / "table_a.dat"]}

    with caplog.at_level("WARNING"):
        filtered = loader._filter_source_files_for_benchmark(benchmark, source_files)

    assert set(filtered.keys()) == {"table_a"}
    assert "source filtering is disabled" in caplog.text


def test_convert_data_prunes_untracked_cache_leaf_files(tmp_path: Path) -> None:
    loader = DataFrameDataLoader(cache_dir=tmp_path)
    benchmark = _BenchmarkStub()

    source_file = tmp_path / "store_sales.dat"
    source_file.write_text("1|\n", encoding="utf-8")

    cache_path = loader.cache.get_cache_path("tpcds", 1.0, DataFormat.PARQUET)
    cache_path.mkdir(parents=True, exist_ok=True)
    stale_leaf = cache_path / "stale.parquet"
    stale_leaf.write_text("old", encoding="utf-8")
    keep_dir = cache_path / "old_subdir"
    keep_dir.mkdir()
    (keep_dir / "nested.txt").write_text("keep", encoding="utf-8")

    def _fake_convert(**kwargs):
        target_path = kwargs["target_path"]
        target_path.write_text("new", encoding="utf-8")
        return ConversionStatus.SUCCESS, 1

    with patch("benchbox.core.dataframe.data_loader.FormatConverter.convert_csv_to_parquet", side_effect=_fake_convert):
        converted = loader._convert_data(
            benchmark=benchmark,
            benchmark_name="tpcds",
            scale_factor=1.0,
            source_files={"store_sales": [source_file]},
            source_format=DataFormat.CSV,
            target_format=DataFormat.PARQUET,
            source_hash="abc123",
            write_config=None,
        )

    assert "store_sales" in converted
    assert not stale_leaf.exists()
    assert keep_dir.exists()
    assert (cache_path / "_manifest.json").exists()


