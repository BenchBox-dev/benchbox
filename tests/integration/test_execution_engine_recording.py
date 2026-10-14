from __future__ import annotations

import functools
import json
import types
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.integration._cli_e2e_utils import run_cli_command

pytestmark = [pytest.mark.integration, pytest.mark.medium]

pl = pytest.importorskip("polars")


def _bundles(results_dir: Path) -> list[Path]:
    return sorted(path for path in results_dir.glob("*.json") if not path.name.startswith("_"))


def _only_bundle(results_dir: Path) -> dict[str, Any]:
    bundles = _bundles(results_dir)
    assert len(bundles) == 1, bundles
    return json.loads(bundles[0].read_text(encoding="utf-8"))


def _cli_run(tmp_path: Path, platform: str, *args: str, benchmark: str = "tpch") -> Any:
    return run_cli_command(
        [
            "run",
            "--platform",
            platform,
            "--benchmark",
            benchmark,
            "--scale",
            "0.01",
            *args,
            "--non-interactive",
            "--output",
            str(tmp_path / "out"),
        ],
        env={"BENCHBOX_OUTPUT_DIR": str(tmp_path)},
        timeout=300,
    )


@pytest.mark.parametrize(
    ("engine_args", "requested", "collect_kwargs", "variant"),
    [
        (("--execution-engine", "streaming"), "streaming", {"engine": "streaming"}, "polars-df+streaming"),
        ((), "default", {}, "polars-df"),
    ],
)
def test_cli_polars_run_records_requested_engine_and_collect_kwargs(
    tmp_path: Path,
    engine_args: tuple[str, ...],
    requested: str,
    collect_kwargs: dict[str, str],
    variant: str,
) -> None:
    result = _cli_run(tmp_path, "polars-df", *engine_args, "--queries", "6", "--phases", "generate,load,power")
    assert result.returncode == 0, result.stdout + result.stderr

    bundle = _only_bundle(tmp_path / "results")
    assert bundle["config"]["execution_engine"] == requested
    assert bundle["platform"]["execution_engine"]["requested"] == requested
    assert bundle["platform"]["execution_engine"]["applied_native"]["collect_kwargs"] == collect_kwargs
    assert bundle["platform"]["variant"] == variant


_DEFAULT_RECEIPT = {
    "requested": "default",
    "applied": None,
    "applied_class": None,
    "resolution": "platform_default",
    "observed": "not_captured",
    "observed_source": "none",
}


@pytest.mark.parametrize(("platform", "variant"), [("duckdb", "duckdb"), ("pandas-df", "pandas-df")])
def test_cli_run_on_a_platform_without_engines_records_the_default_receipt(
    tmp_path: Path, platform: str, variant: str
) -> None:
    pytest.importorskip("pandas")
    result = _cli_run(tmp_path, platform, "--queries", "6", "--phases", "generate,load,power")
    assert result.returncode == 0, result.stdout + result.stderr

    bundle = _only_bundle(tmp_path / "results")
    assert bundle["config"]["execution_engine"] == "default"
    assert bundle["platform"]["execution_engine"] == _DEFAULT_RECEIPT
    assert bundle["platform"]["variant"] == variant


@contextmanager
def _collect_calls() -> Iterator[list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    original = pl.LazyFrame.collect

    @functools.wraps(original)
    def spy(self: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(dict(kwargs))
        return original(self, *args, **kwargs)

    pl.LazyFrame.collect = spy
    try:
        yield calls
    finally:
        pl.LazyFrame.collect = original


@pytest.mark.parametrize(("engine", "expected_engines"), [("streaming", {"streaming"}), ("default", set())])
def test_mcp_run_benchmark_records_requested_engine_and_collect_kwargs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine: str, expected_engines: set[str]
) -> None:
    from benchbox.mcp import create_server
    from tests.unit.mcp.public_api import call_tool

    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path))
    server = create_server(results_dir=tmp_path / "results", charts_dir=tmp_path / "charts")
    with _collect_calls() as calls:
        response = call_tool(
            server,
            "run_benchmark",
            platform="polars-df",
            benchmark="tpch",
            scale_factor=0.01,
            queries="6",
            phases="load,power",
            execution_engine=engine,
        )
    assert response["mcp_metadata"]["status"] == "completed", response

    assert calls
    assert {call["engine"] for call in calls if "engine" in call} == expected_engines
    bundle = _only_bundle(tmp_path / "results")
    assert bundle["config"]["execution_engine"] == engine
    assert bundle["platform"]["execution_engine"]["requested"] == engine


_STREAMING_TUNING = """execution:
  engine_affinity: streaming
_metadata:
  version: "1.0"
  format: dataframe_tuning
  platform: polars
"""


@pytest.mark.parametrize("source", ["explicit", "legacy_option", "tuning_file"])
def test_cli_write_primitives_with_non_default_engine_fails_before_work(tmp_path: Path, source: str) -> None:
    tuning_file = tmp_path / "streaming.yaml"
    tuning_file.write_text(_STREAMING_TUNING, encoding="utf-8")
    engine_args = {
        "explicit": ("--execution-engine", "streaming"),
        "legacy_option": ("--platform-option", "streaming=true"),
        "tuning_file": ("--tuning", str(tuning_file)),
    }[source]
    result = _cli_run(tmp_path, "polars-df", *engine_args, benchmark="write_primitives")
    output = result.stdout + result.stderr

    assert result.returncode != 0, output
    assert "write_primitives benchmark does not run its timed DataFrame operations" in " ".join(output.split())
    generated = [path for path in (tmp_path / "out").rglob("*") if path.is_file()]
    assert generated == []


def test_mcp_write_primitives_with_non_default_engine_fails_before_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchbox.mcp import create_server
    from tests.unit.mcp.public_api import call_tool

    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(tmp_path))
    server = create_server(results_dir=tmp_path / "results", charts_dir=tmp_path / "charts")
    response = call_tool(
        server,
        "run_benchmark",
        platform="polars-df",
        benchmark="write_primitives",
        scale_factor=0.01,
        execution_engine="streaming",
    )

    assert "write_primitives benchmark does not run its timed DataFrame operations" in json.dumps(response)
    assert not any(path.is_file() and path.suffix in {".parquet", ".tbl", ".csv"} for path in tmp_path.rglob("*"))


def test_tpcdi_dataframe_workload_passes_adapter_engine_to_maintenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import benchbox.core.tpcdi.benchmark as tpcdi_module
    from benchbox.platforms import get_adapter

    created: list[Any] = []
    factory = tpcdi_module.get_maintenance_operations_for_platform

    def recording_factory(*args: Any, **kwargs: Any) -> Any:
        operations = factory(*args, **kwargs)
        created.append(operations)
        return operations

    monkeypatch.setattr(tpcdi_module, "get_maintenance_operations_for_platform", recording_factory)
    adapter = get_adapter("polars-df", mode="dataframe", working_dir=tmp_path, execution_engine="streaming")
    benchmark = tpcdi_module.TPCDIBenchmark(scale_factor=0.01, output_dir=tmp_path / "data")

    benchmark.execute_dataframe_workload(
        ctx=adapter.create_context(),
        adapter=adapter,
        benchmark_config=types.SimpleNamespace(options={}),
        query_filter={"__none__"},
    )

    assert created
    assert {operations.execution_engine for operations in created} == {"streaming"}
