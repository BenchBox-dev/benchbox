from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SOURCE = "id,name,when\n1,N/A,2024-01-01\n2,NULL,2024-01-02\n3,,2024-01-03\n"


def test_tpcdi_source_staging_keeps_null_looking_text(tmp_path):
    pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    from benchbox.core.tpcdi.benchmark import TPCDIBenchmark

    path = tmp_path / "source.csv"
    path.write_text(SOURCE)

    frame = TPCDIBenchmark._read_source_csv(str(path))

    assert frame["name"].tolist()[:2] == ["N/A", "NULL"]
    assert bool(frame["name"].isna().tolist()[2])


def test_write_primitives_pandas_bulk_load_keeps_null_looking_text(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    from benchbox.core.write_primitives.dataframe_operations import DataFrameWriteOperationsManager

    source = tmp_path / "source.csv"
    source.write_text(SOURCE)
    target = tmp_path / "out"

    rows, _, _ = DataFrameWriteOperationsManager("pandas-df")._bulk_load_pandas(
        source, target, "csv", "parquet", None, None
    )

    written = pd.read_parquet(target / "part-00000.parquet")
    assert rows == 3
    assert written["name"].tolist()[:2] == ["N/A", "NULL"]
    assert bool(written["name"].isna().tolist()[2])
