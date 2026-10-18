# Copyright 2026 Joe Harris / BenchBox Project
# Licensed under the MIT License.

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_patch_safety.py"


def test_no_unsafe_patch_string_calls():
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, "Unsafe patch() string paths detected.\n\n" + (result.stdout or result.stderr)
