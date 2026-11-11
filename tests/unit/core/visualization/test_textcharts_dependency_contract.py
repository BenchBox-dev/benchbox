from __future__ import annotations

import importlib
import tomllib
from importlib.metadata import version
from pathlib import Path

import pytest
from packaging.requirements import Requirement

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_textcharts_is_installed_and_legacy_shim_delegates() -> None:
    package = importlib.import_module("textcharts")
    from benchbox.core.visualization.ascii.bar_chart import BarChart

    assert version("textcharts")
    assert BarChart is package.BarChart


def test_textcharts_remains_a_core_manifest_dependency() -> None:
    manifest_path = Path(__file__).resolve().parents[4] / "pyproject.toml"
    with manifest_path.open("rb") as handle:
        manifest = tomllib.load(handle)

    dependencies = manifest["project"]["dependencies"]
    assert any(Requirement(str(requirement)).name.lower() == "textcharts" for requirement in dependencies)


