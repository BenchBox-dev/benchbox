# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import sys
from unittest.mock import MagicMock, mock_open, patch

import pytest

import benchbox.core.dataframe.tuning.defaults as defaults_mod
from benchbox.core.dataframe.tuning.defaults import _get_available_memory_gb

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _patch_no_psutil():
    saved = sys.modules.get("psutil")
    sys.modules["psutil"] = None

    class _Ctx:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            if saved is None:
                del sys.modules["psutil"]
            else:
                sys.modules["psutil"] = saved

    return _Ctx()


class TestGetAvailableMemoryLinux:
    def test_reads_memavailable_from_proc(self):
        meminfo = "MemTotal:       16384000 kB\nMemAvailable:    8192000 kB\nSwapTotal: 0 kB\n"
        with _patch_no_psutil(), patch("builtins.open", mock_open(read_data=meminfo)):
            result = _get_available_memory_gb()
        assert abs(result - 8192000 / (1024**2)) < 0.001

    def test_falls_through_when_no_memavailable_line(self):
        meminfo = "MemTotal:       16384000 kB\nSwapTotal: 0 kB\n"
        sysctl_result = MagicMock(returncode=0, stdout=str(16 * 1024**3))

        with (
            _patch_no_psutil(),
            patch("builtins.open", mock_open(read_data=meminfo)),
            patch("subprocess.run", return_value=sysctl_result),
        ):
            result = _get_available_memory_gb()
        assert abs(result - 16 * 0.7) < 0.01

    def test_oserror_falls_through(self):
        sysctl_result = MagicMock(returncode=0, stdout=str(8 * 1024**3))
        with (
            _patch_no_psutil(),
            patch("builtins.open", side_effect=OSError("no /proc")),
            patch("subprocess.run", return_value=sysctl_result),
        ):
            result = _get_available_memory_gb()
        assert abs(result - 8 * 0.7) < 0.01


class TestGetAvailableMemoryMacOS:
    def test_sysctl_success(self):
        total_bytes = 32 * 1024**3
        sysctl_result = MagicMock(returncode=0, stdout=str(total_bytes))

        with (
            _patch_no_psutil(),
            patch("builtins.open", side_effect=OSError("no /proc on macOS")),
            patch("subprocess.run", return_value=sysctl_result),
        ):
            result = _get_available_memory_gb()
        assert abs(result - 32 * 0.7) < 0.01

    def test_sysctl_failure_returns_fallback(self):
        import subprocess

        with (
            _patch_no_psutil(),
            patch("builtins.open", side_effect=OSError("no /proc")),
            patch("subprocess.run", side_effect=subprocess.SubprocessError("sysctl failed")),
        ):
            result = _get_available_memory_gb()
        assert result == 8.0


class TestGetAvailableMemoryWindowsFallback:
    def test_all_paths_fail_returns_default(self):
        import subprocess

        with (
            _patch_no_psutil(),
            patch("builtins.open", side_effect=OSError("no /proc on Windows")),
            patch("subprocess.run", side_effect=FileNotFoundError("sysctl not found")),
        ):
            result = _get_available_memory_gb()
        assert result == 8.0


