from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from benchbox.cli.app import cli
from benchbox.cli.exit_codes import EXIT_CODE_MEANINGS, ExitCode
from benchbox.core.results.integrity_validator import RESULT_COMPANION_SUFFIXES
from tests.unit.core.results.test_integrity_validator import _make_valid_tpch_result

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[3]


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _passing(directory: Path, name: str = "ok.json") -> Path:
    return _write(directory / name, _make_valid_tpch_result())


def _warning(directory: Path, name: str = "warn.json") -> Path:
    payload = _make_valid_tpch_result()
    payload["phases"]["data_loading"]["duration_ms"] = 0
    return _write(directory / name, payload)


def _failing(directory: Path, name: str = "bad.json") -> Path:
    payload = _make_valid_tpch_result()
    del payload["queries"]
    return _write(directory / name, payload)


def _validate(*args: str):
    return CliRunner().invoke(cli, ["results", "validate", *args])


def _payload(*args: str) -> Any:
    return json.loads(_validate(*args).stdout)


class TestExitStatus:
    def test_passing_file_exits_zero(self, tmp_path: Path):
        assert _validate(str(_passing(tmp_path))).exit_code == ExitCode.SUCCESS

    def test_fail_finding_exits_five(self, tmp_path: Path):
        result = _validate(str(_failing(tmp_path)))

        assert result.exit_code == 5
        assert "FAIL" in result.output

    def test_warning_exits_zero_unless_fail_on_warn(self, tmp_path: Path):
        path = _warning(tmp_path)
        assert _payload(str(path), "--json")["overall_status"] == "WARN"

        assert _validate(str(path)).exit_code == 0
        assert _validate(str(path), "--fail-on-warn").exit_code == 5

    def test_missing_path_exits_one(self, tmp_path: Path):
        result = _validate(str(tmp_path / "absent.json"))

        assert result.exit_code == 1
        assert "not found" in result.output

    def test_unreadable_file_exits_one(self, tmp_path: Path):
        corrupt = tmp_path / "corrupt.json"
        corrupt.write_text("{not json", encoding="utf-8")

        assert _validate(str(corrupt)).exit_code == 1

    def test_directory_with_a_failing_file_exits_five(self, tmp_path: Path):
        _passing(tmp_path)
        _failing(tmp_path)

        result = _validate(str(tmp_path))

        assert result.exit_code == 5
        assert "Aggregate: 2 files - 1 PASS, 0 WARN, 1 FAIL" in result.output

    def test_empty_directory_exits_zero(self, tmp_path: Path):
        result = _validate(str(tmp_path))

        assert result.exit_code == 0
        assert "No result files found." in result.output

    def test_usage_error_exits_two(self):
        assert CliRunner().invoke(cli, ["results", "validate"]).exit_code == 2

    def test_code_five_is_documented_in_help(self):
        output = " ".join(CliRunner().invoke(cli, ["results", "validate", "--help"]).output.split())

        assert EXIT_CODE_MEANINGS[ExitCode.VALIDATION_FAILED] in output


class TestJsonOutput:
    def test_file_shape(self, tmp_path: Path):
        payload = _payload(str(_failing(tmp_path)), "--json")

        assert set(payload) == {
            "file",
            "benchmark_id",
            "platform",
            "scale_factor",
            "overall_status",
            "summary",
            "checks",
        }
        assert payload["overall_status"] == "FAIL"
        assert {"category", "name", "status", "message", "details"} == set(payload["checks"][0])

    def test_pass_checks_only_with_verbose(self, tmp_path: Path):
        path = str(_passing(tmp_path))

        quiet = _payload(path, "--json")["checks"]
        verbose = _payload(path, "--json", "--verbose")["checks"]

        assert all(check["status"] != "PASS" for check in quiet)
        assert any(check["status"] == "PASS" for check in verbose)

    def test_directory_shape(self, tmp_path: Path):
        _passing(tmp_path)
        _failing(tmp_path)

        payload = _payload(str(tmp_path), "--json")

        assert (payload["total"], payload["pass"], payload["warn"], payload["fail"]) == (2, 1, 0, 1)
        assert len(payload["files"]) == len(payload["reports"]) == 2

    def test_benchmark_filter(self, tmp_path: Path):
        _passing(tmp_path)

        assert _payload(str(tmp_path), "--json", "--benchmark", "tpcds")["total"] == 0
        assert _payload(str(tmp_path), "--json", "--benchmark", "tpch")["total"] == 1

    @pytest.mark.parametrize("suffix", RESULT_COMPANION_SUFFIXES)
    def test_companion_files_are_not_validated_as_results(self, tmp_path: Path, suffix: str):
        _passing(tmp_path)
        (tmp_path / f"ok{suffix}").write_text("{}", encoding="utf-8")

        assert _payload(str(tmp_path), "--json")["total"] == 1


class TestAgreesWithMcpValidateResults:
    @pytest.fixture
    def server(self, tmp_path: Path):
        pytest.importorskip("mcp", reason="MCP SDK not installed")
        from benchbox.mcp import create_server

        return create_server(results_dir=tmp_path, charts_dir=tmp_path)

    @pytest.mark.parametrize("make", [_passing, _warning, _failing])
    @pytest.mark.parametrize("verbose", [False, True])
    def test_single_file_findings_are_equal(self, server, tmp_path: Path, make, verbose: bool):
        from tests.unit.mcp.public_api import call_tool

        path = make(tmp_path)
        args = ["--verbose"] if verbose else []

        cli_payload = _payload(str(path), "--json", *args)
        mcp_payload = call_tool(server, "validate_results", result_file=path.name, verbose=verbose)

        assert cli_payload == mcp_payload

    def test_directory_findings_are_equal(self, server, tmp_path: Path):
        from tests.unit.mcp.public_api import call_tool

        _passing(tmp_path)
        _warning(tmp_path)
        _failing(tmp_path)
        (tmp_path / "ok.manifest.json").write_text("{}", encoding="utf-8")

        cli_payload = _payload(str(tmp_path), "--json", "--verbose")
        mcp_payload = call_tool(server, "validate_results", directory=str(tmp_path))

        reports = cli_payload.pop("reports")
        assert cli_payload == mcp_payload
        per_file = {
            Path(report["file"]).name: call_tool(
                server, "validate_results", result_file=Path(report["file"]).name, verbose=True
            )
            for report in reports
        }
        assert reports == [per_file[Path(report["file"]).name] for report in reports]


class TestRepositoryScript:
    def test_script_runs_the_same_command(self, tmp_path: Path):
        path = _failing(tmp_path)
        script = REPO_ROOT / "_project" / "scripts" / "validate_results.py"

        completed = subprocess.run(
            [sys.executable, str(script), str(path), "--json"], capture_output=True, text=True, check=False
        )

        assert completed.returncode == 5
        assert json.loads(completed.stdout) == _payload(str(path), "--json")
