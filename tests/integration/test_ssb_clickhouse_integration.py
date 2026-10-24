import shutil
import tempfile
from pathlib import Path

import pytest

from benchbox.core.ssb.benchmark import SSBBenchmark
from benchbox.platforms.clickhouse import ClickHouseAdapter
from tests.utilities.optional_engines import require_chdb

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


chdb = require_chdb()


def _drop_default_tables(client) -> None:
    rows = client.execute("SELECT name FROM system.tables WHERE database = 'default'")
    for (name,) in rows:
        client.execute(f"DROP TABLE `{name}`")


@pytest.fixture
def chdb_client():
    from benchbox.platforms.clickhouse import ClickHouseLocalClient

    client = ClickHouseLocalClient()
    _drop_default_tables(client)
    try:
        yield client
    finally:
        _drop_default_tables(client)
        client.close()


class TestSSBClickHouseIntegration:
    def test_ssb_csv_loading_configuration(self):

        benchmark = SSBBenchmark(scale_factor=0.01, compress_data=False, compression_type="none")

        csv_config = benchmark.get_csv_loading_config("date")

        assert csv_config is not None
        assert isinstance(csv_config, list)
        assert len(csv_config) > 0

        delimiter_configs = [item for item in csv_config if "delim=" in item]
        assert len(delimiter_configs) > 0
        assert any("|" in item for item in delimiter_configs)

    def test_clickhouse_tuning_configuration_override(self):

        adapter = ClickHouseAdapter(deployment_mode="local")

        config = adapter.get_effective_tuning_configuration()
        assert config is not None

        assert config.primary_keys.enabled is True

        assert config.foreign_keys.enabled is False

    def test_clickhouse_constraint_configuration(self):

        adapter = ClickHouseAdapter(deployment_mode="local")

        enable_primary_keys, enable_foreign_keys = adapter._get_constraint_configuration()

        assert enable_primary_keys is True
        assert enable_foreign_keys is False

    def test_ssb_schema_creation_without_engine(self, chdb_client):
        client = chdb_client

        adapter = ClickHouseAdapter(deployment_mode="local")
        benchmark = SSBBenchmark(scale_factor=0.01, compress_data=False, compression_type="none")

        duration = adapter.create_schema(benchmark, client)
        assert duration > 0

        tables = client.execute("SHOW TABLES")
        table_names = {t[0] for t in tables}
        expected_tables = {"date", "customer", "supplier", "part", "lineorder"}
        assert table_names == expected_tables

        engine_query = "SELECT name, engine FROM system.tables WHERE database = 'default' ORDER BY name"
        engine_result = client.execute(engine_query)

        for name, engine in engine_result:
            assert engine == "MergeTree", f"Table {name} should use MergeTree engine, got {engine}"

    def test_ssb_data_loading_with_clickhouse(self, chdb_client):

        data_dir = Path(tempfile.mkdtemp(prefix="ssb-data-"))
        client = chdb_client

        try:
            adapter = ClickHouseAdapter(deployment_mode="local")
            benchmark = SSBBenchmark(
                scale_factor=0.01,
                output_dir=data_dir,
                compress_data=False,
                compression_type="none",
            )

            benchmark.generate_data()
            assert benchmark.tables
            assert len(benchmark.tables) == 5

            adapter.create_schema(benchmark, client)

            test_tables = ["date", "customer"]
            for table_name in test_tables:
                data_file = Path(benchmark.tables[table_name])
                assert data_file.exists(), f"Data file for {table_name} should exist"

                table_stats, load_time, _ = adapter.load_data(benchmark, client, data_file.parent)
                assert table_name in table_stats
                assert table_stats[table_name] > 0, f"Should have loaded rows into {table_name}"

            for table_name in test_tables:
                count_result = client.execute(f"SELECT COUNT(*) FROM {table_name}")
                row_count = count_result[0][0]
                assert row_count > 0, f"Table {table_name} should have data"
        finally:
            if data_dir.exists():
                shutil.rmtree(data_dir)

    def test_ssb_query_execution_clickhouse(self, chdb_client):
        data_dir = Path(tempfile.mkdtemp(prefix="ssb-data-"))
        client = chdb_client

        try:
            adapter = ClickHouseAdapter(deployment_mode="local")
            benchmark = SSBBenchmark(
                scale_factor=0.01,
                output_dir=data_dir,
                compress_data=False,
                compression_type="none",
            )

            benchmark.generate_data()
            adapter.create_schema(benchmark, client)

            data_file = Path(benchmark.tables["date"])
            adapter.load_data(benchmark, client, data_file.parent)

            simple_query = """
                SELECT d_year, COUNT(*)
                FROM date
                WHERE d_year >= 1992 AND d_year <= 1997
                GROUP BY d_year
                ORDER BY d_year
            """

            result = client.execute(simple_query)
            assert len(result) > 0
        finally:
            if data_dir.exists():
                shutil.rmtree(data_dir)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
