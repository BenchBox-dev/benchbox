# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License.

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.core.connection import DatabaseConnection
from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark
from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestPrimitivesDatabaseReuse:
    @pytest.fixture
    def primitives_benchmark(self):
        return ReadPrimitivesBenchmark(scale_factor=0.01, verbose=False)

    @pytest.fixture
    def mock_connection(self):
        connection = Mock(spec=DatabaseConnection)
        return connection

    def test_check_compatible_tpch_database_success(self, primitives_benchmark, mock_connection):
        mock_connection.execute.return_value = [(1,)]

        def mock_execute(query):
            if "COUNT(*) FROM lineitem" in query:
                return [(60000,)]
            else:
                return [(1,)]

        mock_connection.execute.side_effect = mock_execute

        result = primitives_benchmark._check_compatible_tpch_database(mock_connection)

        assert result is True
        expected_tables = [
            "region",
            "nation",
            "customer",
            "supplier",
            "part",
            "partsupp",
            "orders",
            "lineitem",
        ]
        table_queries = [call.args[0] for call in mock_connection.execute.call_args_list if "COUNT(*)" in call.args[0]]

        assert len(table_queries) >= len(expected_tables)

    def test_check_compatible_tpch_database_missing_table(self, primitives_benchmark, mock_connection):
        mock_connection.execute.side_effect = Exception("Table doesn't exist")

        result = primitives_benchmark._check_compatible_tpch_database(mock_connection)

        assert result is False

    def test_check_compatible_tpch_database_wrong_scale(self, primitives_benchmark, mock_connection):

        def mock_execute(query):
            if "COUNT(*) FROM lineitem" in query:
                return [(6000000,)]
            else:
                return [(1,)]

        mock_connection.execute.side_effect = mock_execute

        result = primitives_benchmark._check_compatible_tpch_database(mock_connection)

        assert result is False

    def test_load_data_with_compatible_database(self, primitives_benchmark, mock_connection):
        with patch.object(primitives_benchmark, "_check_compatible_tpch_database", return_value=True):
            primitives_benchmark._load_data(mock_connection)

        assert mock_connection.execute.call_count == 0

    def test_load_data_without_compatible_database(self, primitives_benchmark, mock_connection):
        primitives_benchmark.tables = {
            "region": Path("/tmp/region.tbl"),
            "nation": Path("/tmp/nation.tbl"),
        }

        with patch.object(primitives_benchmark, "_check_compatible_tpch_database", return_value=False):
            with (
                patch("pathlib.Path.exists", return_value=True),
                patch("builtins.open", mock_open_csv_data()),
                patch.object(
                    primitives_benchmark,
                    "get_create_tables_sql",
                    return_value="CREATE TABLE test (id INT)",
                ),
            ):
                primitives_benchmark._load_data(mock_connection)

        assert mock_connection.execute.call_count > 0

    def test_validate_database_configuration_compatibility(self, primitives_benchmark):
        tpch_config = {"benchmark_type": "tpch", "scale_factor": 0.01}

        result = primitives_benchmark._validate_database_configuration_compatibility(tpch_config)
        assert result is True

        other_config = {"benchmark_type": "tpcds", "scale_factor": 0.01}

        result = primitives_benchmark._validate_database_configuration_compatibility(other_config)
        assert result is False

        scale_config = {
            "benchmark_type": "tpch",
            "scale_factor": 1.0,
        }

        result = primitives_benchmark._validate_database_configuration_compatibility(scale_config)
        assert result is False


class TestTPCHavocDatabaseReuse:
    @pytest.fixture
    def tpchavoc_benchmark(self):
        return TPCHavocBenchmark(scale_factor=0.01, verbose=False)

    @pytest.fixture
    def mock_connection(self):
        connection = Mock()
        return connection

    def test_check_compatible_tpch_database_success(self, tpchavoc_benchmark, mock_connection):

        def mock_execute(query):
            if "COUNT(*) FROM lineitem" in query:
                return [(60000,)]
            else:
                return [(1,)]

        mock_connection.execute = Mock(side_effect=mock_execute)
        mock_connection.commit = Mock()

        result = tpchavoc_benchmark._check_compatible_tpch_database(mock_connection)

        assert result is True

    def test_check_compatible_tpch_database_missing_table(self, tpchavoc_benchmark, mock_connection):
        mock_connection.execute = Mock(side_effect=Exception("Table doesn't exist"))
        mock_connection.commit = Mock()

        result = tpchavoc_benchmark._check_compatible_tpch_database(mock_connection)

        assert result is False

    def test_load_data_with_compatible_database(self, tpchavoc_benchmark, mock_connection):
        with (
            patch.object(tpchavoc_benchmark, "_check_compatible_tpch_database", return_value=True),
            patch("benchbox.core.tpch.benchmark.TPCHBenchmark._load_data") as mock_parent_load,
        ):
            tpchavoc_benchmark._load_data(mock_connection)

        mock_parent_load.assert_not_called()

    def test_load_data_without_compatible_database(self, tpchavoc_benchmark, mock_connection):
        with patch.object(tpchavoc_benchmark, "_check_compatible_tpch_database", return_value=False):
            with patch("benchbox.core.tpch.benchmark.TPCHBenchmark._load_data") as mock_parent_load:
                tpchavoc_benchmark._load_data(mock_connection)

                mock_parent_load.assert_called_once()

    def test_validate_database_configuration_compatibility(self, tpchavoc_benchmark):
        tpch_config = {"benchmark_type": "tpch", "scale_factor": 0.01}

        result = tpchavoc_benchmark._validate_database_configuration_compatibility(tpch_config)
        assert result is True

        havoc_config = {"benchmark_type": "tpchavoc", "scale_factor": 0.01}

        result = tpchavoc_benchmark._validate_database_configuration_compatibility(havoc_config)
        assert result is True

        other_config = {"benchmark_type": "tpcds", "scale_factor": 0.01}

        result = tpchavoc_benchmark._validate_database_configuration_compatibility(other_config)
        assert result is False


class TestDatabaseReuseIntegration:
    def test_primitives_inherits_tpch_compatibility(self):
        primitives = ReadPrimitivesBenchmark(scale_factor=0.01)

        tpch_config = {
            "benchmark_type": "tpch",
            "scale_factor": 0.01,
            "tuning_enabled": False,
        }

        result = primitives._validate_database_configuration_compatibility(tpch_config)
        assert result is True

    def test_tpchavoc_inherits_tpch_compatibility(self):
        tpchavoc = TPCHavocBenchmark(scale_factor=0.01)

        tpch_config = {
            "benchmark_type": "tpch",
            "scale_factor": 0.01,
            "tuning_enabled": False,
        }

        result = tpchavoc._validate_database_configuration_compatibility(tpch_config)
        assert result is True

    def test_cross_benchmark_compatibility(self):
        ReadPrimitivesBenchmark(scale_factor=0.01)
        tpchavoc = TPCHavocBenchmark(scale_factor=0.01)

        primitives_config = {"benchmark_type": "read_primitives", "scale_factor": 0.01}

        result = tpchavoc._validate_database_configuration_compatibility(primitives_config)
        assert result is False

    def test_scale_factor_mismatch_prevents_reuse(self):
        primitives_small = ReadPrimitivesBenchmark(scale_factor=0.01)

        large_config = {
            "benchmark_type": "tpch",
            "scale_factor": 1.0,
        }

        result = primitives_small._validate_database_configuration_compatibility(large_config)
        assert result is False


def mock_open_csv_data():
    from unittest.mock import mock_open

    csv_content = "1|REGION1|\n2|REGION2|\n"
    return mock_open(read_data=csv_content)


@pytest.mark.integration
class TestDatabaseReuseEndToEnd:
    def test_primitives_database_reuse_workflow(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            primitives = ReadPrimitivesBenchmark(scale_factor=0.01, output_dir=temp_dir, verbose=True)

            mock_connection = Mock(spec=DatabaseConnection)

            def mock_execute(query):
                if "COUNT(*) FROM lineitem" in query:
                    return [(60000,)]
                else:
                    return [(1,)]

            mock_connection.execute.side_effect = mock_execute

            result = primitives._check_compatible_tpch_database(mock_connection)
            assert result is True

            with patch.object(primitives, "_check_compatible_tpch_database", return_value=True):
                primitives._load_data(mock_connection)

    def test_tpchavoc_database_reuse_workflow(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tpchavoc = TPCHavocBenchmark(scale_factor=0.01, output_dir=temp_dir, verbose=True)

            mock_connection = Mock()
            mock_db_connection = Mock(spec=DatabaseConnection)

            def mock_execute(query):
                if "COUNT(*) FROM lineitem" in query:
                    return [(60000,)]
                else:
                    return [(1,)]

            mock_db_connection.execute.side_effect = mock_execute

            mock_connection.execute = Mock(side_effect=mock_execute)
            mock_connection.commit = Mock()

            result = tpchavoc._check_compatible_tpch_database(mock_connection)
            assert result is True

            with patch.object(tpchavoc, "_check_compatible_tpch_database", return_value=True):
                tpchavoc._load_data(mock_connection)


