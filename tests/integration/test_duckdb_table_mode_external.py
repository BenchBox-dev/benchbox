from __future__ import annotations

import duckdb
import pytest

from benchbox.core.platform_registry import PlatformRegistry
from benchbox.core.runner.runner import LifecyclePhases, ValidationOptions, run_benchmark_lifecycle
from benchbox.core.schemas import BenchmarkConfig, DatabaseConfig
from benchbox.core.system import SystemProfiler
from benchbox.tpch import TPCH

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]


def _run_tpch_duckdb(
    *,
    benchmark_instance,
    output_dir,
    database_path,
    table_mode: str,
    phases: LifecyclePhases,
    force_regenerate: bool,
):
    adapter_config = {
        "platform": "duckdb",
        "benchmark": "tpch",
        "scale_factor": 0.01,
        "database_path": str(database_path),
        "output_dir": str(output_dir),
        "force": True,
    }

    adapter = PlatformRegistry.create_adapter("duckdb", adapter_config)

    options: dict[str, object] = {
        "power_iterations": 1,
        "power_warmup_iterations": 0,
        "force_regenerate": force_regenerate,
        "table_mode": table_mode,
    }

    benchmark_config = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=0.01,
        queries=["1"],
        concurrency=1,
        options=options,
        test_execution_type="power",
    )

    database_config = DatabaseConfig(
        type="duckdb",
        name=f"duckdb_tpch_{table_mode}",
        options={"database_path": str(database_path)},
    )

    return run_benchmark_lifecycle(
        benchmark_config=benchmark_config,
        database_config=database_config,
        system_profile=SystemProfiler().get_system_profile(),
        platform_config={
            "database_path": str(database_path),
            "force": True,
            "benchmark": "tpch",
            "scale_factor": 0.01,
        },
        platform_adapter=adapter,
        benchmark_instance=benchmark_instance,
        phases=phases,
        validation_opts=ValidationOptions(),
        output_root=str(output_dir),
    )


@pytest.mark.integration
@pytest.mark.duckdb
def test_tpch_duckdb_external_mode_matches_native_results(tmp_path):

    native_output = tmp_path / "native_data"
    external_output = tmp_path / "external_data"
    native_output.mkdir(parents=True, exist_ok=True)
    external_output.mkdir(parents=True, exist_ok=True)

    native_db = tmp_path / "native.duckdb"
    external_db = tmp_path / "external.duckdb"

    benchmark_native = TPCH(
        scale_factor=0.01,
        output_dir=str(native_output),
        parallel=1,
        force_regenerate=True,
        verbose=False,
    )

    native_result = _run_tpch_duckdb(
        benchmark_instance=benchmark_native,
        output_dir=native_output,
        database_path=native_db,
        table_mode="native",
        phases=LifecyclePhases(generate=True, load=True, execute=True),
        force_regenerate=True,
    )

    parquet_tables: dict[str, str] = {}
    con = duckdb.connect(str(native_db))
    try:
        for table_name in sorted(benchmark_native.tables.keys()):
            parquet_path = external_output / f"{table_name}.parquet"
            con.execute(f"COPY (SELECT * FROM {table_name}) TO '{parquet_path}' (FORMAT PARQUET)")
            parquet_tables[table_name] = str(parquet_path)
    finally:
        con.close()

    benchmark_external = TPCH(
        scale_factor=0.01,
        output_dir=str(external_output),
        parallel=1,
        force_regenerate=False,
        verbose=False,
    )
    benchmark_external._impl.tables = parquet_tables

    external_result = _run_tpch_duckdb(
        benchmark_instance=benchmark_external,
        output_dir=external_output,
        database_path=external_db,
        table_mode="external",
        phases=LifecyclePhases(generate=False, load=True, execute=True),
        force_regenerate=False,
    )

    native_summary = [(qr["query_id"], qr["status"], qr["rows_returned"]) for qr in native_result.query_results]
    external_summary = [(qr["query_id"], qr["status"], qr["rows_returned"]) for qr in external_result.query_results]

    assert native_summary == external_summary

    ext_con = duckdb.connect(str(external_db))
    try:
        table_types = ext_con.execute(
            "SELECT table_name, table_type FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchall()
        views = {name for name, ttype in table_types if ttype == "VIEW"}
        base_tables = {name for name, ttype in table_types if ttype == "BASE TABLE"}

        for table_name in parquet_tables:
            assert table_name in views, (
                f"Expected '{table_name}' to be a VIEW in external mode, "
                f"but found base_tables={base_tables}, views={views}"
            )
    finally:
        ext_con.close()


@pytest.mark.integration
@pytest.mark.duckdb
def test_external_mode_works_on_reused_database(tmp_path):

    native_dir = tmp_path / "native_data"
    external_dir = tmp_path / "external_data"
    native_dir.mkdir()
    external_dir.mkdir()
    db_path = tmp_path / "reuse.duckdb"

    benchmark = TPCH(
        scale_factor=0.01,
        output_dir=str(native_dir),
        parallel=1,
        force_regenerate=True,
        verbose=False,
    )

    _run_tpch_duckdb(
        benchmark_instance=benchmark,
        output_dir=native_dir,
        database_path=db_path,
        table_mode="native",
        phases=LifecyclePhases(generate=True, load=True, execute=True),
        force_regenerate=True,
    )

    con = duckdb.connect(str(db_path))
    try:
        native_types = dict(
            con.execute(
                "SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        )
        assert any(t == "BASE TABLE" for t in native_types.values()), (
            f"Expected BASE TABLEs after native run, got {native_types}"
        )

        parquet_tables: dict[str, str] = {}
        for table_name in sorted(benchmark.tables.keys()):
            parquet_path = external_dir / f"{table_name}.parquet"
            con.execute(f"COPY (SELECT * FROM {table_name}) TO '{parquet_path}' (FORMAT PARQUET)")
            parquet_tables[table_name] = str(parquet_path)
    finally:
        con.close()

    benchmark_ext = TPCH(
        scale_factor=0.01,
        output_dir=str(external_dir),
        parallel=1,
        force_regenerate=False,
        verbose=False,
    )
    benchmark_ext._impl.tables = parquet_tables

    external_result = _run_tpch_duckdb(
        benchmark_instance=benchmark_ext,
        output_dir=external_dir,
        database_path=db_path,
        table_mode="external",
        phases=LifecyclePhases(generate=False, load=True, execute=True),
        force_regenerate=False,
    )

    con = duckdb.connect(str(db_path))
    try:
        types_after = dict(
            con.execute(
                "SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        )
        views = {name for name, ttype in types_after.items() if ttype == "VIEW"}
        expected_tables = set(benchmark.tables.keys())
        missing_views = expected_tables - views
        assert not missing_views, (
            f"External mode on reused DB failed to create VIEWs for: {missing_views}. Table types: {types_after}"
        )
    finally:
        con.close()

    assert external_result.total_queries >= 1
    assert all(qr["status"] == "SUCCESS" for qr in external_result.query_results)


