from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.slow,
    pytest.mark.resource_heavy,
]


def test_compat_lint_passes():
    result = subprocess.run(
        [sys.executable, "scripts/compat_lint.py"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"compat_lint exited {result.returncode}.\nstderr: {result.stderr}\nstdout: {result.stdout}"
    )


