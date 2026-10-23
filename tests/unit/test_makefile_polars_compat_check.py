from __future__ import annotations

import subprocess
from pathlib import Path

import polars
import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _run_target(required_major: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "polars-df-compat-check", f"POLARS_COMPAT_REQUIRED_MAJOR={required_major}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


def test_target_fails_fast_when_installed_major_differs_from_required() -> None:
    mismatched = str(int(polars.__version__.split(".")[0]) + 1)

    result = _run_target(mismatched)

    assert result.returncode != 0
    assert f"requires Polars major {mismatched}.x" in result.stderr
    assert f"found {polars.__version__}" in result.stderr


def test_default_required_major_is_two() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "POLARS_COMPAT_REQUIRED_MAJOR ?= 2" in makefile
