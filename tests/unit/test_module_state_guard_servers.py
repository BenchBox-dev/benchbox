from __future__ import annotations

import subprocess
import sys

import pytest

from tests.utilities.paths import REPO_ROOT

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def test_module_building_servers_inside_tests_passes_the_guard() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/mcp/test_mcp_server.py",
            "-q",
            "-n",
            "0",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout[-3000:]
    assert "process-wide state" not in result.stdout
