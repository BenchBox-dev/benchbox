import pytest

from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark
from benchbox.core.runner.runner import LifecyclePhases, ValidationOptions, run_benchmark_lifecycle
from benchbox.core.schemas import BenchmarkConfig, DatabaseConfig
from benchbox.core.system import SystemProfiler

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]


@pytest.mark.integration
@pytest.mark.duckdb
def test_primitives_lifecycle_end_to_end(tmp_path):

    db_path = tmp_path / "primitives_lifecycle.duckdb"
    output_root = tmp_path / "bb_output"
    data_dir = tmp_path / "data"
    output_root.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    adapter_config = {
        "platform": "duckdb",
        "benchmark": "read_primitives",
        "scale_factor": 0.01,
        "database_path": str(db_path),
        "output_dir": str(data_dir),
        "memory_limit": "512MB",
        "force": True,
    }

    adapter = PlatformRegistry.create_adapter("duckdb", adapter_config)

    benchmark_config = BenchmarkConfig(
        name="read_primitives",
        display_name="Primitives",
        scale_factor=0.01,
        queries=["aggregation_distinct"],
        concurrency=1,
        options={
            "power_iterations": 1,
            "power_warmup_iterations": 0,
            "force_regenerate": True,
        },
        test_execution_type="power",
    )

    database_config = DatabaseConfig(
        type="duckdb",
        name="duckdb_primitives_lifecycle",
        options={"database_path": str(db_path)},
    )

    system_profile = SystemProfiler().get_system_profile()

    benchmark_instance = ReadPrimitivesBenchmark(
        scale_factor=0.01,
        output_dir=str(data_dir),
        parallel=1,
        force_regenerate=True,
    )

    result = run_benchmark_lifecycle(
        benchmark_config=benchmark_config,
        database_config=database_config,
        system_profile=system_profile,
        platform_config={
            "database_path": str(db_path),
            "force": True,
            "benchmark": "read_primitives",
            "scale_factor": 0.01,
        },
        platform_adapter=adapter,
        benchmark_instance=benchmark_instance,
        phases=LifecyclePhases(generate=True, load=True, execute=True),
        validation_opts=ValidationOptions(),
        output_root=str(data_dir),
    )

    assert result.total_queries > 0
    assert result.failed_queries == 0
    assert result.successful_queries == result.total_queries
    assert result.query_results, "Expected at least one query result entry"


