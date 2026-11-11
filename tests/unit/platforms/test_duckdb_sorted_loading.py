from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.duckdb import DuckDBAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_tuning_config(table_tunings: dict):

    config = Mock()
    config.table_tunings = table_tunings
    return config


def _make_table_tuning_with_sort(sort_columns: list[str]):

    from benchbox.core.tuning.interface import TuningColumn, TuningType

    table_tuning = Mock()

    real_cols = [
        TuningColumn(name=col_name, type="VARCHAR", order=i) for i, col_name in enumerate(sort_columns, start=1)
    ]

    def get_columns_by_type(tuning_type):
        if tuning_type == TuningType.SORTING:
            return real_cols
        return []

    table_tuning.get_columns_by_type.side_effect = get_columns_by_type
    return table_tuning


class TestDuckdbBuildCtasSortSql:
    def setup_method(self):
        with patch("benchbox.platforms.duckdb.duckdb"):
            self.adapter = DuckDBAdapter()

    def test_builds_or_replace_ctas_with_ordered_columns(self):
        from benchbox.core.tuning.interface import TuningColumn

        col_b = TuningColumn(name="l_orderkey", type="INTEGER", order=2)
        col_a = TuningColumn(name="l_shipdate", type="DATE", order=1)

        sql = self.adapter._build_ctas_sort_sql("lineitem", [col_a, col_b])

        assert sql == ("CREATE OR REPLACE TABLE lineitem AS SELECT * FROM lineitem ORDER BY l_shipdate, l_orderkey;")


class TestCtasSortIntegration:
    @pytest.fixture(autouse=True)
    def _require_duckdb(self):
        pytest.importorskip("duckdb")

    def test_apply_ctas_sort_produces_sorted_data(self):

        import duckdb

        conn = duckdb.connect(":memory:")

        conn.execute("CREATE TABLE lineitem (l_orderkey INTEGER, l_shipdate DATE)")
        conn.execute("""
            INSERT INTO lineitem VALUES
                (3, '2024-03-01'),
                (1, '2024-01-15'),
                (2, '2024-02-10'),
                (1, '2024-01-01')
        """)

        with patch("benchbox.platforms.duckdb.duckdb"):
            adapter = DuckDBAdapter()

        table_tuning = _make_table_tuning_with_sort(["l_shipdate", "l_orderkey"])
        tuning_config = _make_tuning_config({"lineitem": table_tuning})

        result = adapter.apply_ctas_sort("lineitem", tuning_config, conn)

        assert result is True

        rows = conn.execute("SELECT l_shipdate, l_orderkey FROM lineitem").fetchall()
        dates = [r[0] for r in rows]
        assert dates == sorted(dates), f"Expected sorted dates, got: {dates}"

    def test_row_count_preserved_after_ctas_sort(self):

        import duckdb

        conn = duckdb.connect(":memory:")
        conn.execute("CREATE TABLE orders (o_orderkey INTEGER, o_orderdate DATE)")
        conn.execute("""
            INSERT INTO orders VALUES
                (5, '2024-05-01'),
                (3, '2024-03-01'),
                (1, '2024-01-01'),
                (4, '2024-04-01'),
                (2, '2024-02-01')
        """)

        with patch("benchbox.platforms.duckdb.duckdb"):
            adapter = DuckDBAdapter()

        table_tuning = _make_table_tuning_with_sort(["o_orderdate"])
        tuning_config = _make_tuning_config({"orders": table_tuning})

        adapter.apply_ctas_sort("orders", tuning_config, conn)

        count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        assert count == 5

    def test_unsorted_table_unchanged_when_not_in_tuning_config(self):

        import duckdb

        conn = duckdb.connect(":memory:")
        conn.execute("CREATE TABLE parts (p_partkey INTEGER)")
        conn.execute("INSERT INTO parts VALUES (3), (1), (2)")

        with patch("benchbox.platforms.duckdb.duckdb"):
            adapter = DuckDBAdapter()

        table_tuning = _make_table_tuning_with_sort(["l_shipdate"])
        tuning_config = _make_tuning_config({"lineitem": table_tuning})

        result = adapter.apply_ctas_sort("parts", tuning_config, conn)

        assert result is False

        count = conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0]
        assert count == 3

    def test_duckdb_adapter_load_data_with_sorting(self, tmp_path):

        import duckdb

        from benchbox.platforms.base.data_loading import DataLoader, DuckDBNativeHandler
        from benchbox.utils.file_format import is_tpc_format

        tbl_file = tmp_path / "lineitem.tbl"
        tbl_file.write_text("3|2024-03-01|\n1|2024-01-15|\n2|2024-02-10|\n1|2024-01-01|\n")

        conn = duckdb.connect(":memory:")
        conn.execute("CREATE TABLE lineitem (l_orderkey INTEGER, l_shipdate VARCHAR)")

        with patch("benchbox.platforms.duckdb.duckdb"):
            adapter = DuckDBAdapter()

        adapter.tuning_enabled = True

        table_tuning = _make_table_tuning_with_sort(["l_shipdate", "l_orderkey"])
        tuning_config = _make_tuning_config({"lineitem": table_tuning})
        adapter.unified_tuning_configuration = tuning_config

        benchmark = Mock(spec=["tables", "get_schema"])
        benchmark.tables = {"lineitem": tbl_file}
        benchmark.get_schema.return_value = {}

        def duckdb_handler_factory(file_path, adp, bm):
            if is_tpc_format(file_path):
                return DuckDBNativeHandler("|", adp, bm)
            return None

        loader = DataLoader(
            adapter=adapter,
            benchmark=benchmark,
            connection=conn,
            data_dir=tmp_path,
            handler_factory=duckdb_handler_factory,
            tuning_config=tuning_config,
        )

        table_stats, _ = loader.load()

        assert "lineitem" in table_stats
        assert table_stats["lineitem"] == 4

        rows = conn.execute("SELECT l_shipdate FROM lineitem").fetchall()
        dates = [r[0] for r in rows]
        assert dates == sorted(dates), f"Expected sorted dates, got: {dates}"


