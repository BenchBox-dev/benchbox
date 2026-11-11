# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from datetime import datetime
from typing import Any
from unittest.mock import Mock, patch

import pytest

from benchbox.core.results.models import (
    BenchmarkResults,
    DataGenerationPhase,
    DataLoadingPhase,
    ExecutionPhases,
    QueryDefinition,
    QueryExecution,
    SchemaCreationPhase,
    SetupPhase,
    TableCreationStats,
    TableGenerationStats,
    TableLoadingStats,
)
from benchbox.platforms.base import PlatformAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class MockPlatformAdapter(PlatformAdapter):
    @staticmethod
    def add_cli_arguments(parser) -> None:
        pass

    @classmethod
    def from_config(cls, config):
        return cls(**config)

    @property
    def platform_name(self) -> str:
        return "TestDB"

    def get_platform_info(self, connection=None) -> dict[str, Any]:
        return {
            "platform_type": "testdb",
            "platform_name": "TestDB",
            "connection_mode": "memory",
            "configuration": {},
        }

    def get_target_dialect(self) -> str:
        return "testdb"

    def create_connection(self, **config):
        mock_conn = Mock()
        mock_conn._mock_name = "mock_connection"
        return mock_conn

    def close_connection(self, connection):
        pass

    def create_schema(self, benchmark, connection):
        return 0.5

    def load_data(self, benchmark, connection, data_dir):
        return {"test_table": 1000}, 1.0, None

    def configure_for_benchmark(self, connection, benchmark_type):
        pass

    def execute_query(self, connection, query, query_id):
        return {
            "query_id": query_id,
            "status": "SUCCESS",
            "execution_time_seconds": 0.1,
            "rows_returned": 10,
        }

    def apply_table_tunings(self, table_tuning, connection):
        pass

    def generate_tuning_clause(self, table_tuning):
        return ""

    def apply_unified_tuning(self, unified_config, connection):
        pass

    def apply_platform_optimizations(self, platform_config, connection):
        pass

    def apply_constraint_configuration(self, primary_key_config, foreign_key_config, connection):
        pass

    def get_table_info(self, connection, table_name):
        return {
            "columns": [("id", "INTEGER"), ("name", "VARCHAR")],
            "row_count": 1000,
            "exists": True,
        }

    def check_table_exists(self, connection, table_name):
        return True

    def list_tables(self, connection):
        return ["test_table"]

    def _get_existing_tables(self, connection):
        return ["test_table"]


class TestEnhancedBenchmarkResultsDataStructures:
    def test_table_generation_stats_success(self):
        stats = TableGenerationStats(
            generation_time_ms=1500,
            status="SUCCESS",
            rows_generated=10000,
            data_size_bytes=1024000,
            file_path="test_table.tbl",
        )

        assert stats.generation_time_ms == 1500
        assert stats.status == "SUCCESS"
        assert stats.rows_generated == 10000
        assert stats.data_size_bytes == 1024000
        assert stats.file_path == "test_table.tbl"
        assert stats.error_type is None
        assert stats.error_message is None

    def test_table_generation_stats_failure(self):
        stats = TableGenerationStats(
            generation_time_ms=500,
            status="FAILED",
            rows_generated=0,
            data_size_bytes=0,
            file_path="test_table.tbl",
            error_type="DISK_FULL",
            error_message="Disk full: unable to write test_table.tbl",
            rows_attempted=10000,
            bytes_attempted=1024000,
            error_timestamp="2025-09-03T10:15:30.123",
        )

        assert stats.status == "FAILED"
        assert stats.error_type == "DISK_FULL"
        assert stats.error_message == "Disk full: unable to write test_table.tbl"
        assert stats.rows_attempted == 10000
        assert stats.bytes_attempted == 1024000

    def test_data_generation_phase(self):
        table_stats = {
            "customers": TableGenerationStats(
                generation_time_ms=800,
                status="SUCCESS",
                rows_generated=5000,
                data_size_bytes=512000,
                file_path="customers.tbl",
            ),
            "orders": TableGenerationStats(
                generation_time_ms=1200,
                status="SUCCESS",
                rows_generated=15000,
                data_size_bytes=1536000,
                file_path="orders.tbl",
            ),
        }

        phase = DataGenerationPhase(
            duration_ms=2000,
            status="SUCCESS",
            tables_generated=2,
            total_rows_generated=20000,
            total_data_size_bytes=2048000,
            per_table_stats=table_stats,
        )

        assert phase.duration_ms == 2000
        assert phase.status == "SUCCESS"
        assert phase.tables_generated == 2
        assert phase.total_rows_generated == 20000
        assert phase.total_data_size_bytes == 2048000
        assert len(phase.per_table_stats) == 2
        assert "customers" in phase.per_table_stats
        assert "orders" in phase.per_table_stats

    def test_query_execution(self):
        execution = QueryExecution(
            query_id="Q1",
            stream_id="power_test",
            execution_order=1,
            execution_time_ms=1500,
            status="SUCCESS",
            rows_returned=100,
            resource_usage={"peak_memory_mb": 256},
        )

        assert execution.query_id == "Q1"
        assert execution.stream_id == "power_test"
        assert execution.execution_order == 1
        assert execution.execution_time_ms == 1500
        assert execution.status == "SUCCESS"
        assert execution.rows_returned == 100
        assert execution.resource_usage == {"peak_memory_mb": 256}

    def test_enhanced_benchmark_results_construction(self):
        setup_phase = SetupPhase(
            data_generation=DataGenerationPhase(
                duration_ms=2000,
                status="SUCCESS",
                tables_generated=1,
                total_rows_generated=1000,
                total_data_size_bytes=100000,
                per_table_stats={
                    "test_table": TableGenerationStats(
                        generation_time_ms=2000,
                        status="SUCCESS",
                        rows_generated=1000,
                        data_size_bytes=100000,
                        file_path="test_table.tbl",
                    )
                },
            )
        )

        execution_phases = ExecutionPhases(setup=setup_phase)

        query_definitions = {"standard": {"Q1": QueryDefinition(sql="SELECT COUNT(*) FROM test_table", parameters={})}}

        results = BenchmarkResults(
            benchmark_name="TestBenchmark",
            platform="TestDB",
            scale_factor=0.1,
            execution_id="test123",
            timestamp=datetime.now(),
            duration_seconds=5.0,
            query_definitions=query_definitions,
            execution_phases=execution_phases,
            total_queries=1,
            successful_queries=1,
            failed_queries=0,
            total_execution_time=1.5,
            average_query_time=1.5,
        )

        assert results.benchmark_name == "TestBenchmark"
        assert results.platform == "TestDB"
        assert results.scale_factor == 0.1
        assert results.execution_id == "test123"
        assert results.total_queries == 1
        assert results.successful_queries == 1
        assert results.failed_queries == 0
        assert results.total_execution_time == 1.5
        assert results.average_query_time == 1.5
        assert "standard" in results.query_definitions
        assert "Q1" in results.query_definitions["standard"]
        assert results.execution_phases.setup is not None


class TestPlatformAdapterEnhancedMethods:
    def setup_method(self):
        self.adapter = MockPlatformAdapter()
        self.mock_benchmark = Mock()
        self.mock_benchmark._name = "TestBenchmark"
        self.mock_benchmark.scale_factor = 0.1
        self.mock_benchmark.tables = {
            "test_table": [
                {"id": 1, "name": "row1"},
                {"id": 2, "name": "row2"},
                {"id": 3, "name": "row3"},
            ]
        }

    def test_create_enhanced_data_generation_phase(self):
        phase = self.adapter._create_enhanced_data_generation_phase(self.mock_benchmark)

        assert phase is not None
        assert phase.status == "SUCCESS"
        assert phase.tables_generated == 1
        assert phase.total_rows_generated == 3
        assert "test_table" in phase.per_table_stats

        table_stats = phase.per_table_stats["test_table"]
        assert table_stats.status == "SUCCESS"
        assert table_stats.rows_generated == 3
        assert table_stats.file_path == "test_table.tbl"

    def test_create_enhanced_data_generation_phase_no_tables(self):
        benchmark_without_tables = Mock()
        if hasattr(benchmark_without_tables, "tables"):
            del benchmark_without_tables.tables
        benchmark_without_tables._impl = None

        phase = self.adapter._create_enhanced_data_generation_phase(benchmark_without_tables)
        assert phase is None

    def test_create_enhanced_schema_creation_phase(self):
        connection = Mock()
        schema_time = 0.5

        self.mock_benchmark.tables = {"test_table": []}

        phase = self.adapter._create_enhanced_schema_creation_phase(self.mock_benchmark, connection, schema_time)

        assert phase.duration_ms == 500
        assert phase.status == "SUCCESS"
        assert phase.tables_created == 1
        assert "test_table" in phase.per_table_creation

        table_creation = phase.per_table_creation["test_table"]
        assert table_creation.status == "SUCCESS"
        assert table_creation.constraints_applied == 1
        assert table_creation.indexes_created == 1

    def test_create_enhanced_data_loading_phase(self):
        table_stats = {"test_table": 1000, "another_table": 500}
        loading_time = 2.5

        phase = self.adapter._create_enhanced_data_loading_phase(table_stats, loading_time)

        assert phase.duration_ms == 2500
        assert phase.status == "SUCCESS"
        assert phase.total_rows_loaded == 1500
        assert phase.tables_loaded == 2
        assert "test_table" in phase.per_table_stats
        assert "another_table" in phase.per_table_stats

        test_table_stats = phase.per_table_stats["test_table"]
        assert test_table_stats.rows == 1000
        assert test_table_stats.status == "SUCCESS"

    def test_create_throughput_phase(self):
        from benchbox.core.tpch.throughput_test import (
            TPCHThroughputStreamResult,
            TPCHThroughputTestConfig,
            TPCHThroughputTestResult,
        )

        config = TPCHThroughputTestConfig(scale_factor=0.01, num_streams=1)
        stream_result = TPCHThroughputStreamResult(
            stream_id=0,
            start_time=1_700_000_000.0,
            end_time=1_700_000_001.0,
            duration=1.0,
            queries_executed=2,
            queries_successful=2,
            queries_failed=0,
            query_results=[
                {
                    "query_id": 1,
                    "position": 1,
                    "execution_time_seconds": 0.1,
                    "success": True,
                    "result_count": 42,
                },
                {
                    "query_id": 2,
                    "position": 2,
                    "execution_time_seconds": 0.2,
                    "success": True,
                    "result_count": 84,
                },
            ],
            success=True,
        )

        throughput_result = TPCHThroughputTestResult(
            config=config,
            start_time="2024-01-01T00:00:00Z",
            end_time="2024-01-01T00:00:01Z",
            total_time=1.0,
            throughput_at_size=123.45,
            streams_executed=1,
            streams_successful=1,
            stream_results=[stream_result],
            query_throughput=44.0,
        )

        phase = self.adapter._create_throughput_phase(throughput_result)

        assert phase is not None
        assert phase.throughput_at_size == 123.45
        assert phase.num_streams == 1
        assert phase.total_queries_executed == 2
        assert len(phase.streams) == 1

        first_stream = phase.streams[0]
        assert first_stream.stream_id == 0
        assert len(first_stream.query_executions) == 2
        first_execution = first_stream.query_executions[0]
        assert first_execution.execution_order == 1
        assert first_execution.status == "SUCCESS"

    def test_create_enhanced_validation_phase(self):
        phase = self.adapter._create_enhanced_validation_phase()

        assert phase.duration_ms >= 50
        assert phase.row_count_validation == "PASSED"
        assert phase.schema_validation == "PASSED"
        assert phase.data_integrity_checks == "PASSED"
        assert phase.validation_details is not None
        assert phase.validation_details["row_count_matches"] is True

    def test_extract_query_definitions(self):
        queries = {
            "Q1": "SELECT COUNT(*) FROM test_table",
            "Q2": "SELECT * FROM test_table LIMIT 10",
        }
        stream_id = "power_test"

        query_defs = self.adapter._extract_query_definitions(self.mock_benchmark, queries, stream_id)

        assert stream_id in query_defs
        assert "Q1" in query_defs[stream_id]
        assert "Q2" in query_defs[stream_id]

        q1_def = query_defs[stream_id]["Q1"]
        assert q1_def.sql == "SELECT COUNT(*) FROM test_table"
        assert q1_def.parameters == {}

    def test_create_standard_execution_phase(self):
        query_results = [
            {
                "query_id": "Q1",
                "status": "SUCCESS",
                "execution_time_seconds": 1.5,
                "rows_returned": 100,
            },
            {
                "query_id": "Q2",
                "status": "FAILED",
                "execution_time_seconds": 0.5,
                "rows_returned": 0,
                "error": "Table not found",
            },
        ]
        stream_id = "standard"

        executions = self.adapter._create_standard_execution_phase(query_results, stream_id)

        assert len(executions) == 2

        exec1 = executions[0]
        assert exec1.query_id == "Q1"
        assert exec1.stream_id == "standard"
        assert exec1.execution_order == 1
        assert exec1.execution_time_ms == 1500
        assert exec1.status == "SUCCESS"
        assert exec1.rows_returned == 100

        exec2 = executions[1]
        assert exec2.query_id == "Q2"
        assert exec2.status == "FAILED"
        assert exec2.error_message == "Table not found"


class TestEnhancedBenchmarkIntegration:
    def setup_method(self):
        self.adapter = MockPlatformAdapter()
        self.mock_benchmark = Mock()
        self.mock_benchmark._name = "TestBenchmark"
        self.mock_benchmark.scale_factor = 0.1
        self.mock_benchmark.output_dir = "/tmp/test"
        self.mock_benchmark.tables = {"test_table": [{"id": 1, "name": "test1"}, {"id": 2, "name": "test2"}]}

        self.mock_benchmark.get_queries.return_value = {
            "Q1": "SELECT COUNT(*) FROM test_table",
            "Q2": "SELECT * FROM test_table",
        }

        def mock_create_enhanced_benchmark_result(platform, query_results, **kwargs):
            from datetime import datetime

            return BenchmarkResults(
                benchmark_name="TestBenchmark",
                platform=platform,
                scale_factor=0.1,
                execution_id=kwargs.get("execution_id", "test123"),
                timestamp=kwargs.get("timestamp", datetime.now()),
                duration_seconds=kwargs.get("duration_seconds", 2.5),
                query_definitions={
                    "standard": {
                        "Q1": QueryDefinition(sql="SELECT COUNT(*) FROM test_table"),
                        "Q2": QueryDefinition(sql="SELECT * FROM test_table"),
                    }
                },
                execution_phases=kwargs.get("phases", ExecutionPhases(setup=SetupPhase())),
                total_queries=len(query_results),
                successful_queries=len([r for r in query_results if r.get("status") == "SUCCESS"]),
                failed_queries=len([r for r in query_results if r.get("status") != "SUCCESS"]),
                total_execution_time=sum(r.get("execution_time_seconds", 0) for r in query_results),
                average_query_time=sum(r.get("execution_time_seconds", 0) for r in query_results)
                / max(len(query_results), 1),
            )

        self.mock_benchmark.create_enhanced_benchmark_result = mock_create_enhanced_benchmark_result

        self.adapter._execute_all_queries = Mock(
            return_value=[
                {
                    "query_id": "Q1",
                    "status": "SUCCESS",
                    "execution_time_seconds": 1.0,
                    "rows_returned": 2,
                },
                {
                    "query_id": "Q2",
                    "status": "SUCCESS",
                    "execution_time_seconds": 1.5,
                    "rows_returned": 2,
                },
            ]
        )

        self.adapter.get_effective_tuning_configuration = Mock(return_value=None)
        self.adapter._calculate_data_size = Mock(return_value=1.0)
        self.adapter._get_platform_metadata = Mock(return_value={})
        self.adapter._hash_connection_config = Mock(return_value="abc123")

    @patch("uuid.uuid4")
    def test_run_enhanced_benchmark_success(self, mock_uuid):
        mock_uuid_obj = Mock()
        mock_uuid_obj.__getitem__ = Mock(return_value="test123")
        mock_uuid.return_value = mock_uuid_obj

        with patch("benchbox.core.results.anonymization.AnonymizationManager") as mock_anon:
            mock_anon_instance = Mock()
            mock_anon_instance.get_system_profile.return_value = {"os": "TestOS"}
            mock_anon_instance.get_anonymous_machine_id.return_value = "machine123"
            mock_anon.return_value = mock_anon_instance

            results = self.adapter.run_enhanced_benchmark(self.mock_benchmark)

        assert isinstance(results, BenchmarkResults)
        assert results.benchmark_name == "TestBenchmark"
        assert results.platform == "TestDB"
        assert results.scale_factor == 0.1
        assert results.total_queries == 2
        assert results.successful_queries == 2
        assert results.failed_queries == 0

        assert "standard" in results.query_definitions
        assert "Q1" in results.query_definitions["standard"]
        assert "Q2" in results.query_definitions["standard"]

        assert results.execution_phases.setup is not None
        assert results.execution_phases.setup.data_generation is not None
        assert results.execution_phases.setup.schema_creation is not None
        assert results.execution_phases.setup.data_loading is not None
        assert results.execution_phases.setup.validation is not None


class TestEnhancedResultsErrorHandling:
    def test_data_generation_phase_with_errors(self):
        table_stats = {
            "success_table": TableGenerationStats(
                generation_time_ms=1000,
                status="SUCCESS",
                rows_generated=5000,
                data_size_bytes=512000,
                file_path="success_table.tbl",
            ),
            "failed_table": TableGenerationStats(
                generation_time_ms=500,
                status="FAILED",
                rows_generated=0,
                data_size_bytes=0,
                file_path="failed_table.tbl",
                error_type="DISK_FULL",
                error_message="Insufficient disk space",
                rows_attempted=3000,
                bytes_attempted=300000,
                error_timestamp="2025-09-03T10:15:30.123",
            ),
        }

        phase = DataGenerationPhase(
            duration_ms=1500,
            status="PARTIAL_FAILURE",
            tables_generated=1,
            total_rows_generated=5000,
            total_data_size_bytes=512000,
            per_table_stats=table_stats,
        )

        assert phase.status == "PARTIAL_FAILURE"
        assert phase.tables_generated == 1

        success_stats = phase.per_table_stats["success_table"]
        assert success_stats.status == "SUCCESS"
        assert success_stats.error_type is None

        failed_stats = phase.per_table_stats["failed_table"]
        assert failed_stats.status == "FAILED"
        assert failed_stats.error_type == "DISK_FULL"
        assert failed_stats.error_message == "Insufficient disk space"
        assert failed_stats.rows_attempted == 3000
        assert failed_stats.bytes_attempted == 300000

    def test_schema_creation_phase_with_errors(self):
        per_table_creation = {
            "success_table": TableCreationStats(
                creation_time_ms=200,
                status="SUCCESS",
                constraints_applied=2,
                indexes_created=1,
            ),
            "failed_table": TableCreationStats(
                creation_time_ms=0,
                status="FAILED",
                constraints_applied=0,
                indexes_created=0,
                error_type="TABLE_EXISTS",
                error_message="Table 'failed_table' already exists",
                error_timestamp="2025-09-03T10:15:25.456",
            ),
        }

        phase = SchemaCreationPhase(
            duration_ms=200,
            status="PARTIAL_FAILURE",
            tables_created=1,
            constraints_applied=2,
            indexes_created=1,
            per_table_creation=per_table_creation,
        )

        assert phase.status == "PARTIAL_FAILURE"
        assert phase.tables_created == 1

        failed_creation = phase.per_table_creation["failed_table"]
        assert failed_creation.status == "FAILED"
        assert failed_creation.error_type == "TABLE_EXISTS"
        assert failed_creation.error_message == "Table 'failed_table' already exists"

    def test_data_loading_phase_with_errors(self):
        per_table_stats = {
            "success_table": TableLoadingStats(rows=1000, load_time_ms=1500, status="SUCCESS"),
            "failed_table": TableLoadingStats(
                rows=0,
                load_time_ms=500,
                status="FAILED",
                error_type="CONSTRAINT_VIOLATION",
                error_message="Foreign key constraint violation",
                rows_processed=500,
                rows_successful=0,
                error_timestamp="2025-09-03T10:16:30.123",
            ),
        }

        phase = DataLoadingPhase(
            duration_ms=2000,
            status="PARTIAL_FAILURE",
            total_rows_loaded=1000,
            tables_loaded=1,
            per_table_stats=per_table_stats,
        )

        assert phase.status == "PARTIAL_FAILURE"
        assert phase.total_rows_loaded == 1000
        assert phase.tables_loaded == 1

        failed_loading = phase.per_table_stats["failed_table"]
        assert failed_loading.status == "FAILED"
        assert failed_loading.error_type == "CONSTRAINT_VIOLATION"
        assert failed_loading.rows_processed == 500
        assert failed_loading.rows_successful == 0


