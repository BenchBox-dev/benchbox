import json
from unittest.mock import Mock, patch

import pytest

from benchbox.core.validation import (
    BenchmarkExpectations,
    DatabaseValidationEngine,
    DataValidationEngine,
    ValidationResult,
    ValidationSummary,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestValidationResult:
    def test_validation_result_valid(self):
        result = ValidationResult(
            is_valid=True,
            errors=[],
            warnings=["Some warning"],
            details={"key": "value"},
        )

        assert result.is_valid
        assert not result.errors
        assert result.warnings == ["Some warning"]
        assert result.details == {"key": "value"}

    def test_validation_result_invalid(self):
        result = ValidationResult(is_valid=False, errors=["Error 1", "Error 2"], warnings=[], details={})

        assert not result.is_valid
        assert result.errors == ["Error 1", "Error 2"]
        assert not result.warnings
        assert result.details == {}


class TestValidationSummary:
    def test_validation_summary(self):
        summary = ValidationSummary(
            total_validations=5,
            passed_validations=3,
            failed_validations=2,
            warnings_count=4,
        )

        assert summary.total_validations == 5
        assert summary.passed_validations == 3
        assert summary.failed_validations == 2
        assert summary.warnings_count == 4


class TestBenchmarkExpectations:
    def test_benchmark_expectations_creation(self):
        expectations = BenchmarkExpectations(
            expected_table_count=25,
            critical_tables=["customer", "item"],
            dimension_tables=["customer"],
            fact_tables=["store_sales"],
            validation_thresholds={"min_file_size_bytes": 10},
        )

        assert expectations.expected_table_count == 25
        assert expectations.critical_tables == ["customer", "item"]
        assert expectations.dimension_tables == ["customer"]
        assert expectations.fact_tables == ["store_sales"]
        assert expectations.validation_thresholds == {"min_file_size_bytes": 10}

    def test_benchmark_expectations_defaults(self):
        expectations = BenchmarkExpectations()

        assert expectations.expected_table_count == 0
        assert expectations.critical_tables == []
        assert expectations.dimension_tables == []
        assert expectations.fact_tables == []
        assert expectations.validation_thresholds == {}


class TestDataValidationEngine:
    def setup_method(self):
        self.engine = DataValidationEngine()

    def test_init(self):
        assert isinstance(self.engine, DataValidationEngine)
        assert hasattr(self.engine, "BENCHMARK_EXPECTATIONS")

    def test_benchmark_expectations_registry(self):

        assert "tpcds" in self.engine.BENCHMARK_EXPECTATIONS
        tpcds_exp = self.engine.BENCHMARK_EXPECTATIONS["tpcds"]
        assert tpcds_exp.expected_table_count == 25
        assert "customer" in tpcds_exp.critical_tables
        assert "store_sales" in tpcds_exp.fact_tables

        assert "tpch" in self.engine.BENCHMARK_EXPECTATIONS
        tpch_exp = self.engine.BENCHMARK_EXPECTATIONS["tpch"]
        assert tpch_exp.expected_table_count == 8
        assert "customer" in tpch_exp.critical_tables
        assert "lineitem" in tpch_exp.fact_tables

    def test_validate_preflight_conditions_success(self, tmp_path):
        result = self.engine.validate_preflight_conditions(
            benchmark_type="tpcds", scale_factor=1.0, output_dir=tmp_path
        )

        assert isinstance(result, ValidationResult)
        assert result.is_valid
        assert not result.errors
        assert isinstance(result.warnings, list)
        assert result.details["benchmark_type"] == "tpcds"
        assert result.details["scale_factor"] == 1.0

    def test_validate_preflight_conditions_invalid_benchmark(self, tmp_path):
        result = self.engine.validate_preflight_conditions(
            benchmark_type="invalid_benchmark", scale_factor=1.0, output_dir=tmp_path
        )

        assert isinstance(result, ValidationResult)
        assert not result.is_valid
        assert "Unsupported benchmark type" in result.errors[0]

    def test_validate_preflight_conditions_invalid_scale(self, tmp_path):
        result = self.engine.validate_preflight_conditions(benchmark_type="tpcds", scale_factor=0, output_dir=tmp_path)

        assert isinstance(result, ValidationResult)
        assert not result.is_valid
        assert "Scale factor must be positive" in result.errors[0]

    def test_validate_preflight_conditions_missing_directory(self, tmp_path):
        missing_dir = tmp_path / "nonexistent" / "nested" / "path"

        result = self.engine.validate_preflight_conditions(
            benchmark_type="tpcds", scale_factor=1.0, output_dir=missing_dir
        )

        assert isinstance(result, ValidationResult)
        assert result.is_valid
        assert missing_dir.exists()

    def test_validate_generated_data_success(self, tmp_path):

        manifest_data = {
            "version": 2,
            "benchmark": "tpcds",
            "scale_factor": 1.0,
            "tables": {
                "customer": [{"path": "customer.dat", "size_bytes": 1000, "row_count": 100}],
                "item": [{"path": "item.dat", "size_bytes": 2000, "row_count": 200}],
                "store_sales": [{"path": "store_sales.dat", "size_bytes": 5000, "row_count": 500}],
            },
        }

        manifest_path = tmp_path / "_datagen_manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f)

        for table_name, file_list in manifest_data["tables"].items():
            for file_info in file_list:
                (tmp_path / file_info["path"]).write_text("x" * file_info["size_bytes"])

        result = self.engine.validate_generated_data(manifest_path)

        assert isinstance(result, ValidationResult)

        assert result.details["benchmark_type"] == "tpcds"

    @pytest.mark.parametrize("benchmark_name", ["tpcds", "unlisted_benchmark"])
    def test_validate_generated_data_rejects_manifest_without_tables(self, tmp_path, benchmark_name):
        manifest_path = tmp_path / "_datagen_manifest.json"
        manifest_path.write_text(json.dumps({"benchmark": benchmark_name, "scale_factor": 1.0, "tables": {}}))

        result = self.engine.validate_generated_data(manifest_path)

        assert not result.is_valid
        assert any("lists no tables" in error for error in result.errors)

    def test_validate_generated_data_rejects_tables_without_files(self, tmp_path):
        manifest_path = tmp_path / "_datagen_manifest.json"
        manifest_path.write_text(json.dumps({"benchmark": "unlisted_benchmark", "tables": {"item": []}}))

        result = self.engine.validate_generated_data(manifest_path)

        assert not result.is_valid
        assert any("lists no tables" in error for error in result.errors)

    def test_validate_generated_data_missing_manifest(self, tmp_path):
        missing_manifest = tmp_path / "missing_manifest.json"

        result = self.engine.validate_generated_data(missing_manifest)

        assert isinstance(result, ValidationResult)
        assert not result.is_valid
        assert "Manifest file not found" in result.errors[0]

    def test_validate_generated_data_invalid_json(self, tmp_path):
        manifest_path = tmp_path / "_datagen_manifest.json"
        manifest_path.write_text("invalid json")

        result = self.engine.validate_generated_data(manifest_path)

        assert isinstance(result, ValidationResult)
        assert not result.is_valid
        assert "Failed to parse manifest" in result.errors[0]

    def test_validate_table_count(self):
        manifest_data = {"tables": {"table1": [], "table2": [], "table3": []}}
        expectations = BenchmarkExpectations(expected_table_count=3)

        errors, warnings = self.engine._validate_table_count(manifest_data, expectations)

        assert not errors
        assert not warnings

    def test_validate_table_count_insufficient(self):
        manifest_data = {"tables": {"table1": [], "table2": []}}
        expectations = BenchmarkExpectations(expected_table_count=5)

        errors, warnings = self.engine._validate_table_count(manifest_data, expectations)

        assert len(errors) == 1
        assert "Expected 5 tables, found 2" in errors[0]

    def test_validate_critical_tables(self):
        manifest_data = {"tables": {"customer": [], "item": [], "store_sales": []}}
        expectations = BenchmarkExpectations(critical_tables=["customer", "item", "store_sales"])

        errors, warnings = self.engine._validate_critical_tables(manifest_data, expectations)

        assert not errors

    def test_validate_critical_tables_missing(self):
        manifest_data = {"tables": {"customer": [], "item": []}}
        expectations = BenchmarkExpectations(critical_tables=["customer", "item", "store_sales"])

        errors, warnings = self.engine._validate_critical_tables(manifest_data, expectations)

        assert len(errors) == 1
        assert "Missing critical tables: store_sales" in errors[0]

    def test_validate_file_sizes(self, tmp_path):

        (tmp_path / "large_file.dat").write_text("x" * 1000)
        (tmp_path / "small_file.dat").write_text("x" * 5)

        manifest_data = {
            "tables": {
                "large_table": [{"path": "large_file.dat", "size_bytes": 1000}],
                "small_table": [{"path": "small_file.dat", "size_bytes": 5}],
            }
        }
        expectations = BenchmarkExpectations(validation_thresholds={"min_file_size_bytes": 10})

        errors, warnings = self.engine._validate_file_sizes(manifest_data, expectations, tmp_path)

        assert len(warnings) >= 1

        assert any("small_table" in warning for warning in warnings)

    def test_validate_row_counts(self):
        manifest_data = {
            "tables": {
                "good_table": [{"row_count": 100}],
                "empty_table": [{"row_count": 0}],
            }
        }
        expectations = BenchmarkExpectations(validation_thresholds={"min_row_count": 1})

        errors, warnings = self.engine._validate_row_counts(manifest_data, expectations)

        assert len(warnings) >= 1
        assert any("empty_table" in warning for warning in warnings)


class TestDatabaseValidationEngine:
    def setup_method(self):
        self.engine = DatabaseValidationEngine()

    def test_init(self):
        assert isinstance(self.engine, DatabaseValidationEngine)

    @patch("benchbox.core.validation.DatabaseValidationEngine._get_table_list")
    @patch("benchbox.core.validation.DatabaseValidationEngine._get_table_row_count")
    def test_validate_loaded_data_success(self, mock_row_count, mock_table_list):
        mock_connection = Mock()
        mock_table_list.return_value = ["customer", "item", "store_sales", "date_dim"]
        mock_row_count.return_value = 100

        result = self.engine.validate_loaded_data(connection=mock_connection, benchmark_type="tpcds", scale_factor=1.0)

        assert isinstance(result, ValidationResult)
        mock_table_list.assert_called_once_with(mock_connection)
        assert result.details["benchmark_type"] == "tpcds"
        assert result.details["scale_factor"] == 1.0

    @patch("benchbox.core.validation.DatabaseValidationEngine._get_table_list")
    def test_validate_loaded_data_connection_error(self, mock_table_list):
        mock_connection = Mock()
        mock_table_list.side_effect = Exception("Connection failed")

        result = self.engine.validate_loaded_data(connection=mock_connection, benchmark_type="tpcds", scale_factor=1.0)

        assert isinstance(result, ValidationResult)
        assert not result.is_valid
        assert "Database validation failed" in result.errors[0]

    def test_get_table_list_duckdb_style(self):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("customer",), ("item",), ("store_sales",)]

        mock_cursor.execute.side_effect = [Exception("No info schema"), None]

        tables = self.engine._get_table_list(mock_connection)

        assert tables == ["customer", "item", "store_sales"]

    def test_get_table_row_count(self):
        mock_connection = Mock()
        mock_cursor = Mock()
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (150,)

        count = self.engine._get_table_row_count(mock_connection, "customer")

        assert count == 150
        mock_cursor.execute.assert_called_once_with("SELECT COUNT(*) FROM customer")

    def test_validate_loaded_data_missing_tables(self):
        mock_connection = Mock()

        with patch.object(self.engine, "_get_table_list") as mock_table_list:
            mock_table_list.return_value = ["customer", "item"]

            result = self.engine.validate_loaded_data(
                connection=mock_connection, benchmark_type="tpcds", scale_factor=1.0
            )

            assert isinstance(result, ValidationResult)

            assert not result.is_valid
            assert any("Missing tables" in error for error in result.errors)

    def test_validate_loaded_data_unknown_benchmark(self):
        mock_connection = Mock()

        result = self.engine.validate_loaded_data(
            connection=mock_connection,
            benchmark_type="unknown_benchmark",
            scale_factor=1.0,
        )

        assert isinstance(result, ValidationResult)

        assert "No validation expectations" in result.warnings[0]


class TestValidationIntegration:
    def test_data_and_database_engines_together(self, tmp_path):
        data_engine = DataValidationEngine()
        db_engine = DatabaseValidationEngine()

        preflight_result = data_engine.validate_preflight_conditions("tpcds", 1.0, tmp_path)
        assert preflight_result.is_valid

        mock_connection = Mock()
        with patch.object(db_engine, "_get_table_list") as mock_table_list:
            mock_table_list.return_value = ["customer", "item", "store_sales"]

            db_result = db_engine.validate_loaded_data(mock_connection, "tpcds", 1.0)
            assert isinstance(db_result, ValidationResult)


