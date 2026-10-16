# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

from datetime import datetime, timezone, tzinfo
from pathlib import Path

import pytest

import benchbox.utils.data_validation as data_validation
from benchbox.utils.data_validation import (
    BenchmarkDataValidator,
    DataValidationResult,
    TableExpectation,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

NOW = datetime(2026, 1, 15, 12, 0, 0)


@pytest.fixture(autouse=True)
def _validation_wallclock(monkeypatch: pytest.MonkeyPatch) -> None:

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return NOW if tz is None else NOW.replace(tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(data_validation, "datetime", FixedDatetime)


class TestDataValidationResult:
    def test_fields_are_stored(self):
        now = NOW
        result = DataValidationResult(
            valid=True,
            tables_validated={"orders": True, "lineitem": True},
            missing_tables=[],
            row_count_mismatches={},
            file_size_info={"orders.tbl": 1024},
            validation_timestamp=now,
            issues=[],
        )
        assert result.valid is True
        assert result.tables_validated == {"orders": True, "lineitem": True}
        assert result.missing_tables == []
        assert result.row_count_mismatches == {}
        assert result.file_size_info == {"orders.tbl": 1024}
        assert result.validation_timestamp is now
        assert result.issues == []

    def test_invalid_result_with_issues(self):
        result = DataValidationResult(
            valid=False,
            tables_validated={"orders": False},
            missing_tables=["lineitem"],
            row_count_mismatches={"orders": (1500000, 999)},
            file_size_info={},
            validation_timestamp=NOW,
            issues=["Missing data files for table lineitem"],
        )
        assert result.valid is False
        assert "lineitem" in result.missing_tables
        assert result.row_count_mismatches["orders"] == (1500000, 999)
        assert len(result.issues) == 1


class TestTableExpectation:
    def test_required_fields(self):
        texp = TableExpectation(name="customer", expected_rows=150000, expected_files=["customer.tbl"])
        assert texp.name == "customer"
        assert texp.expected_rows == 150000
        assert texp.expected_files == ["customer.tbl"]

    def test_default_optional_fields(self):
        texp = TableExpectation(name="t", expected_rows=0, expected_files=[])
        assert texp.min_file_size == 0
        assert texp.allow_zero_rows is False

    def test_allow_zero_rows_flag(self):
        texp = TableExpectation(name="empty_table", expected_rows=0, expected_files=[], allow_zero_rows=True)
        assert texp.allow_zero_rows is True


class TestValidatorInit:
    def test_tpch_benchmark_loads_expectations(self):
        v = BenchmarkDataValidator("tpch", scale_factor=1.0)
        assert v.benchmark_name == "tpch"
        assert v.scale_factor == 1.0
        assert "lineitem" in v.table_expectations
        assert "customer" in v.table_expectations

    def test_tpcds_benchmark_loads_expectations(self):
        v = BenchmarkDataValidator("tpcds", scale_factor=1.0)
        assert "catalog_sales" in v.table_expectations
        assert "store_sales" in v.table_expectations

    def test_case_insensitive_benchmark_name(self):
        v = BenchmarkDataValidator("TPCH", scale_factor=1.0)
        assert v.benchmark_name == "tpch"
        assert len(v.table_expectations) == 8

    def test_unknown_benchmark_has_empty_expectations(self):
        v = BenchmarkDataValidator("custom_bench", scale_factor=1.0)
        assert v.table_expectations == {}


class TestTableExpectationsConstants:
    def test_tpch_expectations_non_empty(self):
        exps = BenchmarkDataValidator.TPCH_TABLE_EXPECTATIONS
        assert len(exps) == 8
        for name, texp in exps.items():
            assert isinstance(texp, TableExpectation)
            assert texp.name == name
            assert texp.expected_rows > 0
            assert len(texp.expected_files) >= 1

    def test_tpcds_expectations_non_empty(self):
        exps = BenchmarkDataValidator.TPCDS_TABLE_EXPECTATIONS
        assert len(exps) == 24
        for name, texp in exps.items():
            assert isinstance(texp, TableExpectation)
            assert texp.name == name
            assert len(texp.expected_files) >= 1

    def test_tpch_nation_has_25_rows_at_sf1(self):
        assert BenchmarkDataValidator.TPCH_TABLE_EXPECTATIONS["nation"].expected_rows == 25

    def test_tpch_region_has_5_rows_at_sf1(self):
        assert BenchmarkDataValidator.TPCH_TABLE_EXPECTATIONS["region"].expected_rows == 5


class TestTpcdsScaleExpectations:
    @pytest.mark.parametrize(
        ("scale", "table", "rows"),
        [
            (10.0, "store", 102),
            (10.0, "promotion", 500),
            (10.0, "item", 102_000),
            (10.0, "customer_address", 250_000),
            (10.0, "reason", 75),
            (10.0, "web_site", 42),
            (100.0, "web_site", 24),
            (100.0, "customer", 2_000_000),
            (1.0, "reason", 75),
        ],
    )
    def test_spec_scales_use_generated_row_counts(self, scale, table, rows):
        assert BenchmarkDataValidator("tpcds", scale_factor=scale).table_expectations[table].expected_rows == rows

    def test_other_scales_check_only_linear_tables(self):
        expectations = BenchmarkDataValidator("tpcds", scale_factor=1000.0).table_expectations

        assert expectations["store_sales"].expected_rows == 2_880_404 * 1000
        assert expectations["customer"].expected_rows == 0
        assert expectations["store"].expected_rows == 0

    @pytest.mark.parametrize("scale", [1.0, 10.0, 100.0])
    def test_agrees_with_post_load_row_count_strategy(self, scale):
        from benchbox.platforms.base.validation import TPCDSRowCountStrategy

        strategy = TPCDSRowCountStrategy(scale_factor=scale)
        for table, expectation in BenchmarkDataValidator("tpcds", scale_factor=scale).table_expectations.items():
            expected = strategy.get_expected_range(table)
            assert expected is not None, table
            low, high = expected
            assert low <= expectation.expected_rows <= high, table


class TestScaleAdjustedExpectations:
    def test_sf001_scales_variable_tables(self):
        v = BenchmarkDataValidator("tpch", scale_factor=0.01)
        assert v.table_expectations["lineitem"].expected_rows == int(6001215 * 0.01)
        assert v.table_expectations["customer"].expected_rows == 1500

    def test_sf001_fixed_tables_unchanged(self):
        v = BenchmarkDataValidator("tpch", scale_factor=0.01)
        assert v.table_expectations["nation"].expected_rows == 25
        assert v.table_expectations["region"].expected_rows == 5

    def test_sf10_scales_up(self):
        v = BenchmarkDataValidator("tpch", scale_factor=10.0)
        assert v.table_expectations["customer"].expected_rows == 1500000
        assert v.table_expectations["nation"].expected_rows == 25

    def test_tpcds_fixed_tables_at_different_scale(self):
        v = BenchmarkDataValidator("tpcds", scale_factor=0.5)
        assert v.table_expectations["call_center"].expected_rows == max(1, int(6 * 0.5))
        assert v.table_expectations["ship_mode"].expected_rows == max(1, int(20 * 0.5))
        assert v.table_expectations["warehouse"].expected_rows == max(1, int(5 * 0.5))
        assert v.table_expectations["time_dim"].expected_rows == max(1, int(86400 * 0.5))
        expected_cs = max(1, int(1441548 * 0.5))
        assert v.table_expectations["catalog_sales"].expected_rows == expected_cs

    def test_tpcds_fixed_tables_at_official_scale(self):
        v = BenchmarkDataValidator("tpcds", scale_factor=1.0)
        assert v.table_expectations["call_center"].expected_rows == 6
        assert v.table_expectations["ship_mode"].expected_rows == 20
        assert v.table_expectations["warehouse"].expected_rows == 5
        assert v.table_expectations["time_dim"].expected_rows == 86400


class TestValidateDataDirectory:
    def test_nonexistent_directory_returns_invalid(self, tmp_path):
        v = BenchmarkDataValidator("tpch", scale_factor=0.01)
        result = v.validate_data_directory(tmp_path / "does_not_exist")
        assert result.valid is False
        assert any("does not exist" in issue for issue in result.issues)

    def test_empty_directory_reports_missing_tables(self, tmp_path):
        v = BenchmarkDataValidator("tpch", scale_factor=0.01)
        result = v.validate_data_directory(tmp_path)
        assert result.valid is False
        assert len(result.missing_tables) == 8
        assert "lineitem" in result.missing_tables
        assert "customer" in result.missing_tables

    def test_unknown_benchmark_empty_dir_reports_no_data(self, tmp_path):
        v = BenchmarkDataValidator("custom", scale_factor=1.0)
        result = v.validate_data_directory(tmp_path)
        assert result.valid is False
        assert any("No data files" in issue for issue in result.issues)

    def test_unknown_benchmark_with_files_is_valid(self, tmp_path):
        (tmp_path / "data.csv").write_text("a,b,c\n1,2,3\n")
        v = BenchmarkDataValidator("custom", scale_factor=1.0)
        result = v.validate_data_directory(tmp_path)
        assert result.valid is True
        assert "data.csv" in result.file_size_info
        assert result.file_size_info["data.csv"] > 0

    def test_unknown_benchmark_empty_file_flagged(self, tmp_path):
        (tmp_path / "empty.csv").write_text("")
        v = BenchmarkDataValidator("custom", scale_factor=1.0)
        result = v.validate_data_directory(tmp_path)
        assert any("Empty data file" in issue for issue in result.issues)


class TestTableExistenceValidation:
    def test_present_table_file_is_validated(self, tmp_path):
        lines = "\n".join([f"row{i}" for i in range(1500)]) + "\n"
        (tmp_path / "customer.tbl").write_text(lines)
        v = BenchmarkDataValidator("tpch", scale_factor=0.01)
        result = v.validate_data_directory(tmp_path)
        assert "customer" not in result.missing_tables

    def test_missing_table_reported(self, tmp_path):
        (tmp_path / "customer.tbl").write_text("row\n")
        v = BenchmarkDataValidator("tpch", scale_factor=0.01)
        result = v.validate_data_directory(tmp_path)
        assert "lineitem" in result.missing_tables

    def test_compressed_gz_variant_resolved(self, tmp_path):
        import gzip

        data = b"row1\nrow2\nrow3\n"
        with gzip.open(tmp_path / "customer.tbl.gz", "wb") as f:
            f.write(data)
        v = BenchmarkDataValidator("tpch", scale_factor=0.01)
        result = v.validate_data_directory(tmp_path)
        assert "customer" not in result.missing_tables


class TestRowCountValidation:
    def test_matching_row_count_passes(self, tmp_path):
        lines = "\n".join([f"row{i}" for i in range(25)]) + "\n"
        (tmp_path / "nation.tbl").write_text(lines)
        v = BenchmarkDataValidator("tpch", scale_factor=0.01)
        missing = []
        mismatches: dict[str, tuple[int, int]] = {}
        sizes: dict[str, int] = {}
        issues: list[str] = []
        expectation = v.table_expectations["nation"]
        valid = v._validate_single_table(tmp_path, "nation", expectation, missing, mismatches, sizes, issues)
        assert valid is True
        assert "nation" not in mismatches

    def test_mismatching_row_count_detected(self, tmp_path):
        lines = "\n".join([f"row{i}" for i in range(100)]) + "\n"
        (tmp_path / "nation.tbl").write_text(lines)
        v = BenchmarkDataValidator("tpch", scale_factor=0.01)
        missing = []
        mismatches: dict[str, tuple[int, int]] = {}
        sizes: dict[str, int] = {}
        issues: list[str] = []
        expectation = v.table_expectations["nation"]
        valid = v._validate_single_table(tmp_path, "nation", expectation, missing, mismatches, sizes, issues)
        assert valid is False
        assert "nation" in mismatches
        expected_rows, actual_rows = mismatches["nation"]
        assert expected_rows == 25
        assert actual_rows == 100

    def test_empty_file_invalid_when_zero_rows_not_allowed(self, tmp_path):
        (tmp_path / "customer.tbl").write_text("")
        v = BenchmarkDataValidator("tpch", scale_factor=0.01)
        missing = []
        mismatches: dict[str, tuple[int, int]] = {}
        sizes: dict[str, int] = {}
        issues: list[str] = []
        expectation = v.table_expectations["customer"]
        valid = v._validate_single_table(tmp_path, "customer", expectation, missing, mismatches, sizes, issues)
        assert valid is False
        assert any("empty" in issue.lower() for issue in issues)


class TestShouldRegenerateData:
    def test_force_regenerate_always_returns_true(self, tmp_path):
        v = BenchmarkDataValidator("tpch", scale_factor=0.01)
        should_regen, result = v.should_regenerate_data(tmp_path, force_regenerate=True)
        assert should_regen is True
        assert result.valid is False
        assert any("Force regeneration" in issue for issue in result.issues)

    def test_invalid_data_triggers_regeneration(self, tmp_path):
        v = BenchmarkDataValidator("tpch", scale_factor=0.01)
        should_regen, result = v.should_regenerate_data(tmp_path)
        assert should_regen is True
        assert result.valid is False


class TestValidationTimestamp:
    def test_timestamp_uses_consumer_clock(self, tmp_path):
        v = BenchmarkDataValidator("tpch", scale_factor=0.01)
        result = v.validate_data_directory(tmp_path)
        assert result.validation_timestamp == NOW


class TestFormatBytes:
    def test_zero_bytes(self):
        v = BenchmarkDataValidator("tpch")
        assert v._format_bytes(0) == "0 B"

    def test_kilobytes(self):
        v = BenchmarkDataValidator("tpch")
        formatted = v._format_bytes(1024)
        assert "KB" in formatted
        assert "1.0" in formatted

    def test_megabytes(self):
        v = BenchmarkDataValidator("tpch")
        formatted = v._format_bytes(1048576)
        assert "MB" in formatted

    def test_gigabytes(self):
        v = BenchmarkDataValidator("tpch")
        formatted = v._format_bytes(1073741824)
        assert "GB" in formatted


class TestEmptyManifestIsNotReusable:
    @staticmethod
    def _write_empty_manifest(data_dir: Path, benchmark: str) -> None:
        import json

        from benchbox.utils.datagen_version import current_datagen_stamp

        manifest = {
            "benchmark": benchmark,
            "scale_factor": 1.0,
            "tables": {},
            **current_datagen_stamp(benchmark),
        }
        (data_dir / "_datagen_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    @pytest.mark.parametrize("benchmark_name", ["tpch", "tpcds", "custom_bench"])
    def test_empty_manifest_with_data_files_is_invalid(self, tmp_path, benchmark_name):
        (tmp_path / "customer.tbl.zst").write_bytes(b"\x28\xb5\x2f\xfd" + b"x" * 64)
        self._write_empty_manifest(tmp_path, benchmark_name)

        result = BenchmarkDataValidator(benchmark_name, scale_factor=1.0).validate_data_directory(tmp_path)

        assert result.valid is False
        assert any("manifest" in issue.lower() for issue in result.issues), result.issues
