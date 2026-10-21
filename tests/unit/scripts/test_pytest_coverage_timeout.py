from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]
ROOT = Path(__file__).resolve().parents[3]
BODY = (
    "import time\n"
    "import pytest\n\n"
    "def test_global_timeout():\n    time.sleep(1.5)\n\n"
    "@pytest.mark.timeout(1)\n"
    "def test_marker_timeout():\n    time.sleep(1.5)\n\n"
    "@pytest.mark.timeout(0)\n"
    "def test_disabled_timeout():\n    time.sleep(0.1)\n"
)


def _run(tmp_path: Path, *plugins: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "test_slow.py").write_text(BODY)
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts =\n")
    plugin_args = [arg for plugin in plugins for arg in ("-p", plugin)]
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "pytest_timeout", *plugin_args, "--timeout=1", "test_slow.py"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_timeouts_fire_without_the_coverage_plugin(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 1, result.stdout
    assert "2 failed, 1 passed" in result.stdout


def test_coverage_plugin_scales_global_and_marker_timeouts(tmp_path: Path) -> None:
    result = _run(tmp_path, "scripts.pytest_coverage_timeout")
    assert result.returncode == 0, result.stdout
    assert "3 passed" in result.stdout
