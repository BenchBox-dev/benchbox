from __future__ import annotations

import warnings

import duckdb
import pytest

from benchbox import TPCDS, TPCH
from benchbox.core.tpcds.benchmark import TPCDSBenchmark
from benchbox.core.tpcds.official_benchmark import TPCDSOfficialBenchmark, TPCDSOfficialBenchmarkConfig
from benchbox.core.tpch.official_benchmark import TPCHOfficialBenchmark, TPCHOfficialBenchmarkConfig
from benchbox.platforms.base.connection_wrappers import (
    StreamConnectionCapability,
    resolve_stream_connection_capability,
)
from benchbox.platforms.duckdb import DuckDBAdapter

pytestmark = [
    pytest.mark.integration,
    pytest.mark.duckdb,
    pytest.mark.medium,
    pytest.mark.filterwarnings("ignore::DeprecationWarning"),
]

SCALE_FACTOR = 0.01


def _loaded_database(benchmark, directory):
    benchmark.generate_data()
    path = directory / "bench.duckdb"
    adapter = DuckDBAdapter(database_path=str(path))
    connection = adapter.create_connection()
    adapter.create_schema(benchmark, connection)
    adapter.load_data(benchmark, connection, directory)
    return adapter, connection, path


@pytest.fixture(scope="module")
def tpch_database(tmp_path_factory):
    directory = tmp_path_factory.mktemp("tpch_sf001")
    benchmark = TPCH(scale_factor=SCALE_FACTOR, output_dir=directory)
    adapter, connection, path = _loaded_database(benchmark, directory)
    yield adapter, connection, path
    connection.close()


@pytest.fixture(scope="module")
def tpcds_database(tmp_path_factory):
    directory = tmp_path_factory.mktemp("tpcds_sf001")
    benchmark = TPCDS(scale_factor=SCALE_FACTOR, output_dir=directory)
    adapter, connection, path = _loaded_database(benchmark, directory)
    yield adapter, connection, path
    connection.close()


@pytest.fixture
def unsupported_adapter(tmp_path):
    datafusion = pytest.importorskip("benchbox.platforms.datafusion")
    adapter = datafusion.DataFusionAdapter(working_dir=str(tmp_path / "wd"))
    assert resolve_stream_connection_capability(adapter) == (StreamConnectionCapability.UNSUPPORTED, True)
    return adapter


def _tpch_config(directory, **overrides):
    values = {
        "scale_factor": SCALE_FACTOR,
        "num_streams": 2,
        "power_test_enabled": False,
        "maintenance_test_enabled": False,
        "output_dir": directory,
    }
    values.update(overrides)
    return TPCHOfficialBenchmarkConfig(**values)


class TestTPCHOfficialBenchmark:
    def test_supported_adapter_runs_throughput_and_warns(self, tpch_database, tmp_path):
        adapter, _connection, path = tpch_database
        official = TPCHOfficialBenchmark(scale_factor=SCALE_FACTOR, output_dir=tmp_path)

        with pytest.warns(DeprecationWarning, match="TPCHOfficialBenchmark.run_official_benchmark is deprecated"):
            result = official.run_official_benchmark(
                lambda: duckdb.connect(str(path)), _tpch_config(tmp_path), adapter=adapter
            )

        assert result.success is True, result.errors
        assert result.throughput_at_size > 0
        assert result.throughput_test_result.streams_successful == 2
        assert not hasattr(result, "qphh_at_size")

    def test_unsupported_adapter_is_refused_and_publishes_no_metric(self, tpch_database, unsupported_adapter, tmp_path):
        _adapter, _connection, path = tpch_database
        official = TPCHOfficialBenchmark(scale_factor=SCALE_FACTOR, output_dir=tmp_path)

        result = official.run_official_benchmark(
            lambda: duckdb.connect(str(path)), _tpch_config(tmp_path), adapter=unsupported_adapter
        )

        assert result.success is False
        assert result.throughput_at_size == 0.0
        assert any("UNSUPPORTED" in error for error in result.errors)

    def test_missing_adapter_is_refused(self, tpch_database, tmp_path):
        _adapter, _connection, path = tpch_database
        official = TPCHOfficialBenchmark(scale_factor=SCALE_FACTOR, output_dir=tmp_path)

        with pytest.raises(TypeError, match=r"adapter=.*benchbox run --phases throughput"):
            official.run_official_benchmark(lambda: duckdb.connect(str(path)), _tpch_config(tmp_path))

    def test_facade_forwards_the_adapter(self, tpch_database, unsupported_adapter, tmp_path):
        adapter, _connection, path = tpch_database
        facade = TPCH(scale_factor=SCALE_FACTOR, output_dir=tmp_path)
        config = _tpch_config(tmp_path)

        with pytest.raises(TypeError, match="adapter="):
            facade.run_official_benchmark(lambda: duckdb.connect(str(path)), config)
        refused = facade.run_official_benchmark(lambda: duckdb.connect(str(path)), config, adapter=unsupported_adapter)
        ran = facade.run_official_benchmark(lambda: duckdb.connect(str(path)), config, adapter=adapter)

        assert any("UNSUPPORTED" in error for error in refused.errors)
        assert ran.success is True, ran.errors


class TestTPCDSOfficialBenchmark:
    def _config(self, directory):
        return TPCDSOfficialBenchmarkConfig(
            scale_factor=SCALE_FACTOR,
            num_streams=2,
            power_test_enabled=False,
            maintenance_test_enabled=False,
            output_dir=directory,
        )

    def _official(self, directory):
        return TPCDSOfficialBenchmark(scale_factor=SCALE_FACTOR, output_dir=directory, dialect="duckdb")

    def test_supported_adapter_runs_throughput_and_warns(self, tpcds_database, tmp_path):
        adapter, _connection, path = tpcds_database

        with pytest.warns(DeprecationWarning, match="TPCDSOfficialBenchmark.run_official_benchmark is deprecated"):
            result = self._official(tmp_path).run_official_benchmark(
                lambda: duckdb.connect(str(path)), self._config(tmp_path), adapter=adapter
            )

        assert result.success is True, result.errors
        assert result.throughput_at_size > 0
        assert result.throughput_test_result.config.scale_factor == SCALE_FACTOR
        assert not hasattr(result, "qphds_at_size")

    def test_unsupported_adapter_is_refused_and_publishes_no_metric(
        self, tpcds_database, unsupported_adapter, tmp_path
    ):
        _adapter, _connection, path = tpcds_database

        result = self._official(tmp_path).run_official_benchmark(
            lambda: duckdb.connect(str(path)), self._config(tmp_path), adapter=unsupported_adapter
        )

        assert result.success is False
        assert result.throughput_at_size == 0.0
        assert any("UNSUPPORTED" in error for error in result.errors)

    def test_missing_adapter_is_refused(self, tpcds_database, tmp_path):
        _adapter, _connection, path = tpcds_database

        with pytest.raises(TypeError, match=r"adapter=.*benchbox run --phases throughput"):
            self._official(tmp_path).run_official_benchmark(lambda: duckdb.connect(str(path)), self._config(tmp_path))


class TestTPCDSBenchmarkThroughput:
    def _benchmark(self, directory):
        return TPCDSBenchmark(scale_factor=SCALE_FACTOR, output_dir=directory)

    def test_supported_adapter_runs_and_warns(self, tpcds_database, tmp_path):
        adapter, connection, _path = tpcds_database

        with pytest.warns(DeprecationWarning, match="TPCDSBenchmark.run_throughput_test is deprecated"):
            result = self._benchmark(tmp_path).run_throughput_test(
                num_streams=2, base_seed=7, adapter=adapter, connection=connection
            )

        assert result.success is True, result.error
        assert result.streams_successful == 2
        assert result.throughput_at_size > 0

    def test_connection_factory_is_optional_and_warns_when_given(self, tpcds_database, tmp_path):
        adapter, connection, _path = tpcds_database
        benchmark = self._benchmark(tmp_path)

        with pytest.warns(UserWarning, match="connection_factory is ignored"):
            benchmark.run_throughput_test(lambda: None, num_streams=2, adapter=adapter, connection=connection)
        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            result = benchmark.run_throughput_test(num_streams=2, adapter=adapter, connection=connection)

        assert result.success is True, result.error

    def test_unsupported_adapter_is_refused(self, tpcds_database, unsupported_adapter, tmp_path):
        _adapter, connection, _path = tpcds_database

        with pytest.raises(RuntimeError, match="UNSUPPORTED"):
            self._benchmark(tmp_path).run_throughput_test(
                num_streams=2, adapter=unsupported_adapter, connection=connection
            )

    def test_missing_adapter_is_refused(self, tpcds_database, tmp_path):
        _adapter, _connection, path = tpcds_database

        with pytest.raises(TypeError, match=r"adapter=.*benchbox run --phases throughput"):
            self._benchmark(tmp_path).run_throughput_test(lambda: duckdb.connect(str(path)), num_streams=2)

    def test_official_run_with_supported_adapter(self, tpcds_database, tmp_path):
        adapter, connection, _path = tpcds_database

        result = self._benchmark(tmp_path).run_official_benchmark(
            connection,
            num_streams=2,
            power_test=False,
            maintenance_test=False,
            dialect="duckdb",
            adapter=adapter,
        )

        assert result["success"] is True, result["errors"]
        assert result["throughput_at_size"] > 0
        assert "qphds_at_size" not in result

    def test_official_run_with_unsupported_adapter_publishes_no_metric(
        self, tpcds_database, unsupported_adapter, tmp_path
    ):
        _adapter, connection, _path = tpcds_database

        result = self._benchmark(tmp_path).run_official_benchmark(
            connection,
            num_streams=2,
            power_test=False,
            maintenance_test=False,
            adapter=unsupported_adapter,
        )

        assert result["success"] is False
        assert result["throughput_at_size"] == 0.0
        assert any("UNSUPPORTED" in error for error in result["errors"])

    def test_official_run_without_adapter_is_refused(self, tpcds_database, tmp_path):
        _adapter, connection, _path = tpcds_database

        with pytest.raises(TypeError, match=r"adapter=.*benchbox run --phases throughput"):
            self._benchmark(tmp_path).run_official_benchmark(
                connection, num_streams=2, power_test=False, maintenance_test=False
            )


class TestAdapterEntryPoint:
    def test_supported_adapter_returns_the_legacy_keys(self, tpcds_database, tmp_path):
        adapter, connection, _path = tpcds_database
        benchmark = TPCDS(scale_factor=SCALE_FACTOR, output_dir=tmp_path)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = adapter.run_throughput_test(benchmark, connection=connection, num_streams=2, base_seed=7)

        assert result["success"] is True
        assert result["throughput_at_size"] > 0
        assert result["total_duration"] == result["total_time"]
        assert isinstance(result["stream_results"][0], dict)
        assert result["error"] is None

    def test_unsupported_adapter_is_refused(self, tpcds_database, unsupported_adapter, tmp_path):
        _adapter, connection, _path = tpcds_database
        benchmark = TPCDS(scale_factor=SCALE_FACTOR, output_dir=tmp_path)

        with pytest.raises(RuntimeError, match="UNSUPPORTED"):
            unsupported_adapter.run_throughput_test(benchmark, connection=connection, num_streams=2)


