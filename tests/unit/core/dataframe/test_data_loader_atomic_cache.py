from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from benchbox.core.dataframe.capabilities import DataFormat
from benchbox.core.dataframe.data_loader import (
    ConversionStatus,
    DataCache,
    DataFrameDataLoader,
    FormatConverter,
    _atomic_cache_write,
    _fsync_path,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _temp_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.tmp"))


def _write_source(path: Path, rows: int) -> None:
    path.write_text("".join(f"{i}|name{i}|\n" for i in range(rows)), encoding="utf-8")


def _convert(source: Path, target: Path) -> tuple[ConversionStatus, int]:
    return FormatConverter.convert_csv_to_parquet(source, target, column_names=["id", "name"])


class TestAtomicCacheWrite:
    def test_final_file_is_complete_until_the_replace(self, tmp_path: Path) -> None:
        target = tmp_path / "t.bin"
        target.write_bytes(b"old complete content")

        with _atomic_cache_write(target) as tmp:
            assert tmp.parent == target.parent
            assert tmp != target
            tmp.write_bytes(b"new")
            assert target.read_bytes() == b"old complete content"

        assert target.read_bytes() == b"new"
        assert _temp_files(tmp_path) == []

    def test_failed_write_keeps_previous_file_and_leaves_no_temp(self, tmp_path: Path) -> None:
        target = tmp_path / "t.bin"
        target.write_bytes(b"previous")

        with pytest.raises(RuntimeError, match="disk full"), _atomic_cache_write(target) as tmp:
            tmp.write_bytes(b"partial")
            raise RuntimeError("disk full")

        assert target.read_bytes() == b"previous"
        assert _temp_files(tmp_path) == []

    def test_failure_without_previous_file_creates_nothing(self, tmp_path: Path) -> None:
        target = tmp_path / "t.bin"

        with pytest.raises(RuntimeError), _atomic_cache_write(target) as tmp:
            tmp.write_bytes(b"partial")
            raise RuntimeError("boom")

        assert not target.exists()
        assert list(tmp_path.iterdir()) == []

    def test_concurrent_writers_use_distinct_temp_files(self, tmp_path: Path) -> None:
        target = tmp_path / "t.bin"
        with _atomic_cache_write(target) as first, _atomic_cache_write(target) as second:
            assert first != second
            first.write_bytes(b"first")
            second.write_bytes(b"second")
        assert target.read_bytes() in {b"first", b"second"}
        assert _temp_files(tmp_path) == []


class TestParquetConversionIsAtomic:
    def test_target_is_never_partial_while_writing(self, tmp_path: Path) -> None:
        source = tmp_path / "src.tbl"
        _write_source(source, 50)
        target = tmp_path / "out.parquet"
        assert _convert(source, target) == (ConversionStatus.SUCCESS, 50)
        _write_source(source, 80)

        real_write_table = pq.write_table
        observed: list[int] = []

        def observing_write_table(table, where, **kwargs):
            real_write_table(table, where, **kwargs)
            assert Path(where) != target
            observed.append(pq.read_table(target).num_rows)

        with patch("pyarrow.parquet.write_table", side_effect=observing_write_table):
            assert _convert(source, target) == (ConversionStatus.SUCCESS, 80)

        assert observed == [50]
        assert pq.read_table(target).num_rows == 80
        assert _temp_files(tmp_path) == []

    def test_failed_conversion_keeps_previous_parquet_and_no_temp(self, tmp_path: Path) -> None:
        source = tmp_path / "src.tbl"
        _write_source(source, 50)
        target = tmp_path / "out.parquet"
        _convert(source, target)
        previous = target.read_bytes()

        def failing_write_table(table, where, **kwargs):
            Path(where).write_bytes(b"PAR1 truncated")
            raise OSError("no space left on device")

        with patch("pyarrow.parquet.write_table", side_effect=failing_write_table):
            status, rows = _convert(source, target)

        assert (status, rows) == (ConversionStatus.FAILED, 0)
        assert target.read_bytes() == previous
        assert _temp_files(tmp_path) == []

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows cannot replace a file that is memory-mapped; the POSIX guarantee that a mapped reader "
        "survives a rewrite does not exist",
    )
    def test_memory_mapped_reader_survives_a_rewrite(self, tmp_path: Path) -> None:
        source = tmp_path / "src.tbl"
        _write_source(source, 200)
        target = tmp_path / "out.parquet"
        _convert(source, target)

        with pa.memory_map(str(target), "r") as mapped:
            _write_source(source, 10)
            assert _convert(source, target) == (ConversionStatus.SUCCESS, 10)
            assert pq.read_table(mapped).num_rows == 200

        assert pq.read_table(target).num_rows == 10


class TestManifestWriteIsAtomic:
    def _save(self, cache: DataCache, source_hash: str) -> Path:
        cache.save_manifest("tpch", 0.01, DataFormat.PARQUET, source_hash, {"t": {"file": "t.parquet", "row_count": 1}})
        return cache.get_manifest_path("tpch", 0.01, DataFormat.PARQUET)

    def test_save_manifest_leaves_no_temp_file(self, tmp_path: Path) -> None:
        cache = DataCache(tmp_path)
        manifest_path = self._save(cache, "hash1")
        assert json.loads(manifest_path.read_text())["source_hash"] == "hash1"
        assert _temp_files(manifest_path.parent) == []

    def test_failed_manifest_write_keeps_previous_manifest(self, tmp_path: Path) -> None:
        cache = DataCache(tmp_path)
        manifest_path = self._save(cache, "hash1")

        with patch("benchbox.core.dataframe.data_loader.json.dump", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                self._save(cache, "hash2")

        assert json.loads(manifest_path.read_text())["source_hash"] == "hash1"
        assert _temp_files(manifest_path.parent) == []


class TestPrunePreservesWriterTempFiles:
    def _loader_and_cache(self, tmp_path: Path) -> tuple[DataFrameDataLoader, Path]:
        loader = DataFrameDataLoader(cache_dir=tmp_path)
        cache_path = loader.cache.get_cache_path("tpch", 0.01, DataFormat.PARQUET)
        cache_path.mkdir(parents=True, exist_ok=True)
        return loader, cache_path

    @staticmethod
    def _make_temp(cache_path: Path, name: str, age_seconds: float) -> Path:
        path = cache_path / f"{name}.4242.{'ab' * 16}.tmp"
        path.write_bytes(b"partial")
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
        return path

    def test_recent_temp_file_of_another_writer_is_kept(self, tmp_path: Path) -> None:
        loader, cache_path = self._loader_and_cache(tmp_path)
        live = self._make_temp(cache_path, "orders.parquet", age_seconds=5)

        loader._prune_cache_leaf_files(cache_path, set())

        assert live.exists()

    def test_old_temp_file_is_kept(self, tmp_path: Path) -> None:
        loader, cache_path = self._loader_and_cache(tmp_path)
        stale = self._make_temp(cache_path, "orders.parquet", age_seconds=3600)

        removed = loader._prune_cache_leaf_files(cache_path, set())

        assert stale.exists()
        assert removed == 0

    def test_live_writer_survives_pruning_after_a_clock_jump(self, tmp_path: Path) -> None:
        loader, cache_path = self._loader_and_cache(tmp_path)
        target = cache_path / "orders.parquet"
        with _atomic_cache_write(target) as temporary:
            temporary.write_bytes(b"complete")
            stamp = time.time() - 86400
            os.utime(temporary, (stamp, stamp))
            assert loader._prune_cache_leaf_files(cache_path, set()) == 0
            assert temporary.exists()
        assert target.read_bytes() == b"complete"

    def test_untracked_non_temp_files_are_still_pruned(self, tmp_path: Path) -> None:
        loader, cache_path = self._loader_and_cache(tmp_path)
        tracked = cache_path / "keep.parquet"
        tracked.write_bytes(b"x")
        (cache_path / "_manifest.json").write_text("{}")
        untracked = cache_path / "gone.parquet"
        untracked.write_bytes(b"x")
        lookalike = cache_path / "notes.tmp"
        lookalike.write_bytes(b"x")

        loader._prune_cache_leaf_files(cache_path, {"keep.parquet"})

        assert tracked.exists()
        assert (cache_path / "_manifest.json").exists()
        assert not untracked.exists()
        assert not lookalike.exists()


@pytest.mark.parametrize("platform, flags", [("win32", os.O_RDWR), ("darwin", os.O_RDONLY), ("linux", os.O_RDONLY)])
def test_fsync_opens_file_with_platform_required_access(tmp_path: Path, platform: str, flags: int) -> None:
    target = tmp_path / "complete.bin"
    target.write_bytes(b"complete")
    with (
        patch("benchbox.core.dataframe.data_loader.sys.platform", platform),
        patch("benchbox.core.dataframe.data_loader.os.open", wraps=os.open) as opening,
        patch("benchbox.core.dataframe.data_loader.os.fsync"),
    ):
        _fsync_path(target)
    opening.assert_called_once_with(target, flags)


