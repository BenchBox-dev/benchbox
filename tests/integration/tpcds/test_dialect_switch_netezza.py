# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import Mock, patch

import pytest

from benchbox.core.tpcds.c_tools import DSQGenBinary
from benchbox.core.tpcds.queries import TPCDSQueryManager
from benchbox.platforms.base import PlatformAdapter

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


class TestTPCDSDialectSwitch:
    def test_tpcds_query_manager_default_dialect_netezza(self):

        mock_dsqgen = Mock(spec=DSQGenBinary)
        mock_dsqgen.generate.return_value = "SELECT * FROM test LIMIT 100"

        with patch("benchbox.core.tpcds.queries.DSQGenBinary", return_value=mock_dsqgen):
            manager = TPCDSQueryManager()

            sql = manager.get_query(1, seed=12345, scale_factor=1.0)

            mock_dsqgen.generate.assert_called_once_with(
                1, seed=12345, scale_factor=1.0, stream_id=None, dialect="netezza"
            )
            assert sql == "SELECT * FROM test LIMIT 100"

    def test_tpcds_query_manager_explicit_ansi_dialect(self):

        mock_dsqgen = Mock(spec=DSQGenBinary)
        mock_dsqgen.generate.return_value = "SELECT TOP 100 * FROM test"

        with patch("benchbox.core.tpcds.queries.DSQGenBinary", return_value=mock_dsqgen):
            manager = TPCDSQueryManager()

            sql = manager.get_query(1, seed=12345, scale_factor=1.0, dialect="ansi")

            mock_dsqgen.generate.assert_called_once_with(
                1, seed=12345, scale_factor=1.0, stream_id=None, dialect="ansi"
            )
            assert sql == "SELECT TOP 100 * FROM test"

    def test_platform_adapter_tpcds_base_dialect(self):

        class MockAdapter(PlatformAdapter):
            @staticmethod
            def add_cli_arguments(parser):
                pass

            @classmethod
            def from_config(cls, config):
                return cls()

            @property
            def platform_name(self):
                return "Mock"

            def get_target_dialect(self):
                return "mock"

            def create_connection(self):
                pass

            def create_schema(self, benchmark):
                pass

            def load_data(self, benchmark, file_path, table_name):
                pass

            def execute_query(self, query):
                pass

            def configure_for_benchmark(self, benchmark):
                pass

            def apply_platform_optimizations(self, benchmark):
                pass

            def apply_constraint_configuration(self, benchmark):
                pass

        adapter = MockAdapter()

        base_dialect = adapter.get_tpc_base_dialect("tpcds")
        assert base_dialect == "netezza"

        base_dialect = adapter.get_tpc_base_dialect("TPCDS")
        assert base_dialect == "netezza"

        base_dialect = adapter.get_tpc_base_dialect("tpch")
        assert base_dialect == "netezza"

        base_dialect = adapter.get_tpc_base_dialect("ssb")
        assert base_dialect == "netezza"

    def test_all_supported_dialects_work(self):
        mock_dsqgen = Mock(spec=DSQGenBinary)

        test_cases = [
            ("netezza", "SELECT * FROM test LIMIT 100"),
            ("ansi", "SELECT TOP 100 * FROM test"),
            ("oracle", "SELECT * FROM test WHERE ROWNUM <= 100"),
            ("db2", "SELECT * FROM test FETCH FIRST 100 ROWS ONLY"),
            ("sqlserver", "SELECT TOP 100 * FROM test"),
        ]

        with patch("benchbox.core.tpcds.queries.DSQGenBinary", return_value=mock_dsqgen):
            manager = TPCDSQueryManager()

            for dialect, expected_sql in test_cases:
                mock_dsqgen.generate.return_value = expected_sql
                sql = manager.get_query(1, dialect=dialect)

                mock_dsqgen.generate.assert_called_with(1, seed=None, scale_factor=1.0, stream_id=None, dialect=dialect)
                assert sql == expected_sql

    def test_generate_with_parameters_uses_netezza_default(self):

        mock_dsqgen = Mock(spec=DSQGenBinary)
        mock_dsqgen.generate_with_parameters.return_value = "SELECT * FROM test WHERE x = 5 LIMIT 10"

        with patch("benchbox.core.tpcds.queries.DSQGenBinary", return_value=mock_dsqgen):
            manager = TPCDSQueryManager()

            sql = manager.generate_with_parameters(1, {"x": 5}, scale_factor=1.0)

            mock_dsqgen.generate_with_parameters.assert_called_once_with(
                1, {"x": 5}, scale_factor=1.0, dialect="netezza"
            )
            assert sql == "SELECT * FROM test WHERE x = 5 LIMIT 10"

    def test_backward_compatibility_maintained(self):

        mock_dsqgen = Mock(spec=DSQGenBinary)
        mock_dsqgen.generate.return_value = "SELECT TOP 100 * FROM test"

        with patch("benchbox.core.tpcds.queries.DSQGenBinary", return_value=mock_dsqgen):
            manager = TPCDSQueryManager()

            sql = manager.get_query(1, seed=42, scale_factor=0.1, dialect="ansi")

            mock_dsqgen.generate.assert_called_once_with(1, seed=42, scale_factor=0.1, stream_id=None, dialect="ansi")
            assert sql == "SELECT TOP 100 * FROM test"
