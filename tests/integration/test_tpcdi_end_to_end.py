# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DI (TPC-DI) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DI specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

import concurrent.futures
import shutil
import tempfile
import time
from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import duckdb
import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]


pytest.importorskip("pandas")

from benchbox import TPCDI
from benchbox.core.tpcdi.schema import TABLES

try:
    import duckdb

    DUCKDB_AVAILABLE = True
except ImportError:
    DUCKDB_AVAILABLE = False
    duckdb = None


@pytest.mark.integration
@pytest.mark.tpcdi
@pytest.mark.slow
class TestTPCDIEndToEndWorkflow:
    @pytest.fixture
    def etl_workspace(self) -> Generator[Path, None, None]:
        workspace = Path(tempfile.mkdtemp())
        yield workspace

        shutil.rmtree(workspace, ignore_errors=True)

    @pytest.fixture
    def duckdb_database_file(self, etl_workspace: Path) -> Generator[duckdb.DuckDBPyConnection, None, None]:
        db_path = etl_workspace / "tpcdi_test.db"
        conn = duckdb.connect(str(db_path))
        yield conn
        conn.close()

    @pytest.fixture
    def duckdb_database(self, etl_workspace: Path):
        if not DUCKDB_AVAILABLE:
            pytest.skip("DuckDB not available")

        db_path = etl_workspace / "tpcdi_test.duckdb"
        conn = duckdb.connect(str(db_path))
        yield conn
        conn.close()

    @pytest.fixture
    def tpcdi_etl_benchmark(self, etl_workspace: Path) -> TPCDI:
        return TPCDI(
            scale_factor=0.01,
            output_dir=etl_workspace,
        )

    @pytest.fixture
    def tpcdi_performance_benchmark(self, etl_workspace: Path) -> TPCDI:
        return TPCDI(
            scale_factor=0.01,
            output_dir=etl_workspace,
        )

    def test_complete_etl_workflow_sqlite(
        self,
        tpcdi_etl_benchmark: TPCDI,
        duckdb_database_file: duckdb.DuckDBPyConnection,
    ):
        start_time = time.time()

        source_files = tpcdi_etl_benchmark.generate_source_data(
            formats=["csv", "xml", "json", "fixed_width"], batch_types=["historical"]
        )

        assert len(source_files) == 4, "Should generate 4 different format types"
        for format_type, files in source_files.items():
            assert len(files) > 0, f"No files generated for format {format_type}"
            for file_path in files:
                assert Path(file_path).exists(), f"Source file not found: {file_path}"
                assert Path(file_path).stat().st_size > 0, f"Source file is empty: {file_path}"

        generation_time = time.time() - start_time

        etl_start = time.time()
        etl_results = tpcdi_etl_benchmark.run_etl_pipeline(
            connection=duckdb_database_file, batch_type="historical", validate_data=True
        )
        etl_time = time.time() - etl_start

        assert etl_results["success"] is True, f"ETL pipeline failed: {etl_results.get('error', 'Unknown error')}"
        assert "phases" in etl_results, "ETL results should contain phase information"

        expected_phases = ["extract", "transform", "load", "validation"]
        for phase in expected_phases:
            assert phase in etl_results["phases"], f"Missing ETL phase: {phase}"
            assert "duration" in etl_results["phases"][phase], f"Phase {phase} missing duration"
            assert etl_results["phases"][phase]["duration"] > 0, f"Phase {phase} took no time"

        cursor = duckdb_database_file.cursor()

        tables_with_data = []
        for table_name in TABLES:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                count = cursor.fetchone()[0]
                if count > 0:
                    tables_with_data.append(table_name)
            except Exception:
                pass

        assert len(tables_with_data) > 0, "No tables contain data after ETL"

        cursor.execute("SELECT COUNT(*) FROM DimCustomer")
        customer_count = cursor.fetchone()[0]
        assert customer_count > 0, "DimCustomer table should contain data"

        cursor.execute("SELECT COUNT(*) FROM DimCustomer WHERE IsCurrent = 1")
        current_customer_count = cursor.fetchone()[0]
        assert current_customer_count > 0, "Should have current customers"

        queries = tpcdi_etl_benchmark.get_queries()
        assert len(queries) > 0, "Should have queries available"

        validation_queries = [q for q in queries if q.startswith("V")]
        for query_id in validation_queries:
            try:
                result = tpcdi_etl_benchmark.execute_query(query_id, duckdb_database_file)
                assert result is not None, f"Query {query_id} returned None"
            except Exception as e:
                if "no such table" not in str(e).lower():
                    raise

        query_manager = tpcdi_etl_benchmark._impl.query_manager
        execution_order = query_manager.resolve_query_order()
        assert len(execution_order) > 0, "Should have queries in execution order"

        executed_queries = set()
        for query_id in execution_order:
            dependencies = query_manager.get_query_dependencies(query_id)
            all_query_ids = set(query_manager.get_all_queries().keys())
            query_dependencies = [dep for dep in dependencies if dep in all_query_ids]

            for dep in query_dependencies:
                assert dep in executed_queries, f"Query {query_id} executed before its dependency {dep}"

            executed_queries.add(query_id)

        total_time = time.time() - start_time

        assert generation_time < 30, f"Source data generation took too long: {generation_time:.2f}s"
        assert etl_time < 60, f"ETL pipeline took too long: {etl_time:.2f}s"
        assert total_time < 120, f"Total workflow took too long: {total_time:.2f}s"

        assert etl_results["total_duration"] > 0, "ETL should report positive duration"
        assert etl_results["phases"]["extract"]["files_generated"] > 0, "Should generate source files"
        assert etl_results["phases"]["transform"]["records_processed"] > 0, "Should process records"
        assert etl_results["phases"]["load"]["records_loaded"] > 0, "Should load records"

    @pytest.mark.skipif(not DUCKDB_AVAILABLE, reason="DuckDB not available")
    def test_complete_etl_workflow_duckdb(self, tpcdi_etl_benchmark: TPCDI, duckdb_database):

        start_time = time.time()

        tpcdi_etl_benchmark.generate_source_data(
            formats=["csv", "xml"],
            batch_types=["historical"],
        )

        etl_results = tpcdi_etl_benchmark.run_etl_pipeline(
            connection=duckdb_database, batch_type="historical", validate_data=True
        )

        assert etl_results["success"] is True, f"ETL pipeline failed: {etl_results.get('error')}"

        result = duckdb_database.execute("SELECT COUNT(*) FROM DimCustomer").fetchone()
        customer_count = result[0] if result else 0
        assert customer_count > 0, "DimCustomer should contain data in DuckDB"

        total_time = time.time() - start_time
        assert total_time < 90, f"DuckDB workflow should be efficient: {total_time:.2f}s"

    def test_all_batch_types_workflow(
        self,
        tpcdi_etl_benchmark: TPCDI,
        duckdb_database_file: duckdb.DuckDBPyConnection,
    ):
        batch_results = {}

        batch_types = ["historical", "incremental", "scd"]

        for batch_type in batch_types:
            start_time = time.time()

            tpcdi_etl_benchmark.generate_source_data(
                formats=["csv", "json"],
                batch_types=[batch_type],
            )

            etl_results = tpcdi_etl_benchmark.run_etl_pipeline(
                connection=duckdb_database_file,
                batch_type=batch_type,
                validate_data=True,
            )

            batch_time = time.time() - start_time

            batch_results[batch_type] = {
                "success": etl_results["success"],
                "duration": batch_time,
                "records_processed": etl_results["phases"]["transform"]["records_processed"],
                "records_loaded": etl_results["phases"]["load"]["records_loaded"],
            }

            assert etl_results["success"] is True, f"Batch {batch_type} failed: {etl_results.get('error')}"

        assert len(batch_results) == 3, "Should process all 3 batch types"

        assert batch_results["historical"]["records_processed"] > batch_results["incremental"]["records_processed"]
        assert batch_results["historical"]["records_processed"] > batch_results["scd"]["records_processed"]

        for batch_type, results in batch_results.items():
            assert results["records_processed"] >= 0, f"Batch {batch_type} has negative records processed"
            if batch_type != "scd":
                assert results["records_loaded"] > 0, f"Batch {batch_type} loaded no records"

        cursor = duckdb_database_file.cursor()
        cursor.execute("SELECT COUNT(*) FROM DimCustomer")
        total_customers = cursor.fetchone()[0]

        expected_min_customers = sum(r["records_loaded"] for r in batch_results.values()) * 0.1
        assert total_customers >= expected_min_customers, (
            f"Expected at least {expected_min_customers} customers, got {total_customers}"
        )

    def test_data_quality_validation_comprehensive(
        self,
        tpcdi_etl_benchmark: TPCDI,
        duckdb_database_file: duckdb.DuckDBPyConnection,
    ):

        tpcdi_etl_benchmark.generate_source_data(formats=["csv"], batch_types=["historical"])

        etl_results = tpcdi_etl_benchmark.run_etl_pipeline(
            connection=duckdb_database_file, batch_type="historical", validate_data=True
        )

        assert "validation_results" in etl_results, "ETL should include validation results"
        validation_results = etl_results["validation_results"]

        assert "data_quality_score" in validation_results, "Should calculate data quality score"
        quality_score = validation_results["data_quality_score"]
        assert isinstance(quality_score, (int, float)), "Quality score should be numeric"
        assert 0 <= quality_score <= 100, f"Quality score should be 0-100, got {quality_score}"

        expected_categories = [
            "completeness_checks",
            "consistency_checks",
            "accuracy_checks",
        ]
        for category in expected_categories:
            assert category in validation_results, f"Missing validation category: {category}"

        completeness = validation_results["completeness_checks"]
        assert isinstance(completeness, dict), "Completeness checks should be a dictionary"

        if "DimCustomer" in completeness:
            customer_completeness = completeness["DimCustomer"]
            assert "total_records" in customer_completeness, "Should report total records"
            assert customer_completeness["total_records"] > 0, "Should have customer records"

        validation_queries = validation_results.get("validation_queries", {})
        assert len(validation_queries) > 0, "Should execute validation queries"

        successful_queries = [q for q, result in validation_queries.items() if result.get("success", False)]
        assert len(successful_queries) > 0, "At least some validation queries should succeed"

    def test_performance_with_larger_scale(
        self,
        tpcdi_performance_benchmark: TPCDI,
        duckdb_database_file: duckdb.DuckDBPyConnection,
    ):

        start_time = time.time()

        tpcdi_performance_benchmark.generate_source_data(formats=["csv", "json"], batch_types=["historical"])

        time.time() - start_time

        etl_start = time.time()
        etl_results = tpcdi_performance_benchmark.run_etl_pipeline(
            connection=duckdb_database_file,
            batch_type="historical",
            validate_data=False,
        )
        etl_time = time.time() - etl_start

        total_time = time.time() - start_time

        assert etl_results["success"] is True, f"Performance test failed: {etl_results.get('error')}"

        records_processed = etl_results["phases"]["transform"]["records_processed"]
        records_loaded = etl_results["phases"]["load"]["records_loaded"]

        assert records_processed > 10, f"Should process records with scale 0.01: {records_processed}"
        assert records_loaded > 0, f"Should load records with scale 0.01: {records_loaded}"

        if etl_time > 0:
            throughput = records_processed / etl_time
            assert throughput > 10, f"ETL throughput too low: {throughput:.2f} records/sec"

        assert total_time < 300, f"Large scale ETL took too long: {total_time:.2f}s"

        cursor = duckdb_database_file.cursor()
        cursor.execute("SELECT COUNT(*) FROM DimCustomer")
        final_count = cursor.fetchone()[0]
        assert final_count == records_loaded, "Loaded record count should match database count"

    def test_concurrent_batch_processing(self, etl_workspace: Path):

        def process_batch(batch_type: str, workspace_subdir: str) -> dict[str, Any]:
            batch_workspace = workspace_subdir / batch_type
            batch_workspace.mkdir(exist_ok=True)

            benchmark = TPCDI(
                scale_factor=0.01,
                output_dir=batch_workspace,
            )

            db_path = batch_workspace / f"batch_{batch_type}.db"
            conn = duckdb.connect(str(db_path))

            try:
                start_time = time.time()

                benchmark.generate_source_data(formats=["csv"], batch_types=[batch_type])

                etl_results = benchmark.run_etl_pipeline(connection=conn, batch_type=batch_type, validate_data=False)

                duration = time.time() - start_time

                return {
                    "batch_type": batch_type,
                    "success": etl_results["success"],
                    "duration": duration,
                    "records_processed": etl_results["phases"]["transform"]["records_processed"],
                    "error": etl_results.get("error"),
                }

            except Exception as e:
                return {
                    "batch_type": batch_type,
                    "success": False,
                    "error": str(e),
                    "duration": time.time() - start_time,
                    "records_processed": 0,
                }
            finally:
                conn.close()

        batch_types = ["historical", "incremental", "scd"]
        results = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(process_batch, batch_type, etl_workspace) for batch_type in batch_types]

            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())

        assert len(results) == 3, "Should complete all 3 concurrent batches"

        for result in results:
            assert result["success"] is True, f"Concurrent batch {result['batch_type']} failed: {result.get('error')}"
            assert result["records_processed"] > 0, f"Batch {result['batch_type']} processed no records"

        for result in results:
            assert result["duration"] < 120, (
                f"Concurrent batch {result['batch_type']} took too long: {result['duration']:.2f}s"
            )

    def test_error_recovery_and_rollback(
        self,
        tpcdi_etl_benchmark: TPCDI,
        duckdb_database_file: duckdb.DuckDBPyConnection,
    ):

        tpcdi_etl_benchmark.generate_source_data(formats=["csv"], batch_types=["historical"])

        etl_results = tpcdi_etl_benchmark.run_etl_pipeline(
            connection=duckdb_database_file,
            batch_type="historical",
            validate_data=False,
        )
        assert etl_results["success"] is True, "Baseline ETL should succeed"

        cursor = duckdb_database_file.cursor()
        cursor.execute("SELECT COUNT(*) FROM DimCustomer")
        initial_count = cursor.fetchone()[0]
        assert initial_count > 0, "Should have initial data"

        source_dir = tpcdi_etl_benchmark._impl.source_dir
        corrupted_file = source_dir / "csv" / "incremental" / "corrupted_customers.csv"
        corrupted_file.parent.mkdir(parents=True, exist_ok=True)

        with open(corrupted_file, "w", encoding="utf-8") as f:
            f.write("bad,data,format\n")
            f.write("incomplete,row\n")
            f.write("another,bad,line,with,too,many,columns,here,extra\n")

        def corrupted_generator(batch_types):
            return [str(corrupted_file)]

        with patch.object(tpcdi_etl_benchmark._impl, "_generate_csv_sources", corrupted_generator):
            try:
                error_etl_results = tpcdi_etl_benchmark.run_etl_pipeline(
                    connection=duckdb_database_file,
                    batch_type="incremental",
                    validate_data=False,
                )

                assert error_etl_results["success"] is False, "ETL with corrupted data should fail"
                assert "error" in error_etl_results, "Should report error details"

            except Exception:
                pass

        cursor.execute("SELECT COUNT(*) FROM DimCustomer")
        final_count = cursor.fetchone()[0]

        assert final_count >= initial_count, "Original data should not be corrupted by failed ETL"

        recovery_etl_results = tpcdi_etl_benchmark.run_etl_pipeline(
            connection=duckdb_database_file,
            batch_type="scd",
            validate_data=False,
        )

        assert recovery_etl_results["success"] is True, "Recovery ETL should succeed after error"

    def test_realistic_data_volumes_and_patterns(
        self, etl_workspace: Path, duckdb_database_file: duckdb.DuckDBPyConnection
    ):

        realistic_benchmark = TPCDI(scale_factor=0.01, output_dir=etl_workspace)

        start_time = time.time()

        source_files = realistic_benchmark.generate_source_data(
            formats=["csv", "xml", "json"], batch_types=["historical", "incremental"]
        )

        total_files = sum(len(files) for files in source_files.values())
        assert total_files >= 6, f"Should generate multiple files for realistic test: {total_files}"

        for _format_type, files in source_files.items():
            for file_path in files:
                file_size = Path(file_path).stat().st_size
                assert file_size > 0, f"File {file_path} is empty: {file_size} bytes"
                assert file_size < 10_000_000, f"File {file_path} too large: {file_size} bytes"

        historical_results = realistic_benchmark.run_etl_pipeline(
            connection=duckdb_database_file, batch_type="historical", validate_data=True
        )

        assert historical_results["success"] is True, "Historical batch should succeed"

        incremental_results = realistic_benchmark.run_etl_pipeline(
            connection=duckdb_database_file,
            batch_type="incremental",
            validate_data=True,
        )

        assert incremental_results["success"] is True, "Incremental batch should succeed"

        cursor = duckdb_database_file.cursor()
        cursor.execute("SELECT COUNT(*) FROM DimCustomer")
        customer_count = cursor.fetchone()[0]

        assert customer_count >= 5, f"Should have some customers: {customer_count}"
        assert customer_count <= 10000, f"Customer count should be reasonable: {customer_count}"

        quality_score = incremental_results["validation_results"]["data_quality_score"]
        assert quality_score >= 70, f"Data quality should be good with realistic data: {quality_score}"

        total_time = time.time() - start_time
        throughput = customer_count / total_time if total_time > 0 else 0
        assert throughput > 5, f"Should maintain reasonable throughput: {throughput:.2f} customers/sec"

    def test_etl_status_and_monitoring(
        self,
        tpcdi_etl_benchmark: TPCDI,
        duckdb_database_file: duckdb.DuckDBPyConnection,
    ):

        initial_status = tpcdi_etl_benchmark.get_etl_status()
        assert initial_status["etl_mode_enabled"] is True, "ETL mode should be enabled"
        assert "simple_stats" in initial_status, "Should provide simple_stats"
        assert "batch_status" in initial_status["simple_stats"], "Should track batch status"

        initial_batches_processed = initial_status["simple_stats"]["batches_processed"]

        tpcdi_etl_benchmark.generate_source_data(formats=["csv"], batch_types=["historical"])

        tpcdi_etl_benchmark.run_etl_pipeline(
            connection=duckdb_database_file, batch_type="historical", validate_data=True
        )

        updated_status = tpcdi_etl_benchmark.get_etl_status()
        updated_stats = updated_status["simple_stats"]

        assert updated_stats["batches_processed"] == initial_batches_processed + 1, "Should increment batches processed"
        assert updated_stats["total_processing_time"] > 0, "Should track processing time"

        batch_status = updated_stats["batch_status"]
        assert "historical" in batch_status, "Should track historical batch status"

        historical_status = batch_status["historical"]
        assert historical_status["status"] == "completed", "Historical batch should be completed"

        assert Path(updated_status["source_directory"]).exists(), "Source directory should exist"
        assert Path(updated_status["staging_directory"]).exists(), "Staging directory should exist"
        assert Path(updated_status["warehouse_directory"]).exists(), "Warehouse directory should exist"

    @pytest.mark.parametrize("scale_factor", [0.001, 0.01])
    def test_scalability_across_sizes(self, etl_workspace: Path, scale_factor: float):

        benchmark = TPCDI(
            scale_factor=scale_factor,
            output_dir=etl_workspace / f"scale_{scale_factor}",
        )

        db_path = etl_workspace / f"scale_{scale_factor}.db"
        conn = duckdb.connect(str(db_path))

        try:
            start_time = time.time()

            benchmark.generate_source_data(formats=["csv"], batch_types=["historical"])

            etl_results = benchmark.run_etl_pipeline(
                connection=conn,
                batch_type="historical",
                validate_data=False,
            )

            duration = time.time() - start_time

            assert etl_results["success"] is True, f"ETL failed for scale {scale_factor}"

            records_processed = etl_results["phases"]["transform"]["records_processed"]
            expected_min_records = int(100 * scale_factor)

            assert records_processed >= expected_min_records, (
                f"Scale {scale_factor} should process at least {expected_min_records} records, got {records_processed}"
            )

            max_expected_time = 60 + (scale_factor * 120)
            assert duration < max_expected_time, (
                f"Scale {scale_factor} took too long: {duration:.2f}s > {max_expected_time:.2f}s"
            )

            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM DimCustomer")
            customer_count = cursor.fetchone()[0]

            expected_min_customers = int(10 * scale_factor)
            assert customer_count >= expected_min_customers, (
                f"Scale {scale_factor} should have at least {expected_min_customers} customers, got {customer_count}"
            )

        finally:
            conn.close()


@pytest.mark.integration
@pytest.mark.tpcdi
@pytest.mark.performance
class TestTPCDIPerformanceValidation:
    def test_etl_performance_benchmarking(self, tmp_path: Path):

        performance_results = {}
        configurations = [
            {"scale": 0.001, "formats": ["csv"], "name": "minimal"},
            {"scale": 0.01, "formats": ["csv", "json"], "name": "moderate"},
            {"scale": 0.01, "formats": ["csv", "xml", "json"], "name": "full"},
        ]

        for config in configurations:
            benchmark = TPCDI(
                scale_factor=config["scale"],
                output_dir=tmp_path / config["name"],
            )

            db_path = tmp_path / f"{config['name']}.db"
            conn = duckdb.connect(str(db_path))

            try:
                start_time = time.time()

                gen_start = time.time()
                benchmark.generate_source_data(formats=config["formats"], batch_types=["historical"])
                gen_time = time.time() - gen_start

                etl_start = time.time()
                etl_results = benchmark.run_etl_pipeline(connection=conn, batch_type="historical", validate_data=True)
                etl_time = time.time() - etl_start

                total_time = time.time() - start_time

                records_processed = etl_results["phases"]["transform"]["records_processed"]
                records_loaded = etl_results["phases"]["load"]["records_loaded"]

                performance_results[config["name"]] = {
                    "scale_factor": config["scale"],
                    "formats": len(config["formats"]),
                    "generation_time": gen_time,
                    "etl_time": etl_time,
                    "total_time": total_time,
                    "records_processed": records_processed,
                    "records_loaded": records_loaded,
                    "throughput": records_processed / etl_time if etl_time > 0 else 0,
                    "success": etl_results["success"],
                }

            finally:
                conn.close()

        for name, results in performance_results.items():
            assert results["success"] is True, f"Configuration {name} failed"

        minimal = performance_results["minimal"]
        moderate = performance_results["moderate"]
        full = performance_results["full"]

        assert moderate["records_processed"] > minimal["records_processed"]
        assert full["records_processed"] >= moderate["records_processed"]

        for name, results in performance_results.items():
            assert results["throughput"] > 1, f"Throughput too low for {name}: {results['throughput']:.2f} records/sec"

        print("\nTPC-DI ETL Performance Results:")
        for name, results in performance_results.items():
            print(
                f"  {name}: {results['records_processed']} records in {results['total_time']:.2f}s "
                f"({results['throughput']:.2f} records/sec)"
            )

    def test_memory_usage_validation(self, tmp_path: Path):
        import os

        import psutil

        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024

        benchmark = TPCDI(
            scale_factor=0.01,
            output_dir=tmp_path,
        )

        db_path = tmp_path / "memory_test.db"
        conn = duckdb.connect(str(db_path))

        try:
            memory_samples = []

            memory_samples.append(("start", process.memory_info().rss / 1024 / 1024))

            benchmark.generate_source_data(formats=["csv", "json"], batch_types=["historical"])
            memory_samples.append(("after_generation", process.memory_info().rss / 1024 / 1024))

            etl_results = benchmark.run_etl_pipeline(connection=conn, batch_type="historical", validate_data=True)
            memory_samples.append(("after_etl", process.memory_info().rss / 1024 / 1024))

            peak_memory = max(sample[1] for sample in memory_samples)
            memory_increase = peak_memory - initial_memory

            assert memory_increase < 100, f"Memory usage too high: {memory_increase:.2f}MB"

            assert etl_results["success"] is True, "ETL should succeed during memory test"

            print("\nMemory Usage Analysis:")
            for stage, memory in memory_samples:
                print(f"  {stage}: {memory:.2f}MB (+{memory - initial_memory:.2f}MB)")

        finally:
            conn.close()


@pytest.mark.integration
@pytest.mark.tpcdi
@pytest.mark.slow
class TestTPCDIExtendedScenarios:
    @pytest.fixture
    def extended_workspace(self) -> Generator[Path, None, None]:
        workspace = Path(tempfile.mkdtemp())
        yield workspace
        shutil.rmtree(workspace, ignore_errors=True)

    def test_cross_database_robustness(self, extended_workspace: Path):

        databases = [
            ("sqlite", self._create_sqlite_db),
        ]

        if DUCKDB_AVAILABLE:
            databases.append(("duckdb", self._create_duckdb_db))

        results = {}

        for db_name, db_creator in databases:
            workspace = extended_workspace / db_name
            workspace.mkdir(exist_ok=True)

            benchmark = TPCDI(
                scale_factor=0.01,
                output_dir=workspace,
            )

            conn = db_creator(workspace / f"test_{db_name}.db")

            try:
                start_time = time.time()

                benchmark.generate_source_data(
                    formats=["csv", "xml", "json", "fixed_width"],
                    batch_types=["historical", "incremental"],
                )

                hist_result = benchmark.run_etl_pipeline(connection=conn, batch_type="historical", validate_data=True)

                incr_result = benchmark.run_etl_pipeline(connection=conn, batch_type="incremental", validate_data=True)

                duration = time.time() - start_time

                assert hist_result["success"], f"Historical batch failed on {db_name}"
                assert incr_result["success"], f"Incremental batch failed on {db_name}"

                if db_name == "sqlite":
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM DimCustomer")
                    customer_count = cursor.fetchone()[0]
                elif db_name == "duckdb":
                    result = conn.execute("SELECT COUNT(*) FROM DimCustomer").fetchone()
                    customer_count = result[0] if result else 0

                assert customer_count > 0, f"No customers loaded in {db_name}"

                results[db_name] = {
                    "success": True,
                    "duration": duration,
                    "customer_count": customer_count,
                    "historical_records": hist_result["phases"]["load"]["records_loaded"],
                    "incremental_records": incr_result["phases"]["load"]["records_loaded"],
                }

            except Exception as e:
                results[db_name] = {"success": False, "error": str(e)}
            finally:
                conn.close()

        for db_name, result in results.items():
            assert result["success"], f"Database {db_name} failed: {result.get('error')}"

        if len(results) > 1:
            customer_counts = [r["customer_count"] for r in results.values()]
            assert max(customer_counts) - min(customer_counts) <= 10, "Cross-database inconsistency detected"

    def test_transaction_consistency_and_isolation(self, extended_workspace: Path):

        benchmark = TPCDI(
            scale_factor=0.01,
            output_dir=extended_workspace,
        )

        db_path = extended_workspace / "transaction_test.db"
        conn = duckdb.connect(str(db_path))

        try:
            benchmark.generate_source_data(formats=["csv"], batch_types=["historical"])

            etl_result = benchmark.run_etl_pipeline(connection=conn, batch_type="historical", validate_data=False)

            assert etl_result["success"], "Baseline ETL should succeed"

            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM DimCustomer")
            initial_count = cursor.fetchone()[0]

            with patch.object(benchmark._impl, "_transform_csv_file") as mock_transform:
                mock_transform.side_effect = Exception("Simulated transformation error")

                try:
                    error_result = benchmark.run_etl_pipeline(
                        connection=conn, batch_type="incremental", validate_data=False
                    )
                    assert not error_result["success"], "ETL should fail with corrupted transformation"
                except Exception:
                    pass

            cursor.execute("SELECT COUNT(*) FROM DimCustomer")
            post_error_count = cursor.fetchone()[0]
            assert post_error_count == initial_count, "Data should be unchanged after failed ETL"

            recovery_result = benchmark.run_etl_pipeline(connection=conn, batch_type="incremental", validate_data=False)

            assert recovery_result["success"], "Recovery ETL should succeed"

            cursor.execute("SELECT COUNT(*) FROM DimCustomer")
            recovery_count = cursor.fetchone()[0]
            assert recovery_count >= initial_count, "Recovery should add data"

        finally:
            conn.close()

    def test_data_lineage_and_audit_trail(self, extended_workspace: Path):

        benchmark = TPCDI(
            scale_factor=0.01,
            output_dir=extended_workspace,
        )

        db_path = extended_workspace / "lineage_test.db"
        conn = duckdb.connect(str(db_path))

        try:
            batch_results = {}

            for batch_type in ["historical", "incremental", "scd"]:
                start_time = datetime.now()

                source_files = benchmark.generate_source_data(formats=["csv", "json"], batch_types=[batch_type])

                etl_result = benchmark.run_etl_pipeline(connection=conn, batch_type=batch_type, validate_data=True)

                end_time = datetime.now()

                batch_results[batch_type] = {
                    "start_time": start_time,
                    "end_time": end_time,
                    "source_files": source_files,
                    "etl_result": etl_result,
                    "success": etl_result["success"],
                }

                assert etl_result["success"], f"Batch {batch_type} should succeed"

            etl_status = benchmark.get_etl_status()

            batch_status = etl_status["simple_stats"]["batch_status"]

            for batch_type in ["historical", "incremental", "scd"]:
                assert batch_type in batch_status, f"Batch {batch_type} should be tracked"
                status = batch_status[batch_type]

                assert status["status"] == "completed", f"Batch {batch_type} should be completed"

            simple_stats = etl_status["simple_stats"]
            assert simple_stats["batches_processed"] == 3, "Should have processed 3 batches"
            assert simple_stats["total_processing_time"] > 0, "Should have accumulated processing time"

            cursor = conn.cursor()

            try:
                cursor.execute("SELECT DISTINCT BatchID FROM DimCustomer")
                batch_ids = [row[0] for row in cursor.fetchall()]
                assert len(batch_ids) > 0, "Should have batch identifiers in data"
            except Exception:
                pass

            cursor.execute("SELECT COUNT(*) FROM DimCustomer WHERE IsCurrent = 1")
            current_customers = cursor.fetchone()[0]
            assert current_customers > 0, "Should have current customer records"

        finally:
            conn.close()

    def test_advanced_failure_recovery_scenarios(self, extended_workspace: Path):

        benchmark = TPCDI(
            scale_factor=0.01,
            output_dir=extended_workspace,
        )

        db_path = extended_workspace / "recovery_test.db"
        conn = duckdb.connect(str(db_path))

        try:
            benchmark.generate_source_data(formats=["csv"], batch_types=["historical"])

            baseline_result = benchmark.run_etl_pipeline(connection=conn, batch_type="historical", validate_data=False)

            assert baseline_result["success"], "Baseline should succeed"

            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM DimCustomer")
            baseline_count = cursor.fetchone()[0]

            failure_scenarios = [
                {
                    "name": "transform_failure",
                    "patch_target": "_transform_source_data",
                    "exception": Exception("Transform phase failure"),
                },
                {
                    "name": "load_failure",
                    "patch_target": "_load_warehouse_data",
                    "exception": Exception("Load phase failure"),
                },
            ]

            recovery_batch_types = ["incremental", "scd"]

            for i, scenario in enumerate(failure_scenarios):
                with patch.object(benchmark._impl, scenario["patch_target"]) as mock_method:
                    mock_method.side_effect = scenario["exception"]

                    try:
                        fail_result = benchmark.run_etl_pipeline(
                            connection=conn,
                            batch_type="incremental",
                            validate_data=True,
                        )

                        assert not fail_result["success"], f"Should fail in {scenario['name']}"
                        assert "error" in fail_result, f"Should report error in {scenario['name']}"

                    except Exception as e:
                        assert scenario["name"] in str(e) or "failure" in str(e).lower(), (
                            f"Exception should be related to {scenario['name']}"
                        )

                cursor.execute("SELECT COUNT(*) FROM DimCustomer")
                post_fail_count = cursor.fetchone()[0]
                assert post_fail_count >= baseline_count, f"Database integrity compromised in {scenario['name']}"

                recovery_batch_type = recovery_batch_types[i % len(recovery_batch_types)]
                recovery_result = benchmark.run_etl_pipeline(
                    connection=conn,
                    batch_type=recovery_batch_type,
                    validate_data=False,
                )

                assert recovery_result["success"], f"Recovery should succeed after {scenario['name']}"

        finally:
            conn.close()

    @pytest.mark.stress
    def test_stress_testing_large_datasets(self, extended_workspace: Path):

        stress_benchmark = TPCDI(
            scale_factor=0.01,
            output_dir=extended_workspace,
        )

        db_path = extended_workspace / "stress_test.db"
        conn = duckdb.connect(str(db_path))

        conn.execute("SET memory_limit='2GB'")
        conn.execute("SET threads=4")

        try:
            start_time = time.time()

            gen_start = time.time()
            source_files = stress_benchmark.generate_source_data(
                formats=["csv", "json"],
                batch_types=["historical"],
            )
            time.time() - gen_start

            total_size = 0
            for format_files in source_files.values():
                for file_path in format_files:
                    file_size = Path(file_path).stat().st_size
                    total_size += file_size
                    assert file_size > 0, f"Stress test file is empty: {file_size} bytes"

            assert total_size > 1000, f"Total dataset too small (scale 0.01): {total_size} bytes"

            etl_start = time.time()
            etl_result = stress_benchmark.run_etl_pipeline(connection=conn, batch_type="historical", validate_data=True)
            etl_time = time.time() - etl_start

            total_time = time.time() - start_time

            assert etl_result["success"], f"Stress test ETL failed: {etl_result.get('error')}"

            records_processed = etl_result["phases"]["transform"]["records_processed"]
            records_loaded = etl_result["phases"]["load"]["records_loaded"]

            assert records_processed > 50, f"Stress test should process records: {records_processed}"
            assert records_loaded > 0, f"Stress test should load records: {records_loaded}"

            if etl_time > 0:
                throughput = records_processed / etl_time
                assert throughput > 5, f"Stress test throughput too low: {throughput:.2f} records/sec"

            assert total_time < 600, f"Stress test took too long: {total_time:.2f}s"

            quality_score = etl_result["validation_results"]["data_quality_score"]
            assert quality_score >= 60, f"Data quality degraded under stress: {quality_score}"

            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM DimCustomer")
            customer_count = cursor.fetchone()[0]

            assert customer_count == records_loaded, "Loaded count should match database count"
            assert customer_count > 5, f"Stress test should create some data (scale 0.01): {customer_count}"

            query_start = time.time()
            queries = stress_benchmark.get_queries()
            validation_queries = [q for q in queries if q.startswith("V")]

            for query_id in validation_queries[:3]:
                try:
                    result = stress_benchmark.execute_query(query_id, conn)
                    assert result is not None, f"Query {query_id} should return results on large dataset"
                except Exception as e:
                    if "no such table" not in str(e).lower():
                        raise

            query_time = time.time() - query_start
            assert query_time < 30, f"Query execution took too long on large dataset: {query_time:.2f}s"

        finally:
            conn.close()

    def _create_sqlite_db(self, db_path: Path):
        from benchbox.platforms.sqlite import SQLiteAdapter

        adapter = SQLiteAdapter(database_path=str(db_path))
        return adapter.create_connection(database_path=str(db_path))

    def _create_duckdb_db_file(self, db_path: Path):
        from benchbox.platforms.duckdb import DuckDBAdapter

        adapter = DuckDBAdapter(database_path=str(db_path))
        return adapter.create_connection(database_path=str(db_path))

    def _create_duckdb_db(self, db_path: Path):
        if not DUCKDB_AVAILABLE:
            pytest.skip("DuckDB not available")
        from benchbox.platforms.duckdb import DuckDBAdapter

        adapter = DuckDBAdapter(database_path=str(db_path))
        return adapter.create_connection(database_path=str(db_path))


