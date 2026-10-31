from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from benchbox.core.write_primitives.benchmark import (
    OperationResult,
    WritePrimitivesBenchmark,
    _check_validation_query,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


def _make_val_query(
    expected_rows=None,
    expected_rows_min=None,
    expected_rows_max=None,
):
    return SimpleNamespace(
        id="VQ_1",
        sql="SELECT 1",
        expected_rows=expected_rows,
        expected_rows_min=expected_rows_min,
        expected_rows_max=expected_rows_max,
        platform_overrides={},
    )


def _make_operation(
    *,
    write_sql="INSERT INTO t VALUES (1)",
    platform_overrides=None,
    requires_setup=False,
    validation_queries=None,
    cleanup_sql=None,
    aggregate_state=None,
    id="OP_1",
    category="insert",
):
    return SimpleNamespace(
        id=id,
        write_sql=write_sql,
        platform_overrides=platform_overrides or {},
        requires_setup=requires_setup,
        validation_queries=validation_queries or [],
        cleanup_sql=cleanup_sql,
        aggregate_state=aggregate_state,
        category=category,
    )


@pytest.fixture()
def wp(tmp_path: Path) -> WritePrimitivesBenchmark:
    return WritePrimitivesBenchmark(output_dir=tmp_path)


class TestCheckValidationQuery:
    def test_exact_match_passes(self):
        vq = _make_val_query(expected_rows=5)
        assert _check_validation_query(vq, 5) is True

    def test_exact_match_fails(self):
        vq = _make_val_query(expected_rows=5)
        assert _check_validation_query(vq, 3) is False

    def test_min_max_both_present_passes(self):
        vq = _make_val_query(expected_rows_min=2, expected_rows_max=10)
        assert _check_validation_query(vq, 5) is True

    def test_min_max_both_present_fails_below(self):
        vq = _make_val_query(expected_rows_min=2, expected_rows_max=10)
        assert _check_validation_query(vq, 1) is False

    def test_min_max_both_present_fails_above(self):
        vq = _make_val_query(expected_rows_min=2, expected_rows_max=10)
        assert _check_validation_query(vq, 11) is False

    def test_only_min_set_passes(self):
        vq = _make_val_query(expected_rows_min=3)
        assert _check_validation_query(vq, 100) is True

    def test_only_min_set_fails(self):
        vq = _make_val_query(expected_rows_min=3)
        assert _check_validation_query(vq, 2) is False

    def test_only_max_set_passes(self):
        vq = _make_val_query(expected_rows_max=10)
        assert _check_validation_query(vq, 5) is True

    def test_only_max_set_fails(self):
        vq = _make_val_query(expected_rows_max=10)
        assert _check_validation_query(vq, 11) is False

    def test_no_constraints_returns_true(self):
        vq = _make_val_query()
        assert _check_validation_query(vq, 999) is True


class TestInitDefaultOutputDir:
    def test_default_output_dir_uses_tpch_path(self, tmp_path):
        with patch("benchbox.core.write_primitives.benchmark.get_benchmark_runs_datagen_path") as mock_path:
            mock_path.return_value = tmp_path
            bm = WritePrimitivesBenchmark()
            mock_path.assert_called_once_with("tpch", 1.0)
            assert str(bm.output_dir) == str(tmp_path)

    def test_quiet_kwarg_is_consumed(self, tmp_path):
        bm = WritePrimitivesBenchmark(output_dir=tmp_path, quiet=True)
        assert bm is not None


class TestOutputDirSetter:
    def test_setter_updates_data_generator(self, tmp_path):
        bm = WritePrimitivesBenchmark(output_dir=tmp_path)
        new_dir = tmp_path / "newdir"
        new_dir.mkdir()
        bm.output_dir = new_dir
        assert str(bm.data_generator.output_dir) == str(new_dir)

    def test_setter_updates_tpch_generator_if_present(self, tmp_path):
        bm = WritePrimitivesBenchmark(output_dir=tmp_path)
        new_dir = tmp_path / "sub"
        new_dir.mkdir()
        if hasattr(bm.data_generator, "tpch_generator"):
            bm.output_dir = new_dir
            assert str(bm.data_generator.tpch_generator.output_dir) == str(new_dir)


class TestGenerateData:
    def test_returns_list_of_paths(self, wp, tmp_path):
        fake_tables = {"orders": tmp_path / "orders.tbl", "lineitem": tmp_path / "lineitem.tbl"}
        with patch.object(wp.data_generator, "generate", return_value=fake_tables):
            result = wp.generate_data()
        assert isinstance(result, list)
        assert len(result) == 2
        assert wp.tables == fake_tables

    def test_generates_subset_of_tables(self, wp, tmp_path):
        fake_tables = {"orders": tmp_path / "orders.tbl"}
        with patch.object(wp.data_generator, "generate", return_value=fake_tables):
            result = wp.generate_data(tables=["orders"])
        assert len(result) == 1


class TestEnsureAuxiliaryDataFiles:
    def test_no_op_when_files_exist(self, wp):
        with patch.object(wp.data_generator, "check_bulk_load_files_exist", return_value=True):
            wp.ensure_auxiliary_data_files()

    def test_generates_when_files_missing_and_lock_acquired(self, wp, tmp_path):
        wp.data_generator.files_dir = tmp_path / "aux"
        with (
            patch.object(wp.data_generator, "check_bulk_load_files_exist", return_value=False),
            patch.object(wp.data_generator, "_acquire_bulk_load_lock", return_value=True),
            patch.object(wp.data_generator, "generate_bulk_load_files", return_value=[tmp_path / "f.csv"]) as mock_gen,
            patch.object(wp.data_generator, "_release_bulk_load_lock") as mock_release,
        ):
            wp.ensure_auxiliary_data_files()
            mock_gen.assert_called_once()
            mock_release.assert_called_once()

    def test_skips_generation_when_lock_not_acquired(self, wp):
        with (
            patch.object(wp.data_generator, "check_bulk_load_files_exist", return_value=False),
            patch.object(wp.data_generator, "_acquire_bulk_load_lock", return_value=False),
            patch.object(wp.data_generator, "generate_bulk_load_files") as mock_gen,
        ):
            wp.ensure_auxiliary_data_files()
            mock_gen.assert_not_called()

    def test_handles_generation_exception(self, wp, tmp_path):
        wp.data_generator.files_dir = tmp_path / "aux"
        with (
            patch.object(wp.data_generator, "check_bulk_load_files_exist", return_value=False),
            patch.object(wp.data_generator, "_acquire_bulk_load_lock", return_value=True),
            patch.object(wp.data_generator, "generate_bulk_load_files", side_effect=RuntimeError("disk full")),
            patch.object(wp.data_generator, "_release_bulk_load_lock"),
        ):
            wp.ensure_auxiliary_data_files()

    def test_skips_regeneration_when_another_process_generated(self, wp):
        call_count = {"n": 0}

        def check_files():
            call_count["n"] += 1
            return call_count["n"] > 1

        with (
            patch.object(wp.data_generator, "check_bulk_load_files_exist", side_effect=check_files),
            patch.object(wp.data_generator, "_acquire_bulk_load_lock", return_value=True),
            patch.object(wp.data_generator, "generate_bulk_load_files") as mock_gen,
            patch.object(wp.data_generator, "_release_bulk_load_lock"),
        ):
            wp.ensure_auxiliary_data_files()
            mock_gen.assert_not_called()


class TestAcquireSetupLock:
    def test_datafusion_dialect_skips_lock(self, wp):
        conn = MagicMock()
        result = wp._acquire_setup_lock(conn, dialect="datafusion")
        assert result is True
        conn.execute.assert_not_called()

    def test_fails_when_create_table_raises(self, wp):
        conn = MagicMock()
        conn.execute.side_effect = Exception("permission denied")
        result = wp._acquire_setup_lock(conn, dialect="standard")
        assert result is False

    def test_acquires_lock_on_first_try(self, wp):
        conn = MagicMock()
        conn.execute.return_value = MagicMock()
        result = wp._acquire_setup_lock(conn, timeout_seconds=5, dialect="standard")
        assert result is True

    def test_returns_false_on_timeout(self, wp):
        conn = MagicMock()
        call_count = {"n": 0}

        def side_effect(sql):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return MagicMock()
            raise Exception("duplicate key constraint violation")

        conn.execute.side_effect = side_effect
        result = wp._acquire_setup_lock(conn, timeout_seconds=0, dialect="standard")
        assert result is False

    def test_unexpected_error_during_insert_returns_false(self, wp):
        conn = MagicMock()
        call_count = {"n": 0}

        def side_effect(sql):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return MagicMock()
            raise Exception("network error")

        conn.execute.side_effect = side_effect
        result = wp._acquire_setup_lock(conn, timeout_seconds=5, dialect="standard")
        assert result is False


class TestReleaseSetupLock:
    def test_datafusion_dialect_is_noop(self, wp):
        conn = MagicMock()
        wp._release_setup_lock(conn, dialect="datafusion")
        conn.execute.assert_not_called()

    def test_executes_delete(self, wp):
        conn = MagicMock()
        wp._release_setup_lock(conn, dialect="standard")
        conn.execute.assert_called_once()
        assert "DELETE FROM" in conn.execute.call_args[0][0]

    def test_handles_exception_gracefully(self, wp):
        conn = MagicMock()
        conn.execute.side_effect = Exception("connection lost")
        wp._release_setup_lock(conn, dialect="standard")


class TestGetEffectiveWriteSql:
    def test_sql_override_takes_priority(self, wp):
        op = _make_operation(write_sql="INSERT INTO a VALUES(1)")
        sql, skip = wp._get_effective_write_sql(op, sql_override="INSERT INTO b VALUES(2)")
        assert sql == "INSERT INTO b VALUES(2)"
        assert skip is None

    def test_platform_override_applied(self, wp):
        op = _make_operation(
            write_sql="INSERT INTO a VALUES(1)",
            platform_overrides={"duckdb": "INSERT INTO duckdb_t VALUES(1)"},
        )
        sql, skip = wp._get_effective_write_sql(op, platform_key="duckdb")
        assert sql == "INSERT INTO duckdb_t VALUES(1)"
        assert skip is None

    def test_none_override_returns_skip_reason(self, wp):
        op = _make_operation(
            write_sql="INSERT INTO a VALUES(1)",
            platform_overrides={"datafusion": None},
            id="OP_SKIP",
        )
        sql, skip = wp._get_effective_write_sql(op, platform_key="datafusion")
        assert sql is None
        assert skip is not None
        assert "unsupported" in skip.lower()

    def test_aggregate_state_op_returns_skip_reason_for_sql_path(self, wp):
        op = _make_operation(
            id="sketch_df_hll_persist_merge",
            write_sql="",
            aggregate_state=SimpleNamespace(sketch_type="hll"),
        )
        sql, skip = wp._get_effective_write_sql(op, platform_key="duckdb")
        assert sql is None
        assert skip is not None
        assert "DataFrame aggregate-state only" in skip
        assert "duckdb" in skip

    def test_no_override_returns_write_sql(self, wp):
        op = _make_operation(write_sql="INSERT INTO a VALUES(1)")
        sql, skip = wp._get_effective_write_sql(op)
        assert sql == "INSERT INTO a VALUES(1)"
        assert skip is None


class TestAggregateStateSourceConversion:
    def test_find_aggregate_tbl_sources_requires_actual_matches(self, tmp_path):
        stale_root = tmp_path / "stale_root"
        stale_root.mkdir()
        good_root = tmp_path / "good_root"
        good_root.mkdir()
        shard_2 = good_root / "lineitem.tbl.2"
        shard_1 = good_root / "lineitem.tbl.1"
        shard_2.write_text("2|", encoding="utf-8")
        shard_1.write_text("1|", encoding="utf-8")

        files = WritePrimitivesBenchmark._find_aggregate_tbl_sources("lineitem", [stale_root, good_root])

        assert files == [shard_1, shard_2]

    def test_resolve_aggregate_source_path_uses_actual_gzip_match_and_duckdb_infers_compression(self, wp, tmp_path):
        wp.scale_factor = 0.01
        datagen_root = tmp_path.parent / "datagen" / "tpch_sf001"
        datagen_root.mkdir(parents=True)
        gz_file = datagen_root / "lineitem.tbl.1.gz"
        gz_file.write_bytes(b"not read by mocked duckdb")
        conn = MagicMock()

        with patch("duckdb.connect", return_value=conn):
            result = wp._resolve_aggregate_source_path("lineitem", adapter=None, output_dir=tmp_path, spark=None)

        assert result == tmp_path / "_aggregate_state_sources" / "lineitem"
        sql = conn.execute.call_args.args[0]
        assert "read_csv([" in sql
        assert "lineitem.tbl.1.gz" in sql
        assert "compression='zstd'" not in sql


class TestGetPopulationSql:
    def test_merge_ops_target(self, wp):
        sql = wp._get_population_sql("merge_ops_target", "orders")
        assert "merge_ops_target" in sql
        assert "0.5" in sql

    def test_merge_ops_source(self, wp):
        sql = wp._get_population_sql("merge_ops_source", "orders")
        assert "merge_ops_source" in sql
        assert "0.5" in sql

    def test_merge_ops_lineitem_target(self, wp):
        sql = wp._get_population_sql("merge_ops_lineitem_target", "lineitem")
        assert "merge_ops_lineitem_target" in sql

    def test_ddl_truncate_target(self, wp):
        sql = wp._get_population_sql("ddl_truncate_target", "orders")
        assert "ddl_truncate_target" in sql
        assert "o_orderkey" in sql

    def test_generic_table_full_copy(self, wp):
        sql = wp._get_population_sql("insert_ops_lineitem", "lineitem")
        assert "SELECT * FROM" in sql


class TestPopulateStagingTables:
    def test_skips_already_populated_table(self, wp):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = (100,)
        status = wp._populate_staging_tables(conn, {"update_ops_orders": "orders"})
        assert status["update_ops_orders"] == 100

    def test_populates_empty_table(self, wp):
        conn = MagicMock()
        call_count = {"n": 0}

        def execute_side(sql):
            call_count["n"] += 1
            mock = MagicMock()
            if call_count["n"] == 1:
                mock.fetchone.return_value = (0,)
            elif call_count["n"] == 2 or call_count["n"] == 4:
                mock.fetchone.return_value = (50,)
            return mock

        conn.execute.side_effect = execute_side
        status = wp._populate_staging_tables(conn, {"update_ops_orders": "orders"})
        assert status["update_ops_orders"] == 50

    def test_skips_optional_table_when_source_missing(self, wp):
        conn = MagicMock()
        call_count = {"n": 0}

        def execute_side(sql):
            call_count["n"] += 1
            mock = MagicMock()
            if call_count["n"] == 1:
                mock.fetchone.return_value = (0,)
            else:
                raise Exception("table does not exist")
            return mock

        conn.execute.side_effect = execute_side
        status = wp._populate_staging_tables(conn, {"delete_ops_supplier": "supplier"})
        assert status["delete_ops_supplier"] == 0

    def test_raises_when_required_source_missing(self, wp):
        conn = MagicMock()
        call_count = {"n": 0}

        def execute_side(sql):
            call_count["n"] += 1
            mock = MagicMock()
            if call_count["n"] == 1:
                mock.fetchone.return_value = (0,)
            else:
                raise Exception("table does not exist")
            return mock

        conn.execute.side_effect = execute_side
        with pytest.raises(RuntimeError, match="Cannot validate source table"):
            wp._populate_staging_tables(conn, {"update_ops_orders": "orders"})

    def test_raises_when_required_source_is_empty(self, wp):
        conn = MagicMock()
        call_count = {"n": 0}

        def execute_side(sql):
            call_count["n"] += 1
            mock = MagicMock()
            mock.fetchone.return_value = (0,)
            return mock

        conn.execute.side_effect = execute_side
        with pytest.raises(RuntimeError, match="is empty"):
            wp._populate_staging_tables(conn, {"update_ops_orders": "orders"})


class TestSetup:
    def test_raises_when_required_table_missing(self, wp):
        conn = MagicMock()
        conn.execute.side_effect = Exception("table not found")
        with pytest.raises(RuntimeError, match="Required TPC-H table"):
            wp.setup(conn)

    def test_raises_when_lock_not_acquired(self, wp):
        conn = MagicMock()
        call_count = {"n": 0}

        def execute_side(sql):
            call_count["n"] += 1
            if call_count["n"] <= 2:
                return MagicMock()
            raise Exception("permission denied")

        conn.execute.side_effect = execute_side
        with pytest.raises(RuntimeError, match="Could not acquire setup lock"):
            wp.setup(conn)

    def test_happy_path_returns_success_dict(self, wp):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = (5,)

        with (
            patch.object(wp, "_acquire_setup_lock", return_value=True),
            patch.object(wp, "_release_setup_lock"),
            patch.object(wp, "_table_exists", return_value=False),
            patch.object(wp, "_populate_staging_tables", return_value={}),
        ):
            result = wp.setup(conn)

        assert result["success"] is True
        assert "tables_created" in result

    def test_force_drops_existing_tables(self, wp):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = (0,)

        with (
            patch.object(wp, "_acquire_setup_lock", return_value=True),
            patch.object(wp, "_release_setup_lock"),
            patch.object(wp, "_table_exists", return_value=True),
            patch.object(wp, "_populate_staging_tables", return_value={}),
        ):
            result = wp.setup(conn, force=True)

        assert result["success"] is True
        drop_calls = [c for c in conn.execute.call_args_list if "DROP TABLE" in str(c)]
        assert len(drop_calls) > 0

    def test_datafusion_dialect_skips_lock_table(self, wp):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = (5,)

        with (
            patch.object(wp, "_acquire_setup_lock", return_value=True),
            patch.object(wp, "_release_setup_lock"),
            patch.object(wp, "_table_exists", return_value=True),
            patch.object(wp, "_populate_staging_tables", return_value={}),
        ):
            result = wp.setup(conn, dialect="datafusion")

        assert result["success"] is True


class TestTeardown:
    def test_drops_all_staging_tables(self, wp):
        conn = MagicMock()
        wp.teardown(conn)
        calls = [str(c) for c in conn.execute.call_args_list]
        drop_calls = [c for c in calls if "DROP TABLE" in c]
        assert len(drop_calls) > 0

    def test_handles_drop_exception_gracefully(self, wp):
        conn = MagicMock()
        conn.execute.side_effect = Exception("permission denied")
        wp.teardown(conn)


class TestCleanupAuxiliaryFiles:
    def test_removes_existing_dir(self, wp, tmp_path):
        aux_dir = tmp_path / "write_primitives_auxiliary"
        aux_dir.mkdir()
        (aux_dir / "test.csv").write_text("data")
        wp.data_generator.files_dir = aux_dir

        wp.cleanup_auxiliary_files()
        assert not aux_dir.exists()

    def test_noop_when_dir_does_not_exist(self, wp, tmp_path):
        aux_dir = tmp_path / "nonexistent_aux"
        wp.data_generator.files_dir = aux_dir
        wp.cleanup_auxiliary_files()

    def test_handles_rmtree_exception(self, wp, tmp_path):
        import shutil

        aux_dir = tmp_path / "write_primitives_auxiliary"
        aux_dir.mkdir()
        wp.data_generator.files_dir = aux_dir

        with patch.object(shutil, "rmtree", side_effect=OSError("permission denied")):
            wp.cleanup_auxiliary_files()


class TestReset:
    def test_truncates_and_repopulates(self, wp):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = (10,)

        with patch.object(wp, "_populate_staging_tables", return_value={}) as mock_pop:
            wp.reset(conn)
            mock_pop.assert_called_once()

    def test_handles_truncate_exception(self, wp):
        conn = MagicMock()
        conn.execute.side_effect = Exception("truncate not supported")

        with patch.object(wp, "_populate_staging_tables", return_value={}):
            wp.reset(conn)


class TestIsSetup:
    def test_returns_true_when_all_tables_populated(self, wp):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = (5,)
        assert wp.is_setup(conn) is True

    def test_returns_false_when_table_empty(self, wp):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = (0,)
        assert wp.is_setup(conn) is False

    def test_returns_false_on_exception(self, wp):
        conn = MagicMock()
        conn.execute.side_effect = Exception("table not found")
        assert wp.is_setup(conn) is False

    def test_returns_false_when_fetchone_returns_none(self, wp):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None
        assert wp.is_setup(conn) is False


class TestGetSchema:
    def test_returns_dict_with_tables(self, wp):
        schema = wp.get_schema()
        assert isinstance(schema, dict)
        assert len(schema) > 0

    def test_each_table_has_columns(self, wp):
        schema = wp.get_schema()
        for table_name, table_def in schema.items():
            assert "columns" in table_def, f"{table_name} missing 'columns'"

    def test_dialect_parameter_accepted(self, wp):
        schema = wp.get_schema(dialect="snowflake")
        assert isinstance(schema, dict)


class TestGetCreateTablesSql:
    def test_returns_nonempty_string(self, wp):
        sql = wp.get_create_tables_sql()
        assert isinstance(sql, str)
        assert len(sql) > 0

    def test_includes_create_table_statements(self, wp):
        sql = wp.get_create_tables_sql()
        assert "CREATE TABLE" in sql.upper()

    def test_accepts_dialect(self, wp):
        sql = wp.get_create_tables_sql(dialect="snowflake")
        assert "CREATE TABLE" in sql.upper()


class TestGetBenchmarkInfo:
    def test_returns_expected_keys(self, wp):
        info = wp.get_benchmark_info()
        assert "name" in info
        assert "version" in info
        assert "description" in info
        assert "scale_factor" in info
        assert "total_operations" in info
        assert "categories" in info
        assert "tables" in info
        assert info["data_source"] == "tpch"

    def test_total_operations_positive(self, wp):
        info = wp.get_benchmark_info()
        assert info["total_operations"] > 0


class TestQueryAccessors:
    def test_get_query_returns_sql_string(self, wp):
        op_id = next(iter(wp.get_all_operations()))
        sql = wp.get_query(op_id)
        assert isinstance(sql, str)
        assert len(sql) > 0

    def test_get_queries_returns_all(self, wp):
        queries = wp.get_queries()
        ops = wp.get_all_operations()
        sql_ops = {op_id: op for op_id, op in ops.items() if op.aggregate_state is None}
        assert len(queries) == len(sql_ops)
        assert queries.keys() == sql_ops.keys()
        assert "sketch_df_hll_persist_merge" not in queries
        assert "sketch_df_topk_persist_merge" not in queries

    def test_get_query_rejects_dataframe_only_aggregate_state_op(self, wp):
        with pytest.raises(ValueError, match="DataFrame aggregate-state only"):
            wp.get_query("sketch_df_hll_persist_merge")

    def test_get_queries_by_category_returns_subset(self, wp):
        categories = wp.get_operation_categories()
        if categories:
            cat = categories[0]
            result = wp.get_queries_by_category(cat)
            assert isinstance(result, dict)
            assert len(result) > 0


class TestExecuteOperation:
    def test_raises_on_none_connection(self, wp):
        with pytest.raises(ValueError, match="Connection is None"):
            wp.execute_operation("OP_1", None)

    def test_raises_on_invalid_connection_type(self, wp):
        with pytest.raises(ValueError, match="Invalid connection type"):
            wp.execute_operation("OP_1", "not_a_connection")

    def test_success_path_with_mock_operation(self, wp):
        conn = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 10
        mock_result.fetchall.return_value = [("row1",)]
        conn.execute.return_value = mock_result

        op = _make_operation(
            id="INSERT_001",
            write_sql="INSERT INTO t VALUES (1)",
            validation_queries=[
                _make_val_query(expected_rows=1),
            ],
        )

        with patch.object(wp.operations_manager, "get_operation", return_value=op):
            result = wp.execute_operation("INSERT_001", conn)

        assert result.operation_id == "INSERT_001"
        assert result.success is True
        assert result.rows_affected == 10
        assert result.write_duration_ms >= 0

    def test_skipped_operation_when_none_sql(self, wp):
        conn = MagicMock()
        op = _make_operation(
            id="SKIP_OP",
            platform_overrides={"datafusion": None},
            requires_setup=False,
        )

        with patch.object(wp.operations_manager, "get_operation", return_value=op):
            result = wp.execute_operation("SKIP_OP", conn, platform_key="datafusion")

        assert result.status == "SKIPPED"
        assert result.success is True

    def test_execute_raises_returns_failed_result(self, wp):
        conn = MagicMock()
        conn.execute.side_effect = Exception("SQL error")

        op = _make_operation(id="FAIL_OP", requires_setup=False)
        with patch.object(wp.operations_manager, "get_operation", return_value=op):
            result = wp.execute_operation("FAIL_OP", conn)

        assert result.success is False
        assert result.status == "FAILED"
        assert "FAIL_OP" in result.error

    def test_auto_setup_when_required(self, wp):
        conn = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_result.fetchall.return_value = []
        conn.execute.return_value = mock_result

        op = _make_operation(id="SETUP_OP", requires_setup=True)

        with (
            patch.object(wp.operations_manager, "get_operation", return_value=op),
            patch.object(wp, "is_setup", return_value=False),
            patch.object(wp, "setup", return_value={"success": True}) as mock_setup,
        ):
            result = wp.execute_operation("SETUP_OP", conn)

        mock_setup.assert_called_once()
        assert result.success is True

    def test_auto_setup_failure_raises(self, wp):
        conn = MagicMock()
        op = _make_operation(id="SETUP_FAIL", requires_setup=True)

        with (
            patch.object(wp.operations_manager, "get_operation", return_value=op),
            patch.object(wp, "is_setup", return_value=False),
            patch.object(wp, "setup", side_effect=RuntimeError("no TPC-H data")),
        ):
            with pytest.raises(RuntimeError, match="Failed to initialize staging tables"):
                wp.execute_operation("SETUP_FAIL", conn)

    def test_cleanup_failure_captured_in_result(self, wp):
        conn = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 5
        mock_result.fetchall.return_value = []
        conn.execute.return_value = mock_result

        op = _make_operation(
            id="CLEANUP_FAIL_OP",
            cleanup_sql="DELETE FROM t",
            requires_setup=False,
        )

        def execute_side(sql):
            if sql == "DELETE FROM t":
                raise Exception("cleanup error")
            return mock_result

        conn.execute.side_effect = execute_side

        with patch.object(wp.operations_manager, "get_operation", return_value=op):
            result = wp.execute_operation("CLEANUP_FAIL_OP", conn)

        assert result.success is True
        assert result.cleanup_success is False
        assert result.cleanup_warning is not None


class TestRunBenchmark:
    def test_runs_all_operations_by_default(self, wp):
        conn = MagicMock()

        fake_result = OperationResult(
            operation_id="OP_1",
            success=True,
            write_duration_ms=1.0,
            rows_affected=1,
            validation_duration_ms=0.5,
            validation_passed=True,
            validation_results=[],
            cleanup_duration_ms=0.1,
            cleanup_success=True,
        )

        with patch.object(wp, "execute_operation", return_value=fake_result) as mock_exec:
            results = wp.run_benchmark(conn)

        assert len(results) == len(wp.get_all_operations())
        assert mock_exec.call_count == len(wp.get_all_operations())

    def test_runs_specific_operation_ids(self, wp):
        conn = MagicMock()
        op_ids = list(wp.get_all_operations().keys())[:2]

        fake_result = OperationResult(
            operation_id="OP",
            success=True,
            write_duration_ms=1.0,
            rows_affected=1,
            validation_duration_ms=0.5,
            validation_passed=True,
            validation_results=[],
            cleanup_duration_ms=0.1,
            cleanup_success=True,
        )

        with patch.object(wp, "execute_operation", return_value=fake_result) as mock_exec:
            results = wp.run_benchmark(conn, operation_ids=op_ids)

        assert len(results) == 2
        assert mock_exec.call_count == 2

    def test_runs_by_category(self, wp):
        conn = MagicMock()
        categories = wp.get_operation_categories()[:1]

        fake_result = OperationResult(
            operation_id="OP",
            success=True,
            write_duration_ms=1.0,
            rows_affected=1,
            validation_duration_ms=0.5,
            validation_passed=True,
            validation_results=[],
            cleanup_duration_ms=0.1,
            cleanup_success=True,
        )

        with patch.object(wp, "execute_operation", return_value=fake_result):
            results = wp.run_benchmark(conn, categories=categories)

        assert len(results) > 0


class TestDataFrameSupport:
    def test_supports_dataframe_mode(self, wp):
        assert wp.supports_dataframe_mode() is True

    def test_skip_dataframe_data_loading(self, wp):
        assert wp.skip_dataframe_data_loading() is True

    def test_get_dataframe_operations_unknown_platform(self, wp):
        result = wp.get_dataframe_operations("unknown-platform")
        assert result is None

    def test_get_dataframe_capabilities_none_for_unknown_platform(self, wp):
        result = wp.get_dataframe_capabilities("unknown-platform")
        assert result is None


class TestExecuteDataframeWorkload:
    def test_returns_list_with_error_on_exception(self, wp):
        benchmark_config = SimpleNamespace(options={})

        with patch.object(
            wp,
            "_execute_dataframe_sql_parity_workload",
            side_effect=RuntimeError("DuckDB not available"),
        ):
            results = wp.execute_dataframe_workload(
                ctx=None,
                adapter=None,
                benchmark_config=benchmark_config,
            )

        assert isinstance(results, list)
        assert len(results) == 1
        assert results[0]["status"] == "FAILED"
        assert "DuckDB not available" in results[0]["error"]

    def test_measurement_iterations_respected(self, wp):
        benchmark_config = SimpleNamespace(options={"power_iterations": 2, "power_warmup_iterations": 1})
        fake_rows = [{"query_id": "OP_1", "status": "SUCCESS", "execution_time_seconds": 0.1, "rows_returned": 5}]

        with patch.object(wp, "_execute_dataframe_sql_parity_workload", return_value=fake_rows) as mock_exec:
            results = wp.execute_dataframe_workload(
                ctx=None,
                adapter=None,
                benchmark_config=benchmark_config,
            )

        assert mock_exec.call_count == 3
        measurement_rows = [r for r in results if r.get("run_type") == "measurement"]
        assert len(measurement_rows) == 2

    def test_warmup_iteration_included(self, wp):
        benchmark_config = SimpleNamespace(options={"power_warmup_iterations": 1, "power_iterations": 1})
        fake_rows = [{"query_id": "OP_1", "status": "SUCCESS", "execution_time_seconds": 0.1, "rows_returned": 5}]

        with patch.object(wp, "_execute_dataframe_sql_parity_workload", return_value=fake_rows):
            results = wp.execute_dataframe_workload(
                ctx=None,
                adapter=None,
                benchmark_config=benchmark_config,
            )

        warmup_rows = [r for r in results if r.get("run_type") == "warmup"]
        assert len(warmup_rows) == 1


class TestSelectDataframeOperationIds:
    def test_returns_all_when_no_filter(self, wp):
        ids = wp._select_dataframe_operation_ids(query_filter=None)
        assert len(ids) == len(wp.operations_manager.get_all_operations())

    def test_filters_by_op_id(self, wp):
        all_ids = list(wp.get_all_operations().keys())
        first_id = all_ids[0]
        ids = wp._select_dataframe_operation_ids(query_filter={first_id})
        assert first_id in ids
        assert len(ids) == 1

    def test_filters_by_q_prefixed_id(self, wp):
        all_ids = list(wp.get_all_operations().keys())
        first_id = all_ids[0]
        ids = wp._select_dataframe_operation_ids(query_filter={f"Q{first_id}"})
        assert first_id in ids

    def test_empty_filter_returns_all(self, wp):
        ids = wp._select_dataframe_operation_ids(query_filter=set())
        assert len(ids) == len(wp.operations_manager.get_all_operations())


def test_questdb_staging_tables_use_parameterless_types():
    from benchbox.core.write_primitives.schema import STAGING_TABLES, get_create_table_sql

    for table_name in STAGING_TABLES:
        sql = get_create_table_sql(table_name, dialect="questdb", if_not_exists=True)
        assert "VARCHAR(" not in sql, table_name
        assert "DECIMAL" not in sql, table_name

    assert "DECIMAL(15,2)" in get_create_table_sql("insert_ops_orders", dialect="postgres")
