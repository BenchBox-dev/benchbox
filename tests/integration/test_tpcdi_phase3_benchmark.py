# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


pytest.importorskip("pandas")

from benchbox.core.tpcdi.benchmark import TPCDIBenchmark
from benchbox.core.tpcdi.config import TPCDIConfig
from benchbox.utils.clock import elapsed_seconds, mono_time


class TestTPCDIPhase3BenchmarkIntegration:
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
    def tpcdi_config(self, temp_dir):
        return TPCDIConfig(scale_factor=0.01, output_dir=temp_dir, enable_parallel=True, max_workers=2)

    @pytest.fixture
    def tpcdi_benchmark(self, tpcdi_config):
        return TPCDIBenchmark(config=tpcdi_config)

    def test_enhanced_benchmark_initialization(self, tpcdi_benchmark):
        assert tpcdi_benchmark is not None
        assert hasattr(tpcdi_benchmark, "run_enhanced_etl_pipeline")
        assert hasattr(tpcdi_benchmark, "get_enhanced_etl_status")

        assert tpcdi_benchmark.finwire_processor is None
        assert tpcdi_benchmark.customer_mgmt_processor is None
        assert tpcdi_benchmark.scd_processor is None
        assert tpcdi_benchmark.incremental_loader is None
        assert tpcdi_benchmark.data_quality_monitor is None
        assert tpcdi_benchmark.error_recovery_manager is None

    def test_connection_dependent_systems_initialization(self, tpcdi_benchmark, test_database):
        tpcdi_benchmark._initialize_connection_dependent_systems(test_database, "sqlite")

        assert tpcdi_benchmark.finwire_processor is not None
        assert tpcdi_benchmark.customer_mgmt_processor is not None
        assert tpcdi_benchmark.scd_processor is not None
        assert tpcdi_benchmark.incremental_loader is not None
        assert tpcdi_benchmark.data_quality_monitor is not None
        assert tpcdi_benchmark.error_recovery_manager is not None

    def test_enhanced_etl_status_reporting(self, tpcdi_benchmark, test_database):

        tpcdi_benchmark._initialize_connection_dependent_systems(test_database, "sqlite")

        status = tpcdi_benchmark.get_enhanced_etl_status()

        assert "phase_3_components" in status
        assert "enhanced_features" in status
        assert "basic_etl_status" in status

        phase3_components = status["phase_3_components"]
        assert phase3_components["finwire_processor"] is True
        assert "parallel_batch_processor" not in phase3_components
        assert phase3_components["customer_mgmt_processor"] is True
        assert phase3_components["scd_processor"] is True
        assert phase3_components["incremental_loader"] is True
        assert phase3_components["data_quality_monitor"] is True
        assert phase3_components["error_recovery_manager"] is True

        enhanced_features = status["enhanced_features"]
        assert "parallel_processing_enabled" in enhanced_features
        assert "max_workers" in enhanced_features
        assert "scale_factor" in enhanced_features

    def test_enhanced_etl_pipeline_execution(self, tpcdi_benchmark, test_database):
        tpcdi_benchmark.create_schema(test_database, "sqlite")

        results = tpcdi_benchmark.run_enhanced_etl_pipeline(
            test_database,
            dialect="sqlite",
            enable_data_quality_monitoring=True,
            enable_error_recovery=True,
        )

        assert "start_time" in results
        assert "end_time" in results
        assert "total_duration" in results
        assert "success" in results
        assert "phases" in results
        assert "enhanced_features" in results
        assert "total_records_processed" in results
        assert "quality_score" in results

        enhanced_features = results["enhanced_features"]
        assert enhanced_features["data_quality_monitoring"] is True
        assert enhanced_features["error_recovery"] is True

        phases = results["phases"]
        assert "enhanced_data_processing" in phases
        assert "enhanced_scd_processing" in phases
        assert "parallel_batch_processing" not in phases
        assert "incremental_loading" in phases
        assert "data_quality_monitoring" in phases

        assert results["success"] is True
        assert results["failed_phases"] == []
        assert results["total_records_processed"] > 0
        assert results["quality_score"] > 0
        assert test_database.execute(
            "SELECT CustomerID, FirstName FROM DimCustomer WHERE SK_CustomerID = 1000001"
        ).fetchone() == (100000000, "FirstName0")

    def test_enhanced_data_processing_phase(self, tpcdi_benchmark, test_database):

        tpcdi_benchmark.create_schema(test_database, "sqlite")
        tpcdi_benchmark._initialize_connection_dependent_systems(test_database, "sqlite")

        with (
            patch.object(
                tpcdi_benchmark.finwire_processor,
                "process_finwire_file",
                return_value={"success": True, "records_processed": 10, "errors": []},
            ),
            patch.object(
                tpcdi_benchmark.customer_mgmt_processor,
                "process_customer_management_file",
                return_value={"success": True, "records_processed": 5, "errors": []},
            ),
        ):
            results = tpcdi_benchmark._run_enhanced_data_processing()

        assert results["finwire_records"] > 0
        assert results["customer_mgmt_records"] > 0
        assert results["total_records"] > 0
        assert results["total_records"] == results["finwire_records"] + results["customer_mgmt_records"]

    def test_enhanced_scd_processing_phase(self, tpcdi_benchmark, test_database):
        tpcdi_benchmark.create_schema(test_database, "sqlite")
        tpcdi_benchmark._initialize_connection_dependent_systems(test_database, "sqlite")
        tpcdi_benchmark.generate_data()
        tpcdi_benchmark.load_data_to_database(test_database)

        with patch.object(
            tpcdi_benchmark.scd_processor,
            "process_dimension",
            return_value={"success": True, "records_processed": 20, "changes_detected": 5},
        ):
            results = tpcdi_benchmark._run_enhanced_scd_processing(test_database)

        assert results["success"] is True
        assert results["records_processed"] > 0
        assert results["changes_detected"] >= 0

    def test_incremental_data_loading_phase(self, tpcdi_benchmark, test_database):

        tpcdi_benchmark.create_schema(test_database, "sqlite")
        before = test_database.execute("SELECT COUNT(*) FROM DimCustomer").fetchone()[0]

        results = tpcdi_benchmark._run_incremental_data_loading(test_database)
        after = test_database.execute("SELECT COUNT(*) FROM DimCustomer").fetchone()[0]

        assert results["success"] is True
        assert results["batches_loaded"] == 1
        assert after > before
        assert results["records_loaded"] >= after - before
        assert test_database.execute(
            "SELECT CustomerID, FirstName FROM DimCustomer WHERE SK_CustomerID = 1000001"
        ).fetchone() == (100000000, "FirstName0")

    def test_incremental_data_loading_reports_source_failure(self, tpcdi_benchmark, test_database):
        with patch.object(
            tpcdi_benchmark,
            "run_etl_pipeline",
            return_value={"success": False, "error": "source batch is corrupt", "phases": {}},
        ):
            results = tpcdi_benchmark._run_incremental_data_loading(test_database)

        assert results == {
            "success": False,
            "batches_loaded": 0,
            "records_loaded": 0,
            "error": "source batch is corrupt",
        }

    def test_data_quality_monitoring_phase(self, tpcdi_benchmark, test_database):

        tpcdi_benchmark.create_schema(test_database, "sqlite")
        tpcdi_benchmark._initialize_connection_dependent_systems(test_database, "sqlite")
        tpcdi_benchmark.generate_data()
        tpcdi_benchmark.load_data_to_database(test_database)

        with patch.object(
            tpcdi_benchmark.data_quality_monitor,
            "execute_quality_checks",
            return_value={"rules_executed": 5, "overall_pass_rate": 0.8, "rules_failed": 1},
        ):
            results = tpcdi_benchmark._run_data_quality_monitoring(test_database)

        assert results["success"] is True
        assert results["rules_executed"] > 0
        assert results["quality_score"] > 0
        assert results["issues_detected"] >= 0

    def test_enhanced_pipeline_with_monitoring_disabled(self, tpcdi_benchmark, test_database):

        tpcdi_benchmark.create_schema(test_database, "sqlite")

        results = tpcdi_benchmark.run_enhanced_etl_pipeline(
            test_database,
            dialect="sqlite",
            enable_data_quality_monitoring=False,
            enable_error_recovery=True,
        )

        assert "data_quality_monitoring" not in results["phases"]
        assert results["enhanced_features"]["data_quality_monitoring"] is False
        assert results["quality_score"] == 0.0

        assert "enhanced_data_processing" in results["phases"]
        assert "enhanced_scd_processing" in results["phases"]
        assert "incremental_loading" in results["phases"]

    def test_error_recovery_integration(self, tpcdi_benchmark, test_database):

        tpcdi_benchmark._initialize_connection_dependent_systems(test_database, "sqlite")

        with patch.object(tpcdi_benchmark, "_run_enhanced_data_processing") as mock_processing:
            mock_processing.side_effect = ValueError("Simulated processing error")

            with patch.object(tpcdi_benchmark, "error_recovery_manager", create=True) as mock_recovery:
                mock_recovery.handle_pipeline_error.return_value = {"action": "logged", "should_retry": False}
                results = tpcdi_benchmark.run_enhanced_etl_pipeline(
                    test_database, dialect="sqlite", enable_error_recovery=True
                )
                assert results["success"] is False
                assert "error" in results
                mock_recovery.handle_pipeline_error.assert_called()
            mock_processing.assert_called_once()

    @pytest.mark.parametrize(
        ("failed_method", "failed_phase"),
        [
            ("_run_enhanced_data_processing", "enhanced_data_processing"),
            ("_run_enhanced_scd_processing", "enhanced_scd_processing"),
            ("_run_incremental_data_loading", "incremental_loading"),
        ],
    )
    def test_requested_phase_failure_fails_pipeline_with_identity_and_cause(
        self, tpcdi_benchmark, test_database, failed_method, failed_phase
    ):
        successful_results = {
            "_run_enhanced_data_processing": {"success": True, "total_records": 1},
            "_run_enhanced_scd_processing": {"success": True, "records_processed": 1},
            "_run_incremental_data_loading": {"success": True, "records_loaded": 1},
        }
        with (
            patch.object(tpcdi_benchmark, "_initialize_connection_dependent_systems"),
            patch.object(
                tpcdi_benchmark,
                "_run_enhanced_data_processing",
                return_value=successful_results["_run_enhanced_data_processing"],
            ),
            patch.object(
                tpcdi_benchmark,
                "_run_enhanced_scd_processing",
                return_value=successful_results["_run_enhanced_scd_processing"],
            ),
            patch.object(
                tpcdi_benchmark,
                "_run_incremental_data_loading",
                return_value=successful_results["_run_incremental_data_loading"],
            ),
        ):
            with patch.object(tpcdi_benchmark, failed_method, return_value={"success": False, "error": "boom"}):
                results = tpcdi_benchmark.run_enhanced_etl_pipeline(
                    test_database,
                    dialect="sqlite",
                    enable_data_quality_monitoring=False,
                    enable_error_recovery=False,
                )

        assert results["success"] is False
        assert results["failed_phases"] == [{"phase": failed_phase, "error": "boom"}]
        assert results["phases"][failed_phase]["success"] is False

    def test_enhanced_pipeline_performance_metrics(self, tpcdi_benchmark, test_database):

        tpcdi_benchmark.create_schema(test_database, "sqlite")

        results = tpcdi_benchmark.run_enhanced_etl_pipeline(test_database, dialect="sqlite")

        assert "total_duration" in results
        assert results["total_duration"] > 0

        phases = results["phases"]
        for _phase_name, phase_result in phases.items():
            assert "duration" in phase_result
            assert phase_result["duration"] >= 0

    def test_component_configuration_integration(self, temp_dir):

        custom_config = TPCDIConfig(
            scale_factor=0.005,
            output_dir=temp_dir,
            enable_parallel=False,
            max_workers=1,
        )

        benchmark = TPCDIBenchmark(config=custom_config)

        assert benchmark.scale_factor == 0.005
        assert benchmark.enable_parallel is False
        assert benchmark.max_workers == 1

        parallel_config = TPCDIConfig(scale_factor=0.01, output_dir=temp_dir, enable_parallel=True, max_workers=4)

        parallel_benchmark = TPCDIBenchmark(config=parallel_config)

        assert parallel_benchmark.enable_parallel is True
        assert parallel_benchmark.max_workers == 4

    def test_backward_compatibility(self, temp_dir):
        old_style_benchmark = TPCDIBenchmark(
            scale_factor=0.01, output_dir=temp_dir, enable_parallel=True, max_workers=2
        )

        assert old_style_benchmark is not None
        assert old_style_benchmark.scale_factor == 0.01
        assert old_style_benchmark.enable_parallel is True

        assert hasattr(old_style_benchmark, "run_etl_pipeline")
        assert hasattr(old_style_benchmark, "get_etl_status")
        assert hasattr(old_style_benchmark, "run_benchmark")

        assert hasattr(old_style_benchmark, "run_enhanced_etl_pipeline")
        assert hasattr(old_style_benchmark, "get_enhanced_etl_status")

    def test_full_benchmark_with_phase3_components(self, tpcdi_benchmark, test_database):

        with (
            patch.object(tpcdi_benchmark, "run_etl_benchmark") as mock_etl,
            patch.object(tpcdi_benchmark, "run_data_validation") as mock_validation,
        ):
            mock_etl_result = Mock()
            mock_etl_result.success = True
            mock_etl_result.total_records_processed = 1000
            mock_etl_result.total_execution_time = 30.5
            mock_etl_result.historical_load = Mock()
            mock_etl_result.incremental_loads = []
            mock_etl.return_value = mock_etl_result

            mock_validation_result = Mock()
            mock_validation_result.quality_score = 95.0
            mock_validation_result.total_validations = 20
            mock_validation_result.passed_validations = 19
            mock_validation_result.validations = []
            mock_validation.return_value = mock_validation_result

            with (
                patch.object(tpcdi_benchmark.metrics_calculator, "calculate_detailed_metrics") as mock_metrics,
                patch.object(tpcdi_benchmark.metrics_calculator, "generate_official_report") as mock_report,
                patch.object(tpcdi_benchmark.metrics_calculator, "print_official_results") as mock_print,
                patch.object(tpcdi_benchmark.metrics_calculator, "export_metrics_json") as mock_export,
            ):
                mock_metrics.return_value = {"benchmark_score": 85.0}
                mock_report.return_value = Mock()
                mock_export.return_value = {"score": 85.0}

                results = tpcdi_benchmark.run_full_benchmark(test_database, "sqlite")

                assert results["success"] is True
                assert "metrics" in results
                assert "etl_result" in results
                assert "validation_result" in results
                assert "report" in results
                assert "execution_time_seconds" in results

                mock_etl.assert_called_once()
                mock_validation.assert_called_once()
                mock_metrics.assert_called_once()
                mock_report.assert_called_once()
                mock_print.assert_called_once()
                mock_export.assert_called_once()

    def test_scale_factor_integration(self, temp_dir):
        scale_factors = [0.001, 0.01, 0.1]
        records_loaded = []

        for scale_factor in scale_factors:
            config = TPCDIConfig(
                scale_factor=scale_factor,
                output_dir=temp_dir,
                enable_parallel=True,
                max_workers=2,
            )

            benchmark = TPCDIBenchmark(config=config)

            assert benchmark.scale_factor == scale_factor
            assert benchmark.config.scale_factor == scale_factor

            with sqlite3.connect(":memory:") as conn:
                benchmark.create_schema(conn, "sqlite")
                results = benchmark._run_incremental_data_loading(conn)
                assert results["success"] is True
                customer_count = conn.execute("SELECT COUNT(*) FROM DimCustomer").fetchone()[0]
                if scale_factor >= 0.01:
                    assert results["records_loaded"] > 0
                    assert customer_count > 0
                else:
                    assert results["records_loaded"] == 0
                    assert customer_count == 0
                assert results["records_loaded"] >= customer_count
                records_loaded.append(results["records_loaded"])

        assert records_loaded[0] <= records_loaded[1] < records_loaded[2]

    def test_component_error_isolation(self, tpcdi_benchmark, test_database):
        tpcdi_benchmark.create_schema(test_database, "sqlite")
        tpcdi_benchmark.generate_data()

        with patch.object(
            tpcdi_benchmark,
            "_run_enhanced_data_processing",
            return_value={"success": False, "error": "FinWire input is corrupt"},
        ):
            results = tpcdi_benchmark.run_enhanced_etl_pipeline(
                test_database, dialect="sqlite", enable_data_quality_monitoring=False
            )

        assert results["success"] is False
        assert results["failed_phases"] == [{"phase": "enhanced_data_processing", "error": "FinWire input is corrupt"}]
        assert results["phases"]["incremental_loading"]["success"] is True
        assert results["phases"]["incremental_loading"]["records_loaded"] > 0
        assert test_database.execute(
            "SELECT CustomerID, FirstName FROM DimCustomer WHERE SK_CustomerID = 1000001"
        ).fetchone() == (100000000, "FirstName0")


class TestTPCDIPhase3PerformanceIntegration:
    @pytest.fixture
    def performance_benchmark(self, tmp_path):
        config = TPCDIConfig(
            scale_factor=0.1,
            output_dir=tmp_path,
            enable_parallel=True,
            max_workers=4,
        )
        return TPCDIBenchmark(config=config)

    def test_canonical_parallel_transform_matches_sequential(self, performance_benchmark, tmp_path):
        customer_columns = [
            "CustomerID",
            "TaxID",
            "Status",
            "LastName",
            "FirstName",
            "MiddleInitial",
            "Gender",
            "Tier",
            "DOB",
            "AddressLine1",
            "City",
            "StateProv",
            "PostalCode",
            "Country",
            "Phone1",
            "Email1",
            "EffectiveDate",
            "EndDate",
        ]
        source_files = {"csv": []}
        for index in range(3):
            path = tmp_path / f"customer_batch_{index}.csv"
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(",".join(customer_columns) + "\n")
                for row in range(5):
                    values = [f"{column}_{index}_{row}" for column in customer_columns]
                    values[-2:] = ["1999-01-01", "9999-12-31"]
                    handle.write(",".join(values) + "\n")
            source_files["csv"].append(str(path))

        sequential = performance_benchmark._transform_source_data(dict(source_files), "historical")
        parallel = performance_benchmark._transform_source_data_parallel(dict(source_files), "historical")

        assert sequential["records_processed"] == 15
        assert parallel["records_processed"] == sequential["records_processed"]
        assert sorted(parallel["transformations_applied"]) == sorted(sequential["transformations_applied"])
        assert set(parallel["staged_data"]) == set(sequential["staged_data"]) == {"DimCustomer"}
        sequential_rows = sequential["staged_data"]["DimCustomer"].sort_values("CustomerID").reset_index(drop=True)
        parallel_rows = parallel["staged_data"]["DimCustomer"].sort_values("CustomerID").reset_index(drop=True)
        assert parallel_rows.equals(sequential_rows)
        assert set(parallel_rows["EffectiveDate"]) == {"1999-01-01"}
        assert set(parallel_rows["EndDate"]) == {"9999-12-31"}

    def test_incremental_loading_performance(self, performance_benchmark):

        with sqlite3.connect(":memory:") as conn:
            performance_benchmark.create_schema(conn, "sqlite")
            start_time = mono_time()
            incremental_results = performance_benchmark._run_incremental_data_loading(conn)
            incremental_duration = elapsed_seconds(start_time)

            assert incremental_results["success"] is True
            customer_count = conn.execute("SELECT COUNT(*) FROM DimCustomer").fetchone()[0]
            assert customer_count > 0
            assert incremental_results["records_loaded"] >= customer_count
            assert incremental_duration < 30


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
