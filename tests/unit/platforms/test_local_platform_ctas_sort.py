from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from benchbox.platforms.datafusion import DataFusionAdapter
from benchbox.platforms.postgresql import PostgreSQLAdapter
from benchbox.platforms.sqlite import SQLiteAdapter
from benchbox.platforms.starrocks.adapter import StarRocksAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_sort_column(name: str, order: int) -> Mock:
    col = Mock()
    col.name = name
    col.order = order
    return col


def _make_tuning_config(table_name: str, sort_columns: list[Mock]) -> Mock:
    from benchbox.core.tuning.interface import TuningType

    table_tuning = Mock()

    def get_columns_by_type(tuning_type):
        if tuning_type == TuningType.SORTING:
            return sort_columns
        return []

    table_tuning.get_columns_by_type.side_effect = get_columns_by_type

    config = Mock()
    config.table_tunings = {table_name: table_tuning}
    return config


class TestLocalPlatformHookSqlGeneration:
    def test_datafusion_build_ctas_sort_sql(self):
        adapter = DataFusionAdapter.__new__(DataFusionAdapter)
        sql = adapter._build_ctas_sort_sql(
            "lineitem",
            [_make_sort_column("l_shipdate", 1), _make_sort_column("l_orderkey", 2)],
        )
        assert sql == "CREATE OR REPLACE TABLE lineitem AS SELECT * FROM lineitem ORDER BY l_shipdate, l_orderkey;"

    def test_postgresql_build_ctas_sort_sql(self):
        adapter = PostgreSQLAdapter.__new__(PostgreSQLAdapter)
        sql = adapter._build_ctas_sort_sql(
            "orders",
            [_make_sort_column("o_orderkey", 2), _make_sort_column("o_orderdate", 1)],
        )
        assert sql is None

    def test_starrocks_returns_none(self):
        adapter = StarRocksAdapter.__new__(StarRocksAdapter)
        sql = adapter._build_ctas_sort_sql("lineorder", [_make_sort_column("lo_orderdate", 1)])
        assert sql is None

    def test_sqlite_returns_none(self):
        adapter = SQLiteAdapter(database_path=":memory:")
        sql = adapter._build_ctas_sort_sql("orders", [_make_sort_column("o_orderdate", 1)])
        assert sql is None


class TestDataFusionCtasSortIntegration:
    @pytest.fixture(autouse=True)
    def _require_datafusion(self):
        pytest.importorskip("datafusion")

    def test_datafusion_apply_ctas_sort_orders_rows(self, tmp_path):
        adapter = DataFusionAdapter(working_dir=str(tmp_path / "df_workdir"), data_format="csv")
        connection = adapter.create_connection()

        connection.execute(
            "CREATE TABLE lineitem AS "
            "SELECT * FROM (VALUES (3, '2024-03-01'), (1, '2024-01-01'), (2, '2024-02-01')) "
            "AS t(l_orderkey, l_shipdate)"
        )

        tuning_config = _make_tuning_config(
            "lineitem",
            [_make_sort_column("l_shipdate", 1), _make_sort_column("l_orderkey", 2)],
        )

        assert adapter.apply_ctas_sort("lineitem", tuning_config, connection) is True

        rows = connection.sql("SELECT l_shipdate, l_orderkey FROM lineitem").to_pydict()
        assert rows["l_shipdate"] == ["2024-01-01", "2024-02-01", "2024-03-01"]
        assert rows["l_orderkey"] == [1, 2, 3]

    def test_datafusion_real_ctas_sort_is_recorded_in_ledger(self, tmp_path):
        from benchbox.core.tuning.applied_ledger import AppliedTuningLedger
        from benchbox.core.tuning.interface import TuningColumn, TuningType

        adapter = DataFusionAdapter(working_dir=str(tmp_path / "df_workdir"), data_format="csv")
        adapter._applied_tuning_ledger = AppliedTuningLedger()
        connection = adapter.create_connection()

        connection.execute(
            "CREATE TABLE lineitem AS "
            "SELECT * FROM (VALUES (3, '2024-03-01'), (1, '2024-01-01'), (2, '2024-02-01')) "
            "AS t(l_orderkey, l_shipdate)"
        )

        sort_columns = [
            TuningColumn(name="l_shipdate", type="VARCHAR", order=1),
            TuningColumn(name="l_orderkey", type="INTEGER", order=2),
        ]
        table_tuning = Mock()
        table_tuning.get_columns_by_type.side_effect = lambda tuning_type: (
            sort_columns if tuning_type == TuningType.SORTING else []
        )
        tuning_config = Mock()
        tuning_config.table_tunings = {"lineitem": table_tuning}

        ledger = adapter._applied_tuning_ledger
        assert ledger.overall_status(tuning_enabled=True, has_config=True) == "noop"

        assert adapter.apply_ctas_sort("lineitem", tuning_config, connection) is True

        executed = [s for s in ledger.statements if s.status == "executed"]
        assert len(executed) == 1
        assert executed[0].phase == "post_load"
        assert "ORDER BY l_shipdate, l_orderkey" in executed[0].statement
        assert ledger.overall_status(tuning_enabled=True, has_config=True) == "applied_unverified"

        rows = connection.sql("SELECT l_shipdate, l_orderkey FROM lineitem").to_pydict()
        assert rows["l_shipdate"] == ["2024-01-01", "2024-02-01", "2024-03-01"]
        assert rows["l_orderkey"] == [1, 2, 3]
