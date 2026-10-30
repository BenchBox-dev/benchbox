from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestGenericPowerTest:
    @pytest.fixture
    def mock_adapter(self):

        from benchbox.platforms.duckdb import DuckDBAdapter

        adapter = DuckDBAdapter()

        adapter._execute_all_queries = MagicMock()
        return adapter

    @pytest.fixture
    def mock_benchmark(self):

        benchmark = MagicMock()
        benchmark.scale_factor = 1.0
        return benchmark

    @pytest.fixture
    def mock_connection(self):

        return MagicMock()

    def test_warmup_results_discarded(self, mock_adapter, mock_benchmark, mock_connection):

        run_config = {
            "benchmark_name": "clickbench",
            "iterations": 2,
            "warm_up_iterations": 1,
            "scale_factor": 1.0,
        }

        mock_adapter._execute_all_queries.side_effect = [
            [{"query_id": "Q1", "status": "SUCCESS", "execution_time": 1.0}],
            [{"query_id": "Q1", "status": "SUCCESS", "execution_time": 2.0}],
            [{"query_id": "Q1", "status": "SUCCESS", "execution_time": 3.0}],
        ]

        results = mock_adapter._execute_generic_power_test(mock_benchmark, mock_connection, run_config)

        assert mock_adapter._execute_all_queries.call_count == 3

        assert len(results) == 3

        warmup_results = [r for r in results if r.get("run_type") == "warmup"]
        measurement_results = [r for r in results if r.get("run_type") == "measurement"]

        assert len(warmup_results) == 1
        assert len(measurement_results) == 2
        assert warmup_results[0]["execution_time"] == 1.0
        assert measurement_results[0]["execution_time"] == 2.0
        assert measurement_results[1]["execution_time"] == 3.0

    def test_iteration_tagging(self, mock_adapter, mock_benchmark, mock_connection):

        run_config = {
            "benchmark_name": "clickbench",
            "iterations": 3,
            "warm_up_iterations": 0,
            "scale_factor": 1.0,
        }

        def return_new_result():
            return [{"query_id": "Q1", "status": "SUCCESS", "execution_time": 1.0}]

        mock_adapter._execute_all_queries.side_effect = [
            return_new_result(),
            return_new_result(),
            return_new_result(),
        ]

        results = mock_adapter._execute_generic_power_test(mock_benchmark, mock_connection, run_config)

        assert len(results) == 3

        assert results[0]["iteration"] == 1
        assert results[0]["run_type"] == "measurement"

        assert results[1]["iteration"] == 2
        assert results[1]["run_type"] == "measurement"

        assert results[2]["iteration"] == 3
        assert results[2]["run_type"] == "measurement"

    def test_no_warmup_runs(self, mock_adapter, mock_benchmark, mock_connection):

        run_config = {
            "benchmark_name": "clickbench",
            "iterations": 2,
            "warm_up_iterations": 0,
            "scale_factor": 1.0,
        }

        mock_adapter._execute_all_queries.return_value = [
            {"query_id": "Q1", "status": "SUCCESS", "execution_time": 1.0}
        ]

        results = mock_adapter._execute_generic_power_test(mock_benchmark, mock_connection, run_config)

        assert mock_adapter._execute_all_queries.call_count == 2

        assert len(results) == 2

    def test_default_iterations_value(self, mock_adapter, mock_benchmark, mock_connection):

        run_config = {
            "benchmark_name": "clickbench",
            "warm_up_iterations": 0,
            "scale_factor": 1.0,
        }

        mock_adapter._execute_all_queries.return_value = [
            {"query_id": "Q1", "status": "SUCCESS", "execution_time": 1.0}
        ]

        results = mock_adapter._execute_generic_power_test(mock_benchmark, mock_connection, run_config)

        assert mock_adapter._execute_all_queries.call_count == 3

        assert len(results) == 3

    def test_multiple_queries_per_iteration(self, mock_adapter, mock_benchmark, mock_connection):

        run_config = {
            "benchmark_name": "clickbench",
            "iterations": 2,
            "warm_up_iterations": 1,
            "scale_factor": 1.0,
        }

        def return_new_queries():
            return [
                {"query_id": "Q1", "status": "SUCCESS", "execution_time": 1.0},
                {"query_id": "Q2", "status": "SUCCESS", "execution_time": 2.0},
                {"query_id": "Q3", "status": "SUCCESS", "execution_time": 3.0},
            ]

        mock_adapter._execute_all_queries.side_effect = [
            return_new_queries(),
            return_new_queries(),
            return_new_queries(),
        ]

        results = mock_adapter._execute_generic_power_test(mock_benchmark, mock_connection, run_config)

        assert len(results) == 9

        measurement_results = [r for r in results if r.get("run_type") == "measurement"]
        assert len(measurement_results) == 6

        q1_results = [r for r in measurement_results if r["query_id"] == "Q1"]
        assert len(q1_results) == 2
        assert q1_results[0]["iteration"] == 1
        assert q1_results[1]["iteration"] == 2

    @patch("benchbox.platforms.base.execution.quiet_console")
    def test_console_output_displays_warmup_and_measurement(
        self, mock_console, mock_adapter, mock_benchmark, mock_connection
    ):

        run_config = {
            "benchmark_name": "clickbench",
            "iterations": 2,
            "warm_up_iterations": 1,
            "scale_factor": 1.0,
        }

        def return_new_result():
            return [{"query_id": "Q1", "status": "SUCCESS", "execution_time": 1.0}]

        mock_adapter._execute_all_queries.side_effect = [
            return_new_result(),
            return_new_result(),
            return_new_result(),
        ]

        mock_adapter._execute_generic_power_test(mock_benchmark, mock_connection, run_config)

        console_calls = [str(call) for call in mock_console.print.call_args_list]

        assert any("Warm-up Run" in str(c) for c in console_calls)

        assert any("Measurement Run" in str(c) for c in console_calls)

        assert any("Warm-up runs: 1" in str(c) for c in console_calls)
        assert any("Measurement runs: 2" in str(c) for c in console_calls)

    def test_failed_queries_included_in_results(self, mock_adapter, mock_benchmark, mock_connection):

        run_config = {
            "benchmark_name": "clickbench",
            "iterations": 2,
            "warm_up_iterations": 0,
            "scale_factor": 1.0,
        }

        def return_new_mixed_results():
            return [
                {"query_id": "Q1", "status": "SUCCESS", "execution_time": 1.0},
                {"query_id": "Q2", "status": "FAILED", "execution_time": 0.0, "error": "timeout"},
            ]

        mock_adapter._execute_all_queries.side_effect = [
            return_new_mixed_results(),
            return_new_mixed_results(),
        ]

        results = mock_adapter._execute_generic_power_test(mock_benchmark, mock_connection, run_config)

        assert len(results) == 4

        failed_results = [r for r in results if r["status"] == "FAILED"]
        assert len(failed_results) == 2
        assert all("iteration" in r for r in failed_results)
        assert all(r["run_type"] == "measurement" for r in failed_results)

    def test_dialect_queries_resolved_once_for_all_passes(self, mock_adapter, mock_benchmark, mock_connection):
        run_config = {
            "benchmark_name": "tpch",
            "iterations": 3,
            "warm_up_iterations": 1,
            "scale_factor": 1.0,
        }
        resolved = {"Q1": "SELECT 1"}
        mock_adapter._get_dialect_queries = MagicMock(return_value=resolved)
        mock_adapter._execute_all_queries.side_effect = [[{"query_id": "Q1", "status": "SUCCESS"}] for _ in range(4)]

        mock_adapter._execute_generic_power_test(mock_benchmark, mock_connection, run_config)

        assert mock_adapter._get_dialect_queries.call_count == 1
        for call in mock_adapter._execute_all_queries.call_args_list:
            assert call.args[3] is resolved
