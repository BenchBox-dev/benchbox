# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, Mock, patch

import pytest

from benchbox.core.tuning.interface import TuningType

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _make_adapter(**kwargs):

    try:
        from benchbox.platforms.redshift import RedshiftAdapter
    except ImportError:
        pytest.skip("Redshift drivers not installed")

    defaults = {
        "host": "test.redshift.amazonaws.com",
        "database": "testdb",
        "username": "testuser",
        "password": "testpass",
        "strict_validation": False,
    }
    defaults.update(kwargs)
    return RedshiftAdapter(**defaults)


class TestRedshiftGenerateTuningClause:
    def test_no_tuning_returns_empty(self):
        adapter = _make_adapter()
        result = adapter.generate_tuning_clause(None)
        assert result == ""

    def test_no_distribution_cols_gives_diststyle_even(self):
        adapter = _make_adapter()
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True
        mock_tuning.get_columns_by_type.return_value = []
        clause = adapter.generate_tuning_clause(mock_tuning)

        assert "DISTSTYLE EVEN" in clause
        assert "DISTKEY" not in clause

    def test_with_distribution_col_gives_distkey(self):
        adapter = _make_adapter()
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True
        mock_col = Mock()
        mock_col.name = "l_orderkey"
        mock_col.order = 1

        def _cols_by_type(t):
            if t == TuningType.DISTRIBUTION:
                return [mock_col]
            return []

        mock_tuning.get_columns_by_type.side_effect = _cols_by_type
        clause = adapter.generate_tuning_clause(mock_tuning)

        assert "DISTSTYLE KEY" in clause
        assert "DISTKEY (l_orderkey)" in clause

    def test_with_sort_cols_gives_sortkey(self):
        adapter = _make_adapter()
        mock_tuning = Mock()
        mock_tuning.has_any_tuning.return_value = True
        col1, col2 = Mock(), Mock()
        col1.name = "l_shipdate"
        col1.order = 1
        col2.name = "l_returnflag"
        col2.order = 2

        def _cols_by_type(t):
            if t == TuningType.SORTING:
                return [col1, col2]
            return []

        mock_tuning.get_columns_by_type.side_effect = _cols_by_type
        clause = adapter.generate_tuning_clause(mock_tuning)

        assert "SORTKEY (l_shipdate, l_returnflag)" in clause


class TestRedshiftGetCopyCredentials:
    def test_iam_role_preferred(self):
        adapter = _make_adapter(iam_role="arn:aws:iam::123:role/TestRole")
        result = adapter._get_copy_credentials_clause()
        assert result == "IAM_ROLE 'arn:aws:iam::123:role/TestRole'"

    def test_key_pair_when_no_role(self):
        adapter = _make_adapter(
            aws_access_key_id="AKIA123",
            aws_secret_access_key="secret123",
        )
        result = adapter._get_copy_credentials_clause()
        assert "ACCESS_KEY_ID 'AKIA123'" in result
        assert "SECRET_ACCESS_KEY 'secret123'" in result

    def test_empty_string_when_no_credentials(self):
        adapter = _make_adapter()
        result = adapter._get_copy_credentials_clause()
        assert result == ""


class TestRedshiftBuildS3CopySourceSingleFile:
    def test_single_uri_returns_uri_directly(self):
        adapter = _make_adapter(s3_bucket="mybucket", s3_prefix="data")
        mock_s3 = Mock()
        path, option = adapter._build_s3_copy_source(mock_s3, ["s3://mybucket/data/lineitem_0.tbl"], "lineitem")
        assert path == "s3://mybucket/data/lineitem_0.tbl"
        assert option == ""
        mock_s3.put_object.assert_not_called()


class TestRedshiftConnectionConfig:
    def test_connection_params_include_host_port_db(self):
        adapter = _make_adapter(
            host="mycluster.redshift.amazonaws.com",
            port=5439,
            database="mydb",
            username="admin",
        )
        params = adapter._get_connection_params()
        assert params["host"] == "mycluster.redshift.amazonaws.com"
        assert params["port"] == 5439
        assert params["database"] == "mydb"
        assert params["user"] == "admin"
        assert "sslmode" in params

    def test_overrides_take_precedence(self):
        adapter = _make_adapter(host="original.redshift.amazonaws.com")
        params = adapter._get_connection_params(host="override.redshift.amazonaws.com")
        assert params["host"] == "override.redshift.amazonaws.com"
