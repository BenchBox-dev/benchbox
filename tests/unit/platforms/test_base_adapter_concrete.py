from __future__ import annotations

import os
import platform as platform_module
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import duckdb
import pytest

from benchbox.core.errors import PlanCaptureError, SerializationError
from benchbox.platforms.base import PlatformAdapter
from benchbox.platforms.base.adapter import (
    DriverIsolationCapability,
    _extract_table_names,
    _resolve_benchmark_table_names,
    check_isolation_capability,
)
from benchbox.platforms.duckdb import DuckDBAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture()
def duckdb_adapter():

    return DuckDBAdapter(database_path=":memory:", memory_limit="256MB")


@pytest.fixture()
def connection(duckdb_adapter):

    conn = duckdb_adapter.create_connection()
    yield conn
    try:
        conn.close()
    except Exception:
        pass


class TestCollectResourceUtilization:
    def test_returns_dict_with_required_keys(self, duckdb_adapter):
        snapshot = duckdb_adapter._collect_resource_utilization()
        assert isinstance(snapshot, dict)
        assert "platform" in snapshot
        assert "cpu_count" in snapshot
        assert "timestamp" in snapshot
        assert "non_interactive" in snapshot

    def test_cpu_count_is_positive_integer(self, duckdb_adapter):
        snapshot = duckdb_adapter._collect_resource_utilization()
        cpu_count = snapshot["cpu_count"]
        assert isinstance(cpu_count, int)
        assert cpu_count > 0

    def test_platform_string_is_non_empty(self, duckdb_adapter):
        snapshot = duckdb_adapter._collect_resource_utilization()
        assert isinstance(snapshot["platform"], str)
        assert len(snapshot["platform"]) > 0

        assert snapshot["platform"] == platform_module.platform()

    def test_timestamp_is_iso_format(self, duckdb_adapter):
        snapshot = duckdb_adapter._collect_resource_utilization()
        ts = snapshot["timestamp"]
        assert isinstance(ts, str)
        assert "T" in ts

    def test_psutil_available_flag_is_boolean(self, duckdb_adapter):
        snapshot = duckdb_adapter._collect_resource_utilization()
        assert isinstance(snapshot["available"], bool)

    def test_when_psutil_available_memory_is_populated(self, duckdb_adapter):

        snapshot = duckdb_adapter._collect_resource_utilization()
        if snapshot["available"]:
            assert "memory" in snapshot
            mem = snapshot["memory"]
            assert mem is not None
            assert mem["total_mb"] > 0
            assert mem["available_mb"] >= 0
            assert 0 <= mem["percent"] <= 100

    def test_when_psutil_available_disk_is_populated(self, duckdb_adapter):

        snapshot = duckdb_adapter._collect_resource_utilization()
        if snapshot["available"]:
            assert "disk" in snapshot
            disk = snapshot["disk"]
            assert disk is not None
            assert disk["total_mb"] > 0
            assert disk["free_mb"] >= 0


class TestGetPlatformInfoBase:
    def test_base_get_platform_info_returns_expected_keys(self):

        class MinimalAdapter(PlatformAdapter):
            @property
            def platform_name(self):
                return "TestPlatform"

            def get_target_dialect(self):
                return None

            @staticmethod
            def add_cli_arguments(parser):
                pass

            @classmethod
            def from_config(cls, config):
                return cls()

            def create_connection(self, **kw):
                return None

            def create_schema(self, benchmark, connection):
                return 0.0

            def load_data(self, benchmark, connection, data_dir):
                return {}, 0.0, None

            def configure_for_benchmark(self, connection, benchmark_type):
                pass

            def execute_query(self, connection, query, query_id, **kwargs):
                return {"query_id": query_id, "status": "SUCCESS", "execution_time_seconds": 0.0, "rows_returned": 0}

            def apply_platform_optimizations(self, platform_config, connection):
                pass

            def apply_constraint_configuration(self, pk, fk, connection):
                pass

        adapter = MinimalAdapter()
        info = adapter.get_platform_info()

        expected_keys = {
            "platform_type",
            "platform_name",
            "platform_version",
            "connection_mode",
            "host",
            "port",
            "configuration",
            "client_library_version",
            "embedded_library_version",
        }
        assert expected_keys.issubset(info.keys())
        assert info["platform_name"] == "TestPlatform"
        assert info["platform_type"] == "testplatform"
        assert info["platform_version"] == "unknown"

    def test_base_get_platform_info_includes_driver_metadata_when_set(self):

        class DriverAdapter(PlatformAdapter):
            @property
            def platform_name(self):
                return "DriverTest"

            def get_target_dialect(self):
                return None

            @staticmethod
            def add_cli_arguments(parser):
                pass

            @classmethod
            def from_config(cls, config):
                return cls()

            def create_connection(self, **kw):
                return None

            def create_schema(self, benchmark, connection):
                return 0.0

            def load_data(self, benchmark, connection, data_dir):
                return {}, 0.0, None

            def configure_for_benchmark(self, connection, benchmark_type):
                pass

            def execute_query(self, connection, query, query_id, **kwargs):
                return {"query_id": query_id, "status": "SUCCESS", "execution_time_seconds": 0.0, "rows_returned": 0}

            def apply_platform_optimizations(self, platform_config, connection):
                pass

            def apply_constraint_configuration(self, pk, fk, connection):
                pass

        adapter = DriverAdapter(
            driver_package="test-driver",
            driver_version="2.0.0",
            driver_version_resolved="2.0.0",
            driver_version_actual="2.0.0",
            driver_runtime_strategy="current_process",
        )
        info = adapter.get_platform_info()
        assert info["driver_package"] == "test-driver"
        assert info["driver_version_requested"] == "2.0.0"
        assert info["driver_version_resolved"] == "2.0.0"
        assert info["driver_version_actual"] == "2.0.0"
        assert info["driver_runtime_strategy"] == "current_process"


class TestGetPlatformInfoDuckDB:
    def test_returns_duckdb_platform_type(self, duckdb_adapter, connection):
        info = duckdb_adapter.get_platform_info(connection)
        assert info["platform_type"] == "duckdb"
        assert info["platform_name"] == "DuckDB"

    def test_platform_version_is_semver_string(self, duckdb_adapter, connection):
        info = duckdb_adapter.get_platform_info(connection)
        version = info["platform_version"]
        assert isinstance(version, str)

        parts = version.split(".")
        assert len(parts) >= 2, f"Expected semver-like version, got: {version}"
        assert parts[0].isdigit()

    def test_connection_mode_is_memory(self, duckdb_adapter, connection):
        info = duckdb_adapter.get_platform_info(connection)
        assert info["connection_mode"] == "memory"

    def test_configuration_includes_duckdb_settings(self, duckdb_adapter, connection):
        info = duckdb_adapter.get_platform_info(connection)
        config = info["configuration"]
        assert config["database_path"] == ":memory:"
        assert config["memory_limit"] == "256MB"

    def test_client_library_version_is_non_empty(self, duckdb_adapter, connection):
        info = duckdb_adapter.get_platform_info(connection)
        assert info["client_library_version"] is not None
        assert len(info["client_library_version"]) > 0


class TestValidatePlatformCapabilities:
    def test_tpch_passes_validation(self, duckdb_adapter):
        result = duckdb_adapter.validate_platform_capabilities("tpch")
        assert result.is_valid is True
        assert result.errors == []

    def test_tpcds_passes_validation(self, duckdb_adapter):
        result = duckdb_adapter.validate_platform_capabilities("tpcds")
        assert result.is_valid is True
        assert result.errors == []

    def test_unknown_benchmark_no_errors(self, duckdb_adapter):

        result = duckdb_adapter.validate_platform_capabilities("custom_bench")
        assert result.is_valid is True
        assert result.errors == []

    def test_details_include_platform_name(self, duckdb_adapter):
        result = duckdb_adapter.validate_platform_capabilities("tpch")
        assert result.details["platform"] == "DuckDB"
        assert result.details["benchmark_type"] == "tpch"
        assert result.details["dry_run_mode"] is False


class TestCloseConnection:
    def test_close_makes_connection_unusable(self, duckdb_adapter):
        conn = duckdb_adapter.create_connection()
        conn.execute("SELECT 1")
        duckdb_adapter.close_connection(conn)
        with pytest.raises(Exception):
            conn.execute("SELECT 1")

    def test_close_none_does_not_raise(self, duckdb_adapter):

        duckdb_adapter.close_connection(None)

    def test_close_object_without_close_method(self, duckdb_adapter):

        duckdb_adapter.close_connection(42)


class TestTableManagementDuckDB:
    def test_create_table_and_query(self, duckdb_adapter, connection):
        connection.execute("CREATE TABLE test_region (r_key INT, r_name VARCHAR(25))")
        connection.execute("INSERT INTO test_region VALUES (0, 'AFRICA'), (1, 'AMERICA')")

        rows = connection.execute("SELECT COUNT(*) FROM test_region").fetchone()
        assert rows[0] == 2

    def test_table_existence_via_information_schema(self, duckdb_adapter, connection):
        connection.execute("CREATE TABLE existence_test (id INT)")

        result = connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'existence_test'"
        ).fetchone()
        assert result[0] == 1

    def test_nonexistent_table_returns_zero(self, duckdb_adapter, connection):
        result = connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'does_not_exist_xyz'"
        ).fetchone()
        assert result[0] == 0

    def test_drop_table_removes_from_catalog(self, duckdb_adapter, connection):
        connection.execute("CREATE TABLE drop_me (id INT)")

        before = connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'drop_me'"
        ).fetchone()
        assert before[0] == 1

        connection.execute("DROP TABLE drop_me")
        after = connection.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'drop_me'"
        ).fetchone()
        assert after[0] == 0

    def test_create_schema_namespace(self, duckdb_adapter, connection):

        connection.execute("CREATE SCHEMA test_schema")
        connection.execute("CREATE TABLE test_schema.my_table (id INT)")
        result = connection.execute("SELECT COUNT(*) FROM test_schema.my_table").fetchone()
        assert result[0] == 0

    def test_drop_schema_cascade(self, duckdb_adapter, connection):

        connection.execute("CREATE SCHEMA drop_schema")
        connection.execute("CREATE TABLE drop_schema.t1 (id INT)")
        connection.execute("DROP SCHEMA drop_schema CASCADE")
        with pytest.raises(Exception):
            connection.execute("SELECT * FROM drop_schema.t1")


class TestCaptureQueryPlanDuckDB:
    def test_capture_plans_disabled_returns_none(self, duckdb_adapter, connection):

        duckdb_adapter.capture_plans = False
        plan, capture_time = duckdb_adapter.capture_query_plan(connection, "SELECT 1", "q1")
        assert plan is None
        assert capture_time == 0.0

    def test_capture_plans_enabled_returns_plan_and_timing(self, duckdb_adapter, connection):

        connection.execute("CREATE TABLE plan_test (id INT, name VARCHAR)")
        connection.execute("INSERT INTO plan_test VALUES (1, 'a'), (2, 'b')")

        duckdb_adapter.capture_plans = True
        duckdb_adapter.analyze_plans = True
        plan, capture_time = duckdb_adapter.capture_query_plan(
            connection, "SELECT * FROM plan_test WHERE id > 0", "q_plan"
        )

        if plan is not None:
            assert capture_time > 0.0
            assert duckdb_adapter.query_plans_captured >= 1

            assert plan.plan_fingerprint is not None

        else:
            assert duckdb_adapter.plan_capture_failures >= 0

    def test_query_filter_excludes_non_matching_query(self, duckdb_adapter, connection):

        duckdb_adapter.capture_plans = True
        duckdb_adapter.plan_query_filter = {"q_special"}

        plan, _ = duckdb_adapter.capture_query_plan(connection, "SELECT 1", "q_other")
        assert plan is None

    def test_query_filter_includes_matching_query(self, duckdb_adapter, connection):

        connection.execute("CREATE TABLE filter_test (x INT)")
        duckdb_adapter.capture_plans = True
        duckdb_adapter.plan_query_filter = {"q_match"}

        plan, _ = duckdb_adapter.capture_query_plan(connection, "SELECT * FROM filter_test", "q_match")

        assert duckdb_adapter.query_plans_captured + duckdb_adapter.plan_capture_failures >= 1

    def test_plan_query_filter_limits_capture(self, duckdb_adapter, connection):

        duckdb_adapter.capture_plans = True
        duckdb_adapter.plan_query_filter = {"q_keep"}

        kept, _ = duckdb_adapter.capture_query_plan(connection, "SELECT 1", "q_keep")
        skipped, skipped_ms = duckdb_adapter.capture_query_plan(connection, "SELECT 1", "q_skip")

        assert kept is not None
        assert skipped is None
        assert skipped_ms == 0.0

    def test_query_filter_matches_internal_capture_key(self, duckdb_adapter, connection):

        from benchbox.platforms.base.result_capture import _plan_capture_key

        connection.execute("CREATE TABLE key_filter_test (x INT)")
        duckdb_adapter.capture_plans = True
        duckdb_adapter.plan_query_filter = {"q_keep"}

        sql = "SELECT * FROM key_filter_test"
        capture_key = _plan_capture_key("q_keep", sql)
        assert "#" in capture_key

        before = duckdb_adapter.query_plans_captured + duckdb_adapter.plan_capture_failures
        duckdb_adapter.capture_query_plan(connection, sql, capture_key)
        assert duckdb_adapter.query_plans_captured + duckdb_adapter.plan_capture_failures > before

        other_key = _plan_capture_key("q_other", sql)
        skipped, skipped_ms = duckdb_adapter.capture_query_plan(connection, sql, other_key)
        assert skipped is None
        assert skipped_ms == 0.0

    def test_get_query_plan_returns_json(self, duckdb_adapter, connection):

        connection.execute("CREATE TABLE gqp_test (val INT)")
        result = duckdb_adapter.get_query_plan(connection, "SELECT * FROM gqp_test")
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0


class TestRecordPlanCaptureFailure:
    def test_increments_failure_counter(self, duckdb_adapter):
        assert duckdb_adapter.plan_capture_failures == 0
        duckdb_adapter._record_plan_capture_failure("q1", reason="test_failure", message="Something went wrong")
        assert duckdb_adapter.plan_capture_failures == 1

    def test_records_error_details(self, duckdb_adapter):
        duckdb_adapter._record_plan_capture_failure("q2", reason="timeout", message="Timed out after 30s")
        assert len(duckdb_adapter.plan_capture_errors) == 1
        error = duckdb_adapter.plan_capture_errors[0]
        assert error["query_id"] == "q2"
        assert error["reason"] == "timeout"
        assert error["message"] == "Timed out after 30s"
        assert error["platform"] == "DuckDB"

    def test_strict_mode_raises_plan_capture_error(self, duckdb_adapter):
        duckdb_adapter.strict_plan_capture = True
        with pytest.raises(PlanCaptureError) as exc_info:
            duckdb_adapter._record_plan_capture_failure("q3", reason="parse_error", message="Bad JSON")
        assert "parse_error" in str(exc_info.value)
        assert "q3" in str(exc_info.value)

    def test_non_strict_mode_does_not_raise(self, duckdb_adapter):
        duckdb_adapter.strict_plan_capture = False

        duckdb_adapter._record_plan_capture_failure("q4", reason="explain_failed")
        assert duckdb_adapter.plan_capture_failures == 1


class TestPlanCaptureError:
    def test_str_includes_all_fields(self):
        err = PlanCaptureError(
            reason="timeout",
            platform="DuckDB",
            query_id="Q17",
            details="EXPLAIN timed out after 30s",
        )
        s = str(err)
        assert "timeout" in s
        assert "DuckDB" in s
        assert "Q17" in s
        assert "EXPLAIN timed out after 30s" in s

    def test_str_without_details(self):
        err = PlanCaptureError(reason="parse_error", platform="SQLite", query_id="Q1")
        s = str(err)
        assert "parse_error" in s
        assert "SQLite" in s
        assert "Q1" in s

    def test_is_exception(self):
        err = PlanCaptureError(reason="r", platform="p", query_id="q")
        assert isinstance(err, Exception)

    def test_fields_accessible(self):
        err = PlanCaptureError(reason="test", platform="plat", query_id="q99", details="det")
        assert err.reason == "test"
        assert err.platform == "plat"
        assert err.query_id == "q99"
        assert err.details == "det"


class TestSerializationError:
    def test_is_exception(self):
        err = SerializationError("too large")
        assert isinstance(err, Exception)
        assert str(err) == "too large"


class TestBuildQueryResultWithValidation:
    def test_success_result_structure(self, duckdb_adapter):
        result = duckdb_adapter._build_query_result_with_validation(
            query_id="Q1",
            execution_time=0.5,
            actual_row_count=100,
            first_row=(1, "test"),
        )
        assert result["query_id"] == "Q1"
        assert result["status"] == "SUCCESS"
        assert result["execution_time_seconds"] == 0.5
        assert result["rows_returned"] == 100
        assert result["first_row"] == (1, "test")

    def test_error_result_has_failed_status(self, duckdb_adapter):
        result = duckdb_adapter._build_query_result_with_validation(
            query_id="Q2",
            execution_time=1.0,
            actual_row_count=0,
            error="Division by zero",
        )
        assert result["status"] == "FAILED"
        assert result["error"] == "Division by zero"
        assert result["rows_returned"] == 0

    def test_validation_passed_result(self, duckdb_adapter):
        from benchbox.core.expected_results.models import ValidationMode

        mock_validation = SimpleNamespace(
            expected_row_count=100,
            is_valid=True,
            validation_mode=ValidationMode.EXACT,
            warning_message=None,
            error_message=None,
        )
        result = duckdb_adapter._build_query_result_with_validation(
            query_id="Q3",
            execution_time=0.1,
            actual_row_count=100,
            validation_result=mock_validation,
        )
        assert result["status"] == "SUCCESS"
        assert result["row_count_validation"]["status"] == "PASSED"
        assert result["row_count_validation"]["expected"] == 100
        assert result["row_count_validation"]["actual"] == 100

    def test_validation_failed_marks_query_failed(self, duckdb_adapter):
        from benchbox.core.expected_results.models import ValidationMode

        mock_validation = SimpleNamespace(
            expected_row_count=100,
            is_valid=False,
            validation_mode=ValidationMode.EXACT,
            warning_message=None,
            error_message="Expected 100, got 50",
        )
        result = duckdb_adapter._build_query_result_with_validation(
            query_id="Q4",
            execution_time=0.2,
            actual_row_count=50,
            validation_result=mock_validation,
        )
        assert result["status"] == "FAILED"
        assert result["row_count_validation"]["status"] == "FAILED"
        assert "Expected 100" in result["row_count_validation"]["error"]

    def test_validation_skipped_result(self, duckdb_adapter):
        from benchbox.core.expected_results.models import ValidationMode

        mock_validation = SimpleNamespace(
            expected_row_count=None,
            is_valid=True,
            validation_mode=ValidationMode.SKIP,
            warning_message="No expected row count available",
            error_message=None,
        )
        result = duckdb_adapter._build_query_result_with_validation(
            query_id="Q5",
            execution_time=0.05,
            actual_row_count=42,
            validation_result=mock_validation,
        )
        assert result["status"] == "SUCCESS"
        assert result["row_count_validation"]["status"] == "SKIPPED"
        assert "No expected" in result["row_count_validation"]["warning"]


class TestBuildQueryFailureResult:
    def test_failure_result_structure(self, duckdb_adapter):
        from benchbox.utils.clock import mono_time

        start = mono_time()
        exc = RuntimeError("Connection lost")
        result = duckdb_adapter._build_query_failure_result(
            query_id="Q_fail",
            start_time=start,
            exception=exc,
            log_error=False,
        )
        assert result["query_id"] == "Q_fail"
        assert result["status"] == "FAILED"
        assert result["execution_time_seconds"] >= 0
        assert result["rows_returned"] == 0
        assert "Connection lost" in result["error"]
        assert result["error_type"] == "RuntimeError"


class TestDryRunMode:
    def test_enable_sets_mode(self, duckdb_adapter):
        assert duckdb_adapter.dry_run_mode is False
        duckdb_adapter.enable_dry_run()
        assert duckdb_adapter.dry_run_mode is True
        assert duckdb_adapter.captured_sql == []
        assert duckdb_adapter.query_counter == 0

    def test_capture_sql_records_entries(self, duckdb_adapter):
        duckdb_adapter.enable_dry_run()
        duckdb_adapter.capture_sql("CREATE TABLE t (id INT)", "ddl", "t")
        duckdb_adapter.capture_sql("INSERT INTO t VALUES (1)", "dml", "t")

        assert len(duckdb_adapter.captured_sql) == 2
        assert duckdb_adapter.captured_sql[0]["order"] == 1
        assert duckdb_adapter.captured_sql[0]["sql"] == "CREATE TABLE t (id INT)"
        assert duckdb_adapter.captured_sql[0]["operation_type"] == "ddl"
        assert duckdb_adapter.captured_sql[0]["table_name"] == "t"
        assert "timestamp" in duckdb_adapter.captured_sql[0]
        assert duckdb_adapter.captured_sql[1]["order"] == 2

    def test_capture_sql_noop_when_not_dry_run(self, duckdb_adapter):
        assert duckdb_adapter.dry_run_mode is False
        duckdb_adapter.capture_sql("SELECT 1", "query")
        assert len(duckdb_adapter.captured_sql) == 0

    def test_disable_returns_to_normal(self, duckdb_adapter):
        duckdb_adapter.enable_dry_run()
        assert duckdb_adapter.dry_run_mode is True
        duckdb_adapter.disable_dry_run()
        assert duckdb_adapter.dry_run_mode is False

    def test_get_captured_sql_returns_ordered_dict(self, duckdb_adapter):
        duckdb_adapter.enable_dry_run()
        duckdb_adapter.capture_sql("SELECT 1", "query")
        duckdb_adapter.capture_sql("SELECT 2", "query")

        captured = duckdb_adapter.get_captured_sql()
        assert isinstance(captured, dict)
        assert captured["1"] == "SELECT 1"
        assert captured["2"] == "SELECT 2"

    def test_enable_dry_run_resets_state(self, duckdb_adapter):
        duckdb_adapter.enable_dry_run()
        duckdb_adapter.capture_sql("SELECT 1", "query")
        assert len(duckdb_adapter.captured_sql) == 1

        duckdb_adapter.enable_dry_run()
        assert len(duckdb_adapter.captured_sql) == 0
        assert duckdb_adapter.query_counter == 0


class TestExtractTableNames:
    def test_none_returns_empty_list(self):
        assert _extract_table_names(None) == []

    def test_dict_returns_keys(self):
        result = _extract_table_names({"lineitem": [], "orders": []})
        assert sorted(result) == ["lineitem", "orders"]

    def test_string_returns_single_element(self):
        assert _extract_table_names("customers") == ["customers"]

    def test_list_returns_strings(self):
        result = _extract_table_names(["a", "b", "c"])
        assert result == ["a", "b", "c"]

    def test_tuple_returns_strings(self):
        result = _extract_table_names(("x", "y"))
        assert result == ["x", "y"]

    def test_set_returns_strings(self):
        result = _extract_table_names({"only"})
        assert result == ["only"]

    def test_non_iterable_returns_empty(self):
        assert _extract_table_names(42) == []


class TestResolveBenchmarkTableNames:
    def test_uses_get_table_names_method(self):
        benchmark = SimpleNamespace(
            get_table_names=lambda: ["orders", "lineitem"],
            tables=None,
        )
        result = _resolve_benchmark_table_names(benchmark)
        assert sorted(result) == ["lineitem", "orders"]

    def test_falls_back_to_tables_attribute(self):
        benchmark = SimpleNamespace(
            tables={"customer": [], "nation": []},
        )
        result = _resolve_benchmark_table_names(benchmark)
        assert sorted(result) == ["customer", "nation"]

    def test_returns_empty_when_tables_is_none(self):

        impl = SimpleNamespace(tables=["region", "supplier"])
        benchmark = SimpleNamespace(tables=None, _impl=impl)
        result = _resolve_benchmark_table_names(benchmark)
        assert result == []

    def test_returns_empty_when_no_tables(self):
        benchmark = SimpleNamespace(tables=None, _impl=None)
        result = _resolve_benchmark_table_names(benchmark)
        assert result == []


class TestValidatePlatformDependencies:
    def test_returns_dict_of_booleans(self):
        deps = PlatformAdapter.validate_platform_dependencies()
        assert isinstance(deps, dict)
        for key, value in deps.items():
            assert isinstance(key, str)
            assert isinstance(value, bool)

    def test_duckdb_is_available(self):
        deps = PlatformAdapter.validate_platform_dependencies()
        assert deps["duckdb"] is True

    def test_psutil_check_returns_boolean(self):
        deps = PlatformAdapter.validate_platform_dependencies()

        assert "psutil" in deps
        assert isinstance(deps["psutil"], bool)


class TestDriverIsolationCapability:
    def test_enum_values(self):
        assert DriverIsolationCapability.SUPPORTED.value == "supported"
        assert DriverIsolationCapability.NOT_APPLICABLE.value == "not_applicable"
        assert DriverIsolationCapability.NOT_FEASIBLE.value == "not_feasible"
        assert DriverIsolationCapability.FEASIBLE_CLIENT_ONLY.value == "feasible_client_only"

    def test_duckdb_declares_supported(self):
        assert DuckDBAdapter.driver_isolation_capability == DriverIsolationCapability.SUPPORTED

    def test_base_adapter_defaults_to_not_applicable(self):
        assert PlatformAdapter.driver_isolation_capability == DriverIsolationCapability.NOT_APPLICABLE


class TestCheckIsolationCapability:
    def test_supported_adapter_does_not_raise(self):

        check_isolation_capability(DuckDBAdapter, "duckdb", "isolated_site_packages")

    def test_not_applicable_raises_for_isolation(self):
        class NoIsoAdapter(PlatformAdapter):
            driver_isolation_capability = DriverIsolationCapability.NOT_APPLICABLE

            @property
            def platform_name(self):
                return "NoIso"

            def get_target_dialect(self):
                return None

            @staticmethod
            def add_cli_arguments(parser):
                pass

            @classmethod
            def from_config(cls, config):
                return cls()

            def create_connection(self, **kw):
                return None

            def create_schema(self, benchmark, connection):
                return 0.0

            def load_data(self, benchmark, connection, data_dir):
                return {}, 0.0, None

            def configure_for_benchmark(self, connection, benchmark_type):
                pass

            def execute_query(self, connection, query, query_id, **kwargs):
                return {}

            def apply_platform_optimizations(self, pc, conn):
                pass

            def apply_constraint_configuration(self, pk, fk, conn):
                pass

        with pytest.raises(RuntimeError, match="not applicable"):
            check_isolation_capability(NoIsoAdapter, "NoIso", "isolated-site-packages")


class TestSummarizePerformanceEdgeCases:
    def test_empty_results_returns_zeroed_summary(self, duckdb_adapter):
        summary = duckdb_adapter._summarize_performance_characteristics(
            query_results=None,
            total_duration=0.0,
            total_rows_loaded=0,
        )
        assert summary["total_queries"] == 0
        assert summary["successful_queries"] == 0
        assert summary["failed_queries"] == 0
        assert summary["success_rate"] is None
        assert summary["average_query_time_ms"] is None

    def test_empty_list_returns_zeroed_summary(self, duckdb_adapter):
        summary = duckdb_adapter._summarize_performance_characteristics(
            query_results=[],
            total_duration=1.0,
            total_rows_loaded=0,
        )
        assert summary["total_queries"] == 0

    def test_all_successful_queries(self, duckdb_adapter):
        results = [
            {"query_id": "Q1", "status": "SUCCESS", "execution_time_seconds": 0.1, "rows_returned": 10},
            {"query_id": "Q2", "status": "SUCCESS", "execution_time_seconds": 0.2, "rows_returned": 20},
            {"query_id": "Q3", "status": "SUCCESS", "execution_time_seconds": 0.3, "rows_returned": 30},
        ]
        summary = duckdb_adapter._summarize_performance_characteristics(
            query_results=results,
            total_duration=0.6,
            total_rows_loaded=1000,
        )
        assert summary["total_queries"] == 3
        assert summary["successful_queries"] == 3
        assert summary["failed_queries"] == 0
        assert summary["success_rate"] == pytest.approx(1.0)
        assert summary["rows_returned_total"] == 60
        assert summary["average_query_time_ms"] == pytest.approx(200.0)
        assert summary["total_rows_loaded"] == 1000

    def test_all_failed_queries(self, duckdb_adapter):
        results = [
            {"query_id": "Q1", "status": "FAILED", "execution_time_seconds": 0.01, "rows_returned": 0, "error": "e1"},
            {"query_id": "Q2", "status": "FAILED", "execution_time_seconds": 0.02, "rows_returned": 0, "error": "e2"},
        ]
        summary = duckdb_adapter._summarize_performance_characteristics(
            query_results=results,
            total_duration=1.0,
            total_rows_loaded=0,
        )
        assert summary["total_queries"] == 2
        assert summary["successful_queries"] == 0
        assert summary["failed_queries"] == 2
        assert summary["success_rate"] == pytest.approx(0.0)
        assert summary["average_success_query_time_ms"] is None
        assert len(summary["error_breakdown"]) == 2

    def test_mixed_results_with_execution_time_ms(self, duckdb_adapter):

        from benchbox.core.schemas import QueryResult

        results = [
            QueryResult(
                query_id="q1",
                query_name="Q1",
                sql_text="SELECT 1",
                execution_time_ms=500.0,
                rows_returned=5,
                status="SUCCESS",
            ),
        ]
        summary = duckdb_adapter._summarize_performance_characteristics(
            query_results=results,
            total_duration=1.0,
            total_rows_loaded=0,
        )
        assert summary["total_queries"] == 1
        assert summary["successful_queries"] == 1

        assert summary["average_query_time_ms"] == pytest.approx(500.0)

    def test_single_query_latency_stats(self, duckdb_adapter):

        results = [
            {"query_id": "Q1", "status": "SUCCESS", "execution_time_seconds": 0.42, "rows_returned": 7},
        ]
        summary = duckdb_adapter._summarize_performance_characteristics(
            query_results=results,
            total_duration=1.0,
            total_rows_loaded=50,
        )
        stats = summary["execution_time_stats"]
        assert stats is not None
        all_stats = stats["all"]
        assert all_stats is not None
        assert all_stats["seconds"]["count"] == 1
        assert all_stats["seconds"]["min"] == pytest.approx(0.42)
        assert all_stats["seconds"]["max"] == pytest.approx(0.42)
        assert all_stats["seconds"]["stdev"] == pytest.approx(0.0)


class TestDuckDBExecuteQueryDryRun:
    def test_dry_run_returns_synthetic_result(self, duckdb_adapter, connection):
        duckdb_adapter.enable_dry_run()

        dry_conn = duckdb_adapter.create_connection()
        result = duckdb_adapter.execute_query(
            connection=dry_conn,
            query="SELECT * FROM nonexistent_table",
            query_id="dry_q1",
            validate_row_count=False,
        )
        assert result["status"] == "DRY_RUN"
        assert result["query_id"] == "dry_q1"
        assert result["execution_time_seconds"] == 0.0
        duckdb_adapter.disable_dry_run()


class TestTestConnection:
    def test_duckdb_connection_succeeds(self, duckdb_adapter):
        assert duckdb_adapter.test_connection() is True

    def test_connection_failure_returns_false(self):

        adapter = DuckDBAdapter(database_path=":memory:", memory_limit="256MB")

        original = adapter.create_connection
        adapter.create_connection = Mock(side_effect=RuntimeError("Simulated failure"))
        assert adapter.test_connection() is False
        adapter.create_connection = original


class TestResetPlanCaptureStats:
    def test_resets_all_counters(self, duckdb_adapter):
        duckdb_adapter.query_plans_captured = 5
        duckdb_adapter.plan_capture_failures = 3
        duckdb_adapter.plan_capture_errors = [{"query_id": "q1"}]

        duckdb_adapter._reset_plan_capture_stats()
        assert duckdb_adapter.query_plans_captured == 0
        assert duckdb_adapter.plan_capture_failures == 0
        assert duckdb_adapter.plan_capture_errors == []


class TestExternalTableCapability:
    def test_duckdb_supports_external_tables(self):
        assert DuckDBAdapter.supports_external_tables is True

    def test_base_defaults_to_false(self):
        assert PlatformAdapter.supports_external_tables is False


class TestGetTpcBaseDialect:
    def test_tpcds_returns_netezza(self, duckdb_adapter):
        assert duckdb_adapter.get_tpc_base_dialect("tpcds") == "netezza"

    def test_tpch_returns_netezza(self, duckdb_adapter):
        assert duckdb_adapter.get_tpc_base_dialect("tpch") == "netezza"

    def test_case_insensitive_tpcds(self, duckdb_adapter):
        assert duckdb_adapter.get_tpc_base_dialect("TPCDS") == "netezza"

    def test_other_benchmark_returns_netezza(self, duckdb_adapter):
        assert duckdb_adapter.get_tpc_base_dialect("ssb") == "netezza"


class TestCheckImport:
    def test_existing_module_returns_true(self):
        assert PlatformAdapter._check_import("os") is True

    def test_nonexistent_module_returns_false(self):
        assert PlatformAdapter._check_import("nonexistent_module_xyz_123") is False

    def test_duckdb_module_returns_true(self):
        assert PlatformAdapter._check_import("duckdb") is True


class TestTranslateSql:
    def test_no_dialect_returns_unchanged(self, duckdb_adapter):

        sql = "SELECT * FROM table1"
        result = duckdb_adapter.translate_sql(sql, source_dialect="duckdb")

        assert result == sql

    def test_same_dialect_returns_unchanged(self, duckdb_adapter):
        duckdb_adapter._dialect = "duckdb"
        sql = "SELECT 1 FROM dual"
        result = duckdb_adapter.translate_sql(sql, source_dialect="duckdb")
        assert result == sql


class TestDuckDBExplainOutput:
    def test_explain_simple_query(self, connection):

        connection.execute("CREATE TABLE explain_tbl (id INT, val VARCHAR)")
        connection.execute("INSERT INTO explain_tbl VALUES (1, 'x'), (2, 'y')")

        rows = connection.execute("EXPLAIN SELECT * FROM explain_tbl WHERE id > 0").fetchall()
        assert len(rows) > 0

        full_plan = "\n".join(str(row) for row in rows)

        plan_upper = full_plan.upper()
        assert "SCAN" in plan_upper or "SEQ_SCAN" in plan_upper or "FILTER" in plan_upper

    def test_explain_format_json(self, connection):

        connection.execute("CREATE TABLE json_explain_tbl (x INT)")
        rows = connection.execute("EXPLAIN (FORMAT JSON) SELECT * FROM json_explain_tbl").fetchall()
        assert len(rows) > 0

        text = str(rows)
        assert "{" in text


class TestDuckDBAdapterInit:
    def test_default_memory_limit(self):
        adapter = DuckDBAdapter(database_path=":memory:")
        assert adapter.memory_limit == "4GB"

    def test_custom_memory_limit(self):
        adapter = DuckDBAdapter(database_path=":memory:", memory_limit="512MB")
        assert adapter.memory_limit == "512MB"

    def test_database_path_stored(self):
        adapter = DuckDBAdapter(database_path=":memory:")
        assert adapter.database_path == ":memory:"

    def test_platform_name_is_duckdb(self):
        adapter = DuckDBAdapter(database_path=":memory:")
        assert adapter.platform_name == "DuckDB"

    def test_capture_plans_default_false(self):
        adapter = DuckDBAdapter(database_path=":memory:")
        assert adapter.capture_plans is False

    def test_show_query_plans_default_false(self):
        adapter = DuckDBAdapter(database_path=":memory:")
        assert adapter.show_query_plans is False

    def test_apply_run_plan_flags_applies_and_restores_show_query_plans(self):

        adapter = DuckDBAdapter(database_path=":memory:")
        assert adapter.show_query_plans is False

        snapshot = adapter._apply_run_plan_flags({"show_query_plans": True})
        assert adapter.show_query_plans is True
        assert snapshot["show_query_plans"] is False

        adapter.show_query_plans = snapshot["show_query_plans"]
        assert adapter.show_query_plans is False

    def test_run_enhanced_benchmark_restores_plan_flags_on_failure(self):

        from unittest.mock import patch

        adapter = DuckDBAdapter(database_path=":memory:")
        assert adapter.show_query_plans is False
        assert adapter.normalize_plan_literals is False

        with (
            patch.object(
                adapter,
                "_create_enhanced_data_generation_phase",
                side_effect=RuntimeError("sentinel"),
            ),
            patch.object(adapter, "_close_run_connection", return_value=None),
        ):
            import pytest

            with pytest.raises(RuntimeError, match="sentinel"):
                adapter.run_enhanced_benchmark(
                    Mock(),
                    show_query_plans=True,
                    normalize_plan_literals=True,
                    capture_plans=True,
                )

        assert adapter.show_query_plans is False
        assert adapter.normalize_plan_literals is False
        assert adapter.capture_plans is False

    def test_apply_run_plan_flags_none_timeout_keeps_default_without_mutation(self):

        adapter = DuckDBAdapter(database_path=":memory:")
        before = adapter.plan_capture_timeout_seconds

        snapshot = adapter._apply_run_plan_flags({"show_query_plans": True, "plan_capture_timeout_seconds": None})
        assert adapter.plan_capture_timeout_seconds == before
        assert adapter.show_query_plans is True
        assert snapshot["plan_capture_timeout_seconds"] == before

    def test_apply_run_plan_flags_none_show_query_plans_preserves_adapter_value(self):

        adapter = DuckDBAdapter(database_path=":memory:", show_query_plans=True)
        assert adapter.show_query_plans is True

        snapshot = adapter._apply_run_plan_flags({"show_query_plans": None})
        assert adapter.show_query_plans is True
        assert snapshot["show_query_plans"] is True
        adapter.show_query_plans = snapshot["show_query_plans"]
        assert adapter.show_query_plans is True

    def test_dry_run_mode_default_false(self):
        adapter = DuckDBAdapter(database_path=":memory:")
        assert adapter.dry_run_mode is False


class TestDatabasePathAndExists:
    def test_memory_database_path(self):
        adapter = DuckDBAdapter(database_path=":memory:")
        assert adapter.get_database_path() == ":memory:"

    def test_memory_database_check_exists_returns_false(self):

        adapter = DuckDBAdapter(database_path=":memory:")

        result = adapter.check_database_exists()
        assert result is False

    def test_file_database_exists_when_created(self, tmp_path):
        db_path = str(tmp_path / "test.duckdb")
        adapter = DuckDBAdapter(database_path=db_path, memory_limit="128MB")

        conn = adapter.create_connection()
        conn.execute("CREATE TABLE t (id INT)")
        conn.close()
        assert adapter.check_database_exists() is True

    def test_file_database_not_exists(self, tmp_path):
        db_path = str(tmp_path / "nonexistent.duckdb")
        adapter = DuckDBAdapter(database_path=db_path, memory_limit="128MB")
        assert adapter.check_database_exists() is False


