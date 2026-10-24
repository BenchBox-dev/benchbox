from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from benchbox.cli.main import cli

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def invoke(args: list[str]):
    return CliRunner().invoke(cli, args)


def test_platforms_list_names_gizmosql():
    result = invoke(["platforms", "list"])

    assert result.exit_code == 0
    assert "GizmoSQL" in result.output
    assert "experimental" in result.output


def test_platforms_status_knows_gizmosql():
    result = invoke(["platforms", "status", "gizmosql"])

    assert result.exit_code == 0
    assert "Platform: GizmoSQL" in result.output
    assert "Unknown platform" not in result.output


def test_check_deps_knows_gizmosql():
    result = invoke(["check-deps", "--platform", "gizmosql"])

    assert "gizmosql" in result.output.lower()
    assert "Unknown platform" not in result.output


def test_dry_run_identifies_the_sql_adapter_without_fallback(tmp_path: Path):
    result = invoke(
        [
            "run",
            "--dry-run",
            str(tmp_path),
            "--platform",
            "gizmosql",
            "--benchmark",
            "tpch",
            "--scale",
            "0.01",
            "--non-interactive",
        ]
    )

    assert result.exit_code == 0, result.output
    assert "initialization failed" not in result.output
    payload = json.loads(next(tmp_path.glob("tpch_gizmosql_2*.json")).read_text(encoding="utf-8"))
    assert payload["warnings"] == []
    assert payload["execution_mode"] == "sql"
    assert payload["deployment"] == "self-hosted"
    assert payload["database_config"]["type"] == "gizmosql"
    assert len(payload["queries"]) == 22
    schema_file = next(tmp_path.glob("tpch_gizmosql_schema_*.sql"))
    assert "create table" in schema_file.read_text(encoding="utf-8").lower()


def test_dry_run_redacts_credentials(tmp_path: Path):
    secret = "dry-run-secret-value"

    result = invoke(
        [
            "run",
            "--dry-run",
            str(tmp_path),
            "--platform",
            "gizmosql",
            "--benchmark",
            "tpch",
            "--non-interactive",
            "--platform-option",
            f"password={secret}",
        ]
    )

    assert result.exit_code == 0, result.output
    assert secret not in result.output
    for artifact in tmp_path.iterdir():
        if artifact.is_file():
            assert secret not in artifact.read_text(encoding="utf-8")


def test_insecure_options_require_the_explicit_development_flag(tmp_path: Path):
    result = invoke(
        [
            "run",
            "--dry-run",
            str(tmp_path),
            "--platform",
            "gizmosql",
            "--benchmark",
            "tpch",
            "--non-interactive",
            "--platform-option",
            "tls=false",
        ]
    )

    payload = json.loads(next(tmp_path.glob("tpch_gizmosql_2*.json")).read_text(encoding="utf-8"))
    assert any("allow_insecure_connection" in warning for warning in payload["warnings"]) or result.exit_code != 0
