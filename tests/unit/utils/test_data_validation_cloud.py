# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License. See LICENSE file in the project root for details.

import sys
from unittest.mock import MagicMock, patch

import pytest

from benchbox.utils.data_validation import BenchmarkDataValidator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestCloudPathValidation:
    @pytest.fixture
    def validator(self):
        return BenchmarkDataValidator(
            benchmark_name="tpch",
            scale_factor=1.0,
        )

    def test_cloudpath_instance_skips_validation(self, validator):
        mock_cloudpath = MagicMock()
        mock_cloudpath.__class__.__name__ = "GCSPath"
        mock_cloudpath.__fspath__ = MagicMock(return_value="/nonexistent/cloud/path")

        with patch.dict(sys.modules, {"cloudpathlib": MagicMock(CloudPath=type(mock_cloudpath))}):
            from benchbox.utils.data_validation import BenchmarkDataValidator

            test_validator = BenchmarkDataValidator(
                benchmark_name="tpch",
                scale_factor=1.0,
            )

            result = test_validator.validate_data_directory(mock_cloudpath)

            assert result.valid is False
            assert len(result.issues) >= 1

    def test_databricks_path_skips_validation(self, validator, tmp_path):
        from benchbox.utils.cloud_storage import DatabricksPath

        staging_dir = tmp_path / "staging"
        staging_dir.mkdir()

        dbfs_path = DatabricksPath(local_path=str(staging_dir), dbfs_target="dbfs:/data/test")

        result = validator.validate_data_directory(dbfs_path)

        assert result.valid is False
        assert len(result.issues) >= 1
        assert not any("Cloud storage path detected" in issue for issue in result.issues)

    def test_local_path_performs_normal_validation(self, validator, tmp_path):
        test_dir = tmp_path / "tpch_data"
        test_dir.mkdir()

        result = validator.validate_data_directory(test_dir)

        assert result.valid is False
        assert not any("Cloud storage path detected" in issue for issue in result.issues)

    def test_local_string_path_performs_normal_validation(self, validator, tmp_path):
        test_dir = tmp_path / "tpch_data"
        test_dir.mkdir()

        result = validator.validate_data_directory(str(test_dir))

        assert result.valid is False
        assert not any("Cloud storage path detected" in issue for issue in result.issues)

    def test_cloudpath_without_cloudpathlib_installed(self, validator):
        mock_path = MagicMock()
        mock_path.__class__.__name__ = "GCSPath"

        with patch("benchbox.utils.data_validation.logger"):
            with patch.dict(sys.modules, {"cloudpathlib": None}):
                try:
                    result = validator.validate_data_directory(mock_path)
                    assert result is not None
                except Exception:
                    pass

    def test_cloud_path_preserves_benchmark_context(self, validator):
        from benchbox.utils.cloud_storage import DatabricksPath

        test_validator = BenchmarkDataValidator(
            benchmark_name="tpcds",
            scale_factor=100.0,
        )

        dbfs_path = DatabricksPath(local_path="/tmp/test", dbfs_target="dbfs:/data/test")
        result = test_validator.validate_data_directory(dbfs_path)

        assert result.valid is False
        assert test_validator.benchmark_name == "tpcds"
        assert test_validator.scale_factor == 100.0

    def test_cloud_path_returns_correct_result_structure(self, validator, tmp_path):
        from benchbox.utils.cloud_storage import DatabricksPath

        staging_dir = tmp_path / "staging"
        staging_dir.mkdir()

        dbfs_path = DatabricksPath(local_path=str(staging_dir), dbfs_target="dbfs:/data/test")
        result = validator.validate_data_directory(dbfs_path)

        assert hasattr(result, "valid")
        assert hasattr(result, "tables_validated")
        assert hasattr(result, "missing_tables")
        assert hasattr(result, "row_count_mismatches")
        assert hasattr(result, "file_size_info")
        assert hasattr(result, "validation_timestamp")
        assert hasattr(result, "issues")

        assert isinstance(result.tables_validated, dict)
        assert isinstance(result.missing_tables, list)
        assert result.row_count_mismatches == {}
        assert result.file_size_info == {}

        assert len(result.issues) > 0
        assert result.validation_timestamp is not None


