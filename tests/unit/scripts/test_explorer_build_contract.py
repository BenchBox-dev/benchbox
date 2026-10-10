from __future__ import annotations

import importlib
import json
from typing import cast

import pytest
from click.testing import CliRunner

from _project.scripts.explorer_pipeline.contract import EXPLORER_BUILD_CONTRACT, EXPLORER_READ_MODEL_VERSION

explorer_module = importlib.import_module("_project.scripts.explorer_publish")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_explorer_build_contract_command_emits_expected_json() -> None:
    runner = CliRunner()

    result = runner.invoke(explorer_module.explorer_publish, ["build-contract"])

    assert result.exit_code == 0
    assert json.loads(result.output) == EXPLORER_BUILD_CONTRACT


def test_explorer_build_contract_matches_duckdb_only_output_contract() -> None:
    outputs = EXPLORER_BUILD_CONTRACT["outputs"]
    current_read_model_version = 15

    assert EXPLORER_BUILD_CONTRACT["version"] == "6"
    assert EXPLORER_BUILD_CONTRACT["read_model_version"] == current_read_model_version
    assert current_read_model_version == EXPLORER_READ_MODEL_VERSION
    assert EXPLORER_BUILD_CONTRACT["read_model_compatibility"] == {
        "minimum_supported": current_read_model_version,
        "newer_policy": "warn-and-continue",
    }
    assert isinstance(EXPLORER_BUILD_CONTRACT["read_model_version"], int)
    assert EXPLORER_BUILD_CONTRACT["read_model_version"] >= 1
    assert "results_schema.json" not in outputs["required"]
    assert "results_schema.json" in outputs["removed_legacy"]


def test_explorer_build_flags_match_declared_contract() -> None:
    flag_opts = {opt for param in explorer_module.explorer_build.params for opt in getattr(param, "opts", [])}

    for required_flag in cast("list[str]", EXPLORER_BUILD_CONTRACT["flags"]):
        assert required_flag in flag_opts


def test_explorer_build_contract_command_matches_script_click_path() -> None:
    command_tokens = EXPLORER_BUILD_CONTRACT["command"].split()

    assert command_tokens[:5] == ["uv", "run", "--", "python", "_project/scripts/explorer_publish.py"]

    build_command = explorer_module.explorer_publish.commands.get(command_tokens[5])
    assert build_command is explorer_module.explorer_build


def test_explorer_publisher_does_not_register_legacy_comparison_builder() -> None:
    assert "build-comparison" not in explorer_module.explorer_publish.commands
