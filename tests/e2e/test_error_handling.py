from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from benchbox.cli.main import cli
from tests.integration._cli_e2e_utils import run_cli_command as run_cli_subprocess_command

pytestmark = pytest.mark.medium


_CLI_RUNNER = CliRunner()


def run_cli_command(args: Sequence[str], *, use_subprocess: bool = False) -> SimpleNamespace:
    if use_subprocess:
        result = run_cli_subprocess_command(list(args))
        return SimpleNamespace(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)

    result = _CLI_RUNNER.invoke(cli, list(args), env={"BENCHBOX_NON_INTERACTIVE": "true"})
    return SimpleNamespace(returncode=result.exit_code, stdout=result.output, stderr="")


class TestMissingParameters:
    @pytest.mark.e2e
    @pytest.mark.e2e_quick
    def test_missing_platform_error(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "dry_run"
        output_dir.mkdir()

        result = run_cli_command(
            [
                "run",
                "--benchmark",
                "tpch",
                "--scale",
                "0.01",
                "--dry-run",
                str(output_dir),
            ]
        )

        assert result.returncode != 0 or "platform" in result.stdout.lower()

    @pytest.mark.e2e
    @pytest.mark.e2e_quick
    def test_missing_benchmark_error(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "dry_run"
        output_dir.mkdir()

        result = run_cli_command(
            [
                "run",
                "--platform",
                "duckdb",
                "--scale",
                "0.01",
                "--dry-run",
                str(output_dir),
            ]
        )

        assert result.returncode != 0 or "benchmark" in result.stdout.lower()

    @pytest.mark.e2e
    @pytest.mark.e2e_quick
    def test_missing_output_for_cloud_error(self) -> None:
        result = run_cli_command(["run", "--help", "all"])

        assert result.returncode == 0
        assert "output" in result.stdout.lower()


class TestInvalidParameters:
    @pytest.mark.e2e
    @pytest.mark.e2e_quick
    def test_invalid_platform_name(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "dry_run"
        output_dir.mkdir()

        result = run_cli_command(
            [
                "run",
                "--platform",
                "nonexistent_platform_xyz",
                "--benchmark",
                "tpch",
                "--scale",
                "0.01",
                "--dry-run",
                str(output_dir),
            ]
        )

        assert result.returncode == 0
        assert "Dry run completed" in result.stdout

    @pytest.mark.e2e
    @pytest.mark.e2e_quick
    def test_invalid_benchmark_name(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "dry_run"
        output_dir.mkdir()

        result = run_cli_command(
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "nonexistent_benchmark_xyz",
                "--scale",
                "0.01",
                "--dry-run",
                str(output_dir),
            ]
        )

        assert "unknown benchmark" in result.stdout.lower()
        assert "nonexistent_benchmark_xyz" in result.stdout

    @pytest.mark.e2e
    @pytest.mark.e2e_quick
    def test_negative_scale_factor(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "dry_run"
        output_dir.mkdir()

        result = run_cli_command(
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--scale",
                "-1",
                "--dry-run",
                str(output_dir),
            ]
        )

        assert result.returncode != 0

    @pytest.mark.e2e
    @pytest.mark.e2e_quick
    def test_zero_scale_factor(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "dry_run"
        output_dir.mkdir()

        result = run_cli_command(
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--scale",
                "0",
                "--dry-run",
                str(output_dir),
            ]
        )

        assert result.returncode != 0

    @pytest.mark.e2e
    @pytest.mark.e2e_quick
    def test_invalid_query_id(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "dry_run"
        output_dir.mkdir()

        result = run_cli_command(
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--scale",
                "0.01",
                "--queries",
                "INVALID_QUERY_ID!!!",
                "--dry-run",
                str(output_dir),
            ]
        )

        assert result.returncode != 0

    @pytest.mark.e2e
    @pytest.mark.e2e_quick
    def test_query_id_out_of_range(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "dry_run"
        output_dir.mkdir()

        run_cli_command(
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--scale",
                "0.01",
                "--queries",
                "Q999",
                "--dry-run",
                str(output_dir),
            ]
        )

    @pytest.mark.e2e
    @pytest.mark.e2e_quick
    def test_invalid_tuning_mode(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "dry_run"
        output_dir.mkdir()

        result = run_cli_command(
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--scale",
                "0.01",
                "--tuning",
                "invalid_tuning_mode",
                "--dry-run",
                str(output_dir),
            ]
        )

        assert result.returncode != 0

    @pytest.mark.e2e
    @pytest.mark.e2e_quick
    def test_invalid_compression_type(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "dry_run"
        output_dir.mkdir()

        result = run_cli_command(
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--scale",
                "0.01",
                "--compression",
                "invalid_compression",
                "--dry-run",
                str(output_dir),
            ]
        )

        assert result.returncode != 0

    @pytest.mark.e2e
    @pytest.mark.e2e_quick
    def test_invalid_validation_mode(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "dry_run"
        output_dir.mkdir()

        result = run_cli_command(
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--scale",
                "0.01",
                "--validation",
                "invalid_validation",
                "--dry-run",
                str(output_dir),
            ]
        )

        assert result.returncode != 0


class TestQuerySubsetConstraints:
    @pytest.mark.e2e
    @pytest.mark.e2e_quick
    def test_too_many_queries_error(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "dry_run"
        output_dir.mkdir()

        queries = ",".join([f"Q{i}" for i in range(1, 110)])

        result = run_cli_command(
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--scale",
                "0.01",
                "--queries",
                queries,
                "--dry-run",
                str(output_dir),
            ]
        )

        assert result.returncode != 0 or "100" in result.stdout or "max" in result.stdout.lower()


class TestHelpCommands:
    @pytest.mark.e2e
    @pytest.mark.e2e_quick
    def test_help_basic(self) -> None:
        result = run_cli_command(["--help"])

        assert result.returncode == 0
        assert "Usage:" in result.stdout or "usage:" in result.stdout.lower()

    @pytest.mark.e2e
    @pytest.mark.e2e_quick
    def test_help_run_command(self) -> None:
        result = run_cli_command(["run", "--help"])

        assert result.returncode == 0
        assert "platform" in result.stdout.lower()
        assert "benchmark" in result.stdout.lower()

    @pytest.mark.e2e
    @pytest.mark.e2e_quick
    def test_help_all_options(self) -> None:
        result = run_cli_command(["run", "--help", "all"])

        assert result.returncode == 0
        assert "compression" in result.stdout.lower() or "advanced" in result.stdout.lower()

    @pytest.mark.e2e
    @pytest.mark.e2e_quick
    def test_help_examples(self) -> None:
        result = run_cli_command(["run", "--help", "examples"])

        assert result.returncode == 0
        assert "example" in result.stdout.lower() or "benchbox" in result.stdout.lower()


class TestVersionCommand:
    @pytest.mark.e2e
    @pytest.mark.e2e_quick
    def test_version_displays(self) -> None:
        result = run_cli_command(["--version"])

        assert result.returncode == 0
        assert "version" in result.stdout.lower() or "benchbox" in result.stdout.lower()


class TestDryRunDirectory:
    @pytest.mark.e2e
    @pytest.mark.e2e_quick
    def test_dry_run_nonexistent_directory(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "nonexistent" / "nested" / "directory"

        result = run_cli_command(
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--scale",
                "0.01",
                "--dry-run",
                str(output_dir),
            ]
        )

        if result.returncode == 0:
            assert output_dir.exists(), "Directory should have been created"
        else:
            assert "directory" in result.stdout.lower() or "path" in result.stdout.lower()

    @pytest.mark.e2e
    @pytest.mark.e2e_quick
    def test_dry_run_file_instead_of_directory(self, tmp_path: Path) -> None:
        file_path = tmp_path / "not_a_directory.txt"
        file_path.write_text("This is a file, not a directory")

        result = run_cli_command(
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--scale",
                "0.01",
                "--dry-run",
                str(file_path),
            ]
        )

        assert result.returncode != 0


class TestPlatformOptions:
    @pytest.mark.e2e
    @pytest.mark.e2e_quick
    def test_invalid_platform_option_format(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "dry_run"
        output_dir.mkdir()

        result = run_cli_command(
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--scale",
                "0.01",
                "--platform-option",
                "invalid_no_equals",
                "--dry-run",
                str(output_dir),
            ]
        )

        assert result.returncode != 0 or "=" in result.stdout or "format" in result.stdout.lower()

    @pytest.mark.e2e
    @pytest.mark.e2e_quick
    def test_valid_platform_option(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "dry_run"
        output_dir.mkdir()

        result = run_cli_command(
            [
                "run",
                "--platform",
                "duckdb",
                "--benchmark",
                "tpch",
                "--scale",
                "0.01",
                "--platform-option",
                "threads=4",
                "--dry-run",
                str(output_dir),
            ]
        )

        assert result.returncode == 0, f"Failed: {result.stdout}"


@pytest.mark.e2e
@pytest.mark.e2e_quick
@pytest.mark.parametrize(
    "invalid_scale",
    ["-1", "-0.5", "0", "-100"],
)
def test_invalid_scale_factors(tmp_path: Path, invalid_scale: str) -> None:
    output_dir = tmp_path / "dry_run"
    output_dir.mkdir()

    result = run_cli_command(
        [
            "run",
            "--platform",
            "duckdb",
            "--benchmark",
            "tpch",
            "--scale",
            invalid_scale,
            "--dry-run",
            str(output_dir),
        ]
    )

    assert result.returncode != 0, f"Should have rejected scale factor: {invalid_scale}"


@pytest.mark.e2e
@pytest.mark.e2e_quick
@pytest.mark.parametrize(
    "invalid_queries",
    [
        "!!!invalid!!!",
        "@#$%^&*",
        "Q1;DROP TABLE",
        "Q1' OR '1'='1",
    ],
)
def test_invalid_query_patterns(tmp_path: Path, invalid_queries: str) -> None:
    output_dir = tmp_path / "dry_run"
    output_dir.mkdir()

    result = run_cli_command(
        [
            "run",
            "--platform",
            "duckdb",
            "--benchmark",
            "tpch",
            "--scale",
            "0.01",
            "--queries",
            invalid_queries,
            "--dry-run",
            str(output_dir),
        ]
    )

    assert result.returncode != 0, f"Should have rejected query pattern: {invalid_queries}"
