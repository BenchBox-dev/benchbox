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
