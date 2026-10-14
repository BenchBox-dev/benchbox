# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, Mock, patch

import pytest

from benchbox.core.tuning.interface import TuningType

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture(autouse=True)
def mock_sf_deps():
    with patch("benchbox.platforms.snowflake.check_platform_dependencies", return_value=(True, [])):
        yield


def _make_adapter(**kwargs):
    from benchbox.platforms.snowflake import SnowflakeAdapter

    with patch("benchbox.platforms.snowflake.snowflake"):
        defaults = {
            "account": "test_account",
            "username": "test_user",
            "password": "test_pass",
            "schema": "PUBLIC",
            "database": "BENCHBOX",
        }
        defaults.update(kwargs)
        return SnowflakeAdapter(**defaults)


class TestSnowflakeOptimizeTableDefinition:
    def test_adds_cluster_by_when_missing(self):
        from benchbox.platforms.snowflake import SnowflakeAdapter

        with patch("benchbox.platforms.snowflake.snowflake"):
            adapter = SnowflakeAdapter(
                account="acct",
                username="u",
                password="p",
                clustering_keys=["l_orderkey", "l_shipdate"],
            )
            result = adapter._optimize_table_definition("CREATE TABLE lineitem (l_orderkey BIGINT)")

            assert isinstance(result, str)

    def test_returns_unchanged_if_no_cluster_keys(self):
        from benchbox.platforms.snowflake import SnowflakeAdapter

        with patch("benchbox.platforms.snowflake.snowflake"):
            adapter = SnowflakeAdapter(
                account="acct",
                username="u",
                password="p",
            )
            stmt = "CREATE TABLE orders (o_orderkey BIGINT)"
            result = adapter._optimize_table_definition(stmt)
            assert "CLUSTER BY" not in result

    def test_if_not_exists_kept_without_or_replace(self):

        from benchbox.platforms.snowflake import SnowflakeAdapter

        with patch("benchbox.platforms.snowflake.snowflake"):
            adapter = SnowflakeAdapter(
                account="acct",
                username="u",
                password="p",
            )
            stmt = "CREATE TABLE IF NOT EXISTS DimDate (SK_DateID BIGINT)"
            result = adapter._optimize_table_definition(stmt)
            assert "OR REPLACE" not in result
            assert "IF NOT EXISTS" in result

    def test_quoted_identifiers_uppercased(self):

        from benchbox.platforms.snowflake import SnowflakeAdapter

        with patch("benchbox.platforms.snowflake.snowflake"):
            adapter = SnowflakeAdapter(
                account="acct",
                username="u",
                password="p",
            )
            stmt = 'CREATE TABLE "store_sales" ("ss_sold_date_sk" INTEGER, "note" VARCHAR DEFAULT \'keep me\')'
            result = adapter._optimize_table_definition(stmt)
            assert '"STORE_SALES"' in result
            assert '"SS_SOLD_DATE_SK"' in result
            assert "'keep me'" in result


class TestSnowflakeSafeguardDivision:
    def test_division_routed_through_nullif(self):

        from benchbox.platforms.snowflake import SnowflakeAdapter

        with patch("benchbox.platforms.snowflake.snowflake"):
            adapter = SnowflakeAdapter(
                account="acct",
                username="u",
                password="p",
            )
            result = adapter._safeguard_snowflake_division("SELECT a / b FROM t")
            assert "NULLIF" in result
            assert result.count("NULLIF") == 1

    def test_query_without_division_unchanged(self):
        from benchbox.platforms.snowflake import SnowflakeAdapter

        with patch("benchbox.platforms.snowflake.snowflake"):
            adapter = SnowflakeAdapter(
                account="acct",
                username="u",
                password="p",
            )
            sql = "SELECT a FROM t"
            assert adapter._safeguard_snowflake_division(sql) == sql

    def test_realistic_multi_join_query_guarded(self):

        from benchbox.platforms.snowflake import SnowflakeAdapter

        with patch("benchbox.platforms.snowflake.snowflake"):
            adapter = SnowflakeAdapter(
                account="acct",
                username="u",
                password="p",
            )
            sql = (
                "SELECT CAST(amc AS DECIMAL(15, 4)) / CAST(pmc AS DECIMAL(15, 4)) AS am_pm_ratio "
                "FROM (SELECT COUNT(*) AS amc FROM web_sales, time_dim "
                "WHERE ws_sold_time_sk = time_dim.t_time_sk) AS at, "
                "(SELECT COUNT(*) AS pmc FROM web_sales) AS pm"
            )
            result = adapter._safeguard_snowflake_division(sql)
            assert "NULLIF" in result
            assert "am_pm_ratio" in result
            assert "web_sales" in result


class TestSnowflakeGenerateTuningClause:
    def test_no_tuning_returns_empty(self):
        adapter = _make_adapter()
        assert adapter.generate_tuning_clause(None) == ""

    def test_empty_tuning_returns_empty(self):
        adapter = _make_adapter()
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = False
        assert adapter.generate_tuning_clause(mock_tuning) == ""

    def test_clustering_col_returns_cluster_by(self):
        adapter = _make_adapter()
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True
        col = Mock()
        col.name = "l_orderkey"
        col.order = 1

        def _cols(t):
            if t == TuningType.CLUSTERING:
                return [col]
            return []

        mock_tuning.get_columns_by_type.side_effect = _cols
        clause = adapter.generate_tuning_clause(mock_tuning)

        assert "CLUSTER BY (l_orderkey)" in clause

    def test_multi_cluster_cols(self):
        adapter = _make_adapter()
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True
        col1, col2 = Mock(), Mock()
        col1.name = "a_col"
        col1.order = 1
        col2.name = "b_col"
        col2.order = 2

        def _cols(t):
            if t == TuningType.CLUSTERING:
                return [col1, col2]
            return []

        mock_tuning.get_columns_by_type.side_effect = _cols
        clause = adapter.generate_tuning_clause(mock_tuning)

        assert "CLUSTER BY (a_col, b_col)" in clause


class TestSnowflakeCreateLoadFileFormats:
    def test_csv_format_uses_compression(self):
        adapter = _make_adapter(compression="GZIP")
        mock_cursor = Mock()
        adapter._create_load_file_formats(mock_cursor)
        executed_sql = [call.args[0] for call in mock_cursor.execute.call_args_list if call.args]
        assert any("BENCHBOX_CSV_FORMAT" in sql and "GZIP" in sql for sql in executed_sql)

    def test_tbl_format_created(self):
        adapter = _make_adapter()
        mock_cursor = Mock()
        adapter._create_load_file_formats(mock_cursor)
        executed_sql = [call.args[0] for call in mock_cursor.execute.call_args_list if call.args]
        assert any("BENCHBOX_TBL_FORMAT" in sql for sql in executed_sql)


class TestSnowflakeConnectionParams:
    def test_connection_params_include_account(self):
        adapter = _make_adapter(account="myaccount", warehouse="MY_WH", role="SYSADMIN")
        params = adapter._connect_params({}, include_database=False)
        assert params.get("account") == "myaccount"

    def test_warehouse_in_params(self):
        adapter = _make_adapter(warehouse="COMPUTE_WH")
        params = adapter._connect_params({}, include_database=False)
        assert params["warehouse"] == "COMPUTE_WH"


class TestSnowflakeGetTargetDialect:
    def test_returns_snowflake(self):
        adapter = _make_adapter()
        assert adapter.get_target_dialect() == "snowflake"
