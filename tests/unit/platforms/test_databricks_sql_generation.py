# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, Mock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture(autouse=True)
def databricks_deps():
    with patch("benchbox.platforms.databricks.adapter.check_platform_dependencies", return_value=(True, [])):
        yield


def _make_adapter(**kwargs):

    with patch("benchbox.platforms.databricks.adapter.databricks_sql"):
        from benchbox.platforms.databricks.adapter import DatabricksAdapter

        defaults = {
            "server_hostname": "test.azuredatabricks.net",
            "http_path": "/sql/1.0/warehouses/abc",
            "access_token": "testtoken",
            "catalog": "main",
            "schema": "benchbox",
        }
        defaults.update(kwargs)
        return DatabricksAdapter(**defaults)


def _convert(adapter, sql):
    from benchbox.utils.dialect_utils import translate_ddl

    return " ".join(translate_ddl(sql, "databricks", "standard", **adapter._schema_ddl_options(sql)))


class TestDatabricksBuildCtasSortSql:
    def test_z_order_generates_optimize_zorder(self):
        adapter = _make_adapter()
        col = Mock()
        col.name = "l_orderkey"
        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("on", "z_order")):
            result = adapter._build_ctas_sort_sql("LINEITEM", [col])
        assert result == "OPTIMIZE LINEITEM ZORDER BY (l_orderkey)"

    def test_liquid_clustering_generates_alter_cluster_by(self):
        adapter = _make_adapter()
        col = Mock()
        col.name = "ps_partkey"
        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("on", "liquid_clustering")):
            result = adapter._build_ctas_sort_sql("PARTSUPP", [col])
        assert result == "ALTER TABLE PARTSUPP CLUSTER BY (ps_partkey)"

    def test_ctas_generates_create_or_replace(self):
        adapter = _make_adapter()
        col = Mock()
        col.name = "o_orderdate"
        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("on", "ctas")):
            result = adapter._build_ctas_sort_sql("ORDERS", [col])
        assert "CREATE OR REPLACE TABLE ORDERS" in result
        assert "ORDER BY o_orderdate" in result

    def test_off_mode_returns_none(self):
        adapter = _make_adapter()
        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("off", "auto")):
            result = adapter._build_ctas_sort_sql("LINEITEM", [Mock()])
        assert result is None

    def test_multi_col_z_order(self):
        adapter = _make_adapter()
        col1, col2 = Mock(), Mock()
        col1.name = "l_orderkey"
        col2.name = "l_linenumber"
        with patch.object(adapter, "resolve_sorted_ingestion_strategy", return_value=("on", "z_order")):
            result = adapter._build_ctas_sort_sql("LINEITEM", [col1, col2])
        assert "ZORDER BY (l_orderkey, l_linenumber)" in result


class TestDatabricksTblpropertiesInjection:
    def test_adds_tblproperties_when_missing(self):
        adapter = _make_adapter(delta_auto_optimize=True)

        stmt = "CREATE TABLE main.benchbox.lineitem (l_orderkey BIGINT)"
        result = _convert(adapter, stmt)
        assert "TBLPROPERTIES" in result

    def test_adds_auto_optimize_properties(self):
        adapter = _make_adapter(delta_auto_optimize=True)
        stmt = "CREATE TABLE t (id INT)"
        result = _convert(adapter, stmt)
        assert "'delta.autoOptimize.optimizeWrite'='true'" in result
        assert "'delta.autoOptimize.autoCompact'='true'" in result

    def test_existing_using_clause_is_replaced_by_table_format(self):
        adapter = _make_adapter(delta_auto_optimize=True)
        stmt = "CREATE TABLE t (id INT) USING PARQUET"
        result = _convert(adapter, stmt)
        assert "USING PARQUET" not in result
        assert result.count("USING DELTA") == 1

    def test_no_tblproperties_when_auto_optimize_off(self):
        adapter = _make_adapter(delta_auto_optimize=False)
        stmt = "CREATE TABLE t (id INT)"
        result = _convert(adapter, stmt)

        assert "TBLPROPERTIES" not in result
        assert "autoOptimize" not in result


class TestDatabricksVacuumTable:
    def test_vacuum_executes_vacuum_statement(self):
        adapter = _make_adapter(enable_delta_optimization=True)
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.vacuum_table(mock_conn, "lineitem", hours=24)
        executed_sql = [call.args[0] for call in mock_cursor.execute.call_args_list if call.args]
        assert any("VACUUM" in sql and "LINEITEM" in sql and "24" in sql for sql in executed_sql)


class TestDatabricksGetTargetDialect:
    def test_returns_databricks(self):
        adapter = _make_adapter()
        dialect = adapter.get_target_dialect()
        assert "databricks" in dialect.lower() or "spark" in dialect.lower()


class TestDatabricksCopyIntoSql:
    def test_copy_into_contains_table_name(self):
        adapter = _make_adapter()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (100,)
        mock_benchmark = MagicMock()
        mock_benchmark.get_schema.return_value = {}

        with (
            patch.object(
                adapter,
                "_resolve_file_uri_and_delimiter",
                return_value=("dbfs:/Volumes/data/lineitem.csv", "lineitem.csv", "|"),
            ),
            patch.object(adapter, "_get_column_list_for_table", return_value=""),
            patch.object(adapter, "get_effective_tuning_configuration", return_value=None),
        ):
            adapter._load_single_table(
                mock_cursor,
                MagicMock(),
                mock_benchmark,
                "lineitem",
                "lineitem.csv",
                "dbfs:/Volumes/main/benchbox/vol",
                {"lineitem"},
            )

        executed_sql = [call.args[0] for call in mock_cursor.execute.call_args_list if call.args]
        assert any("COPY INTO" in sql and "LINEITEM" in sql for sql in executed_sql)


def _make_hudi_adapter(**kwargs):
    kwargs.setdefault("hudi_primary_key", "l_orderkey")
    kwargs.setdefault("hudi_precombine_field", "l_commitdate")
    kwargs.setdefault("table_format", "hudi")
    return _make_adapter(**kwargs)


class TestDatabricksHudiSupport:
    def test_default_format_is_delta(self):
        adapter = _make_adapter()
        assert adapter.table_format == "delta"
        result = _convert(adapter, "CREATE TABLE t (a BIGINT)")
        assert "USING DELTA" in result

    def test_hudi_ddl_with_keys(self):
        adapter = _make_hudi_adapter()
        result = _convert(adapter, "CREATE TABLE main.benchbox.lineitem (l_orderkey BIGINT, l_commitdate DATE)")
        assert "USING HUDI" in result
        assert "USING DELTA" not in result
        assert "'type'='cow'" in result
        assert "'primaryKey'='l_orderkey'" in result
        assert "'preCombineField'='l_commitdate'" in result
        assert "delta.autoOptimize" not in result

    def test_hudi_ddl_omits_keys_missing_from_table(self):

        adapter = _make_hudi_adapter()
        result = _convert(adapter, "CREATE TABLE main.benchbox.region (r_regionkey BIGINT)")
        assert "USING HUDI" in result
        assert "'type'='cow'" in result
        assert "primaryKey" not in result
        assert "preCombineField" not in result

    def test_hudi_ddl_omits_only_absent_key(self):
        adapter = _make_hudi_adapter()
        result = _convert(adapter, "CREATE TABLE main.benchbox.orders (o_orderkey BIGINT, l_commitdate DATE)")
        assert "'primaryKey'" not in result
        assert "'preCombineField'='l_commitdate'" in result

    def test_hudi_ddl_without_keys(self):
        adapter = _make_adapter(table_format="hudi")
        result = _convert(adapter, "CREATE TABLE t (a BIGINT)")
        assert "USING HUDI" in result
        assert "'type'='cow'" in result
        assert "primaryKey" not in result

    def test_hudi_mor_table_type(self):
        adapter = _make_hudi_adapter(hudi_table_type="mor")
        result = _convert(adapter, "CREATE TABLE t (a BIGINT)")
        assert "'type'='mor'" in result

    def test_invalid_table_format_rejected(self):
        with pytest.raises(ValueError, match="Unsupported Databricks table_format"):
            _make_adapter(table_format="clickhouse")

    def test_invalid_hudi_table_type_rejected(self):
        with pytest.raises(ValueError, match="Unsupported hudi_table_type"):
            _make_adapter(table_format="hudi", hudi_table_type="cow_mor")

    def test_hudi_tuning_clause(self):
        from benchbox.core.tuning.interface import TuningType

        adapter = _make_hudi_adapter()
        tuning = Mock()
        part_col = Mock()
        part_col.name = "l_shipdate"
        part_col.order = 0
        cluster_col = Mock()
        cluster_col.name = "l_orderkey"
        cluster_col.order = 1
        tuning.has_any_tuning.return_value = True

        def _columns(tuning_type):
            if tuning_type == TuningType.PARTITIONING:
                return [part_col]
            if tuning_type == TuningType.CLUSTERING:
                return [cluster_col]
            return []

        tuning.get_columns_by_type.side_effect = _columns
        result = adapter.generate_tuning_clause(tuning)
        assert "USING HUDI" in result
        assert "PARTITIONED BY (l_shipdate)" in result
        assert "CLUSTER BY" not in result
        assert "ZORDER" not in result

    def test_optimize_table_skipped_for_hudi(self):
        adapter = _make_hudi_adapter()
        connection = MagicMock()
        adapter.optimize_table(connection, "lineitem")
        connection.cursor.assert_not_called()
        assert adapter._skipped_layout_operations
        skipped = adapter._skipped_layout_operations[-1]
        assert skipped["mechanism"] == "optimize"
        assert skipped["status"] == "skipped"

    def test_apply_delta_optimize_skipped_for_hudi(self):
        adapter = _make_hudi_adapter()
        cursor = MagicMock()
        adapter._apply_delta_optimize(cursor, "LINEITEM", phase="post_load")
        cursor.execute.assert_not_called()
        mechanisms = [op["mechanism"] for op in adapter._skipped_layout_operations]
        assert mechanisms == ["optimize", "analyze"]

    def test_apply_zorder_skipped_for_hudi(self):
        adapter = _make_hudi_adapter()
        cursor = MagicMock()
        adapter._apply_delta_optimize(cursor, "LINEITEM", phase="post_load", zorder_columns=["l_orderkey"])
        cursor.execute.assert_not_called()
        assert adapter._skipped_layout_operations[0]["mechanism"] == "z_order"

    def test_vacuum_table_skipped_for_hudi(self):
        adapter = _make_hudi_adapter()
        connection = MagicMock()
        adapter.vacuum_table(connection, "lineitem")
        connection.cursor.assert_not_called()

    def test_ctas_sort_returns_none_for_hudi(self):
        adapter = _make_hudi_adapter()
        assert adapter._build_ctas_sort_sql("LINEITEM", [Mock()]) is None

    def test_metadata_reports_hudi_format(self):
        adapter = _make_hudi_adapter()
        connection = MagicMock()
        with (
            patch.object(type(adapter), "_detect_databricks_region", return_value=None),
            patch.object(type(adapter), "_warehouse_id_from_http_path", return_value=None),
        ):
            info = adapter.get_platform_info(connection)
        configuration = info["configuration"]
        assert configuration["table_format"] == "hudi"
        assert configuration["hudi_primary_key"] == "l_orderkey"
        assert configuration["hudi_precombine_field"] == "l_commitdate"
        assert configuration["hudi_table_type"] == "cow"

    def test_load_data_raises_for_hudi(self):
        adapter = _make_hudi_adapter()
        with pytest.raises(ValueError, match="COPY INTO targets Delta"):
            adapter.load_data(MagicMock(), MagicMock(), MagicMock())

    def test_hudi_ddl_replaces_using_delta(self):
        adapter = _make_hudi_adapter()
        result = _convert(adapter, "CREATE TABLE t (id INT) USING DELTA")
        assert "USING HUDI" in result
        assert "USING DELTA" not in result
        assert "'type'='cow'" in result

    def test_hudi_ddl_merges_existing_tblproperties(self):
        adapter = _make_hudi_adapter()
        result = _convert(
            adapter,
            "CREATE TABLE t (l_orderkey BIGINT) USING DELTA TBLPROPERTIES ('delta.autoOptimize.optimizeWrite' = 'true')",
        )
        assert "USING HUDI" in result
        assert "USING DELTA" not in result
        assert result.count("TBLPROPERTIES") == 1
        assert "'primaryKey'='l_orderkey'" in result
        assert "delta.autoOptimize.optimizeWrite" in result

    def test_invalid_hudi_primary_key_rejected(self):
        from benchbox.platforms.base.data_loading import DataLoadingError

        with pytest.raises(DataLoadingError):
            _make_adapter(table_format="hudi", hudi_primary_key="l_orderkey'; DROP TABLE t; --")

    def test_whitespace_table_format_accepted(self):
        adapter = _make_adapter(table_format=" HUDI ", hudi_table_type=" COW ")
        assert adapter.table_format == "hudi"
        assert adapter.hudi_table_type == "cow"

    def test_optimize_table_records_nothing_when_flag_off(self):
        adapter = _make_hudi_adapter(enable_delta_optimization=False)
        connection = MagicMock()
        adapter.optimize_table(connection, "lineitem")
        connection.cursor.assert_not_called()
        assert adapter._skipped_layout_operations == []
        adapter.vacuum_table(connection, "lineitem")
        assert adapter._skipped_layout_operations == []

    def test_vacuum_table_records_skip_for_hudi(self):
        adapter = _make_hudi_adapter()
        connection = MagicMock()
        adapter.vacuum_table(connection, "lineitem")
        connection.cursor.assert_not_called()
        skipped = adapter._skipped_layout_operations[-1]
        assert skipped["mechanism"] == "vacuum"
        assert skipped["status"] == "skipped"

    def test_skip_statements_are_plain_clauses(self):
        adapter = _make_hudi_adapter()
        cursor = MagicMock()
        adapter._apply_delta_optimize(cursor, "LINEITEM", phase="post_load")
        adapter._apply_delta_optimize(cursor, "LINEITEM", phase="post_load", zorder_columns=["l_orderkey"])
        statements = [op["statement"] for op in adapter._skipped_layout_operations[-4:]]
        assert statements == [
            "OPTIMIZE LINEITEM",
            "ANALYZE TABLE LINEITEM COMPUTE STATISTICS",
            "OPTIMIZE LINEITEM ZORDER BY (l_orderkey)",
            "ANALYZE TABLE LINEITEM COMPUTE STATISTICS",
        ]

    def test_apply_table_tunings_records_skips_for_hudi(self):
        from benchbox.core.tuning.interface import TuningType

        adapter = _make_hudi_adapter()
        tuning = Mock()
        tuning.table_name = "lineitem"
        tuning.has_any_tuning.return_value = True
        cluster_col = Mock()
        cluster_col.name = "l_orderkey"
        cluster_col.order = 1
        tuning.get_columns_by_type.side_effect = lambda t: [cluster_col] if t == TuningType.CLUSTERING else []
        cursor = MagicMock()
        cursor.fetchall.return_value = [("Database", "main"), ("Provider", "hudi")]
        connection = MagicMock()
        connection.cursor.return_value = cursor

        adapter.apply_table_tunings(tuning, connection)

        executed = [call.args[0] for call in cursor.execute.call_args_list if call.args]
        assert not any("OPTIMIZE" in sql for sql in executed)
        assert not any("CLUSTER BY" in sql for sql in executed)
        mechanisms = {op["mechanism"] for op in adapter._skipped_layout_operations if op["status"] == "skipped"}
        assert "z_order" in mechanisms
        assert "optimize" in mechanisms
        assert "analyze" in mechanisms
        assert not any("ANALYZE" in sql for sql in executed)

    def test_hudi_ddl_rewrites_create_or_replace(self):
        adapter = _make_hudi_adapter()
        result = _convert(adapter, "CREATE OR REPLACE TABLE t (l_orderkey BIGINT) USING DELTA")
        assert "USING HUDI" in result
        assert "USING DELTA" not in result
        assert result.count("CREATE OR REPLACE TABLE") == 1
        assert "'primaryKey'='l_orderkey'" in result

    def test_hudi_ddl_rewrites_if_not_exists(self):
        adapter = _make_hudi_adapter()
        result = _convert(adapter, "CREATE TABLE IF NOT EXISTS t (l_orderkey BIGINT)")
        assert "USING HUDI" in result
        assert "OR REPLACE" not in result

    def test_hudi_ddl_ignores_using_in_comment(self):
        adapter = _make_hudi_adapter()
        result = _convert(adapter, "CREATE TABLE t (id INT COMMENT 'use using delta file', l_orderkey BIGINT)")
        assert "use using delta file" in result
        assert result.count("USING HUDI") == 1

    def test_hudi_keys_require_column_definitions(self):
        adapter = _make_adapter(table_format="hudi", hudi_primary_key="main")
        result = _convert(adapter, "CREATE TABLE main.benchbox.region (r_regionkey BIGINT)")
        assert "USING HUDI" in result
        assert "primaryKey" not in result

    def test_hudi_key_ignores_partitioned_by_reference(self):
        adapter = _make_hudi_adapter()
        result = _convert(adapter, "CREATE TABLE t (a BIGINT) PARTITIONED BY (l_orderkey)")
        assert "primaryKey" not in result

    def test_hudi_key_uses_ddl_spelling(self):
        adapter = _make_adapter(table_format="hudi", hudi_precombine_field="L_COMMITDATE")
        result = _convert(adapter, "CREATE TABLE t (l_commitdate DATE)")
        assert "'preCombineField'='l_commitdate'" in result
