from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROWS = "1|N/A|NA|x|\n2|NULL|nan||\n3|n/a|null|y|\n"
COLUMNS = ["k", "a", "b", "c"]
TOKENS = ["N/A", "NULL", "n/a"]


def test_pandas_reader_keeps_null_looking_text(tmp_path):
    pytest.importorskip("pandas")
    from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

    path = tmp_path / "t.tbl"
    path.write_text(ROWS)

    frame = PandasDataFrameAdapter().read_csv(
        path, delimiter="|", header=None, names=COLUMNS, null_marker="", column_types=["BIGINT", "CHAR", "CHAR", "CHAR"]
    )

    assert frame["a"].tolist() == TOKENS
    assert frame["b"].tolist() == ["NA", "nan", "null"]
    assert frame["c"].tolist()[0] == "x"
    assert frame["c"].isna().tolist() == [False, True, False]


def test_dask_reader_keeps_null_looking_text(tmp_path):
    pytest.importorskip("dask.dataframe")
    from benchbox.platforms.dataframe.dask_df import DaskDataFrameAdapter

    path = tmp_path / "t.tbl"
    path.write_text(ROWS)

    frame = DaskDataFrameAdapter(execution_engine="threaded").read_csv(
        path, delimiter="|", header=None, names=COLUMNS, null_marker="", column_types=["BIGINT", "CHAR", "CHAR", "CHAR"]
    )

    assert frame["a"].compute().tolist() == TOKENS


def test_datafusion_pyarrow_fallback_keeps_null_looking_text(tmp_path):
    pytest.importorskip("datafusion")
    from benchbox.platforms.dataframe.datafusion_df import DataFusionDataFrameAdapter

    path = tmp_path / "t.tbl"
    path.write_text(ROWS)

    frame = DataFusionDataFrameAdapter()._read_tbl_via_pyarrow(path, delimiter="|", column_names=COLUMNS)

    table = frame.to_arrow_table()
    assert table.column("a").to_pylist() == TOKENS
    assert table.column("b").to_pylist() == ["NA", "nan", "null"]
    assert table.column("c").to_pylist() == ["x", None, "y"]


def test_sorted_parquet_source_reader_keeps_null_looking_text(tmp_path):
    from benchbox.core.data_organization.sorting import SortedParquetWriter

    path = tmp_path / "t.tbl"
    path.write_text(ROWS)

    table = SortedParquetWriter(config=None)._read_tbl_files([path], COLUMNS)

    assert table.column("a").to_pylist() == TOKENS
    assert table.column("b").to_pylist() == ["NA", "nan", "null"]
    assert table.column("c").to_pylist() == ["x", None, "y"]
