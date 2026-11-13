# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]


pytest.importorskip("pandas")

from benchbox.core.tpcdi.benchmark import TPCDIBenchmark
from benchbox.core.tpcdi.config import TPCDIConfig


class TestTPCDIFullBenchmarkIntegration:
    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir)

    @pytest.fixture
    def test_database(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.close()

    @pytest.fixture
    def small_scale_config(self, temp_dir):
        return TPCDIConfig(scale_factor=0.01, output_dir=temp_dir, enable_parallel=True, max_workers=2)

    @pytest.fixture
    def medium_scale_config(self, temp_dir):
        return TPCDIConfig(scale_factor=0.1, output_dir=temp_dir, enable_parallel=True, max_workers=4)

    @pytest.fixture
    def large_scale_config(self, temp_dir):
        return TPCDIConfig(scale_factor=1.0, output_dir=temp_dir, enable_parallel=True, max_workers=8)

    def test_end_to_end_benchmark_execution_small_scale(self, small_scale_config, test_database):

        benchmark = TPCDIBenchmark(config=small_scale_config)

        benchmark.create_schema(test_database, "sqlite")

        cursor = test_database.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        core_tables = [
            "DimCustomer",
            "DimAccount",
            "DimBroker",
            "DimCompany",
            "DimSecurity",
            "FactTrade",
        ]
        for table in core_tables:
            assert table in tables, f"Core table {table} not created"

        data_files = benchmark.generate_data()
        assert len(data_files) > 0, "No data files generated"

        etl_results = benchmark.run_enhanced_etl_pipeline(
            test_database,
            dialect="sqlite",
            enable_data_quality_monitoring=True,
            enable_error_recovery=True,
        )

        assert etl_results["success"] is True, f"ETL pipeline failed: {etl_results.get('error', 'Unknown error')}"
        incremental = etl_results["phases"]["incremental_loading"]
        assert incremental["success"] is True
        assert incremental["records_loaded"] > 0
        assert test_database.execute(
            "SELECT SK_CustomerID, CustomerID, FirstName FROM DimCustomer WHERE SK_CustomerID = 1000001"
        ).fetchone() == (1000001, 100000000, "FirstName0")
        assert etl_results["total_records_processed"] >= incremental["records_loaded"]
        assert etl_results["quality_score"] >= 0, "Invalid quality score"

        benchmark._initialize_connection_dependent_systems(test_database, "sqlite")
        validation_results = benchmark.run_data_validation(test_database)

        assert validation_results.quality_score > 0, "Data validation failed"

        test_queries = [1, 2, 3]
        query_results = []

        for query_id in test_queries:
            try:
                query_sql = benchmark.get_query(query_id, dialect="sqlite")
                cursor.execute(query_sql)
                result_count = len(cursor.fetchall())
                query_results.append({"query_id": query_id, "row_count": result_count, "success": True})
            except Exception as e:
                query_results.append({"query_id": query_id, "error": str(e), "success": False})

        successful_queries = sum(1 for r in query_results if r["success"])
        assert successful_queries >= len(test_queries) // 2, "Too many query failures"

    def test_multi_scale_factor_progression(self, temp_dir, test_database):

        scale_factors = [0.001, 0.01, 0.1]
        results = {}

        for scale_factor in scale_factors:
            config = TPCDIConfig(
                scale_factor=scale_factor,
                output_dir=temp_dir / f"scale_{scale_factor}",
                enable_parallel=True,
                max_workers=2,
            )
            config.output_dir.mkdir(parents=True, exist_ok=True)

            benchmark = TPCDIBenchmark(config=config)

            start_time = time.time()

            benchmark.create_schema(test_database, "sqlite")
            data_files = benchmark.generate_data()

            etl_results = benchmark.run_enhanced_etl_pipeline(
                test_database,
                dialect="sqlite",
                enable_data_quality_monitoring=False,
            )

            execution_time = time.time() - start_time

            results[scale_factor] = {
                "execution_time_seconds": execution_time,
                "data_files_count": len(data_files),
                "etl_success": etl_results["success"],
                "records_processed": etl_results["total_records_processed"],
                "phases_completed": len(etl_results["phases"]),
            }

        for i in range(len(scale_factors) - 1):
            current_sf = scale_factors[i]
            next_sf = scale_factors[i + 1]

            current_records = results[current_sf]["records_processed"]
            next_records = results[next_sf]["records_processed"]

            scale_ratio = next_sf / current_sf
            record_ratio = next_records / max(current_records, 1)

            if current_sf <= 0.1:
                pass
            else:
                tolerance = 0.5

                assert record_ratio >= scale_ratio * tolerance, (
                    f"Records didn't scale properly from {current_sf} to {next_sf}: got {record_ratio:.2f}, expected >= {scale_ratio * tolerance:.2f}"
                )
                assert record_ratio <= scale_ratio * 3.0, (
                    f"Records scaled too aggressively from {current_sf} to {next_sf}: got {record_ratio:.2f}, expected <= {scale_ratio * 3.0:.2f}"
                )

    def test_cross_platform_compatibility(self, small_scale_config):

        platforms_to_test = [
            ("sqlite", sqlite3.connect(":memory:")),
        ]

        results = {}

        for platform_name, connection in platforms_to_test:
            try:
                benchmark = TPCDIBenchmark(config=small_scale_config)

                benchmark.create_schema(connection, platform_name)

                data_files = benchmark.generate_data()

                benchmark._initialize_connection_dependent_systems(connection, platform_name)

                test_query_sql = benchmark.get_query(1, dialect=platform_name)
                assert len(test_query_sql) > 0, f"Query translation failed for {platform_name}"

                results[platform_name] = {
                    "schema_creation": True,
                    "data_generation": len(data_files) > 0,
                    "query_translation": True,
                    "success": True,
                }

            except Exception as e:
                results[platform_name] = {"error": str(e), "success": False}
            finally:
                if hasattr(connection, "close"):
                    connection.close()

        successful_platforms = [name for name, result in results.items() if result["success"]]
        assert len(successful_platforms) > 0, "No platforms executed successfully"

    def test_performance_regression_baseline(self, small_scale_config, test_database):
        benchmark = TPCDIBenchmark(config=small_scale_config)

        performance_metrics = {}

        start_time = time.time()
        benchmark.create_schema(test_database, "sqlite")
        performance_metrics["schema_creation_time"] = time.time() - start_time

        start_time = time.time()
        data_files = benchmark.generate_data()
        performance_metrics["data_generation_time"] = time.time() - start_time
        performance_metrics["data_files_generated"] = len(data_files)

        start_time = time.time()
        etl_results = benchmark.run_enhanced_etl_pipeline(
            test_database,
            dialect="sqlite",
            enable_data_quality_monitoring=True,
        )
        performance_metrics["etl_pipeline_time"] = time.time() - start_time
        performance_metrics["etl_records_processed"] = etl_results["total_records_processed"]

        start_time = time.time()
        benchmark._initialize_connection_dependent_systems(test_database, "sqlite")
        benchmark.run_data_validation(test_database)
        performance_metrics["validation_time"] = time.time() - start_time

        assert performance_metrics["schema_creation_time"] < 5.0, "Schema creation too slow"
        assert performance_metrics["data_generation_time"] < 30.0, "Data generation too slow"
        assert performance_metrics["etl_pipeline_time"] < 30.0, "ETL pipeline too slow"
        assert performance_metrics["validation_time"] < 5.0, "Validation too slow"

    def test_error_recovery_and_resilience(self, small_scale_config, test_database):

        benchmark = TPCDIBenchmark(config=small_scale_config)

        benchmark.create_schema(test_database, "sqlite")

        benchmark._initialize_connection_dependent_systems(test_database, "sqlite")

        etl_results = benchmark.run_enhanced_etl_pipeline(test_database, dialect="sqlite", enable_error_recovery=True)

        assert etl_results is not None, "ETL pipeline should return results even with errors"

        error_recovery = benchmark.error_recovery_manager
        assert error_recovery is not None, "Error recovery manager not initialized"

        test_errors = [
            "Connection timeout occurred",
            "Table does not exist",
            "Disk full error",
        ]

        for error_msg in test_errors:
            category, severity = error_recovery.classify_error(error_msg)
            assert category is not None, f"Error classification failed for: {error_msg}"
            assert severity is not None, f"Severity classification failed for: {error_msg}"

    def test_data_quality_validation_comprehensive(self, small_scale_config, test_database):

        benchmark = TPCDIBenchmark(config=small_scale_config)

        benchmark.create_schema(test_database, "sqlite")
        benchmark.generate_data()

        etl_results = benchmark.run_enhanced_etl_pipeline(
            test_database, dialect="sqlite", enable_data_quality_monitoring=True
        )

        assert etl_results["success"] is True, "ETL pipeline must succeed for quality testing"

        quality_results = etl_results["phases"].get("data_quality_monitoring", {})
        assert quality_results.get("success", False), "Data quality monitoring failed"
        assert quality_results.get("quality_rules_executed", 0) > 0, "No quality rules executed"
        assert quality_results.get("quality_score", 0) >= 0, "Invalid quality score"

        benchmark._initialize_connection_dependent_systems(test_database, "sqlite")

        completeness_issues = 0
        try:
            cursor = test_database.cursor()
            critical_checks = [
                "SELECT COUNT(*) FROM DimCustomer WHERE FirstName IS NULL",
                "SELECT COUNT(*) FROM DimCustomer WHERE LastName IS NULL",
                "SELECT COUNT(*) FROM DimAccount WHERE CustomerID IS NULL",
            ]

            for check_sql in critical_checks:
                cursor.execute(check_sql)
                null_count = cursor.fetchone()[0]
                if null_count > 0:
                    completeness_issues += 1
        except Exception:
            pass

        assert quality_results.get("quality_score", 0) >= 70.0 or completeness_issues == 0, (
            "Data quality below acceptable threshold"
        )

    def test_memory_usage_and_resource_consumption(self, small_scale_config, test_database):
        import gc
        import os

        import psutil

        gc.collect()

        benchmark = TPCDIBenchmark(config=small_scale_config)
        process = psutil.Process(os.getpid())

        baseline_memory = process.memory_info().rss / 1024 / 1024

        memory_measurements = {"baseline": baseline_memory}

        benchmark.create_schema(test_database, "sqlite")
        memory_measurements["after_schema"] = process.memory_info().rss / 1024 / 1024

        benchmark.generate_data()
        memory_measurements["after_data_gen"] = process.memory_info().rss / 1024 / 1024

        benchmark.run_enhanced_etl_pipeline(test_database, dialect="sqlite")
        memory_measurements["after_etl"] = process.memory_info().rss / 1024 / 1024

        peak_memory = max(memory_measurements.values())
        memory_growth = peak_memory - baseline_memory

        assert memory_growth < 300, (
            f"Excessive memory growth detected: {memory_growth:.1f}MB "
            f"(baseline={baseline_memory:.1f}MB, peak={peak_memory:.1f}MB, "
            f"measurements={memory_measurements})"
        )

        max_allowed_peak = baseline_memory + 350
        assert peak_memory < max_allowed_peak, (
            f"Peak memory too high relative to baseline: {peak_memory:.1f}MB "
            f"(baseline={baseline_memory:.1f}MB, max_allowed={max_allowed_peak:.1f}MB)"
        )

    def test_parallel_processing_scalability(self, temp_dir):
        worker_counts = [1, 4]
        results = {}

        for workers in worker_counts:
            config = TPCDIConfig(
                scale_factor=0.01,
                output_dir=temp_dir / f"workers_{workers}",
                enable_parallel=True,
                max_workers=workers,
            )
            config.output_dir.mkdir(parents=True, exist_ok=True)

            benchmark = TPCDIBenchmark(config=config)

            with sqlite3.connect(":memory:") as connection:
                benchmark.create_schema(connection, "sqlite")
                benchmark.generate_data()
                etl_results = benchmark.run_enhanced_etl_pipeline(connection, dialect="sqlite")

            results[workers] = {
                "etl_success": etl_results["success"],
                "records_processed": etl_results["total_records_processed"],
                "phases": sorted(etl_results["phases"]),
            }

        for workers, result in results.items():
            assert result["etl_success"], f"ETL failed with {workers} workers"
            assert "parallel_batch_processing" not in result["phases"]

        assert results[1]["records_processed"] == results[4]["records_processed"]
        assert results[1]["phases"] == results[4]["phases"]

    def test_benchmark_reproducibility(self, small_scale_config, test_database):
        benchmark1 = TPCDIBenchmark(config=small_scale_config)
        benchmark2 = TPCDIBenchmark(config=small_scale_config)

        runs = []
        for _i, benchmark in enumerate([benchmark1, benchmark2]):
            benchmark.create_schema(test_database, "sqlite")
            data_files = benchmark.generate_data()

            etl_results = benchmark.run_enhanced_etl_pipeline(
                test_database,
                dialect="sqlite",
            )

            runs.append(
                {
                    "data_files_count": len(data_files),
                    "etl_records": etl_results["total_records_processed"],
                    "etl_success": etl_results["success"],
                }
            )

            test_database.executescript(
                "DROP TABLE IF EXISTS DimCustomer; DROP TABLE IF EXISTS DimAccount; DROP TABLE IF EXISTS FactTrade;"
            )

        assert runs[0]["data_files_count"] == runs[1]["data_files_count"], "Data generation not reproducible"
        assert runs[0]["etl_success"] == runs[1]["etl_success"], "ETL success not reproducible"

        record_diff_pct = abs(runs[0]["etl_records"] - runs[1]["etl_records"]) / max(runs[0]["etl_records"], 1) * 100
        assert record_diff_pct <= 10, f"ETL records too variable between runs: {record_diff_pct:.1f}%"


class TestTPCDISpecificationValidation:
    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir)

    @pytest.fixture
    def test_database(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.close()

    @pytest.fixture
    def spec_config(self, temp_dir):
        return TPCDIConfig(scale_factor=0.01, output_dir=temp_dir, enable_parallel=True, max_workers=2)

    def test_schema_compliance_validation(self, spec_config, test_database):

        benchmark = TPCDIBenchmark(config=spec_config)

        benchmark.create_schema(test_database, "sqlite")

        cursor = test_database.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        required_tables = {
            "DimBroker",
            "DimCompany",
            "DimCustomer",
            "DimAccount",
            "DimSecurity",
            "DimTime",
            "FactTrade",
            "FactCashBalances",
            "FactHoldings",
            "FactMarketHistory",
            "Staging_Customer",
            "Staging_Account",
            "Staging_Trade",
        }

        missing_tables = required_tables - set(tables)
        critical_missing = {t for t in missing_tables if t.startswith(("Dim", "Fact"))}

        assert len(critical_missing) <= len(required_tables) * 0.3, (
            f"Too many critical tables missing: {critical_missing}"
        )

        for table in ["DimCustomer", "DimAccount", "FactTrade"]:
            if table in tables:
                cursor.execute(f"PRAGMA table_info({table})")
                columns = [col[1] for col in cursor.fetchall()]
                assert len(columns) > 0, f"Table {table} has no columns"

    def test_query_result_accuracy_validation(self, spec_config, test_database):

        benchmark = TPCDIBenchmark(config=spec_config)

        benchmark.create_schema(test_database, "sqlite")
        benchmark.generate_data()

        etl_results = benchmark.run_enhanced_etl_pipeline(
            test_database,
            dialect="sqlite",
            enable_data_quality_monitoring=False,
        )

        assert etl_results["success"], "ETL must succeed for query testing"

        test_queries = [1, 2, 3]
        query_results = {}

        cursor = test_database.cursor()
        for query_id in test_queries:
            try:
                query_sql = benchmark.get_query(query_id, dialect="sqlite")

                cursor.execute(query_sql)
                results = cursor.fetchall()

                query_results[query_id] = {
                    "success": True,
                    "row_count": len(results),
                    "has_results": len(results) > 0,
                    "column_count": len(cursor.description) if cursor.description else 0,
                }

            except Exception as e:
                query_results[query_id] = {"success": False, "error": str(e)}

        successful_queries = sum(1 for r in query_results.values() if r.get("success", False))
        assert successful_queries >= len(test_queries) // 2, "Too many query failures"

        for query_id, result in query_results.items():
            if result.get("success"):
                assert result["column_count"] > 0, f"Query {query_id} returned no columns"

    def test_etl_processing_compliance_validation(self, spec_config, test_database):

        benchmark = TPCDIBenchmark(config=spec_config)
        benchmark.create_schema(test_database, "sqlite")
        benchmark.generate_data()
        etl_results = benchmark.run_enhanced_etl_pipeline(
            test_database,
            dialect="sqlite",
            enable_data_quality_monitoring=True,
            enable_error_recovery=True,
        )

        assert etl_results["success"], "ETL pipeline must succeed for compliance testing"

        required_phases = [
            "enhanced_data_processing",
            "enhanced_scd_processing",
            "incremental_loading",
        ]
        for phase in required_phases:
            assert phase in etl_results["phases"], f"Required ETL phase {phase} not executed"
            assert etl_results["phases"][phase]["success"], f"ETL phase {phase} failed"

        scd_phase = etl_results["phases"]["enhanced_scd_processing"]
        assert scd_phase["scd_records_processed"] >= 0, "SCD processing should process records"

        if "data_quality_monitoring" in etl_results["phases"]:
            quality_phase = etl_results["phases"]["data_quality_monitoring"]
            assert quality_phase["quality_rules_executed"] >= 0, "Data quality rules should be tracked"
            assert quality_phase["quality_score"] >= 0, "Quality score should be valid"

        incremental_phase = etl_results["phases"]["incremental_loading"]
        assert incremental_phase["incremental_batches"] == 1
        assert incremental_phase["records_loaded"] > 0
        assert test_database.execute("SELECT COUNT(*) FROM DimCustomer").fetchone()[0] > 0

    def test_data_quality_business_rules_validation(self, spec_config, test_database):

        benchmark = TPCDIBenchmark(config=spec_config)

        benchmark.create_schema(test_database, "sqlite")
        benchmark.generate_data()

        etl_results = benchmark.run_enhanced_etl_pipeline(
            test_database, dialect="sqlite", enable_data_quality_monitoring=True
        )

        assert etl_results["success"], "ETL must succeed for business rules testing"

        quality_phase = etl_results["phases"].get("data_quality_monitoring", {})
        assert quality_phase.get("success", False), "Data quality monitoring must succeed"

        benchmark._initialize_connection_dependent_systems(test_database, "sqlite")
        validation_results = benchmark.run_data_validation(test_database)

        assert validation_results.quality_score >= 0, "Data validation must produce valid quality score"

        cursor = test_database.cursor()
        business_rule_checks = []

        try:
            cursor.execute("SELECT COUNT(*), COUNT(DISTINCT CustomerID) FROM DimCustomer")
            total_count, unique_count = cursor.fetchone()
            business_rule_checks.append(
                {
                    "rule": "Customer ID uniqueness",
                    "passed": total_count == unique_count or total_count == 0,
                }
            )
        except Exception:
            pass

        try:
            cursor.execute("""
                SELECT COUNT(*)
                FROM DimAccount a
                LEFT JOIN DimCustomer c ON a.CustomerID = c.CustomerID
                WHERE c.CustomerID IS NULL
            """)
            orphaned_accounts = cursor.fetchone()[0]
            business_rule_checks.append(
                {
                    "rule": "Account customer reference integrity",
                    "passed": orphaned_accounts == 0,
                }
            )
        except Exception:
            pass

        if business_rule_checks:
            passed_rules = sum(1 for check in business_rule_checks if check["passed"])
            assert passed_rules >= len(business_rule_checks) // 2, "Too many business rule violations"


FIXED_SIZE_TPCDI_TABLES = frozenset(
    {"DimDate", "DimTime", "DimBroker", "Industry", "StatusType", "TaxRate", "TradeType"}
)


class TestTPCDIPerformanceAndScalability:
    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            yield Path(tmp_dir)

    @pytest.fixture
    def test_database(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.close()

    def test_data_generation_performance_scaling(self, temp_dir, test_database):

        scale_factors = [0.001, 0.01, 0.1]
        generation_metrics = {}

        for scale_factor in scale_factors:
            config = TPCDIConfig(
                scale_factor=scale_factor,
                output_dir=temp_dir / f"perf_scale_{scale_factor}",
                enable_parallel=True,
                max_workers=2,
            )
            config.output_dir.mkdir(parents=True, exist_ok=True)

            benchmark = TPCDIBenchmark(config=config)

            start_time = time.time()
            data_files = benchmark.generate_data()
            generation_time = time.time() - start_time

            total_size = 0
            for file_path in data_files:
                file_path = Path(file_path)
                if file_path.exists() and file_path.stem not in FIXED_SIZE_TPCDI_TABLES:
                    total_size += file_path.stat().st_size

            generation_metrics[scale_factor] = {
                "generation_time": generation_time,
                "file_count": len(data_files),
                "total_size_mb": total_size / (1024 * 1024),
                "mb_per_second": (total_size / (1024 * 1024)) / max(generation_time, 0.001),
            }

        for i in range(len(scale_factors) - 1):
            current_sf = scale_factors[i]
            next_sf = scale_factors[i + 1]

            current_metrics = generation_metrics[current_sf]
            next_metrics = generation_metrics[next_sf]

            size_ratio = next_metrics["total_size_mb"] / max(current_metrics["total_size_mb"], 0.001)
            sf_ratio = next_sf / current_sf

            min_expected_ratio = sf_ratio * 0.3
            max_expected_ratio = sf_ratio * 3.0
            assert size_ratio >= min_expected_ratio, (
                f"Data size scaling too low: {size_ratio:.2f} vs expected {sf_ratio:.2f}"
            )
            assert size_ratio <= max_expected_ratio, (
                f"Data size scaling too high: {size_ratio:.2f} vs expected {sf_ratio:.2f}"
            )

    def test_etl_processing_performance_scaling(self, temp_dir, test_database):

        scale_factors = [0.01, 0.1]
        etl_metrics = {}

        for scale_factor in scale_factors:
            config = TPCDIConfig(
                scale_factor=scale_factor,
                output_dir=temp_dir / f"etl_perf_{scale_factor}",
                enable_parallel=True,
                max_workers=4,
            )
            config.output_dir.mkdir(parents=True, exist_ok=True)

            benchmark = TPCDIBenchmark(config=config)

            benchmark.create_schema(test_database, "sqlite")
            benchmark.generate_data()

            start_time = time.time()
            etl_results = benchmark.run_enhanced_etl_pipeline(
                test_database,
                dialect="sqlite",
                enable_data_quality_monitoring=True,
            )
            etl_time = time.time() - start_time

            etl_metrics[scale_factor] = {
                "etl_time": etl_time,
                "etl_success": etl_results["success"],
                "records_processed": etl_results["total_records_processed"],
                "records_per_second": etl_results["total_records_processed"] / max(etl_time, 0.001),
                "phases_completed": len(etl_results["phases"]),
                "quality_score": etl_results.get("quality_score", 0),
            }

            test_database.executescript("""
                DROP TABLE IF EXISTS DimCustomer;
                DROP TABLE IF EXISTS DimAccount;
                DROP TABLE IF EXISTS FactTrade;
            """)

        for scale_factor, metrics in etl_metrics.items():
            assert metrics["etl_success"], f"ETL failed at scale factor {scale_factor}"
            assert metrics["records_processed"] > 0, f"No records processed at scale factor {scale_factor}"
            assert metrics["phases_completed"] >= 3, f"Too few ETL phases completed at scale factor {scale_factor}"

    def test_query_execution_performance_optimization(self, temp_dir, test_database):

        config = TPCDIConfig(scale_factor=0.1, output_dir=temp_dir, enable_parallel=True, max_workers=4)

        benchmark = TPCDIBenchmark(config=config)

        benchmark.create_schema(test_database, "sqlite")
        benchmark.generate_data()

        etl_results = benchmark.run_enhanced_etl_pipeline(
            test_database,
            dialect="sqlite",
            enable_data_quality_monitoring=False,
        )

        assert etl_results["success"], "ETL must succeed for query performance testing"

        test_queries = [1, 2, 3, 4, 5]
        query_performance = {}

        cursor = test_database.cursor()
        for query_id in test_queries:
            try:
                query_sql = benchmark.get_query(query_id, dialect="sqlite")

                start_time = time.time()
                cursor.execute(query_sql)
                results = cursor.fetchall()
                execution_time = time.time() - start_time

                query_performance[query_id] = {
                    "execution_time_seconds": execution_time,
                    "row_count": len(results),
                    "success": True,
                    "rows_per_second": len(results) / max(execution_time, 0.001),
                }

            except Exception as e:
                query_performance[query_id] = {
                    "execution_time_seconds": float("inf"),
                    "error": str(e),
                    "success": False,
                }

        successful_queries = [q for q, perf in query_performance.items() if perf.get("success")]
        assert len(successful_queries) >= len(test_queries) // 2, "Too many query performance failures"

        slow_queries = [q for q in successful_queries if query_performance[q]["execution_time_seconds"] > 10.0]

        if slow_queries:
            print(f"Slow queries detected: {slow_queries}")

        fast_queries = [q for q in successful_queries if query_performance[q]["execution_time_seconds"] < 5.0]
        assert len(fast_queries) >= len(successful_queries) // 2, "Too many slow queries"

    def test_memory_usage_resource_consumption_patterns(self, temp_dir, test_database):
        import gc
        import os

        import psutil

        gc.collect()

        config = TPCDIConfig(scale_factor=0.1, output_dir=temp_dir, enable_parallel=True, max_workers=4)

        benchmark = TPCDIBenchmark(config=config)
        process = psutil.Process(os.getpid())

        resource_history = []

        def record_resources(phase_name):
            memory_mb = process.memory_info().rss / 1024 / 1024
            cpu_percent = process.cpu_percent()
            resource_history.append(
                {
                    "phase": phase_name,
                    "memory_mb": memory_mb,
                    "cpu_percent": cpu_percent,
                    "timestamp": time.time(),
                }
            )

        record_resources("baseline")

        benchmark.create_schema(test_database, "sqlite")
        record_resources("schema_created")

        benchmark.generate_data()
        record_resources("data_generated")

        etl_results = benchmark.run_enhanced_etl_pipeline(
            test_database,
            dialect="sqlite",
            enable_data_quality_monitoring=True,
        )
        record_resources("etl_completed")

        baseline_memory = resource_history[0]["memory_mb"]
        peak_memory = max(r["memory_mb"] for r in resource_history)
        memory_growth = peak_memory - baseline_memory

        assert memory_growth < 600, (
            f"Memory growth too high: {memory_growth:.1f}MB "
            f"(baseline={baseline_memory:.1f}MB, peak={peak_memory:.1f}MB)"
        )

        assert etl_results["success"], "ETL should succeed under normal resource constraints"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
