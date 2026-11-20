import logging
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from benchbox.platforms.clickhouse import ClickHouseAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


class TestClickHouseAdapter:
    @patch("benchbox.platforms.clickhouse.setup.ClickHouseClient")
    def test_execute_query_success(self, mock_client_class):
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        mock_client.execute.return_value = [[1, "test"], [2, "test2"]]

        adapter = ClickHouseAdapter(deployment_mode="server")
        connection = adapter.create_connection()

        result = adapter.execute_query(connection, "SELECT * FROM test", "q1")

        assert result["query_id"] == "q1"
        assert result["status"] == "SUCCESS"
        assert result["rows_returned"] == 2
        assert result["first_row"] == [1, "test"]
        assert isinstance(result["execution_time_seconds"], float)


class TestClickHouseWorkloadCoverage:
    @staticmethod
    def _adapter(deployment_mode: str = "server") -> ClickHouseAdapter:
        adapter = ClickHouseAdapter.__new__(ClickHouseAdapter)
        adapter.deployment_mode = deployment_mode
        adapter.fetch_mode = "rows"
        logger = logging.getLogger(f"benchbox.tests.clickhouse.{deployment_mode}")
        logger.debug = Mock()
        adapter.logger = logger
        adapter.verbose_enabled = False
        adapter.very_verbose = False
        adapter.log_verbose = Mock()
        adapter.log_very_verbose = Mock()
        return adapter

    @pytest.mark.parametrize(
        ("deployment_mode", "query_id", "grace"),
        [
            ("server", "21", True),
            ("server", "5", True),
            ("server", "20", False),
            ("local", "21", False),
            ("local", "5", False),
        ],
    )
    def test_datavault_memory_heavy_queries_spill_join_to_disk_on_server(self, deployment_mode, query_id, grace):
        adapter = self._adapter(deployment_mode=deployment_mode)
        connection = Mock()
        connection.execute.return_value = [(1,)]

        adapter.execute_query(connection, "SELECT 1", query_id, benchmark_type="datavault", validate_row_count=False)

        statement = connection.execute.call_args_list[0].args[0]
        assert ("join_algorithm = 'grace_hash'" in statement) is grace
        assert ("grace_hash_join_initial_buckets = 8" in statement) is grace

    def test_server_tpchavoc_resource_variants_keep_session_policy_unchanged(self):
        adapter = self._adapter(deployment_mode="server")
        connection = Mock()
        connection.execute.return_value = [(1,)]

        adapter.execute_query(connection, "SELECT 1", "5_v7", benchmark_type="tpchavoc", validate_row_count=False)

        statement = connection.execute.call_args_list[0].args[0]
        assert "join_algorithm" not in statement

    @pytest.mark.parametrize("tuning_enabled", [True, False])
    @pytest.mark.parametrize("query_id", ["21", "Q21"])
    def test_server_tpch_q21_hash_join_is_a_recorded_harness_requirement_in_every_mode(self, query_id, tuning_enabled):
        from benchbox.core.tuning.applied_ledger import HARNESS_DEFAULT, AppliedTuningLedger

        adapter = self._adapter(deployment_mode="server")
        adapter.tuning_enabled = tuning_enabled
        adapter._applied_tuning_ledger = AppliedTuningLedger()
        connection = Mock()
        connection.execute.return_value = [(1,)]

        adapter.execute_query(connection, "SELECT 1", query_id, benchmark_type="tpch", validate_row_count=False)
        adapter.execute_query(connection, "SELECT 1", query_id, benchmark_type="tpch", validate_row_count=False)

        assert "join_algorithm = 'hash'" in connection.execute.call_args_list[0].args[0]
        recorded = adapter._applied_tuning_ledger.statements
        assert len(recorded) == 1
        assert recorded[0].status == HARNESS_DEFAULT
        assert recorded[0].mechanism == "harness_required"
        assert "join_algorithm = 'hash'" in recorded[0].statement

    @pytest.mark.parametrize(
        ("deployment_mode", "benchmark_type", "query_id"),
        [
            ("server", "tpch", "20"),
            ("server", "tpcds", "21"),
            ("local", "tpch", "21"),
        ],
    )
    def test_statement_level_hash_join_is_limited_to_server_tpch_q21(self, deployment_mode, benchmark_type, query_id):
        adapter = self._adapter(deployment_mode=deployment_mode)
        connection = Mock()
        connection.execute.return_value = [(1,)]

        adapter.execute_query(connection, "SELECT 1", query_id, benchmark_type=benchmark_type, validate_row_count=False)

        assert "join_algorithm" not in connection.execute.call_args_list[0].args[0]

    @pytest.mark.parametrize(
        ("mode", "unvalidated", "expected_status"),
        [("exact", False, "PASSED"), ("skip", False, "SKIPPED"), ("skip", True, "UNVALIDATED")],
    )
    def test_execute_query_adds_row_count_validation_metadata(self, mode, unvalidated, expected_status):
        adapter = self._adapter()
        adapter.verbose_enabled = True
        adapter.very_verbose = True
        connection = Mock()
        connection.execute.return_value = [(1,)]

        validation_result = SimpleNamespace(
            is_valid=True,
            warning_message="expected row count unavailable",
            error_message=None,
            expected_row_count=None,
            unvalidated=unvalidated,
            validation_mode=mode,
        )

        with patch("benchbox.core.validation.query_validation.QueryValidator") as validator_cls:
            validator_cls.return_value.validate_query_result.return_value = validation_result
            result = adapter.execute_query(
                connection,
                "SELECT * FROM orders",
                "Q1",
                benchmark_type="tpch",
                scale_factor=1.0,
            )

        assert result["status"] == "SUCCESS"
        assert result["row_count_validation"]["status"] == expected_status
        assert result["row_count_validation"]["warning"] == "expected row count unavailable"
        first_call = connection.execute.call_args_list[0]
        assert first_call.args == ("SELECT * FROM orders SETTINGS joined_subquery_requires_alias = 0",)
        assert first_call.kwargs["query_id"]

    def test_execute_query_error_capture_falls_back_when_str_is_empty(self):
        adapter = self._adapter()
        connection = Mock()
        connection.execute.side_effect = RuntimeError()

        result = adapter.execute_query(connection, "SELECT 1", "Q_empty")

        assert result["status"] == "FAILED"
        assert result["error"]
        assert result["error_type"] == "RuntimeError"
