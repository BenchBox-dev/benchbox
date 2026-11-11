import pytest

from benchbox.core.reports import generate_report

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestGenerateReport:
    def test_basic_success_report(self):
        result_data = {
            "benchmark": "tpch",
            "scale_factor": 1,
            "platform": "duckdb",
            "success": True,
            "successful_queries": 22,
            "total_queries": 22,
            "total_execution_time": 1.5,
            "average_query_time": 0.068,
        }
        report = generate_report(result_data)
        assert "TPCH" in report
        assert "duckdb" in report
        assert "PASSED" in report
        assert "22/22 successful" in report

    def test_basic_failure_report(self):
        result_data = {
            "benchmark": "tpcds",
            "scale_factor": 10,
            "platform": "sqlite",
            "success": False,
            "successful_queries": 90,
            "total_queries": 99,
        }
        report = generate_report(result_data)
        assert "TPCDS" in report
        assert "FAILED" in report
        assert "90/99 successful" in report

    def test_verbosity_zero_omits_durations(self):
        result_data = {
            "benchmark": "tpch",
            "scale_factor": 1,
            "platform": "duckdb",
            "success": True,
            "total_duration": 10.5,
            "schema_creation_time": 0.5,
            "data_loading_time": 3.0,
        }
        report = generate_report(result_data, verbosity=0)
        assert "Total Duration" not in report
        assert "Schema Creation" not in report

    def test_verbosity_one_includes_durations(self):
        result_data = {
            "benchmark": "tpch",
            "scale_factor": 1,
            "platform": "duckdb",
            "success": True,
            "total_duration": 10.5,
            "schema_creation_time": 0.5,
            "data_loading_time": 3.0,
        }
        report = generate_report(result_data, verbosity=1)
        assert "Total Duration" in report
        assert "Schema Creation" in report
        assert "Data Loading" in report

    def test_missing_fields_use_defaults(self):
        report = generate_report({})
        assert "UNKNOWN" in report
        assert "FAILED" in report

    def test_success_with_execution_time(self):
        result_data = {
            "benchmark": "ssb",
            "scale_factor": 1,
            "platform": "clickhouse",
            "success": True,
            "total_execution_time": 2.345,
            "average_query_time": 0.18,
        }
        report = generate_report(result_data)
        assert "Query Execution Time" in report
        assert "Average Query Time" in report

    def test_failed_report_omits_execution_time(self):
        result_data = {
            "benchmark": "tpch",
            "scale_factor": 1,
            "platform": "duckdb",
            "success": False,
            "total_execution_time": 2.345,
        }
        report = generate_report(result_data)
        assert "Query Execution Time" not in report


