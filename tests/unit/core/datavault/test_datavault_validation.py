# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import json

import pytest

from benchbox.core.datavault.validation import (
    DATAVAULT_ROW_EXPECTATIONS,
    DataVaultValidationReport,
    ValidationResult,
    count_rows_in_file,
    get_expected_row_count,
    get_row_counts_from_manifest,
    validate_referential_integrity,
    validate_row_counts,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestExpectedRowCounts:
    def test_hub_region_fixed_count(self):
        assert get_expected_row_count("hub_region", 0.01) == 5
        assert get_expected_row_count("hub_region", 1.0) == 5
        assert get_expected_row_count("hub_region", 10.0) == 5

    def test_hub_nation_fixed_count(self):
        assert get_expected_row_count("hub_nation", 0.01) == 25
        assert get_expected_row_count("hub_nation", 1.0) == 25
        assert get_expected_row_count("hub_nation", 10.0) == 25

    def test_hub_customer_scales_with_sf(self):
        assert get_expected_row_count("hub_customer", 0.01) == 1_500
        assert get_expected_row_count("hub_customer", 1.0) == 150_000
        assert get_expected_row_count("hub_customer", 10.0) == 1_500_000

    def test_all_datavault_tables_have_expectations(self):
        from benchbox.core.datavault.schema import TABLES

        for table in TABLES:
            assert table.name in DATAVAULT_ROW_EXPECTATIONS, f"Table {table.name} missing row count expectation"

    def test_unknown_table_raises_error(self):
        with pytest.raises(ValueError, match="Unknown Data Vault table"):
            get_expected_row_count("unknown_table", 1.0)

    def test_case_insensitive_table_names(self):
        assert get_expected_row_count("HUB_REGION", 1.0) == 5
        assert get_expected_row_count("Hub_Region", 1.0) == 5


class TestValidationResult:
    def test_exact_match_is_valid(self):
        result = ValidationResult(
            table_name="hub_region",
            actual_count=5,
            expected_count=5,
        )
        assert result.is_valid
        assert result.variance_pct == 0.0

    def test_within_tolerance_is_valid(self):
        result = ValidationResult(
            table_name="hub_lineitem",
            actual_count=6_000_000,
            expected_count=6_001_215,
            tolerance_pct=1.0,
        )
        assert result.is_valid
        assert result.variance_pct < 1.0

    def test_exceeds_tolerance_is_invalid(self):
        result = ValidationResult(
            table_name="hub_customer",
            actual_count=100_000,
            expected_count=150_000,
            tolerance_pct=1.0,
        )
        assert not result.is_valid
        assert result.variance_pct > 1.0

    def test_zero_expected_handling(self):
        result = ValidationResult(
            table_name="test",
            actual_count=0,
            expected_count=0,
        )
        assert result.is_valid
        assert result.variance_pct == 0.0


class TestDataVaultValidationReport:
    def test_all_passed_report(self):
        results = [
            ValidationResult("hub_region", 5, 5),
            ValidationResult("hub_nation", 25, 25),
        ]
        report = DataVaultValidationReport(scale_factor=1.0, results=results)

        assert report.is_valid
        assert report.tables_validated == 2
        assert report.tables_passed == 2
        assert report.tables_failed == 0

    def test_some_failed_report(self):
        results = [
            ValidationResult("hub_region", 5, 5),
            ValidationResult("hub_nation", 20, 25),
        ]
        report = DataVaultValidationReport(scale_factor=1.0, results=results)

        assert not report.is_valid
        assert report.tables_passed == 1
        assert report.tables_failed == 1

    def test_to_dict_serialization(self):
        results = [ValidationResult("hub_region", 5, 5)]
        report = DataVaultValidationReport(scale_factor=1.0, results=results)

        d = report.to_dict()
        assert d["scale_factor"] == 1.0
        assert d["is_valid"] is True
        assert len(d["results"]) == 1

    def test_str_representation(self):
        results = [ValidationResult("hub_region", 5, 5)]
        report = DataVaultValidationReport(scale_factor=1.0, results=results)

        s = str(report)
        assert "PASSED" in s
        assert "SF=1.0" in s


class TestFileOperations:
    def test_count_rows_in_file(self, tmp_path):
        data_file = tmp_path / "test.tbl"
        data_file.write_text("1|a|b|\n2|c|d|\n3|e|f|\n")

        count = count_rows_in_file(data_file)
        assert count == 3

    def test_count_rows_skips_empty_lines(self, tmp_path):
        data_file = tmp_path / "test.tbl"
        data_file.write_text("1|a|\n\n2|b|\n\n")

        count = count_rows_in_file(data_file)
        assert count == 2

    def test_get_row_counts_from_manifest(self, tmp_path):
        manifest = {
            "version": 2,
            "benchmark": "datavault",
            "scale_factor": 0.01,
            "tables": {
                "hub_region": {"formats": {"tbl": [{"path": "hub_region.tbl", "row_count": 5}]}},
                "hub_nation": {"formats": {"tbl": [{"path": "hub_nation.tbl", "row_count": 25}]}},
            },
        }
        manifest_path = tmp_path / "_datagen_manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        counts = get_row_counts_from_manifest(manifest_path)
        assert counts["hub_region"] == 5
        assert counts["hub_nation"] == 25


class TestValidateRowCounts:
    def test_validates_from_manifest(self, tmp_path):
        manifest = {
            "version": 2,
            "benchmark": "datavault",
            "scale_factor": 0.01,
            "tables": {},
        }

        for table_name in DATAVAULT_ROW_EXPECTATIONS:
            expected = get_expected_row_count(table_name, 0.01)
            manifest["tables"][table_name] = {
                "formats": {"tbl": [{"path": f"{table_name}.tbl", "row_count": expected}]}
            }

        manifest_path = tmp_path / "_datagen_manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        report = validate_row_counts(tmp_path, scale_factor=0.01)

        assert report.is_valid
        assert report.tables_validated == 21

    def test_detects_missing_tables(self, tmp_path):
        manifest = {
            "version": 2,
            "benchmark": "datavault",
            "scale_factor": 0.01,
            "tables": {
                "hub_region": {"formats": {"tbl": [{"path": "hub_region.tbl", "row_count": 5}]}},
            },
        }
        manifest_path = tmp_path / "_datagen_manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        report = validate_row_counts(tmp_path, scale_factor=0.01)

        assert not report.is_valid
        assert report.tables_failed > 0


class TestReferentialIntegrity:
    def test_valid_hub_satellite_relationship(self, tmp_path):
        manifest = {
            "tables": {
                "hub_region": {"formats": {"tbl": [{"row_count": 5}]}},
                "sat_region": {"formats": {"tbl": [{"row_count": 5}]}},
                "hub_nation": {"formats": {"tbl": [{"row_count": 25}]}},
                "sat_nation": {"formats": {"tbl": [{"row_count": 25}]}},
            }
        }
        manifest_path = tmp_path / "_datagen_manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        checks = validate_referential_integrity(tmp_path)

        assert checks.get("sat_region→hub_region") is True
        assert checks.get("sat_nation→hub_nation") is True

    def test_invalid_hub_satellite_relationship(self, tmp_path):
        manifest = {
            "tables": {
                "hub_region": {"formats": {"tbl": [{"row_count": 5}]}},
                "sat_region": {"formats": {"tbl": [{"row_count": 10}]}},
            }
        }
        manifest_path = tmp_path / "_datagen_manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        checks = validate_referential_integrity(tmp_path)

        assert checks.get("sat_region→hub_region") is False


