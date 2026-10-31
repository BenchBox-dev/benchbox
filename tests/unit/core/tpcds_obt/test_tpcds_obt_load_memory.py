from __future__ import annotations

import re
from pathlib import Path

import pytest

from benchbox.core.tpcds_obt.benchmark import TPCDSOBTBenchmark
from benchbox.core.tpcds_obt.dataframe_queries import get_dataframe_queries, get_load_columns
from benchbox.core.tpcds_obt.etl.transformer import TPCDSOBTTransformer
from benchbox.core.tpcds_obt.schema import OBT_TABLE_NAME, get_obt_columns

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_UNITS = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3}
MAX_LOAD_COLUMNS = 16
MAX_TRANSFORM_MEMORY_BYTES = 7 * 1000**3
MAX_TRANSFORM_THREADS = 2


def _to_bytes(text: str) -> int:
    value, unit = re.fullmatch(r"([\d.]+)\s*(\w+)", text.strip()).groups()
    return int(float(value) * _UNITS[unit.upper()])


def test_transform_connection_caps_duckdb_memory(tmp_path: Path) -> None:
    pytest.importorskip("duckdb")
    conn = TPCDSOBTTransformer()._connect(tmp_path / "obt.duckdb", len(get_obt_columns("full")))
    try:
        limit = conn.execute("SELECT current_setting('memory_limit')").fetchone()[0]
        temp_directory = conn.execute("SELECT current_setting('temp_directory')").fetchone()[0]
        threads = conn.execute("SELECT current_setting('threads')").fetchone()[0]
    finally:
        conn.close()

    assert _to_bytes(limit) <= MAX_TRANSFORM_MEMORY_BYTES
    assert int(threads) <= MAX_TRANSFORM_THREADS
    assert Path(temp_directory).parent == tmp_path


def test_dataframe_load_columns_are_a_small_obt_subset() -> None:
    declared = get_load_columns()
    obt_columns = {column.name for column in get_obt_columns("full")}

    assert len(declared) == len(set(declared))
    assert set(declared) <= obt_columns
    assert len(declared) <= MAX_LOAD_COLUMNS < len(obt_columns) // 40


def test_every_pandas_query_runs_on_the_declared_columns_alone() -> None:
    pd = pytest.importorskip("pandas")
    declared = get_load_columns()
    numeric = {"quantity": 2, "net_paid": 250.0, "net_profit": 5.0, "ext_discount_amt": 1.0, "coupon_amt": 1.0}
    numeric.update({"net_paid_inc_tax": 260.0, "return_amount": 3.0, "item_sk": 7})
    text = {"channel": "store", "has_return": "Y", "sale_id": "s1"}
    frame = pd.DataFrame({name: [numeric.get(name, text.get(name))] * 3 for name in declared})

    class _Ctx:
        def get_table(self, name: str):
            assert name == OBT_TABLE_NAME
            return frame

    for query in get_dataframe_queries():
        assert query.pandas_impl(_Ctx()) is not None, query.query_id


def test_benchmark_declares_load_columns_only_for_the_obt_table(tmp_path: Path) -> None:
    benchmark = TPCDSOBTBenchmark(scale_factor=1.0, output_dir=tmp_path)

    assert benchmark.get_dataframe_load_columns(OBT_TABLE_NAME) == get_load_columns()
    assert benchmark.get_dataframe_load_columns("item") is None


def test_pandas_adapter_materializes_only_the_declared_columns(tmp_path: Path) -> None:
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    from benchbox.platforms.dataframe.pandas_df import PandasDataFrameAdapter

    declared = get_load_columns()
    wide = {column.name: list(range(8)) for column in get_obt_columns("full")}
    path = tmp_path / f"{OBT_TABLE_NAME}.parquet"
    pd.DataFrame(wide).to_parquet(path)

    class _Benchmark:
        def get_dataframe_load_columns(self, table_name: str) -> list[str] | None:
            return get_load_columns() if table_name == OBT_TABLE_NAME else None

    adapter = PandasDataFrameAdapter()
    ctx = adapter.create_context()
    rows = adapter.load_table(ctx, OBT_TABLE_NAME, [path], format_hint="parquet", benchmark=_Benchmark())

    loaded = ctx.get_table(OBT_TABLE_NAME)
    assert rows == 8
    assert list(loaded.columns) == declared
    assert len(loaded.columns) < len(wide) // 40
