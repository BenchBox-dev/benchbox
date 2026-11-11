# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import scripts.check_make_targets as make_targets

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestLintingConsolidation:
    def test_pyproject_toml_has_ruff_config(self):
        pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"

        with open(pyproject_path, "rb") as f:
            config = tomllib.load(f)

        assert "tool" in config
        assert "ruff" in config["tool"]

        ruff_config = config["tool"]["ruff"]

        assert "line-length" in ruff_config
        assert ruff_config["line-length"] == 120
        assert "exclude" in ruff_config

        assert "lint" in ruff_config
        lint_config = ruff_config["lint"]
        assert "select" in lint_config
        assert "ignore" in lint_config

        selected_rules = lint_config["select"]
        assert "E" in selected_rules
        assert "W" in selected_rules
        assert "F" in selected_rules
        assert "I" in selected_rules

        assert "isort" in ruff_config["lint"]

        assert "format" in ruff_config
        format_config = ruff_config["format"]
        assert "quote-style" in format_config
        assert "indent-style" in format_config

    def test_redundant_tool_configs_removed(self):
        pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"

        with open(pyproject_path, "rb") as f:
            config = tomllib.load(f)

        tool_config = config.get("tool", {})

        redundant_tools = ["isort", "pycodestyle", "flake8", "black"]
        for tool in redundant_tools:
            assert tool not in tool_config, f"Found configuration for redundant tool: {tool}"

    def test_dev_dependencies_only_include_ruff(self):
        pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"

        with open(pyproject_path, "rb") as f:
            config = tomllib.load(f)

        dev_deps = config.get("dependency-groups", {}).get("dev", [])

        ruff_found = any("ruff" in dep for dep in dev_deps)
        assert ruff_found, "ruff not found in dev dependencies"

        redundant_tools = ["black", "isort", "pycodestyle", "flake8"]
        for tool in redundant_tools:
            tool_found = any(tool in dep for dep in dev_deps)
            assert not tool_found, f"Redundant tool {tool} found in dev dependencies"

    def test_ruff_commands_work(self):
        result = subprocess.run(
            ["uv", "run", "ruff", "check", "--help"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        assert result.returncode == 0, f"ruff check failed: {result.stderr}"

        result = subprocess.run(
            ["uv", "run", "ruff", "format", "--help"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        assert result.returncode == 0, f"ruff format failed: {result.stderr}"

    def test_makefile_targets_use_ruff(self):
        makefile_content = make_targets.makefile_sources(Path(__file__).parent.parent.parent)

        assert "lint:" in makefile_content
        assert "uv run ruff check ." in makefile_content

        assert "format:" in makefile_content
        assert "uv run ruff format ." in makefile_content

        redundant_commands = ["black", "isort", "flake8", "pycodestyle"]
        for cmd in redundant_commands:
            assert cmd not in makefile_content, f"Found reference to {cmd} in Makefile"

    def test_github_actions_use_ruff(self):
        lint_workflow_path = Path(__file__).parent.parent.parent / ".github" / "workflows" / "ci.yml"

        with open(lint_workflow_path, encoding="utf-8") as f:
            workflow_content = f.read()

        assert "uv run ruff check ." in workflow_content
        assert "uv run ruff format --check ." in workflow_content

        redundant_tools = ["black", "isort", "flake8", "pycodestyle"]
        for tool in redundant_tools:
            assert f"pip install {tool}" not in workflow_content, f"Found {tool} installation in workflow"

    @pytest.mark.integration
    def test_linting_integration_works(self):
        project_root = Path(__file__).parent.parent.parent

        sample_file = project_root / "benchbox" / "__init__.py"
        assert sample_file.exists()

        result = subprocess.run(
            ["uv", "run", "ruff", "check", str(sample_file)], capture_output=True, text=True, cwd=project_root
        )
        assert result.returncode in [0, 1], f"ruff check failed unexpectedly: {result.stderr}"

        result = subprocess.run(
            ["uv", "run", "ruff", "format", "--check", str(sample_file)],
            capture_output=True,
            text=True,
            cwd=project_root,
        )
        assert result.returncode in [0, 1], f"ruff format check failed unexpectedly: {result.stderr}"


class TestRuffConfiguration:
    def test_ruff_rule_selection(self):
        pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"

        with open(pyproject_path, "rb") as f:
            config = tomllib.load(f)

        selected_rules = config["tool"]["ruff"]["lint"]["select"]

        expected_rule_prefixes = ["E", "W", "F", "I", "N", "UP", "B"]
        for prefix in expected_rule_prefixes:
            assert prefix in selected_rules, f"Missing rule prefix: {prefix}"

    def test_ruff_import_sorting_config(self):
        pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"

        with open(pyproject_path, "rb") as f:
            config = tomllib.load(f)

        isort_config = config["tool"]["ruff"]["lint"]["isort"]

        assert "force-single-line" in isort_config
        assert "combine-as-imports" in isort_config
        assert isort_config["combine-as-imports"] is True

    def test_ruff_format_black_compatibility(self):
        pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"

        with open(pyproject_path, "rb") as f:
            config = tomllib.load(f)

        format_config = config["tool"]["ruff"]["format"]

        assert format_config["quote-style"] == "double"
        assert format_config["indent-style"] == "space"
        assert format_config["line-ending"] == "auto"


