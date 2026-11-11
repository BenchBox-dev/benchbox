from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from benchbox.platforms.base.data_loading import DataLoader, DataLoadingError

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_loader(tmp_path: Path) -> DataLoader:

    adapter = Mock()
    adapter.verbose_enabled = False
    adapter.log_operation_start = Mock()
    adapter.log_operation_complete = Mock()
    adapter.log_very_verbose = Mock()
    benchmark = Mock(spec=[])
    connection = Mock()
    return DataLoader(
        adapter=adapter,
        benchmark=benchmark,
        connection=connection,
        data_dir=tmp_path,
    )


class TestShardPathContamination:
    def test_empty_resolved_shard_list_raises(self, tmp_path: Path) -> None:

        loader = _make_loader(tmp_path)
        missing_a = tmp_path / "customer.tbl.1.zst"
        missing_b = tmp_path / "customer.tbl.2.zst"

        with pytest.raises(DataLoadingError, match="customer.*2 missing"):
            loader._load_file_based_data({"customer": [missing_a, missing_b]})

    def test_directory_shards_filtered_and_raise_when_no_files_remain(self, tmp_path: Path) -> None:

        loader = _make_loader(tmp_path)

        dir_a = tmp_path / "lineitem.tbl.1"
        dir_a.mkdir()
        dir_b = tmp_path / "lineitem.tbl.2"
        dir_b.mkdir()

        with pytest.raises(DataLoadingError, match="lineitem.*directory with no data files"):
            loader._load_file_based_data({"lineitem": [dir_a, dir_b]})

    def test_mixed_dirs_and_missing_raises_with_both_counts(self, tmp_path: Path) -> None:

        loader = _make_loader(tmp_path)
        dir_shard = tmp_path / "orders.tbl.1"
        dir_shard.mkdir()
        missing_shard = tmp_path / "orders.tbl.2.zst"

        with pytest.raises(DataLoadingError, match="orders.*directory with no data files"):
            loader._load_file_based_data({"orders": [dir_shard, missing_shard]})

    def test_partial_shard_contamination_raises(self, tmp_path: Path) -> None:

        loader = _make_loader(tmp_path)
        good_file = tmp_path / "nation.tbl.1"
        good_file.write_text("1|USA\n")
        bad_dir = tmp_path / "nation.tbl.2"
        bad_dir.mkdir()

        with pytest.raises(DataLoadingError, match="nation.*directory with no data files"):
            loader._load_file_based_data({"nation": [good_file, bad_dir]})

    def test_directory_shard_with_data_files_expands(self, tmp_path: Path) -> None:

        loader = _make_loader(tmp_path)
        chunk_dir = tmp_path / "region"
        chunk_dir.mkdir()
        (chunk_dir / "region.tbl.1").write_text("1|AMERICA\n")
        (chunk_dir / "region.tbl.2").write_text("2|EUROPE\n")

        stats = loader._load_file_based_data({"region": [chunk_dir]})
        assert "region" in stats

    def test_legitimate_shards_load_normally(self, tmp_path: Path) -> None:

        loader = _make_loader(tmp_path)
        shard_a = tmp_path / "region.tbl.1"
        shard_a.write_text("1|AMERICA\n")
        shard_b = tmp_path / "region.tbl.2"
        shard_b.write_text("2|EUROPE\n")

        stats = loader._load_file_based_data({"region": [shard_a, shard_b]})
        assert "region" in stats


class TestSingleFileDirectoryGuard:
    def test_directory_input_raises(self, tmp_path: Path) -> None:

        loader = _make_loader(tmp_path)
        table_dir = tmp_path / "customer"
        table_dir.mkdir()

        with pytest.raises(DataLoadingError, match="customer.*expected file but found directory"):
            loader._load_single_file("customer", table_dir)

    def test_missing_file_raises(self, tmp_path: Path) -> None:

        loader = _make_loader(tmp_path)
        missing = tmp_path / "nonexistent.tbl"

        with pytest.raises(DataLoadingError, match="test_table.*data file not found"):
            loader._load_single_file("test_table", missing)


