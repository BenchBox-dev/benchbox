from __future__ import annotations

import shutil
import subprocess
import sys
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


@pytest.mark.skipif(shutil.which("make") is None or sys.platform == "win32", reason="POSIX make is required")
def test_target_fails_fast_when_installed_major_differs_from_required() -> None:
    mismatched = str(int(polars.__version__.split(".")[0]) + 1)

    result = _run_target(mismatched)

    assert result.returncode != 0
    assert f"requires Polars major {mismatched}.x" in result.stderr
    assert f"found {polars.__version__}" in result.stderr
