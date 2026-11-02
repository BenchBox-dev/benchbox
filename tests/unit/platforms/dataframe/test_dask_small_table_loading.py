from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _write_shards(directory, shard_count):
    for index in range(shard_count):
        (directory / f"t.tbl.{index + 1}").write_text(f"{index * 2}|a\n{index * 2 + 1}|b\n")
    return sorted(directory.glob("t.tbl.*"))


def test_small_sharded_table_loads_as_one_persisted_partition(tmp_path):
    pytest.importorskip("dask.dataframe")

    from benchbox.platforms.dataframe.dask_df import DaskDataFrameAdapter

    adapter = DaskDataFrameAdapter(execution_engine="threaded")
    frame = adapter._load_csv_files(
        _write_shards(tmp_path, 5), delimiter="|", has_header=False, column_names=["k", "v"]
    )

    assert frame.npartitions == 1
    assert len(frame) == 10
    assert sorted(frame["k"].compute().tolist()) == list(range(10))


def test_table_above_input_limit_keeps_its_partitions(tmp_path, monkeypatch):
    pytest.importorskip("dask.dataframe")

    from benchbox.platforms.dataframe import dask_df
    from benchbox.platforms.dataframe.dask_df import DaskDataFrameAdapter

    monkeypatch.setattr(dask_df, "_SINGLE_PARTITION_MAX_INPUT_BYTES", 1)
    adapter = DaskDataFrameAdapter(execution_engine="threaded")
    frame = adapter._load_csv_files(
        _write_shards(tmp_path, 5), delimiter="|", has_header=False, column_names=["k", "v"]
    )

    assert frame.npartitions == 5


def test_compressed_table_is_sized_by_its_expanded_bytes(tmp_path, monkeypatch):
    pytest.importorskip("dask.dataframe")
    import gzip

    from benchbox.platforms.dataframe import dask_df
    from benchbox.platforms.dataframe.dask_df import DaskDataFrameAdapter

    plain_paths = _write_shards(tmp_path, 5)
    gz_paths = []
    for index, path in enumerate(plain_paths):
        gz_path = tmp_path / f"c.tbl.{index + 1}.gz"
        gz_path.write_bytes(gzip.compress(path.read_bytes()))
        gz_paths.append(gz_path)

    limit = max(path.stat().st_size for path in gz_paths) * 5 * 2
    monkeypatch.setattr(dask_df, "_SINGLE_PARTITION_MAX_INPUT_BYTES", limit)
    adapter = DaskDataFrameAdapter(execution_engine="threaded")

    plain = adapter._load_csv_files(plain_paths, delimiter="|", has_header=False, column_names=["k", "v"])
    compressed = adapter._load_csv_files(gz_paths, delimiter="|", has_header=False, column_names=["k", "v"])

    assert plain.npartitions == 1
    assert compressed.npartitions == 5
