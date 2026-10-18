from __future__ import annotations

import pytest

from tests.integration._cli_e2e_utils import run_cli_command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.medium,
    pytest.mark.platform_smoke,
]


def test_validate_rejects_malformed_explicit_config(tmp_path):
    config_path = tmp_path / "invalid.yaml"
    config_path.write_text("database: [unclosed\n", encoding="utf-8")

    result = run_cli_command(["validate", "--config", str(config_path)], cwd=tmp_path)

    assert result.returncode != 0
    assert "Configuration is valid" not in result.stdout + result.stderr


def test_invalid_platform_returns_nonzero():
    result = run_cli_command(
        ["run", "--platform", "definitely-not-a-real-platform", "--benchmark", "tpch"],
    )

    assert result.returncode != 0
    assert "not available" in result.stdout + result.stderr


def test_dry_run_preserves_explicit_phase_selection(tmp_path):
    output_dir = tmp_path / "dry-run"

    result = run_cli_command(
        [
            "run",
            "--dry-run",
            str(output_dir),
            "--platform",
            "duckdb",
            "--benchmark",
            "tpch",
            "--scale",
            "0.01",
            "--phases",
            "generate,load",
            "--non-interactive",
        ],
        cwd=tmp_path,
    )

    assert result.returncode == 0
    assert "Load Only (Data Generation)" in result.stdout
    assert list(output_dir.glob("*.json"))
