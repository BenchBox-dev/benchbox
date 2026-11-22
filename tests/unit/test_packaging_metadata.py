from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_pandas_floor_is_declared_in_packaging_metadata() -> None:
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    pandas_pins = re.findall(r'"pandas(>=?[^"]*)"', pyproject.read_text(encoding="utf-8"))
    assert pandas_pins, "no pandas pin found in pyproject.toml"
    for pin in pandas_pins:
        spec = SpecifierSet(pin)
        assert spec.contains(Version("3.0.0")), f"pandas{pin} excludes 3.0.0"
        assert not spec.contains(Version("2.9.9")), f"pandas{pin} still allows 2.x"
