import json
import logging
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from benchbox.base import BaseBenchmark
from benchbox.core.errors import ScaleFactorNotSupportedError
from benchbox.core.nyctaxi.benchmark import NYCTaxiBenchmark
from benchbox.core.results.models import BenchmarkResults
from benchbox.core.runner import LifecyclePhases, ValidationOptions, run_benchmark_lifecycle
from benchbox.core.schemas import BenchmarkConfig, DatabaseConfig, SystemProfile
from benchbox.core.tsbs_devops.benchmark import TSBSDevOpsBenchmark
from benchbox.core.validation import ValidationResult
from benchbox.utils.datagen_version import current_datagen_stamp
from benchbox.utils.verbosity import VerbosityMixin, VerbositySettings
from tests.fixtures.result_dict_fixtures import make_benchmark_results

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


def _mk_system_profile():
    return SystemProfile(
        os_name="Linux",
        os_version="6.6",
        architecture="x86_64",
        cpu_model="Test CPU",
        cpu_cores_physical=4,
        cpu_cores_logical=8,
        memory_total_gb=16.0,
        memory_available_gb=12.0,
        python_version="3.11",
        disk_space_gb=256.0,
        timestamp=datetime.now(),
        hostname="host",
    )


@pytest.mark.unit
def test_lifecycle_rejects_unsupported_scale_before_using_supplied_instance():
    cfg = BenchmarkConfig(
        name="joinorder",
        display_name="JoinOrder",
        scale_factor=0.5,
        test_execution_type="data_only",
    )
    bench = MagicMock()

    with pytest.raises(ScaleFactorNotSupportedError, match=r"joinorder accepts scale_factor in \[1\.0\]"):
        run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=None,
            system_profile=_mk_system_profile(),
            benchmark_instance=bench,
        )

    bench.generate_data.assert_not_called()


@pytest.mark.unit
def test_data_only_mode_returns_result_without_adapter():
    cfg = BenchmarkConfig(name="tpch", display_name="TPC-H", test_execution_type="data_only")
    bench = MagicMock()
    bench.output_dir = None
    bench.tables = None

    expected = make_benchmark_results(
        benchmark_name="TPC-H",
        platform="data_only",
        execution_id="eid",
        query_definitions={},
        test_execution_type="data_only",
    )

    def _create_enhanced(platform, query_results, **kwargs):
        return expected

    bench.create_enhanced_benchmark_result = _create_enhanced
    bench.generate_data = MagicMock()

    res = run_benchmark_lifecycle(
        benchmark_config=cfg,
        database_config=None,
        system_profile=_mk_system_profile(),
        output_root=None,
        benchmark_instance=bench,
    )

    assert isinstance(res, BenchmarkResults)
    assert res.platform == "data_only"


@pytest.mark.unit
def test_data_only_mode_propagates_duration_seconds():
    cfg = BenchmarkConfig(name="tpch", display_name="TPC-H", test_execution_type="data_only")

    class StubBenchmark(BaseBenchmark):
        def __init__(self):
            super().__init__(scale_factor=cfg.scale_factor)
            self._name = "TPC-H"
            self._version = "1.0"
            self._description = "Stub benchmark"
            self.tables = None
            self.output_dir = None

        def generate_data(self):
            self.tables = {"lineitem": Path("lineitem.tbl")}
            return [Path("lineitem.tbl")]

        def get_queries(self):
            return {}

        def get_query(self, query_id, *, params=None):
            raise ValueError(f"Query {query_id} not found")

    bench = StubBenchmark()

    def _slow_ensure_data_generated(*_args, **_kwargs):
        time.sleep(0.02)
        return False, True

    with patch("benchbox.core.runner.runner._ensure_data_generated", side_effect=_slow_ensure_data_generated):
        res = run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=None,
            system_profile=_mk_system_profile(),
            output_root=None,
            benchmark_instance=bench,
        )

    assert isinstance(res, BenchmarkResults)
    assert res.platform == "data_only"
    assert res.duration_seconds > 0.0


@pytest.mark.unit
def test_load_only_mode_invokes_adapter_load(tmp_path):
    cfg = BenchmarkConfig(name="tpch", display_name="TPC-H", test_execution_type="load_only")
    db = DatabaseConfig(type="duckdb", name="test")
    bench = MagicMock()
    bench.output_dir = tmp_path
    bench.tables = None

    def _create_enhanced(platform, query_results, **kwargs):
        dur = kwargs.get("duration_seconds", 0.0)
        return make_benchmark_results(
            benchmark_name="TPC-H",
            platform=platform,
            execution_id="eid",
            duration_seconds=dur,
            total_queries=len(query_results),
            query_definitions={},
            total_execution_time=dur,
            execution_phases=kwargs.get("phases"),
            test_execution_type="load_only",
        )

    bench.create_enhanced_benchmark_result = _create_enhanced
    bench.generate_data = MagicMock()

    class DummyAdapter:
        platform_name = "duckdb"

        def __init__(self):
            self.load_called = False
            self.schema_created = False
            self.closed = False

        def create_connection(self, **kwargs):  # pragma: no cover
            return Mock()

        def close_connection(self, _conn):  # pragma: no cover
            self.closed = True

        def create_schema(self, benchmark, connection):
            self.schema_created = True
            return 0.1

        def load_data(self, benchmark, connection, data_dir):
            self.load_called = True
            return {"table1": 10}, 0.5, None

    adapter_instance = DummyAdapter()

    with patch("benchbox.core.runner.runner.get_platform_adapter", return_value=adapter_instance):
        res = run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=db,
            system_profile=_mk_system_profile(),
            platform_config={"database_path": "test.duckdb"},
            phases=LifecyclePhases(generate=False, load=True, execute=False),
            output_root=str(tmp_path),
            benchmark_instance=bench,
        )

    assert adapter_instance.schema_created is True
    assert adapter_instance.load_called is True
    assert isinstance(res, BenchmarkResults)
    assert res.platform == "duckdb"


@pytest.mark.unit
def test_load_only_mode_runs_statistics_phase_when_requested(tmp_path):
    from benchbox.core.results.models import StatisticsGatheringPhase

    cfg = BenchmarkConfig(name="tpch", display_name="TPC-H", test_execution_type="load_only")
    db = DatabaseConfig(type="duckdb", name="test")
    bench = MagicMock()
    bench.output_dir = tmp_path
    bench.tables = None
    captured_phases = {}

    captured_kwargs = {}

    def _create_enhanced(platform, query_results, **kwargs):
        captured_phases.update(kwargs.get("phases") or {})
        captured_kwargs.update(kwargs)
        return make_benchmark_results(
            benchmark_name="TPC-H",
            platform=platform,
            execution_id="eid",
            query_definitions={},
            test_execution_type="load_only",
        )

    bench.create_enhanced_benchmark_result = _create_enhanced
    bench.generate_data = MagicMock()

    class DummyAdapter:
        platform_name = "duckdb"

        def __init__(self):
            self.statistics_calls = []

        def create_connection(self, **kwargs):
            return Mock()

        def close_connection(self, _conn):
            pass

        def create_schema(self, benchmark, connection):
            return 0.1

        def load_data(self, benchmark, connection, data_dir):
            return {"table1": 10}, 0.5, None

        def run_statistics_phase(
            self,
            benchmark,
            connection,
            *,
            benchmark_name="",
            table_names=None,
            reset=None,
            collect_per_table_timing=False,
        ):
            self.statistics_calls.append((benchmark_name, table_names))
            return StatisticsGatheringPhase(
                duration_ms=5000, status="COMPLETED", stats_mode="explicit", tables_analyzed=1
            )

    adapter_instance = DummyAdapter()

    with patch("benchbox.core.runner.runner.get_platform_adapter", return_value=adapter_instance):
        run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=db,
            system_profile=_mk_system_profile(),
            platform_config={"database_path": "test.duckdb"},
            phases=LifecyclePhases(generate=False, load=True, execute=False, statistics=True),
            output_root=str(tmp_path),
            benchmark_instance=bench,
        )

    assert adapter_instance.statistics_calls == [("tpch", ["table1"])]
    assert captured_phases["statistics"] == {
        "status": "COMPLETED",
        "duration_ms": 5000,
        "stats_mode": "explicit",
        "tables_analyzed": 1,
    }
    assert captured_kwargs["duration_seconds"] == pytest.approx(5.6)


@pytest.mark.unit
def test_load_only_mode_uses_dataframe_adapter_path(tmp_path):
    cfg = BenchmarkConfig(name="tpch", display_name="TPC-H", test_execution_type="standard")
    db = DatabaseConfig(type="datafusion", name="test")
    bench = MagicMock()
    bench.output_dir = tmp_path
    bench.tables = None

    expected = make_benchmark_results(
        benchmark_name="TPC-H",
        platform="DataFusion (DataFrame)",
        execution_id="eid",
        query_definitions={},
    )

    class DataFrameAdapter:
        platform_name = "DataFusion"
        family = "expression"

        def __init__(self):
            self.run_called = False

        def run_benchmark(self, *args, **kwargs):
            self.run_called = True
            return expected

    adapter_instance = DataFrameAdapter()

    with patch("benchbox.core.runner.runner.get_platform_adapter", return_value=adapter_instance):
        res = run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=db,
            system_profile=_mk_system_profile(),
            platform_config={},
            phases=LifecyclePhases(generate=False, load=True, execute=False),
            output_root=str(tmp_path),
            benchmark_instance=bench,
        )

    assert adapter_instance.run_called is True
    assert res is expected


@pytest.mark.unit
def test_load_only_external_mode_requires_adapter_support(tmp_path):
    cfg = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        test_execution_type="load_only",
        options={"table_mode": "external"},
    )
    db = DatabaseConfig(type="duckdb", name="test")
    bench = MagicMock()
    bench.output_dir = tmp_path
    bench.tables = None
    bench.generate_data = MagicMock()

    class DummyAdapter:
        platform_name = "duckdb"
        supports_external_tables = False

        def create_connection(self, **kwargs):  # pragma: no cover
            return Mock()

        def close_connection(self, _conn):  # pragma: no cover
            return None

        def create_schema(self, benchmark, connection):
            raise AssertionError("create_schema should not be called for unsupported external mode")

        def load_data(self, benchmark, connection, data_dir):
            raise AssertionError("load_data should not be called for unsupported external mode")

    adapter_instance = DummyAdapter()

    with (
        patch("benchbox.core.runner.runner.get_platform_adapter", return_value=adapter_instance),
        pytest.raises(RuntimeError, match="does not support --table-mode external"),
    ):
        run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=db,
            system_profile=_mk_system_profile(),
            platform_config={"database_path": "test.duckdb"},
            phases=LifecyclePhases(generate=False, load=True, execute=False),
            output_root=str(tmp_path),
            benchmark_instance=bench,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("database_type", "platform_name"),
    [
        ("sqlite", "SQLite"),
        ("postgresql", "PostgreSQL"),
    ],
)
def test_load_only_external_mode_reports_platform_specific_unsupported_errors(
    tmp_path, database_type: str, platform_name: str
):
    cfg = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        test_execution_type="load_only",
        options={"table_mode": "external"},
    )
    db = DatabaseConfig(type=database_type, name=f"{database_type}_test")
    bench = MagicMock()
    bench.output_dir = tmp_path
    bench.tables = None
    bench.generate_data = MagicMock()

    class DummyAdapter:
        supports_external_tables = False

        @property
        def platform_name(self):
            return platform_name

        def create_connection(self, **kwargs):  # pragma: no cover
            return Mock()

        def close_connection(self, _conn):  # pragma: no cover
            return None

    with (
        patch("benchbox.core.runner.runner.get_platform_adapter", return_value=DummyAdapter()),
        pytest.raises(RuntimeError, match=f"Platform '{platform_name}' does not support --table-mode external"),
    ):
        run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=db,
            system_profile=_mk_system_profile(),
            platform_config={},
            phases=LifecyclePhases(generate=False, load=True, execute=False),
            output_root=str(tmp_path),
            benchmark_instance=bench,
        )


@pytest.mark.unit
def test_load_only_external_mode_invokes_adapter_runtime_validation_hook(tmp_path):
    cfg = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        test_execution_type="load_only",
        options={"table_mode": "external"},
    )
    db = DatabaseConfig(type="duckdb", name="test")
    bench = MagicMock()
    bench.output_dir = tmp_path
    bench.tables = None
    bench.generate_data = MagicMock()

    def _create_enhanced(platform, query_results, **kwargs):
        dur = kwargs.get("duration_seconds", 0.0)
        return make_benchmark_results(
            benchmark_name="TPC-H",
            platform=platform,
            execution_id="eid",
            duration_seconds=dur,
            total_queries=len(query_results),
            query_definitions={},
            total_execution_time=dur,
            execution_phases=kwargs.get("phases"),
            test_execution_type="load_only",
        )

    bench.create_enhanced_benchmark_result = _create_enhanced

    class DummyAdapter:
        platform_name = "duckdb"
        supports_external_tables = True

        def __init__(self):
            self.validated_external = False
            self.external_created = False

        def create_connection(self, **kwargs):  # pragma: no cover
            return Mock()

        def close_connection(self, _conn):  # pragma: no cover
            return None

        def validate_external_table_requirements(self):
            self.validated_external = True

        def create_schema(self, benchmark, connection):
            raise AssertionError("create_schema should not be called in external table mode")

        def load_data(self, benchmark, connection, data_dir):
            raise AssertionError("load_data should not be called in external table mode")

        def create_external_tables(self, benchmark, connection, data_dir):
            self.external_created = True
            return {"table1": 10}, 0.2, None

    adapter_instance = DummyAdapter()

    with patch("benchbox.core.runner.runner.get_platform_adapter", return_value=adapter_instance):
        res = run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=db,
            system_profile=_mk_system_profile(),
            platform_config={"database_path": "test.duckdb"},
            phases=LifecyclePhases(generate=False, load=True, execute=False),
            output_root=str(tmp_path),
            benchmark_instance=bench,
        )

    assert adapter_instance.validated_external is True
    assert adapter_instance.external_created is True
    assert isinstance(res, BenchmarkResults)


@pytest.mark.unit
def test_load_only_external_mode_surfaces_adapter_validation_guidance(tmp_path):
    cfg = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        test_execution_type="load_only",
        options={"table_mode": "external"},
    )
    db = DatabaseConfig(type="bigquery", name="test")
    bench = MagicMock()
    bench.output_dir = tmp_path
    bench.tables = None
    bench.generate_data = MagicMock()

    class DummyAdapter:
        platform_name = "BigQuery"
        supports_external_tables = True

        def create_connection(self, **kwargs):  # pragma: no cover
            return Mock()

        def close_connection(self, _conn):  # pragma: no cover
            return None

        def validate_external_table_requirements(self):
            raise ValueError("BigQuery external mode requires a GCS bucket")

    with (
        patch("benchbox.core.runner.runner.get_platform_adapter", return_value=DummyAdapter()),
        pytest.raises(ValueError, match="requires a GCS bucket"),
    ):
        run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=db,
            system_profile=_mk_system_profile(),
            platform_config={},
            phases=LifecyclePhases(generate=False, load=True, execute=False),
            output_root=str(tmp_path),
            benchmark_instance=bench,
        )


@pytest.mark.unit
def test_standard_mode_delegates_to_adapter_run_benchmark():
    cfg = BenchmarkConfig(name="tpch", display_name="TPC-H", test_execution_type="standard")
    db = DatabaseConfig(type="duckdb", name="test")
    bench = MagicMock()
    bench.output_dir = None
    bench.tables = None

    expected = make_benchmark_results(
        benchmark_name="TPC-H",
        platform="duckdb",
        execution_id="eid",
        duration_seconds=1.0,
        total_queries=1,
        successful_queries=1,
        query_definitions={},
        total_execution_time=1.0,
        average_query_time=1.0,
    )

    mock_adapter = MagicMock()
    mock_adapter.run_benchmark.return_value = expected
    mock_adapter.load_data.return_value = ({"table1": 10}, 0.5, None)
    mock_adapter.create_connection.return_value = Mock()
    mock_adapter.close_connection = Mock()
    with patch("benchbox.core.runner.runner.get_platform_adapter", return_value=mock_adapter):
        res = run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=db,
            system_profile=_mk_system_profile(),
            platform_config={"database_path": "test.duckdb"},
            output_root=None,
            benchmark_instance=bench,
        )

    mock_adapter.run_benchmark.assert_called_once()
    assert res is expected


@pytest.mark.unit
def test_standard_mode_enriches_driver_runtime_metadata():
    cfg = BenchmarkConfig(name="tpch", display_name="TPC-H", test_execution_type="standard")
    db = DatabaseConfig(
        type="duckdb",
        name="test",
        driver_package="duckdb",
        driver_version="1.2.2",
        driver_version_resolved="1.2.2",
        driver_version_actual="1.2.2",
        driver_runtime_strategy="isolated-site-packages",
        driver_runtime_path="/tmp/duckdb-site-packages",
        driver_runtime_python_executable="/tmp/duckdb-python",
        driver_auto_install=False,
    )
    bench = MagicMock()
    bench.output_dir = None
    bench.tables = None
    bench.generate_data = MagicMock()

    expected = make_benchmark_results(
        benchmark_name="TPC-H",
        platform="duckdb",
        execution_id="driver-meta-001",
        duration_seconds=1.0,
        total_queries=1,
        successful_queries=1,
        query_definitions={},
        total_execution_time=1.0,
        average_query_time=1.0,
        execution_metadata={"mode": "sql"},
        platform_info={
            "platform_version": "1.2.2",
            "client_library_version": "1.2.2",
            "execution_mode": "sql",
            "configuration": {},
        },
    )

    mock_adapter = MagicMock()
    mock_adapter.run_benchmark.return_value = expected
    mock_adapter.driver_package = "duckdb"
    mock_adapter.driver_version_requested = "1.2.2"
    mock_adapter.driver_version_resolved = "1.2.2"
    mock_adapter.driver_version_actual = "1.2.2"
    mock_adapter.driver_runtime_strategy = "isolated-site-packages"
    mock_adapter.driver_runtime_path = "/tmp/duckdb-site-packages"
    mock_adapter.driver_runtime_python_executable = "/tmp/duckdb-python"
    mock_adapter.driver_auto_install_used = False

    res = run_benchmark_lifecycle(
        benchmark_config=cfg,
        database_config=db,
        system_profile=_mk_system_profile(),
        platform_config={"database_path": "test.duckdb"},
        benchmark_instance=bench,
        platform_adapter=mock_adapter,
    )

    assert res.driver_package == "duckdb"
    assert res.driver_version_requested == "1.2.2"
    assert res.driver_version_resolved == "1.2.2"
    assert res.driver_version_actual == "1.2.2"
    assert res.driver_runtime_strategy == "isolated-site-packages"
    assert res.driver_runtime_path == "/tmp/duckdb-site-packages"
    assert res.driver_runtime_python_executable == "/tmp/duckdb-python"

    assert isinstance(res.execution_metadata, dict)
    assert res.execution_metadata["driver_package"] == "duckdb"
    assert res.execution_metadata["driver_version_requested"] == "1.2.2"
    assert res.execution_metadata["driver_version_resolved"] == "1.2.2"
    assert res.execution_metadata["driver_version_actual"] == "1.2.2"
    assert res.execution_metadata["driver_runtime_strategy"] == "isolated-site-packages"
    assert res.execution_metadata["driver_runtime_path"] == "/tmp/duckdb-site-packages"
    assert res.execution_metadata["driver_runtime_python_executable"] == "/tmp/duckdb-python"

    assert isinstance(res.platform_info, dict)
    assert res.platform_info["driver_package"] == "duckdb"
    assert res.platform_info["driver_version_requested"] == "1.2.2"
    assert res.platform_info["driver_version_resolved"] == "1.2.2"
    assert res.platform_info["driver_version_actual"] == "1.2.2"
    assert res.platform_info["driver_runtime_strategy"] == "isolated-site-packages"
    assert res.platform_info["driver_runtime_path"] == "/tmp/duckdb-site-packages"
    assert res.platform_info["driver_runtime_python_executable"] == "/tmp/duckdb-python"


@pytest.mark.unit
def test_preflight_and_manifest_validation(tmp_path):
    cfg = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=1.0,
        options={
            "enable_preflight_validation": True,
            "enable_postgen_manifest_validation": True,
        },
    )
    bench = Mock()
    bench.name = "tpch"
    bench.scale_factor = 1.0
    bench.tables = None
    bench.generate_data = Mock()
    bench.validate_preflight.return_value = ValidationResult(True, [], [])
    bench.validate_manifest.return_value = ValidationResult(True, [], [])

    expected = make_benchmark_results(
        benchmark_name="TPC-H",
        platform="unknown",
        scale_factor=1.0,
        execution_id="eid",
        query_definitions={},
    )

    bench.create_enhanced_benchmark_result.return_value = expected
    manifest_path = Path(tmp_path) / "_datagen_manifest.json"
    manifest_path.write_text('{"benchmark": "tpch", "scale_factor": 1.0, "tables": {}}')
    bench.output_dir = tmp_path

    with patch("benchbox.core.runner.runner.get_benchmark_instance", return_value=bench):
        result = run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=None,
            system_profile=_mk_system_profile(),
            phases=LifecyclePhases(generate=True, load=False, execute=False),
            validation_opts=ValidationOptions(
                enable_preflight_validation=True,
                enable_postgen_manifest_validation=True,
            ),
            output_root=str(tmp_path),
        )

    assert result is expected
    stages = result.validation_details.get("stages", [])
    stage_names = {stage["stage"] for stage in stages}
    assert {"preflight", "post_generation_manifest"} <= stage_names
    assert result.validation_status in {"PASSED", "WARNINGS"}


@pytest.mark.unit
def test_preflight_validation_failure_raises(tmp_path):
    cfg = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=1.0,
        options={"enable_preflight_validation": True},
    )
    bench = Mock()
    bench.name = "tpch"
    bench.scale_factor = 1.0
    bench.tables = None
    bench.validate_preflight.return_value = ValidationResult(False, ["disk"], [])
    bench.output_dir = tmp_path

    with patch("benchbox.core.runner.runner.get_benchmark_instance", return_value=bench):
        with pytest.raises(RuntimeError, match="Preflight validation failed: disk"):
            run_benchmark_lifecycle(
                benchmark_config=cfg,
                database_config=None,
                system_profile=_mk_system_profile(),
                phases=LifecyclePhases(generate=True, load=False, execute=False),
                validation_opts=ValidationOptions(enable_preflight_validation=True),
                output_root=str(tmp_path),
            )


@pytest.mark.unit
def test_postload_validation_invoked(tmp_path):
    cfg = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=1.0,
        options={"enable_postload_validation": True},
    )
    db = DatabaseConfig(type="duckdb", name="duckdb")
    bench = Mock()
    bench.scale_factor = 1.0
    bench.tables = {}
    bench.output_dir = str(tmp_path)
    bench.create_enhanced_benchmark_result.return_value = make_benchmark_results(
        benchmark_name="TPC-H",
        platform="duckdb",
        scale_factor=1.0,
        execution_id="eid",
        query_definitions={},
    )

    mock_adapter = Mock()
    mock_adapter.platform_name = "duckdb"
    mock_adapter.create_connection.return_value = Mock()
    mock_adapter.close_connection = Mock()
    mock_adapter.run_benchmark.return_value = bench.create_enhanced_benchmark_result.return_value

    validation_result = ValidationResult(True, [], [])

    with (
        patch("benchbox.core.runner.runner.get_benchmark_instance", return_value=bench),
        patch("benchbox.core.runner.runner.get_platform_adapter", return_value=mock_adapter),
        patch("benchbox.core.validation.DatabaseValidationEngine") as mock_db_engine,
    ):
        mock_db_engine.return_value.validate_loaded_data.return_value = validation_result

        result = run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=db,
            system_profile=_mk_system_profile(),
            platform_config={"database_path": str(tmp_path / "db.duckdb")},
            phases=LifecyclePhases(generate=False, load=False, execute=True),
            validation_opts=ValidationOptions(enable_postload_validation=True),
        )

    stages = result.validation_details.get("stages", [])
    assert any(stage["stage"] == "post_load" for stage in stages)
    mock_db_engine.return_value.validate_loaded_data.assert_called_once()


@pytest.mark.unit
def test_run_benchmark_lifecycle_propagates_verbosity(tmp_path):

    class StubBenchmark(VerbosityMixin):
        def __init__(self):
            self.logger = logging.getLogger("stub-benchmark")
            self.output_dir = tmp_path
            self.tables = None
            self.apply_verbosity(VerbositySettings.default())

        def generate_data(self):
            self.tables = {"table": tmp_path / "data.tbl"}
            return []

        def create_enhanced_benchmark_result(self, **kwargs):
            return make_benchmark_results(
                benchmark_name="TPC-H",
                platform="stub",
                execution_id="eid",
                query_definitions={},
            )

    class StubAdapter(VerbosityMixin):
        platform_name = "stub"

        def __init__(self):
            self.logger = logging.getLogger("stub-adapter")
            self.apply_verbosity(VerbositySettings.default())
            self.last_run_kwargs = None
            self.enable_validation = False

        def run_benchmark(self, benchmark, **run_config):
            self.last_run_kwargs = run_config
            return make_benchmark_results(
                benchmark_name="TPC-H",
                platform=self.platform_name,
                execution_id="eid",
                duration_seconds=0.1,
                query_definitions={},
                total_execution_time=0.1,
            )

    cfg = BenchmarkConfig(name="tpch", display_name="TPC-H")
    db = DatabaseConfig(type="duckdb", name="test", options={})
    benchmark = StubBenchmark()
    adapter = StubAdapter()

    verbosity = VerbositySettings.from_flags(2, False)

    with patch("benchbox.core.runner.runner.get_platform_adapter", return_value=adapter):
        result = run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=db,
            system_profile=_mk_system_profile(),
            platform_config={"database_path": str(tmp_path / "db.duckdb")},
            benchmark_instance=benchmark,
            verbosity=verbosity,
        )

    assert isinstance(result, BenchmarkResults)
    assert benchmark.verbose_level == 2
    assert benchmark.verbose_enabled is True
    assert adapter.verbose_level == 2
    assert adapter.verbose_enabled is True
    assert adapter.last_run_kwargs is not None
    assert adapter.last_run_kwargs["verbose"] is True
    assert adapter.last_run_kwargs["verbose_level"] == 2
    assert adapter.last_run_kwargs["quiet"] is False


@pytest.mark.unit
def test_run_benchmark_lifecycle_propagates_capture_plans(tmp_path):

    class StubBenchmark(VerbosityMixin):
        def __init__(self):
            self.logger = logging.getLogger("stub-benchmark")
            self.output_dir = tmp_path
            self.tables = None
            self.apply_verbosity(VerbositySettings.default())

        def generate_data(self):
            self.tables = {"table": tmp_path / "data.tbl"}
            return []

        def create_enhanced_benchmark_result(self, **kwargs):
            return make_benchmark_results(
                benchmark_name="TPC-H",
                platform="stub",
                scale_factor=1.0,
                execution_id="eid",
                query_definitions={},
            )

    class StubAdapter(VerbosityMixin):
        platform_name = "stub"

        def __init__(self):
            self.logger = logging.getLogger("stub-adapter")
            self.apply_verbosity(VerbositySettings.default())
            self.last_run_kwargs = None
            self.enable_validation = False

        def run_benchmark(self, benchmark, **run_config):
            self.last_run_kwargs = run_config
            return make_benchmark_results(
                benchmark_name="TPC-H",
                platform=self.platform_name,
                scale_factor=1.0,
                execution_id="eid",
                duration_seconds=0.1,
                query_definitions={},
                total_execution_time=0.1,
            )

    cfg = BenchmarkConfig(name="tpch", display_name="TPC-H", capture_plans=True, strict_plan_capture=True)
    db = DatabaseConfig(type="duckdb", name="test", options={})
    benchmark = StubBenchmark()
    adapter = StubAdapter()

    with patch("benchbox.core.runner.runner.get_platform_adapter", return_value=adapter):
        run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=db,
            system_profile=_mk_system_profile(),
            platform_config={"database_path": str(tmp_path / "db.duckdb")},
            benchmark_instance=benchmark,
        )

    assert adapter.last_run_kwargs is not None
    assert adapter.last_run_kwargs["capture_plans"] is True, "capture_plans not forwarded to adapter"
    assert adapter.last_run_kwargs["strict_plan_capture"] is True, "strict_plan_capture not forwarded to adapter"


@pytest.mark.unit
def test_manifest_reuse_accepts_data_source_alias(tmp_path):

    data_dir = tmp_path / "tpch_sf0_1"
    data_dir.mkdir()
    customer_path = data_dir / "customer.tbl"
    contents = "1|cust\n"
    customer_path.write_text(contents)

    manifest = {
        "benchmark": "tpch",
        **current_datagen_stamp("tpch"),
        "scale_factor": 0.1,
        "tables": {
            "customer": {
                "formats": {
                    "tbl": [
                        {
                            "path": "customer.tbl",
                            "size_bytes": customer_path.stat().st_size,
                            "row_count": 1,
                        }
                    ]
                }
            }
        },
    }
    (data_dir / "_datagen_manifest.json").write_text(json.dumps(manifest))

    class AliasBenchmark(BaseBenchmark):
        def __init__(self, output_dir: Path):
            super().__init__(scale_factor=0.1, output_dir=output_dir)
            self.tables = {}

        def get_data_source_benchmark(self) -> str:
            return "tpch"

        def generate_data(self):  # pragma: no cover
            raise AssertionError("generate_data should not be called when manifest is reused")

        def get_queries(self, dialect: str | None = None):
            return {}

        def get_query(self, query_id, *, params=None):
            raise ValueError("Invalid query ID")

    benchmark = AliasBenchmark(data_dir)
    benchmark.generate_data = Mock()

    cfg = BenchmarkConfig(
        name="read_primitives",
        display_name="Primitives",
        scale_factor=0.1,
        compress_data=False,
        options={"force_regenerate": False, "no_regenerate": False},
    )

    result = run_benchmark_lifecycle(
        benchmark_config=cfg,
        database_config=None,
        system_profile=None,
        phases=LifecyclePhases(generate=True, load=False, execute=False),
        benchmark_instance=benchmark,
    )

    assert isinstance(result, BenchmarkResults)
    benchmark.generate_data.assert_not_called()
    assert "customer" in benchmark.tables
    assert benchmark.tables["customer"] == customer_path


@pytest.mark.unit
def test_no_regenerate_respects_alias_manifest(tmp_path):

    data_dir = tmp_path / "tpch_sf0_1"
    data_dir.mkdir()
    orders_path = data_dir / "orders.tbl"
    orders_path.write_text("1|order\n")
    manifest = {
        "benchmark": "tpch",
        **current_datagen_stamp("tpch"),
        "scale_factor": 0.1,
        "tables": {
            "orders": {
                "formats": {
                    "tbl": [
                        {
                            "path": "orders.tbl",
                            "size_bytes": orders_path.stat().st_size,
                            "row_count": 1,
                        }
                    ]
                }
            }
        },
    }
    (data_dir / "_datagen_manifest.json").write_text(json.dumps(manifest))

    class AliasBenchmark(BaseBenchmark):
        def __init__(self, output_dir: Path):
            super().__init__(scale_factor=0.1, output_dir=output_dir)
            self.tables = {}

        def get_data_source_benchmark(self) -> str:
            return "tpch"

        def generate_data(self):  # pragma: no cover
            raise AssertionError("generate_data should not run when no_regenerate is set")

        def get_queries(self, dialect: str | None = None):
            return {}

        def get_query(self, query_id, *, params=None):
            raise ValueError("Invalid query ID")

    benchmark = AliasBenchmark(data_dir)
    benchmark.generate_data = Mock()

    cfg = BenchmarkConfig(
        name="read_primitives",
        display_name="Primitives",
        scale_factor=0.1,
        compress_data=False,
        options={"force_regenerate": False, "no_regenerate": True},
    )

    result = run_benchmark_lifecycle(
        benchmark_config=cfg,
        database_config=None,
        system_profile=None,
        phases=LifecyclePhases(generate=True, load=False, execute=False),
        benchmark_instance=benchmark,
    )

    assert isinstance(result, BenchmarkResults)
    benchmark.generate_data.assert_not_called()
    assert "orders" in benchmark.tables


@pytest.mark.unit
def test_base_benchmark_run_benchmark_avoids_wall_clock_elapsed_arithmetic():
    class DummyBenchmark(BaseBenchmark):
        def generate_data(self):
            return []

        def get_queries(self, dialect: str | None = None):
            return {"Q1": "SELECT 1"}

        def get_query(self, query_id, *, params=None):
            return self.get_queries()[str(query_id)]

    class DummyConnection:
        def execute(self, _):
            return object()

        def fetchall(self, _):
            return []

    benchmark = DummyBenchmark(scale_factor=1.0)
    conn = DummyConnection()

    mock_clock = Mock(side_effect=[10.0, 10.1, 10.4, 10.8])
    with (
        patch("benchbox.base.mono_time", mock_clock),
        patch("benchbox.utils.clock.mono_time", mock_clock),
    ):
        result = benchmark.run_benchmark(conn, query_ids=["Q1"], setup_database=False)

    assert result["successful_queries"] == 1
    assert result["total_execution_time"] == pytest.approx(0.8)
    assert result["query_results"][0]["execution_time_seconds"] == pytest.approx(0.3)


def _mk_representative_benchmark(benchmark_id: str, tmp_path: Path):
    if benchmark_id == "nyctaxi":
        return NYCTaxiBenchmark(
            scale_factor=0.01,
            year=2019,
            months=[1],
            output_dir=tmp_path,
            seed=42,
        )

    if benchmark_id == "tsbs_devops":
        return TSBSDevOpsBenchmark(
            scale_factor=0.1,
            num_hosts=2,
            duration_days=1,
            interval_seconds=3600,
            output_dir=tmp_path,
            seed=42,
        )

    raise AssertionError(f"Unknown benchmark_id: {benchmark_id}")


@pytest.mark.parametrize("benchmark_id", ["nyctaxi", "tsbs_devops"])
def test_representative_benchmarks_data_only_path(benchmark_id: str, tmp_path: Path) -> None:
    cfg = BenchmarkConfig(
        name=benchmark_id,
        display_name=benchmark_id.upper(),
        scale_factor=0.01,
        test_execution_type="data_only",
    )
    benchmark = _mk_representative_benchmark(benchmark_id, tmp_path)

    with patch("benchbox.core.runner.runner._ensure_data_generated", return_value=(False, True)):
        result = run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=None,
            system_profile=_mk_system_profile(),
            phases=LifecyclePhases(generate=True, load=False, execute=False),
            output_root=str(tmp_path),
            benchmark_instance=benchmark,
        )

    assert result.platform == "data_only"
    assert result.execution_metadata.get("mode") == "datagen"


@pytest.mark.parametrize("benchmark_id", ["nyctaxi", "tsbs_devops"])
def test_representative_benchmarks_load_only_path(benchmark_id: str, tmp_path: Path) -> None:
    cfg = BenchmarkConfig(
        name=benchmark_id,
        display_name=benchmark_id.upper(),
        scale_factor=0.01,
        test_execution_type="load_only",
    )
    db = DatabaseConfig(type="duckdb", name="duckdb")
    benchmark = _mk_representative_benchmark(benchmark_id, tmp_path)

    class DummyAdapter:
        platform_name = "duckdb"

        def __init__(self):
            self.created_schema = False
            self.loaded_data = False

        def create_connection(self, **kwargs):
            return Mock()

        def close_connection(self, _conn):
            return None

        def create_schema(self, benchmark, connection):
            self.created_schema = True
            return 0.02

        def load_data(self, benchmark, connection, data_dir):
            self.loaded_data = True
            return {"table": 5}, 0.03, {"table": {"total_ms": 30}}

    adapter = DummyAdapter()

    with patch("benchbox.core.runner.runner._ensure_data_generated", return_value=(False, True)):
        result = run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=db,
            system_profile=_mk_system_profile(),
            platform_config={"database_path": str(tmp_path / "test.duckdb")},
            phases=LifecyclePhases(generate=False, load=True, execute=False),
            benchmark_instance=benchmark,
            platform_adapter=adapter,
        )

    assert adapter.created_schema is True
    assert adapter.loaded_data is True
    assert result.execution_metadata.get("mode") == "load_only"
    assert result.table_statistics.get("table", {}).get("rows") == 5


@pytest.mark.parametrize("benchmark_id", ["nyctaxi", "tsbs_devops"])
def test_representative_benchmarks_setup_only_path(benchmark_id: str, tmp_path: Path) -> None:
    cfg = BenchmarkConfig(
        name=benchmark_id,
        display_name=benchmark_id.upper(),
        scale_factor=0.01,
        test_execution_type="standard",
    )
    benchmark = _mk_representative_benchmark(benchmark_id, tmp_path)

    with patch("benchbox.core.runner.runner._ensure_data_generated", return_value=(False, True)):
        result = run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=None,
            system_profile=_mk_system_profile(),
            phases=LifecyclePhases(generate=False, load=False, execute=False),
            benchmark_instance=benchmark,
        )

    assert result.platform == "unknown"
    assert result.execution_metadata.get("mode") == "setup_only"


@pytest.mark.parametrize("benchmark_id", ["nyctaxi", "tsbs_devops"])
def test_representative_benchmarks_standard_path(benchmark_id: str, tmp_path: Path) -> None:
    cfg = BenchmarkConfig(
        name=benchmark_id,
        display_name=benchmark_id.upper(),
        scale_factor=0.01,
        test_execution_type="standard",
    )
    db = DatabaseConfig(type="duckdb", name="duckdb")
    benchmark = _mk_representative_benchmark(benchmark_id, tmp_path)

    class DummyAdapter:
        platform_name = "duckdb"

        def __init__(self):
            self.called = False
            self.kwargs = None

        def run_benchmark(self, benchmark, **kwargs):
            self.called = True
            self.kwargs = kwargs
            return benchmark.create_enhanced_benchmark_result(
                platform="duckdb",
                query_results=[
                    {
                        "query_id": "Q1",
                        "execution_time_seconds": 0.01,
                        "status": "success",
                        "rows": 1,
                    }
                ],
                duration_seconds=0.01,
                execution_metadata={"mode": "standard"},
            )

    adapter = DummyAdapter()

    with patch("benchbox.core.runner.runner._ensure_data_generated", return_value=(False, True)):
        result = run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=db,
            system_profile=_mk_system_profile(),
            platform_config={"database_path": str(tmp_path / "test.duckdb")},
            phases=LifecyclePhases(generate=False, load=False, execute=True),
            benchmark_instance=benchmark,
            platform_adapter=adapter,
        )

    assert adapter.called is True
    assert adapter.kwargs is not None
    assert result.total_queries == 1


@pytest.mark.unit
@pytest.mark.parametrize("benchmark_id", ["nyctaxi", "tsbs_devops"])
def test_runner_propagates_benchmark_name_slug_to_adapter(benchmark_id: str, tmp_path: Path) -> None:
    cfg = BenchmarkConfig(
        name=benchmark_id,
        display_name=benchmark_id.upper(),
        scale_factor=0.01,
        test_execution_type="standard",
    )
    db = DatabaseConfig(type="duckdb", name="duckdb")
    benchmark = _mk_representative_benchmark(benchmark_id, tmp_path)

    received_kwargs: dict = {}

    class SlugCapturingAdapter:
        platform_name = "duckdb"

        def run_benchmark(self, benchmark_obj, **kwargs):
            received_kwargs.update(kwargs)
            return benchmark_obj.create_enhanced_benchmark_result(
                platform="duckdb",
                query_results=[],
                duration_seconds=0.0,
                execution_metadata={"mode": "standard"},
            )

    with patch("benchbox.core.runner.runner._ensure_data_generated", return_value=(False, True)):
        run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=db,
            system_profile=_mk_system_profile(),
            platform_config={},
            phases=LifecyclePhases(generate=False, load=False, execute=True),
            benchmark_instance=benchmark,
            platform_adapter=SlugCapturingAdapter(),
        )

    assert received_kwargs.get("benchmark_name") == benchmark_id


@pytest.mark.unit
def test_standard_path_propagates_table_mode_to_adapter(tmp_path: Path) -> None:
    cfg = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=0.01,
        test_execution_type="power",
        options={"table_mode": "external"},
    )
    db = DatabaseConfig(type="duckdb", name="duckdb")
    bench = MagicMock()
    bench.output_dir = tmp_path
    bench.tables = None
    bench.generate_data = MagicMock()

    class DummyAdapter:
        platform_name = "duckdb"
        supports_external_tables = True
        table_mode = "native"

        def __init__(self):
            self.run_benchmark_called = False
            self.table_mode_at_run = None

        def run_benchmark(self, benchmark, **kwargs):
            self.run_benchmark_called = True
            self.table_mode_at_run = self.table_mode
            return bench.create_enhanced_benchmark_result(
                platform="duckdb",
                query_results=[],
                duration_seconds=0.0,
                execution_metadata={"mode": "standard"},
            )

    bench.create_enhanced_benchmark_result = lambda platform, query_results, **kwargs: make_benchmark_results(
        benchmark_name="TPC-H",
        platform=platform,
        execution_id="eid",
        query_definitions={},
        test_execution_type="power",
    )

    adapter = DummyAdapter()

    with patch("benchbox.core.runner.runner._ensure_data_generated", return_value=(False, True)):
        run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=db,
            system_profile=_mk_system_profile(),
            platform_config={"database_path": str(tmp_path / "test.duckdb")},
            phases=LifecyclePhases(generate=False, load=False, execute=True),
            benchmark_instance=bench,
            platform_adapter=adapter,
        )

    assert adapter.run_benchmark_called is True
    assert adapter.table_mode_at_run == "external", (
        "Runner must propagate table_mode='external' to adapter before calling run_benchmark"
    )


@pytest.mark.unit
def test_standard_path_propagates_conversion_settings_to_adapter(tmp_path: Path) -> None:
    cfg = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=0.01,
        test_execution_type="power",
        options={
            "table_format": "parquet",
            "table_format_compression": "zstd",
            "table_format_partition_cols": ["region"],
        },
    )
    db = DatabaseConfig(type="duckdb", name="duckdb")
    bench = MagicMock()
    bench.output_dir = tmp_path
    bench.tables = None
    bench.generate_data = MagicMock()

    class DummyAdapter:
        platform_name = "duckdb"

        def __init__(self):
            self.kwargs = None

        def run_benchmark(self, benchmark, **kwargs):
            self.kwargs = kwargs
            return bench.create_enhanced_benchmark_result(
                platform="duckdb",
                query_results=[],
                duration_seconds=0.0,
                execution_metadata={"mode": "standard"},
            )

    bench.create_enhanced_benchmark_result = lambda platform, query_results, **kwargs: make_benchmark_results(
        benchmark_name="TPC-H",
        platform=platform,
        execution_id="eid",
        query_definitions={},
        test_execution_type="power",
    )

    adapter = DummyAdapter()

    with patch("benchbox.core.runner.runner._ensure_data_generated", return_value=(False, True)):
        run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=db,
            system_profile=_mk_system_profile(),
            platform_config={"database_path": str(tmp_path / "test.duckdb")},
            phases=LifecyclePhases(generate=False, load=False, execute=True),
            benchmark_instance=bench,
            platform_adapter=adapter,
        )

    assert adapter.kwargs is not None
    assert adapter.kwargs.get("table_format") == "parquet"
    assert adapter.kwargs.get("table_format_compression") == "zstd"
    assert adapter.kwargs.get("table_format_partition_cols") == ["region"]


@pytest.mark.unit
def test_unsupported_table_format_fails_fast(tmp_path):
    cfg = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        test_execution_type="power",
        options={"table_format": "ducklake"},
    )

    bench = MagicMock(spec=BaseBenchmark)
    bench.name = "TPC-H"
    bench.scale_factor = 0.01
    bench.tables = {"lineitem": [Path("lineitem.tbl")]}
    bench.output_dir = str(tmp_path)
    bench.generate_data = MagicMock()
    bench.get_queries = MagicMock(return_value={"Q1": "SELECT 1"})

    adapter = MagicMock()
    adapter.platform_name = "DataFusion"

    db_cfg = DatabaseConfig(name="test-datafusion", type="datafusion")

    with pytest.raises(RuntimeError, match="does not support table format 'ducklake'"):
        run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=db_cfg,
            system_profile=_mk_system_profile(),
            platform_config={"database_path": str(tmp_path / "test.db")},
            phases=LifecyclePhases(generate=False, load=False, execute=True),
            benchmark_instance=bench,
            platform_adapter=adapter,
        )


@pytest.mark.unit
def test_bigquery_external_delta_without_biglake_connection_fails_fast(tmp_path):
    cfg = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        test_execution_type="power",
        options={"table_mode": "external", "table_format": "delta"},
    )

    bench = MagicMock(spec=BaseBenchmark)
    bench.name = "TPC-H"
    bench.scale_factor = 0.01
    bench.tables = {"lineitem": [Path("lineitem")]}
    bench.output_dir = str(tmp_path)
    bench.generate_data = MagicMock()
    bench.get_queries = MagicMock(return_value={"Q1": "SELECT 1"})

    class DummyAdapter:
        platform_name = "BigQuery"
        staging_root = "gs://benchbox-bucket/prefix"

    db_cfg = DatabaseConfig(name="test-bigquery", type="bigquery")

    with pytest.raises(RuntimeError, match="does not support table format 'delta'"):
        run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=db_cfg,
            system_profile=_mk_system_profile(),
            platform_config={},
            phases=LifecyclePhases(generate=False, load=False, execute=True),
            benchmark_instance=bench,
            platform_adapter=DummyAdapter(),
        )


@pytest.mark.unit
def test_snowflake_external_iceberg_without_volume_fails_fast(tmp_path):
    cfg = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        test_execution_type="power",
        options={"table_mode": "external", "table_format": "iceberg"},
    )

    bench = MagicMock(spec=BaseBenchmark)
    bench.name = "TPC-H"
    bench.scale_factor = 0.01
    bench.tables = {"lineitem": [Path("lineitem")]}
    bench.output_dir = str(tmp_path)
    bench.generate_data = MagicMock()
    bench.get_queries = MagicMock(return_value={"Q1": "SELECT 1"})

    class DummyAdapter:
        platform_name = "Snowflake"
        staging_root = "s3://benchbox-bucket/prefix"

    db_cfg = DatabaseConfig(name="test-snowflake", type="snowflake")

    with pytest.raises(RuntimeError, match="does not support table format 'iceberg'"):
        run_benchmark_lifecycle(
            benchmark_config=cfg,
            database_config=db_cfg,
            system_profile=_mk_system_profile(),
            platform_config={},
            phases=LifecyclePhases(generate=False, load=False, execute=True),
            benchmark_instance=bench,
            platform_adapter=DummyAdapter(),
        )


class _FakeClickHouseServer:
    def __init__(self) -> None:
        self.tables: dict[str, int] = {}
        self.tuned: bool = False
        self.drops: int = 0


class _FakeClickHouseLoadAdapter:
    platform_name = "clickhouse-server"
    supports_external_tables = False

    def __init__(self, server: _FakeClickHouseServer, *, tuning_enabled: bool) -> None:
        self._server = server
        self.tuning_enabled = tuning_enabled
        self.table_mode = "native"
        self.database_was_reused = False
        self._existing_db_decided = False
        self.schema_calls = 0
        self.load_calls = 0

    def create_connection(self, **kwargs):
        self._existing_db_decided = True
        if not self._server.tables:
            self.database_was_reused = False
        elif self.tuning_enabled != self._server.tuned:
            self._server.tables.clear()
            self._server.tuned = False
            self._server.drops += 1
            self.database_was_reused = False
        else:
            self.database_was_reused = True
        return Mock()

    def close_connection(self, _conn) -> None:
        pass

    def create_schema(self, benchmark, connection):
        self.schema_calls += 1
        for table in benchmark.get_schema():
            if table in self._server.tables:
                raise RuntimeError(f"Code: 57. DB::Exception: Table default.{table} already exists.")
            self._server.tables[table] = 0
        return 0.1

    def load_data(self, benchmark, connection, data_dir):
        self.load_calls += 1
        stats = dict.fromkeys(benchmark.get_schema(), 10)
        self._server.tables.update(stats)
        if self.tuning_enabled:
            self._server.tuned = True
        return stats, 0.5, None

    def get_table_row_count(self, connection, table: str) -> int:
        return self._server.tables.get(table, 0)


def _run_clickhouse_load_only(bench, adapter, tmp_path):
    cfg = BenchmarkConfig(name="tpch", display_name="TPC-H", test_execution_type="load_only")
    db = DatabaseConfig(type="clickhouse-server", name="test")
    return run_benchmark_lifecycle(
        benchmark_config=cfg,
        database_config=db,
        system_profile=_mk_system_profile(),
        platform_config={},
        phases=LifecyclePhases(generate=False, load=True, execute=False),
        output_root=str(tmp_path),
        benchmark_instance=bench,
        platform_adapter=adapter,
    )


def _make_clickhouse_load_bench(tmp_path, captured):
    bench = MagicMock()
    bench.output_dir = tmp_path
    bench.tables = None
    bench.get_schema = MagicMock(return_value={"region": {}, "nation": {}})

    def _create_enhanced(platform, query_results, **kwargs):
        captured.update(kwargs)
        return make_benchmark_results(
            benchmark_name="TPC-H",
            platform=platform,
            execution_id="eid",
            query_definitions={},
            test_execution_type="load_only",
        )

    bench.create_enhanced_benchmark_result = _create_enhanced
    bench.generate_data = MagicMock()
    return bench


@pytest.mark.unit
def test_load_only_notuning_rerun_reuses_existing_tables(tmp_path):
    server = _FakeClickHouseServer()
    first = _FakeClickHouseLoadAdapter(server, tuning_enabled=False)
    captured_first: dict = {}
    res = _run_clickhouse_load_only(_make_clickhouse_load_bench(tmp_path, captured_first), first, tmp_path)
    assert isinstance(res, BenchmarkResults)
    assert first.schema_calls == 1
    assert server.tables == {"region": 10, "nation": 10}

    second = _FakeClickHouseLoadAdapter(server, tuning_enabled=False)
    captured: dict = {}
    res = _run_clickhouse_load_only(_make_clickhouse_load_bench(tmp_path, captured), second, tmp_path)
    assert isinstance(res, BenchmarkResults)
    assert server.drops == 0
    assert server.tables == {"region": 10, "nation": 10}
    assert second.schema_calls == 0
    assert second.load_calls == 0
    assert captured["phases"]["schema_creation"]["status"] == "SKIPPED"
    assert captured["phases"]["data_loading"]["status"] == "SKIPPED"
    assert captured["table_statistics"] == {"region": 10, "nation": 10}


@pytest.mark.unit
def test_load_only_tuned_rerun_reuses_compatible_tables(tmp_path):
    server = _FakeClickHouseServer()
    first = _FakeClickHouseLoadAdapter(server, tuning_enabled=True)
    _run_clickhouse_load_only(_make_clickhouse_load_bench(tmp_path, {}), first, tmp_path)
    assert first.schema_calls == 1
    assert server.tuned is True

    second = _FakeClickHouseLoadAdapter(server, tuning_enabled=True)
    captured: dict = {}
    res = _run_clickhouse_load_only(_make_clickhouse_load_bench(tmp_path, captured), second, tmp_path)
    assert isinstance(res, BenchmarkResults)
    assert server.drops == 0
    assert second.schema_calls == 0
    assert second.load_calls == 0
    assert captured["phases"]["schema_creation"]["status"] == "SKIPPED"
    assert captured["phases"]["data_loading"]["status"] == "SKIPPED"
    assert captured["table_statistics"] == {"region": 10, "nation": 10}


@pytest.mark.unit
def test_load_only_tuned_run_recreates_mismatched_tables(tmp_path):
    server = _FakeClickHouseServer()
    baseline = _FakeClickHouseLoadAdapter(server, tuning_enabled=False)
    _run_clickhouse_load_only(_make_clickhouse_load_bench(tmp_path, {}), baseline, tmp_path)
    assert server.tuned is False

    tuned = _FakeClickHouseLoadAdapter(server, tuning_enabled=True)
    captured: dict = {}
    res = _run_clickhouse_load_only(_make_clickhouse_load_bench(tmp_path, captured), tuned, tmp_path)
    assert isinstance(res, BenchmarkResults)
    assert server.drops == 1
    assert tuned.schema_calls == 1
    assert tuned.load_calls == 1
    assert server.tables == {"region": 10, "nation": 10}
    assert server.tuned is True
    assert captured["phases"]["schema_creation"]["status"] == "COMPLETED"
