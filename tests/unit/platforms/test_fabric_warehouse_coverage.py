# Copyright 2026 Joe Harris / BenchBox Project

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

import benchbox.platforms.fabric_warehouse as fabric_module
from benchbox.core.exceptions import ConfigurationError
from benchbox.platforms.base.data_loading import DataSource
from benchbox.platforms.fabric_warehouse import FabricWarehouseAdapter

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.fixture()
def _fabric_stubs(monkeypatch):
    mock_pyodbc = MagicMock()
    mock_pyodbc.Error = Exception
    monkeypatch.setattr(fabric_module, "pyodbc", mock_pyodbc)
    monkeypatch.setattr(fabric_module, "check_platform_dependencies", lambda *a, **kw: (True, []))
    return mock_pyodbc


@pytest.fixture()
def adapter(_fabric_stubs):
    return FabricWarehouseAdapter(
        workspace="ws-guid",
        warehouse="test_wh",
        auth_method="default_credential",
    )


class TestServicePrincipalValidation:
    def test_missing_only_client_id(self, _fabric_stubs):
        with pytest.raises(ConfigurationError, match="client_id"):
            FabricWarehouseAdapter(
                workspace="ws",
                warehouse="wh",
                auth_method="service_principal",
                client_secret="s",
                tenant_id="t",
            )

    def test_missing_only_client_secret(self, _fabric_stubs):
        with pytest.raises(ConfigurationError, match="client_secret"):
            FabricWarehouseAdapter(
                workspace="ws",
                warehouse="wh",
                auth_method="service_principal",
                client_id="c",
                tenant_id="t",
            )

    def test_missing_only_tenant_id(self, _fabric_stubs):
        with pytest.raises(ConfigurationError, match="tenant_id"):
            FabricWarehouseAdapter(
                workspace="ws",
                warehouse="wh",
                auth_method="service_principal",
                client_id="c",
                client_secret="s",
            )


class TestCreateConnectionErrors:
    def test_cross_region_error_logged(self, adapter, _fabric_stubs):
        _fabric_stubs.connect.side_effect = Exception("cross-region connection not allowed")
        with pytest.raises(ConnectionError):
            adapter.create_connection()

    def test_read_only_error_logged(self, adapter, _fabric_stubs):
        _fabric_stubs.connect.side_effect = Exception("permission denied read-only")
        with pytest.raises(ConnectionError):
            adapter.create_connection()


class TestCheckServerDatabaseExists:
    def test_returns_true_when_connection_succeeds(self, adapter):
        mock_conn = MagicMock()
        with patch.object(adapter, "create_connection", return_value=mock_conn):
            assert adapter.check_server_database_exists() is True

    def test_returns_false_when_connection_fails(self, adapter):
        with patch.object(adapter, "create_connection", side_effect=ConnectionError("fail")):
            assert adapter.check_server_database_exists() is False


class TestGetPlatformInfo:
    def test_includes_basic_fields(self, adapter):
        with patch.object(adapter, "create_connection", side_effect=Exception("no conn")):
            info = adapter.get_platform_info()
        assert info["platform"] == "Fabric Warehouse"
        assert "server" in info

    def test_fetches_version_when_connected(self, adapter):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("SQL Server 2022",)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        with patch.object(adapter, "create_connection", return_value=mock_conn):
            info = adapter.get_platform_info()
        assert info.get("version") == "SQL Server 2022"


class TestCreateSchema:
    def test_executes_table_creation(self, adapter, _fabric_stubs):
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_benchmark = MagicMock()

        schema_sql = "CREATE TABLE lineitem (id INT);"
        with (
            patch.object(adapter, "_create_schema_with_tuning", return_value=schema_sql),
            patch.object(adapter, "_extract_table_name", return_value="lineitem"),
            patch.object(adapter, "drop_table"),
        ):
            elapsed = adapter.create_schema(mock_benchmark, mock_conn)

        mock_cursor.execute.assert_called()
        assert elapsed >= 0

    def test_non_dbo_schema_creates_schema(self, _fabric_stubs):
        adp = FabricWarehouseAdapter(workspace="ws", warehouse="wh", auth_method="default_credential", schema="bench")
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_benchmark = MagicMock()

        with (
            patch.object(adp, "_create_schema_with_tuning", return_value="CREATE TABLE t (id INT);"),
            patch.object(adp, "_extract_table_name", return_value="t"),
            patch.object(adp, "drop_table"),
        ):
            adp.create_schema(mock_benchmark, mock_conn)

        assert any("SCHEMA" in str(c) for c in mock_cursor.execute.call_args_list)

    def test_permission_error_raises_runtime_error(self, adapter, _fabric_stubs):
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_benchmark = MagicMock()

        with (
            patch.object(adapter, "_create_schema_with_tuning", return_value="CREATE TABLE t (id INT);"),
            patch.object(adapter, "drop_table"),
        ):
            mock_cursor.execute.side_effect = _fabric_stubs.Error("permission denied")
            with pytest.raises(RuntimeError, match="READ-ONLY"):
                adapter.create_schema(mock_benchmark, mock_conn)


class TestLoadData:
    def test_loads_from_benchmark_tables(self, adapter, _fabric_stubs, tmp_path):
        data_file = tmp_path / "lineitem.tbl"
        data_file.write_text("1|2|3")

        mock_conn = MagicMock()
        benchmark = SimpleNamespace(tables={"lineitem": [data_file]})

        with patch.object(adapter, "_load_data_via_onelake", return_value=1000):
            stats, elapsed, meta = adapter.load_data(benchmark, mock_conn, tmp_path)

        assert stats["lineitem"] == 1000
        assert meta is None

    def test_raises_when_no_tables(self, adapter, tmp_path):
        mock_conn = MagicMock()
        benchmark = SimpleNamespace(tables={})

        with pytest.raises(ValueError, match="No data files found"):
            adapter.load_data(benchmark, mock_conn, tmp_path)

    def test_no_data_files_skips_table(self, adapter, tmp_path):
        mock_conn = MagicMock()
        benchmark = SimpleNamespace(tables={"lineitem": [tmp_path / "missing.tbl"]})

        stats, elapsed, _ = adapter.load_data(benchmark, mock_conn, tmp_path)

        assert stats["lineitem"] == 0

    def test_directory_inputs_are_not_treated_as_loadable_files(self, adapter, tmp_path):
        mock_conn = MagicMock()
        table_dir = tmp_path / "lineitem"
        table_dir.mkdir()
        benchmark = SimpleNamespace(tables={"lineitem": [table_dir]})

        stats, elapsed, _ = adapter.load_data(benchmark, mock_conn, tmp_path)

        assert stats["lineitem"] == 0

    def test_onelake_failure_falls_back_to_direct(self, adapter, _fabric_stubs, tmp_path):
        data_file = tmp_path / "lineitem.tbl"
        data_file.write_text("1|2|3")
        mock_conn = MagicMock()
        benchmark = SimpleNamespace(tables={"lineitem": [data_file]})
        _fabric_stubs.Error = Exception

        with (
            patch.object(adapter, "_load_data_via_onelake", side_effect=Exception("upload failed")),
            patch.object(adapter, "_load_data_direct", return_value=500),
        ):
            stats, elapsed, _ = adapter.load_data(benchmark, mock_conn, tmp_path)

        assert stats["lineitem"] == 500


class TestGetQueryPlan:
    def test_returns_plan_string(self, adapter):
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("HashMatch",), ("TableScan",)]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        plan = adapter.get_query_plan(mock_conn, "SELECT 1")

        assert "HashMatch" in plan
        assert "TableScan" in plan

    def test_returns_none_on_exception(self, adapter):
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("plan not available")
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        plan = adapter.get_query_plan(mock_conn, "SELECT 1")
        assert plan is None

    def test_failed_explain_records_explain_failed(self, adapter):
        adapter.capture_plans = True
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("plan not available")
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        plan, _ = adapter.capture_query_plan(mock_conn, "SELECT 1", "q-err-fabric")
        assert plan is None
        assert adapter.plan_capture_errors[-1]["reason"] == "explain_failed"


class TestAnalyzeTable:
    def test_executes_statistics_update(self, adapter):
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.analyze_table(mock_conn, "lineitem")
        mock_cursor.execute.assert_called_once()
        assert "UPDATE STATISTICS" in str(mock_cursor.execute.call_args)

    def test_raises_on_pyodbc_error(self, adapter, _fabric_stubs):
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = _fabric_stubs.Error("stats failed")
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with pytest.raises(_fabric_stubs.Error, match="stats failed"):
            adapter.analyze_table(mock_conn, "lineitem")
        mock_cursor.close.assert_called_once()


class TestApplyPlatformOptimizations:
    def test_noop_when_config_is_none(self, adapter):
        adapter.apply_platform_optimizations(None, MagicMock())

    def test_logs_when_config_present(self, adapter):
        config = MagicMock()
        adapter.apply_platform_optimizations(config, MagicMock())


class TestApplyConstraintConfiguration:
    def test_noop_when_configs_are_none(self, adapter):
        adapter.apply_constraint_configuration(None, None, MagicMock())

    def test_logs_when_primary_keys_enabled(self, adapter):
        pk_config = SimpleNamespace(enabled=True)
        fk_config = SimpleNamespace(enabled=True)
        adapter.apply_constraint_configuration(pk_config, fk_config, MagicMock())


class TestSupportsTuningType:
    def test_returns_false_when_import_fails(self, adapter):
        with patch.dict("sys.modules", {"benchbox.core.tuning.interface": None}):
            result = adapter.supports_tuning_type(MagicMock())
        assert result is False

    def test_returns_true_for_clustering(self, adapter):
        mock_tuning = MagicMock()
        mock_tuning.TuningType.CLUSTERING = "CLUSTERING"
        with patch.dict("sys.modules", {"benchbox.core.tuning.interface": mock_tuning}):
            result = adapter.supports_tuning_type("CLUSTERING")
        assert result is True


class TestGenerateTuningClause:
    def test_returns_empty_string(self, adapter):
        assert adapter.generate_tuning_clause(MagicMock()) == ""


class TestCreateSchemaDdl:
    def test_executes_schema_qualified_statement(self, adapter):
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with (
            patch.object(adapter, "_create_schema_with_tuning", return_value="CREATE TABLE lineitem (id INT);"),
            patch.object(adapter, "drop_table") as drop,
        ):
            adapter.create_schema(MagicMock(), mock_conn)

        drop.assert_called_once_with(mock_conn, "lineitem")
        executed = mock_cursor.execute.call_args[0][0]
        assert "[dbo].[lineitem]" in executed

    def test_skips_non_table_statement(self, adapter):
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with (
            patch.object(adapter, "_create_schema_with_tuning", return_value="CREATE INDEX ix ON t (id);"),
            patch.object(adapter, "drop_table"),
        ):
            adapter.create_schema(MagicMock(), mock_conn)

        mock_cursor.execute.assert_not_called()


class TestFromConfig:
    def test_warehouse_key_used_as_database(self, _fabric_stubs):
        config = {
            "workspace": "ws-guid",
            "warehouse": "my_wh",
            "auth_method": "default_credential",
        }
        adp = FabricWarehouseAdapter.from_config(config)
        assert adp.database == "my_wh"

    def test_database_key_takes_precedence_over_warehouse(self, _fabric_stubs):
        config = {
            "workspace": "ws-guid",
            "warehouse": "my_wh",
            "database": "explicit_db",
            "auth_method": "default_credential",
        }
        adp = FabricWarehouseAdapter.from_config(config)
        assert adp.database == "explicit_db"


class TestConfigureForBenchmark:
    def test_noop_for_non_olap_type(self, adapter):
        mock_conn = MagicMock()
        adapter.configure_for_benchmark(mock_conn, "custom_workload")
        mock_conn.cursor.assert_not_called()

    def test_olap_executes_ansi_settings(self, adapter, _fabric_stubs):
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.configure_for_benchmark(mock_conn, "tpch")

        executed = [str(c) for c in mock_cursor.execute.call_args_list]
        assert any("ANSI_NULLS" in s for s in executed)
        assert any("ANSI_WARNINGS" in s for s in executed)

    def test_clears_query_store_when_disable_result_cache(self, adapter, _fabric_stubs):
        adapter.disable_result_cache = True
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.configure_for_benchmark(mock_conn, "olap")

        executed = [str(c) for c in mock_cursor.execute.call_args_list]
        assert any("QUERY_STORE" in s for s in executed)

    def test_query_store_pyodbc_error_silenced(self, adapter, _fabric_stubs):
        adapter.disable_result_cache = True
        mock_cursor = MagicMock()

        def execute_side_effect(sql, *args):
            if "QUERY_STORE" in sql:
                raise _fabric_stubs.Error("not supported")

        mock_cursor.execute.side_effect = execute_side_effect
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.configure_for_benchmark(mock_conn, "olap")

    def test_result_cache_enabled_skips_query_store(self, adapter, _fabric_stubs):
        adapter.disable_result_cache = False
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        adapter.configure_for_benchmark(mock_conn, "olap")

        executed = [str(c) for c in mock_cursor.execute.call_args_list]
        assert not any("QUERY_STORE" in s for s in executed)
        assert any("ANSI_NULLS" in s for s in executed)

    def test_outer_exception_logged_as_warning(self, adapter):
        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = Exception("connection dead")

        adapter.configure_for_benchmark(mock_conn, "tpch")


class TestTestConnection:
    def test_write_failure_logged_as_warning(self, adapter, _fabric_stubs):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("SQL Server 2022",)

        call_count = [0]

        def execute_side_effect(sql, *args):
            call_count[0] += 1
            if "benchbox_test_temp" in sql and "CREATE" in sql:
                raise _fabric_stubs.Error("permission denied")

        mock_cursor.execute.side_effect = execute_side_effect
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.close = MagicMock()

        with patch.object(adapter, "create_connection", return_value=mock_conn):
            result = adapter.test_connection()

        assert result["write_capable"] is False


class TestCreateSchemaBranches:
    def test_pyodbc_non_permission_error_reraises(self, adapter, _fabric_stubs):
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_benchmark = MagicMock()

        with (
            patch.object(adapter, "_create_schema_with_tuning", return_value="CREATE TABLE t (id INT);"),
            patch.object(adapter, "drop_table"),
        ):
            mock_cursor.execute.side_effect = _fabric_stubs.Error("syntax error")
            with pytest.raises(_fabric_stubs.Error):
                adapter.create_schema(mock_benchmark, mock_conn)


class TestLoadDataExtended:
    def test_loads_from_manifest_json(self, adapter, tmp_path):
        mock_conn = MagicMock()
        benchmark = object()
        resolved = DataSource(
            source_type="test",
            tables={
                "lineitem": [tmp_path / "lineitem.tbl"],
                "orders": [tmp_path / "orders.tbl"],
            },
        )

        with patch.object(adapter, "_resolve_data_files", return_value=resolved):
            stats, _, _ = adapter.load_data(benchmark, mock_conn, tmp_path)

        assert "lineitem" in stats
        assert "orders" in stats

    def test_loads_from_manifest_with_data_files(self, adapter, tmp_path):
        data_file = tmp_path / "lineitem.tbl"
        data_file.write_text("1|2|3")

        mock_conn = MagicMock()
        benchmark = object()
        ds = DataSource(source_type="test", tables={"lineitem": [data_file]})

        with (
            patch.object(adapter, "_resolve_data_files", return_value=ds),
            patch.object(adapter, "_load_data_via_onelake", return_value=1000),
        ):
            stats, elapsed, _ = adapter.load_data(benchmark, mock_conn, tmp_path)

        assert stats["lineitem"] == 1000

    def test_use_onelake_false_uses_direct_path(self, adapter, _fabric_stubs, tmp_path):
        adapter.use_onelake = False
        data_file = tmp_path / "lineitem.tbl"
        data_file.write_text("1|2|3")
        mock_conn = MagicMock()
        benchmark = SimpleNamespace(tables={"lineitem": data_file})

        with patch.object(adapter, "_load_data_direct", return_value=42):
            stats, _, _ = adapter.load_data(benchmark, mock_conn, tmp_path)

        assert stats["lineitem"] == 42

    def test_pyodbc_permission_error_raises_runtime(self, adapter, _fabric_stubs, tmp_path):
        data_file = tmp_path / "lineitem.tbl"
        data_file.write_text("1|2|3")
        mock_conn = MagicMock()
        benchmark = SimpleNamespace(tables={"lineitem": data_file})

        with patch.object(
            adapter,
            "_load_data_via_onelake",
            side_effect=_fabric_stubs.Error("permission denied read-only"),
        ):
            with pytest.raises(RuntimeError, match="READ-ONLY"):
                adapter.load_data(benchmark, mock_conn, tmp_path)

    def test_pyodbc_fallback_direct_also_fails(self, adapter, _fabric_stubs, tmp_path):
        data_file = tmp_path / "lineitem.tbl"
        data_file.write_text("1|2|3")
        mock_conn = MagicMock()
        benchmark = SimpleNamespace(tables={"lineitem": data_file})

        with (
            patch.object(adapter, "_load_data_via_onelake", side_effect=_fabric_stubs.Error("network timeout")),
            patch.object(adapter, "_load_data_direct", side_effect=Exception("insert failed")),
        ):
            stats, _, _ = adapter.load_data(benchmark, mock_conn, tmp_path)

        assert stats["lineitem"] == 0

    def test_raises_when_no_manifest_and_no_tables_attr(self, adapter, tmp_path):
        mock_conn = MagicMock()
        benchmark = object()
        with pytest.raises(ValueError, match="No data files found"):
            adapter.load_data(benchmark, mock_conn, tmp_path)

    def test_use_onelake_false_pyodbc_error_sets_zero(self, adapter, _fabric_stubs, tmp_path):
        adapter.use_onelake = False
        data_file = tmp_path / "lineitem.tbl"
        data_file.write_text("1|2|3")
        mock_conn = MagicMock()
        benchmark = SimpleNamespace(tables={"lineitem": data_file})

        with patch.object(adapter, "_load_data_direct", side_effect=_fabric_stubs.Error("failed")):
            stats, _, _ = adapter.load_data(benchmark, mock_conn, tmp_path)

        assert stats["lineitem"] == 0

    def test_generic_exception_sets_row_count_to_zero(self, adapter, _fabric_stubs, tmp_path):
        data_file = tmp_path / "lineitem.tbl"
        data_file.write_text("1|2|3")
        mock_conn = MagicMock()
        benchmark = SimpleNamespace(tables={"lineitem": data_file})

        with (
            patch.object(adapter, "_load_data_via_onelake", side_effect=_fabric_stubs.Error("network err")),
            patch.object(adapter, "_load_data_direct", return_value=0),
        ):
            stats, _, _ = adapter.load_data(benchmark, mock_conn, tmp_path)
        assert stats["lineitem"] == 0


class TestLoadDataDirect:
    def test_inserts_rows_from_pipe_delimited_file(self, adapter, tmp_path):
        from benchbox.platforms.base.data_loading import CsvDialect

        data_file = tmp_path / "lineitem.tbl"
        data_file.write_text("1|Alice|100\n2|Bob|200\n3|Carol|300\n")

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        tbl_dialect = CsvDialect(delimiter="|", has_header=False, null_marker="", normalize_booleans=False, quote=None)
        with patch("benchbox.platforms.fabric_warehouse.resolve_csv_dialect", return_value=tbl_dialect):
            row_count = adapter._load_data_direct(mock_conn, "lineitem", [data_file])

        assert row_count == 3
        mock_cursor.execute.assert_called()

    def test_skips_empty_file(self, adapter, tmp_path):
        data_file = tmp_path / "lineitem.tbl"
        data_file.write_text("")

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = MagicMock()

        row_count = adapter._load_data_direct(mock_conn, "lineitem", [data_file])
        assert row_count == 0

    def test_skips_empty_rows(self, adapter, tmp_path):
        from benchbox.platforms.base.data_loading import CsvDialect

        data_file = tmp_path / "lineitem.tbl"
        data_file.write_text("1|Alice\n\n   \n2|Bob\n")

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        tbl_dialect = CsvDialect(delimiter="|", has_header=False, null_marker="", normalize_booleans=False, quote=None)
        with patch("benchbox.platforms.fabric_warehouse.resolve_csv_dialect", return_value=tbl_dialect):
            row_count = adapter._load_data_direct(mock_conn, "lineitem", [data_file])

        assert row_count == 2

    def test_batches_large_file(self, adapter, tmp_path):
        from benchbox.platforms.base.data_loading import CsvDialect

        lines = "\n".join(f"{i}|val{i}" for i in range(1, 1502))
        data_file = tmp_path / "lineitem.tbl"
        data_file.write_text(lines + "\n")

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        tbl_dialect = CsvDialect(delimiter="|", has_header=False, null_marker="", normalize_booleans=False, quote=None)
        with patch("benchbox.platforms.fabric_warehouse.resolve_csv_dialect", return_value=tbl_dialect):
            row_count = adapter._load_data_direct(mock_conn, "lineitem", [data_file])

        assert row_count == 1501
        assert mock_cursor.execute.call_count >= 2

    def test_exactly_1000_rows_leaves_empty_final_batch(self, adapter, tmp_path):
        from benchbox.platforms.base.data_loading import CsvDialect

        lines = "\n".join(f"{i}|val{i}" for i in range(1, 1001))
        data_file = tmp_path / "lineitem.tbl"
        data_file.write_text(lines + "\n")

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        tbl_dialect = CsvDialect(delimiter="|", has_header=False, null_marker="", normalize_booleans=False, quote=None)
        with patch("benchbox.platforms.fabric_warehouse.resolve_csv_dialect", return_value=tbl_dialect):
            row_count = adapter._load_data_direct(mock_conn, "lineitem", [data_file])

        assert row_count == 1000


class TestLoadDataViaOnelake:
    def test_csv_copy_into(self, adapter, tmp_path):
        from benchbox.platforms.base.data_loading import CsvDialect

        data_file = tmp_path / "lineitem.tbl"
        data_file.write_text("1|2|3")

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (100,)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        tbl_dialect = CsvDialect(delimiter="|", has_header=False, null_marker="", normalize_booleans=False, quote=None)
        with (
            patch.object(adapter, "_upload_to_onelake", return_value="https://onelake.dfs.fabric.microsoft.com/x"),
            patch("benchbox.platforms.fabric_warehouse.is_parquet_format", return_value=False),
            patch("benchbox.platforms.fabric_warehouse.resolve_csv_dialect", return_value=tbl_dialect),
        ):
            row_count = adapter._load_data_via_onelake(mock_conn, "lineitem", [data_file])

        assert row_count == 100
        assert mock_cursor.execute.call_count == 2

    def test_parquet_copy_into(self, adapter, tmp_path):
        data_file = tmp_path / "lineitem.parquet"
        data_file.write_bytes(b"PAR1fake")

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (50,)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with (
            patch.object(adapter, "_upload_to_onelake", return_value="https://onelake.dfs.fabric.microsoft.com/x"),
            patch("benchbox.platforms.fabric_warehouse.is_parquet_format", return_value=True),
        ):
            row_count = adapter._load_data_via_onelake(mock_conn, "lineitem", [data_file])

        assert row_count == 50
        copy_call = str(mock_cursor.execute.call_args_list[0])
        assert "PARQUET" in copy_call

    def test_skips_empty_file(self, adapter, tmp_path):
        data_file = tmp_path / "lineitem.tbl"
        data_file.write_text("")

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(adapter, "_upload_to_onelake") as mock_upload:
            row_count = adapter._load_data_via_onelake(mock_conn, "lineitem", [data_file])

        mock_upload.assert_not_called()
        assert row_count == 0

    def test_non_tpc_uses_delimiter_detection(self, adapter, tmp_path):
        from benchbox.platforms.base.data_loading import CsvDialect

        data_file = tmp_path / "lineitem.csv"
        data_file.write_text("a,b,c\n1,2,3\n")

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        csv_dialect = CsvDialect(
            delimiter=",", has_header=False, null_marker=None, normalize_booleans=False, quote=None
        )
        with (
            patch.object(adapter, "_upload_to_onelake", return_value="https://onelake.x"),
            patch("benchbox.platforms.fabric_warehouse.is_parquet_format", return_value=False),
            patch("benchbox.platforms.fabric_warehouse.resolve_csv_dialect", return_value=csv_dialect),
        ):
            row_count = adapter._load_data_via_onelake(mock_conn, "lineitem", [data_file])

        assert row_count == 1


class TestTestConnectionPaths:
    def test_write_capable_success_returns_true(self, adapter, _fabric_stubs):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("SQL Server 2022",)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.close = MagicMock()

        with patch.object(adapter, "create_connection", return_value=mock_conn):
            result = adapter.test_connection()

        assert result["write_capable"] is True
        assert result["success"] is True

    def test_connection_failure_returns_error_dict(self, adapter):
        with patch.object(adapter, "create_connection", side_effect=Exception("auth failed")):
            result = adapter.test_connection()

        assert result["success"] is False
        assert "auth failed" in result["error"]


class TestExtractTableName:
    def test_extracts_simple_name(self, adapter):
        assert adapter._extract_table_name("CREATE TABLE lineitem (id INT)") == "lineitem"

    def test_extracts_with_schema_prefix(self, adapter):
        assert adapter._extract_table_name("CREATE TABLE dbo.lineitem (id INT)") == "lineitem"

    def test_extracts_with_brackets(self, adapter):
        assert adapter._extract_table_name("CREATE TABLE [dbo].[lineitem] (id INT)") == "lineitem"

    def test_returns_none_for_non_match(self, adapter):
        assert adapter._extract_table_name("SELECT 1") is None


class TestInsertBatch:
    def test_noop_on_empty_batch(self, adapter):
        mock_cursor = MagicMock()
        adapter._insert_batch(mock_cursor, "[dbo].[t]", [])
        mock_cursor.execute.assert_not_called()

    def test_inserts_null_for_empty_values(self, adapter):
        mock_cursor = MagicMock()
        adapter._insert_batch(mock_cursor, "[dbo].[t]", [["", "NULL", "hello"]])
        sql = str(mock_cursor.execute.call_args[0][0])
        assert "NULL" in sql
        assert "'hello'" in sql

    def test_escapes_single_quotes(self, adapter):
        mock_cursor = MagicMock()
        adapter._insert_batch(mock_cursor, "[dbo].[t]", [["it's"]])
        sql = str(mock_cursor.execute.call_args[0][0])
        assert "it''s" in sql
