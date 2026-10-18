import shutil
import tempfile
from pathlib import Path

import pytest

from benchbox.core.clickbench.benchmark import ClickBenchBenchmark
from benchbox.platforms.clickhouse import ClickHouseAdapter
from tests.utilities.optional_engines import require_chdb

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
]


chdb = require_chdb()


class TestClickHouseLocalCrossPlatform:
    def test_primary_keys_always_enabled(self):

        adapter = ClickHouseAdapter(deployment_mode="local")

        enable_primary_keys, enable_foreign_keys = adapter._get_constraint_configuration()

        assert enable_primary_keys is True
        assert enable_foreign_keys is False

    def test_database_directory_removal(self):

        from pathlib import Path

        temp_dir = tempfile.mkdtemp(suffix=".db.chdb")
        temp_path = Path(temp_dir)

        (temp_path / "data.bin").touch()
        (temp_path / "metadata.json").touch()

        assert temp_path.exists()
        assert temp_path.is_dir()

        try:
            if temp_path.is_file():
                temp_path.unlink()
            elif temp_path.is_dir():
                shutil.rmtree(temp_path)
            else:
                pass

            assert not temp_path.exists()

        except Exception as e:
            if temp_path.exists():
                shutil.rmtree(temp_path)
            raise Exception(f"Database directory removal failed: {e}") from e

    def test_local_client_session_handling(self):

        from benchbox.platforms.clickhouse import ClickHouseLocalClient

        temp_dir = tempfile.mkdtemp(suffix=".db.chdb")

        try:
            client = ClickHouseLocalClient(db_path=temp_dir)

            result = client.execute("SELECT 1")
            assert result is not None

            client.close()

            client2 = ClickHouseLocalClient(db_path=temp_dir)
            client2.close()

        finally:
            if Path(temp_dir).exists():
                shutil.rmtree(temp_dir)

    def test_benchmark_integration(self):

        benchmark = ClickBenchBenchmark(scale_factor=0.01)
        ClickHouseAdapter(deployment_mode="local")

        assert hasattr(benchmark, "get_csv_loading_config")

        config = benchmark.get_csv_loading_config("hits")
        assert config is not None
        assert len(config) > 0

        delimiter_config = [item for item in config if "delim=" in item]
        assert len(delimiter_config) > 0

        assert any("|" in item for item in delimiter_config)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
