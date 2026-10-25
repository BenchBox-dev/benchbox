from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from benchbox.core.results.models import BenchmarkResults

pytestmark = pytest.mark.medium


def _make_results(benchmark_name: str, scale: float) -> BenchmarkResults:
    return BenchmarkResults(
        benchmark_name=benchmark_name,
        platform="duckdb",
        scale_factor=scale,
        execution_id="test",
        timestamp=datetime.now(timezone.utc),
        duration_seconds=0.1,
        total_queries=1,
        successful_queries=1,
        failed_queries=0,
        total_execution_time=0.1,
        average_query_time=0.1,
    )


def test_duckdb_tpch_power_generates_data(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from examples.getting_started.local import duckdb_tpch_power as script

    outputs = tmp_path / "duckdb"
    monkeypatch.setattr(script, "_OUTPUT_ROOT", outputs)

    called = {}

    def fake_run(self, benchmark, **run_config):
        called.update(run_config)
        return _make_results("TPC-H", benchmark.scale_factor)

    monkeypatch.setattr(script.DuckDBAdapter, "run_benchmark", fake_run, raising=False)

    script.run_example(scale_factor=0.01, force_regenerate=True)

    data_dir = outputs / "tpch_sf_0.01"
    assert data_dir.exists()
    assert any(data_dir.iterdir())
    assert called["test_execution_type"] == "power"


def test_duckdb_tpch_power_dry_run_invokes_helper(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from examples.getting_started.local import duckdb_tpch_power as script

    def explode_run(*args, **kwargs):
        raise AssertionError("run_benchmark should not execute during dry run")

    monkeypatch.setattr(script.DuckDBAdapter, "run_benchmark", explode_run, raising=False)

    captured = {}

    def fake_execute_example_dry_run(**kwargs):
        captured.update(kwargs)
        preview_dir = kwargs["output_dir"]
        preview_dir.mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(warnings=[], queries={"1": "SELECT 1"}), {}

    monkeypatch.setattr(script, "execute_example_dry_run", fake_execute_example_dry_run)

    script.run_example(scale_factor=0.1, force_regenerate=True, dry_run_output=tmp_path / "preview")

    assert captured["benchmark_config"].name == "tpch"
    assert captured["benchmark_config"].test_execution_type == "power"
    assert captured["database_config"].type == "duckdb"
    assert captured["output_dir"] == tmp_path / "preview"


def test_duckdb_tpcds_power_runs(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from examples.getting_started.local import duckdb_tpcds_power as script

    outputs = tmp_path / "duckdb"
    monkeypatch.setattr(script, "_OUTPUT_ROOT", outputs)

    def fake_generate(self):
        return []

    monkeypatch.setattr(script.TPCDS, "generate_data", fake_generate, raising=False)

    called = {}

    def fake_run(self, benchmark, **run_config):
        called.update(run_config)
        return _make_results("TPC-DS", benchmark.scale_factor)

    monkeypatch.setattr(script.DuckDBAdapter, "run_benchmark", fake_run, raising=False)

    script.run_example(scale_factor=1.0, force_regenerate=True)

    assert called["test_execution_type"] == "power"


def test_duckdb_tpcds_power_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from examples.getting_started.local import duckdb_tpcds_power as script

    monkeypatch.setattr(
        script.DuckDBAdapter,
        "run_benchmark",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not run")),
        raising=False,
    )

    details = {}

    def fake_execute_example_dry_run(**kwargs):
        details.update(kwargs)
        kwargs["output_dir"].mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(warnings=[], queries={"Q1": "SELECT 1"}), {}

    monkeypatch.setattr(script, "execute_example_dry_run", fake_execute_example_dry_run)

    script.run_example(scale_factor=0.05, dry_run_output=tmp_path / "preview")

    assert details["benchmark_config"].name == "tpcds"
    assert details["benchmark_config"].test_execution_type == "power"
    assert details["database_config"].type == "duckdb"


def test_intermediate_subset_passes_queries(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from examples.getting_started.intermediate import duckdb_tpch_query_subset as script

    outputs = tmp_path / "duckdb"
    monkeypatch.setattr(script, "_OUTPUT_ROOT", outputs)

    monkeypatch.setattr(script.TPCH, "generate_data", lambda self: [], raising=False)

    captured: dict[str, list[str]] = {}

    def fake_run(self, benchmark, **run_config):
        captured["subset"] = run_config.get("query_subset")
        return _make_results("TPC-H", benchmark.scale_factor)

    monkeypatch.setattr(script.DuckDBAdapter, "run_benchmark", fake_run, raising=False)

    script.run_example(scale_factor=0.01, queries=("3", "7"), force_regenerate=True)

    assert captured["subset"] == ["3", "7"]


def test_intermediate_subset_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from examples.getting_started.intermediate import duckdb_tpch_query_subset as script

    monkeypatch.setattr(
        script.DuckDBAdapter,
        "run_benchmark",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not run")),
        raising=False,
    )

    captured = {}

    def fake_execute_example_dry_run(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(warnings=[], queries=["5", "9"]), {}

    monkeypatch.setattr(script, "execute_example_dry_run", fake_execute_example_dry_run)

    preview_dir = tmp_path / "preview"

    script.run_example(scale_factor=0.02, queries=("5", "9"), dry_run_output=preview_dir)

    assert captured["benchmark_config"].queries == ["5", "9"]
    assert captured["output_dir"] == preview_dir


def test_databricks_requires_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from examples.getting_started.cloud import databricks_tpch_power as script

    for name in ["DATABRICKS_HOST", "DATABRICKS_HTTP_PATH", "DATABRICKS_TOKEN"]:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError):
        script.run_example(scale_factor=0.01)


def test_databricks_runs_with_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from examples.getting_started.cloud import databricks_tpch_power as script

    outputs = tmp_path / "databricks"
    monkeypatch.setattr(script, "_OUTPUT_DIR", outputs)

    monkeypatch.setenv("DATABRICKS_HOST", "example.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_HTTP_PATH", "/sql/1.0/warehouses/abc")
    monkeypatch.setenv("DATABRICKS_TOKEN", "test-token")

    called = {}

    class DummyOrchestrator:
        def __init__(self, base_dir: str):
            called["base_dir"] = base_dir

        def execute_benchmark(self, **kwargs):
            called.update(kwargs)
            return _make_results("TPC-H", kwargs["config"].scale_factor)

    monkeypatch.setattr(script, "BenchmarkOrchestrator", DummyOrchestrator)

    script.run_example(scale_factor=0.01)

    assert called["phases_to_run"] == ["generate", "load", "power"]
    assert called["config"].name == "tpch"
    assert called["database_config"].type == "databricks"


def test_databricks_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from examples.getting_started.cloud import databricks_tpch_power as script

    monkeypatch.setenv("DATABRICKS_HOST", "example.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_HTTP_PATH", "/sql/1.0/warehouses/abc")
    monkeypatch.setenv("DATABRICKS_TOKEN", "test-token")

    monkeypatch.setattr(
        script,
        "BenchmarkOrchestrator",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("orchestrator should not run in dry mode")),
    )

    captured = {}

    def fake_execute_example_dry_run(**kwargs):
        captured.update(kwargs)
        kwargs["output_dir"].mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(warnings=[], queries={"1": "SELECT 1"}), {}

    monkeypatch.setattr(script, "execute_example_dry_run", fake_execute_example_dry_run)

    preview_dir = tmp_path / "preview"
    script.run_example(scale_factor=0.01, dry_run_output=preview_dir)

    assert captured["benchmark_config"].test_execution_type == "power"
    assert captured["database_config"].type == "databricks"
    assert captured["output_dir"] == preview_dir


def test_bigquery_requires_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from examples.getting_started.cloud import bigquery_tpch_dry_run as script

    for name in ["BIGQUERY_PROJECT", "BIGQUERY_DATASET"]:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError):
        script.run_example(scale_factor=0.01)


def test_bigquery_dry_run_saves_output(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from examples.getting_started.cloud import bigquery_tpch_dry_run as script

    monkeypatch.setenv("BIGQUERY_PROJECT", "test-project")
    monkeypatch.setenv("BIGQUERY_DATASET", "benchbox_dataset")

    captured = {}

    def fake_execute_example_dry_run(**kwargs):
        captured.update(kwargs)
        out_dir = kwargs["output_dir"]
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / "preview.json"
        target.write_text("{}\n")
        return SimpleNamespace(warnings=[], queries={"Q1": "SELECT 1"}), {"json": target}

    monkeypatch.setattr(script, "execute_example_dry_run", fake_execute_example_dry_run)

    preview_dir = tmp_path / "bigquery"
    script.run_example(scale_factor=0.01, dry_run_output=preview_dir)

    assert (preview_dir / "preview.json").exists()
    assert captured["database_config"].type == "bigquery"


def test_firebolt_core_runs_with_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from examples.getting_started.cloud import firebolt_core_tpch as script

    outputs = tmp_path / "firebolt_core"
    monkeypatch.setattr(script, "_OUTPUT_DIR", outputs)

    called = {}

    class DummyOrchestrator:
        def __init__(self, base_dir: str):
            called["base_dir"] = base_dir

        def execute_benchmark(self, **kwargs):
            called.update(kwargs)
            return _make_results("TPC-H", kwargs["config"].scale_factor)

    monkeypatch.setattr(script, "BenchmarkOrchestrator", DummyOrchestrator)

    script.run_example(scale_factor=0.01)

    assert called["phases_to_run"] == ["generate", "load", "power"]
    assert called["config"].name == "tpch"
    assert called["database_config"].type == "firebolt"
    assert "url" in called["database_config"].options


def test_firebolt_core_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from examples.getting_started.cloud import firebolt_core_tpch as script

    monkeypatch.setattr(
        script,
        "BenchmarkOrchestrator",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("orchestrator should not run in dry mode")),
    )

    captured = {}

    def fake_execute_example_dry_run(**kwargs):
        captured.update(kwargs)
        kwargs["output_dir"].mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(warnings=[], queries={"1": "SELECT 1"}), {}

    monkeypatch.setattr(script, "execute_example_dry_run", fake_execute_example_dry_run)

    preview_dir = tmp_path / "preview"
    script.run_example(scale_factor=0.01, dry_run_output=preview_dir)

    assert captured["benchmark_config"].test_execution_type == "power"
    assert captured["database_config"].type == "firebolt"
    assert captured["output_dir"] == preview_dir


def test_firebolt_cloud_requires_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from examples.getting_started.cloud import firebolt_cloud_tpch as script

    for name in ["FIREBOLT_CLIENT_ID", "FIREBOLT_CLIENT_SECRET", "FIREBOLT_ACCOUNT", "FIREBOLT_ENGINE"]:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError):
        script.run_example(scale_factor=0.01)


def test_firebolt_cloud_runs_with_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from examples.getting_started.cloud import firebolt_cloud_tpch as script

    outputs = tmp_path / "firebolt_cloud"
    monkeypatch.setattr(script, "_OUTPUT_DIR", outputs)

    monkeypatch.setenv("FIREBOLT_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("FIREBOLT_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("FIREBOLT_ACCOUNT", "test-account")
    monkeypatch.setenv("FIREBOLT_ENGINE", "test-engine")

    called = {}

    class DummyOrchestrator:
        def __init__(self, base_dir: str):
            called["base_dir"] = base_dir

        def execute_benchmark(self, **kwargs):
            called.update(kwargs)
            return _make_results("TPC-H", kwargs["config"].scale_factor)

    monkeypatch.setattr(script, "BenchmarkOrchestrator", DummyOrchestrator)

    script.run_example(scale_factor=0.01)

    assert called["phases_to_run"] == ["generate", "load", "power"]
    assert called["config"].name == "tpch"
    assert called["database_config"].type == "firebolt"
    assert called["database_config"].options["client_id"] == "test-client-id"
    assert called["database_config"].options["engine_name"] == "test-engine"


def test_firebolt_cloud_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from examples.getting_started.cloud import firebolt_cloud_tpch as script

    monkeypatch.setenv("FIREBOLT_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("FIREBOLT_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("FIREBOLT_ACCOUNT", "test-account")
    monkeypatch.setenv("FIREBOLT_ENGINE", "test-engine")

    monkeypatch.setattr(
        script,
        "BenchmarkOrchestrator",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("orchestrator should not run in dry mode")),
    )

    captured = {}

    def fake_execute_example_dry_run(**kwargs):
        captured.update(kwargs)
        kwargs["output_dir"].mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(warnings=[], queries={"1": "SELECT 1"}), {}

    monkeypatch.setattr(script, "execute_example_dry_run", fake_execute_example_dry_run)

    preview_dir = tmp_path / "preview"
    script.run_example(scale_factor=0.01, dry_run_output=preview_dir)

    assert captured["benchmark_config"].test_execution_type == "power"
    assert captured["database_config"].type == "firebolt"
    assert captured["output_dir"] == preview_dir


def test_athena_requires_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from examples.getting_started.cloud import athena_tpch as script

    monkeypatch.delenv("ATHENA_S3_STAGING_DIR", raising=False)

    with pytest.raises(RuntimeError):
        script.run_example(scale_factor=0.01)


def test_athena_runs_with_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from examples.getting_started.cloud import athena_tpch as script

    outputs = tmp_path / "athena"
    monkeypatch.setattr(script, "_OUTPUT_DIR", outputs)

    monkeypatch.setenv("ATHENA_S3_STAGING_DIR", "s3://test-bucket/benchbox/")

    called = {}

    class DummyOrchestrator:
        def __init__(self, base_dir: str):
            called["base_dir"] = base_dir

        def execute_benchmark(self, **kwargs):
            called.update(kwargs)
            return _make_results("TPC-H", kwargs["config"].scale_factor)

    monkeypatch.setattr(script, "BenchmarkOrchestrator", DummyOrchestrator)

    script.run_example(scale_factor=0.01)

    assert called["phases_to_run"] == ["generate", "load", "power"]
    assert called["config"].name == "tpch"
    assert called["database_config"].type == "athena"
    assert called["database_config"].options["s3_staging_dir"] == "s3://test-bucket/benchbox/"


def test_athena_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from examples.getting_started.cloud import athena_tpch as script

    monkeypatch.setenv("ATHENA_S3_STAGING_DIR", "s3://test-bucket/benchbox/")

    monkeypatch.setattr(
        script,
        "BenchmarkOrchestrator",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("orchestrator should not run in dry mode")),
    )

    captured = {}

    def fake_execute_example_dry_run(**kwargs):
        captured.update(kwargs)
        kwargs["output_dir"].mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(warnings=[], queries={"1": "SELECT 1"}), {}

    monkeypatch.setattr(script, "execute_example_dry_run", fake_execute_example_dry_run)

    preview_dir = tmp_path / "preview"
    script.run_example(scale_factor=0.01, dry_run_output=preview_dir)

    assert captured["benchmark_config"].test_execution_type == "power"
    assert captured["database_config"].type == "athena"
    assert captured["output_dir"] == preview_dir
