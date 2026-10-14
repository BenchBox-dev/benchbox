from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from benchbox.cli.commands.compare import compare
from benchbox.cli.commands.report import report
from benchbox.core.results.database import ResultDatabase
from benchbox.core.results.models import BenchmarkResults, QueryExecution
from benchbox.core.results.schema import build_result_payload
from tests.unit.mcp.public_api import get_tool

pytestmark = [pytest.mark.unit, pytest.mark.fast]

LEGACY_CLICKHOUSE_DEPLOYMENT = {
    "collection_status": "partial",
    "connection_mode": "server",
    "deployment_type": "self_hosted",
    "endpoint_class": "localhost_port",
    "metadata_source": "inferred",
}


def _write_bundle(
    path: Path,
    *,
    platform: str,
    run_id: str,
    deployment: dict | None = None,
    engine: str | None = None,
    gateway: str | None = None,
    query_ms: float = 100.0,
    timestamp: datetime | None = None,
) -> Path:
    platform_info: dict = {}
    if engine:
        platform_info["execution_engine"] = {"requested": engine, "applied": engine, "resolution": "explicit"}
    if gateway:
        platform_info["gateway"] = {"name": gateway, "routed": True}
    result = BenchmarkResults(
        benchmark_name="TPC-H",
        platform=platform,
        scale_factor=0.01,
        execution_id=run_id,
        timestamp=timestamp or datetime.now(timezone.utc),
        duration_seconds=1.0,
        total_queries=2,
        successful_queries=2,
        failed_queries=0,
        platform_info=platform_info,
        platform_deployment=deployment,
        query_results=[
            QueryExecution(query_id="Q1", status="SUCCESS", execution_time_ms=query_ms),
            QueryExecution(query_id="Q2", status="SUCCESS", execution_time_ms=query_ms * 2),
        ],
    )
    path.write_text(json.dumps(build_result_payload(result), default=str), encoding="utf-8")
    return path


def _clickhouse(tmp_path: Path, name: str, selected: str | None) -> Path:
    deployment = dict(LEGACY_CLICKHOUSE_DEPLOYMENT)
    if selected:
        deployment["selected"] = selected
    return _write_bundle(tmp_path / f"{name}.json", platform="ClickHouse Server", run_id=name, deployment=deployment)


@pytest.fixture
def bundles(tmp_path: Path) -> dict[str, Path]:
    return {
        "clickhouse_legacy": _clickhouse(tmp_path, "clickhouse_legacy", None),
        "clickhouse_self_hosted": _clickhouse(tmp_path, "clickhouse_self_hosted", "self-hosted"),
        "clickhouse_cloud": _clickhouse(tmp_path, "clickhouse_cloud", "cloud"),
        "polars_default": _write_bundle(tmp_path / "polars_default.json", platform="Polars", run_id="pd"),
        "polars_streaming": _write_bundle(
            tmp_path / "polars_streaming.json", platform="Polars", run_id="ps", engine="streaming"
        ),
        "snowflake_native": _write_bundle(tmp_path / "snowflake_native.json", platform="Snowflake", run_id="sn"),
        "snowflake_greybeam": _write_bundle(
            tmp_path / "snowflake_greybeam.json", platform="Snowflake", run_id="sg", gateway="greybeam"
        ),
    }


INCOMPARABLE_PAIRS = [
    ("clickhouse_self_hosted", "clickhouse_cloud", "deployment differs (self-hosted vs cloud)"),
    ("clickhouse_legacy", "clickhouse_cloud", "deployment differs (self-hosted vs cloud)"),
    ("polars_default", "polars_streaming", "execution engine differs (default vs streaming)"),
    ("snowflake_native", "snowflake_greybeam", "gateway differs (native vs greybeam)"),
]


def _flat(text: str) -> str:
    return " ".join(text.split())


@pytest.mark.parametrize(("baseline", "current", "difference"), INCOMPARABLE_PAIRS)
def test_cli_compare_refuses_bundles_of_different_variants(
    bundles: dict[str, Path], baseline: str, current: str, difference: str
) -> None:
    result = CliRunner().invoke(compare, [str(bundles[baseline]), str(bundles[current])])

    assert result.exit_code == 1
    assert difference in _flat(result.output)

    allowed = CliRunner().invoke(
        compare, [str(bundles[baseline]), str(bundles[current]), "--allow-heterogeneous-comparison"]
    )
    assert allowed.exit_code == 0, allowed.output


def test_cli_compare_accepts_legacy_bundle_against_default_deployment(bundles: dict[str, Path]) -> None:
    result = CliRunner().invoke(compare, [str(bundles["clickhouse_legacy"]), str(bundles["clickhouse_self_hosted"])])

    assert result.exit_code == 0, result.output
    assert "Total queries compared: 2" in result.output


def _analyze_results(results_dir: Path):
    from mcp.server.mcpserver import MCPServer

    from benchbox.mcp.tools.analytics import register_analytics_tools

    server = MCPServer("test", version="test")
    register_analytics_tools(server, results_dir=results_dir)
    return get_tool(server, "analyze_results")


@pytest.mark.parametrize(("baseline", "current", "difference"), INCOMPARABLE_PAIRS)
def test_mcp_compare_results_refuses_bundles_of_different_variants(
    bundles: dict[str, Path], baseline: str, current: str, difference: str
) -> None:
    tool = _analyze_results(bundles[baseline].parent)

    response = tool.fn(analysis="compare", file1=bundles[baseline].name, file2=bundles[current].name)

    assert response["error"] is True
    assert response["error_code"] == "VALIDATION_ERROR"
    assert difference in response["message"]
    assert difference in response["details"]["differences"]


def test_mcp_compare_results_accepts_legacy_bundle_against_default_deployment(bundles: dict[str, Path]) -> None:
    tool = _analyze_results(bundles["clickhouse_legacy"].parent)

    response = tool.fn(
        analysis="compare", file1=bundles["clickhouse_legacy"].name, file2=bundles["clickhouse_self_hosted"].name
    )

    assert response["summary"]["total_queries_compared"] == 2


def _import_and_report(results: Path, db_path: Path) -> str:
    runner = CliRunner()
    imported = runner.invoke(report, ["import", str(results), "--db-path", str(db_path)])
    assert imported.exit_code == 0, imported.output
    regressions = runner.invoke(report, ["regressions", "--db-path", str(db_path)])
    assert regressions.exit_code == 0, regressions.output
    return _flat(regressions.output)


def test_report_regressions_never_blends_execution_engines(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    results = tmp_path / "results"
    results.mkdir()
    _write_bundle(
        results / "old.json", platform="Polars", run_id="old", query_ms=100.0, timestamp=now - timedelta(days=40)
    )
    _write_bundle(
        results / "new.json", platform="Polars", run_id="new", query_ms=102.0, timestamp=now - timedelta(days=5)
    )
    _write_bundle(
        results / "streaming.json",
        platform="Polars",
        run_id="streaming",
        engine="streaming",
        query_ms=300.0,
        timestamp=now - timedelta(days=5),
    )

    output = _import_and_report(results, tmp_path / "results.db")

    assert "No performance regressions detected!" in output


def test_report_regressions_compares_each_variant_with_itself(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    results = tmp_path / "results"
    results.mkdir()
    _write_bundle(
        results / "old.json", platform="Polars", run_id="old", query_ms=300.0, timestamp=now - timedelta(days=40)
    )
    _write_bundle(
        results / "streaming_old.json",
        platform="Polars",
        run_id="streaming_old",
        engine="streaming",
        query_ms=100.0,
        timestamp=now - timedelta(days=40),
    )
    _write_bundle(
        results / "streaming_new.json",
        platform="Polars",
        run_id="streaming_new",
        engine="streaming",
        query_ms=150.0,
        timestamp=now - timedelta(days=5),
    )

    db_path = tmp_path / "results.db"
    output = _import_and_report(results, db_path)

    assert "Found 1 regression(s)" in output
    assert "Variant" in output
    regressions = ResultDatabase(db_path).detect_regressions(threshold_pct=10.0, lookback_days=30)
    assert [(r.variant, round(r.change_pct or 0)) for r in regressions] == [
        ("deployment=default, execution engine=streaming, gateway=native", 50)
    ]


def test_report_trends_follow_the_latest_runs_variant(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    results = tmp_path / "results"
    results.mkdir()
    _write_bundle(
        results / "old.json", platform="Polars", run_id="old", query_ms=100.0, timestamp=now - timedelta(days=40)
    )
    _write_bundle(
        results / "streaming.json",
        platform="Polars",
        run_id="streaming",
        engine="streaming",
        query_ms=300.0,
        timestamp=now - timedelta(days=5),
    )
    db_path = tmp_path / "results.db"
    _import_and_report(results, db_path)

    trends = ResultDatabase(db_path).get_performance_trends("Polars", "TPC-H", 0.01, periods=2, period_days=30)

    assert [(t.sample_count, t.variant) for t in trends] == [
        (1, "deployment=default, execution engine=streaming, gateway=native")
    ]
