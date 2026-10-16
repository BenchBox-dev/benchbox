from __future__ import annotations

import json

import pytest

from benchbox.core.results.schema_policy import CURRENT_SCHEMA_VERSION
from tests.fixtures.result_dict_fixtures import make_v2_result_dict
from tests.integration._cli_e2e_utils import run_cli_command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
]


def make_v2_result_data(
    benchmark_name: str = "Test Benchmark",
    benchmark_id: str = "test_bench",
    execution_id: str = "test123",
    platform: str = "TestPlatform",
    scale_factor: float = 1.0,
    queries: list | None = None,
) -> dict:

    if queries is None:
        queries = [
            {"id": "1", "ms": 100.0, "rows": 10},
            {"id": "2", "ms": 200.0, "rows": 20},
        ]

    total_ms = sum(q.get("ms", 0) for q in queries)
    return make_v2_result_dict(
        version="2.0",
        benchmark_id=benchmark_id,
        benchmark_name=benchmark_name,
        platform=platform,
        execution_id=execution_id,
        scale_factor=scale_factor,
        total_duration_ms=total_ms + 500,
        query_time_ms=total_ms,
        total_queries=len(queries),
        passed_queries=len(queries),
        total_ms=total_ms,
        queries=queries,
    )


@pytest.mark.integration
def test_export_command_help():

    result = run_cli_command(["export", "--help"])

    assert result.returncode == 0
    assert "Export benchmark results to various formats" in result.stdout
    assert "--format" in result.stdout
    assert "--output-dir" in result.stdout
    assert "--last" in result.stdout
    assert "--benchmark" in result.stdout
    assert "--platform" in result.stdout
    assert "--force" in result.stdout


@pytest.mark.integration
def test_export_command_no_args_shows_guidance():

    result = run_cli_command(["export"])

    assert result.returncode == 0
    assert "Please specify a result file or use --last" in result.stdout
    assert "Examples:" in result.stdout


@pytest.mark.integration
def test_export_last_with_no_results(tmp_path):

    result = run_cli_command(["export", "--last"], cwd=tmp_path)

    assert result.returncode == 0
    assert "No results found" in result.stdout


@pytest.mark.integration
def test_export_nonexistent_file():

    result = run_cli_command(["export", "/nonexistent/file.json"])

    assert "not found" in result.stdout.lower() or result.returncode != 0


@pytest.mark.integration
def test_export_specific_file_to_csv(tmp_path):

    result_file = tmp_path / "test_result.json"
    result_data = make_v2_result_data()

    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result_data, f)

    output_dir = tmp_path / "exports"
    output_dir.mkdir()

    result = run_cli_command(["export", str(result_file), "--format", "csv", "--output-dir", str(output_dir)])

    assert result.returncode == 0
    assert "Loaded: Test Benchmark" in result.stdout
    assert "Export complete" in result.stdout
    assert "CSV:" in result.stdout

    csv_files = list(output_dir.glob("*.csv"))
    assert len(csv_files) == 1
    assert csv_files[0].exists()

    csv_content = csv_files[0].read_text()
    assert "query_id" in csv_content or "id" in csv_content


@pytest.mark.integration
def test_export_multiple_formats(tmp_path):

    result_file = tmp_path / "test_result.json"
    result_data = make_v2_result_data()

    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result_data, f)

    output_dir = tmp_path / "exports"
    output_dir.mkdir()

    result = run_cli_command(
        [
            "export",
            str(result_file),
            "--format",
            "csv",
            "--format",
            "html",
            "--format",
            "json",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert result.returncode == 0
    assert "Exporting to 3 format(s)" in result.stdout
    assert "Export complete" in result.stdout

    assert len(list(output_dir.glob("*.csv"))) == 1
    assert len(list(output_dir.glob("*.html"))) == 1
    assert len(list(output_dir.glob("*.json"))) == 1


@pytest.mark.integration
def test_export_with_force_flag(tmp_path):

    result_file = tmp_path / "test_result.json"
    result_data = make_v2_result_data()

    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result_data, f)

    output_dir = tmp_path / "exports"
    output_dir.mkdir()

    result1 = run_cli_command(
        ["export", str(result_file), "--format", "csv", "--output-dir", str(output_dir), "--force"]
    )

    assert result1.returncode == 0

    result2 = run_cli_command(
        ["export", str(result_file), "--format", "csv", "--output-dir", str(output_dir), "--force"]
    )

    assert result2.returncode == 0
    assert "Export complete" in result2.stdout


@pytest.mark.integration
def test_export_invalid_schema_version(tmp_path):

    result_file = tmp_path / "old_schema.json"

    result_data = {
        "schema_version": "0.5",
        "benchmark": {"name": "Test"},
    }

    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result_data, f)

    result = run_cli_command(["export", str(result_file), "--format", "csv"])

    assert result.returncode == 1
    assert (
        "Unsupported schema version" in result.stdout
        or "Error" in result.stdout
        or "unsupported" in result.stderr.lower()
    )


@pytest.mark.integration
def test_export_corrupted_json(tmp_path):

    result_file = tmp_path / "corrupted.json"

    with open(result_file, "w", encoding="utf-8") as f:
        f.write("{ invalid json content")

    result = run_cli_command(["export", str(result_file), "--format", "csv"])

    assert result.returncode == 1
    assert "Error" in result.stdout or "Invalid" in result.stdout or "invalid" in result.stderr.lower()


@pytest.mark.integration
def test_export_command_appears_in_main_help():

    result = run_cli_command(["--help"])

    assert result.returncode == 0
    assert "export" in result.stdout


@pytest.mark.integration
def test_export_preserves_content(tmp_path):

    result_file = tmp_path / "test_result.json"

    result_data = make_v2_result_data(
        benchmark_name="TPC-H Benchmark",
        benchmark_id="tpc_h_benchmark",
        execution_id="preserve_test",
        platform="DuckDB",
        scale_factor=10.0,
        queries=[
            {"id": "1", "ms": 100.0, "rows": 10},
            {"id": "2", "ms": 200.0, "rows": 20},
            {"id": "3", "ms": 150.0, "rows": 15},
            {"id": "4", "ms": 180.0, "rows": 18},
            {"id": "5", "ms": 170.0, "rows": 17},
        ],
    )

    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result_data, f)

    output_dir = tmp_path / "exports"
    output_dir.mkdir()

    result = run_cli_command(
        ["export", str(result_file), "--format", "json", "--output-dir", str(output_dir), "--force"]
    )

    assert result.returncode == 0

    json_files = list(output_dir.glob("*.json"))
    assert len(json_files) == 1

    with open(json_files[0], encoding="utf-8") as f:
        exported_data = json.load(f)

    assert exported_data["version"] == CURRENT_SCHEMA_VERSION
    assert exported_data["benchmark"]["name"] == "TPC-H"
    assert exported_data["run"]["id"] == "preserve_test"
    assert exported_data["benchmark"]["scale_factor"] == 10.0
    assert exported_data["summary"]["queries"]["total"] == 5


@pytest.mark.integration
def test_export_html_format_creates_standalone_file(tmp_path):

    result_file = tmp_path / "test_result.json"
    result_data = make_v2_result_data(execution_id="html_test")

    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result_data, f)

    output_dir = tmp_path / "exports"
    output_dir.mkdir()

    result = run_cli_command(["export", str(result_file), "--format", "html", "--output-dir", str(output_dir)])

    assert result.returncode == 0

    html_files = list(output_dir.glob("*.html"))
    assert len(html_files) == 1

    html_content = html_files[0].read_text()
    assert "<!DOCTYPE html>" in html_content or "<html" in html_content
    assert "Test Benchmark" in html_content
    assert "TestPlatform" in html_content
