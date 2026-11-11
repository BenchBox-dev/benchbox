# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from unittest.mock import MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _printed_strings(mock_console):
    result = []
    for call in mock_console.print.call_args_list:
        for arg in call.args:
            result.append(str(arg))
    return result


@pytest.fixture
def mock_profile():
    profile = MagicMock()
    profile.platform = "darwin"
    profile.cpu_count = 8
    profile.memory_gb = 16.0
    return profile


class TestSystemProfilerDisplayProfile:
    def test_available_dbs_shown_in_output(self, mock_profile, capsys):
        from benchbox.cli.system import SystemProfiler

        with (
            patch("benchbox.core.platform_registry.PlatformRegistry") as MockReg,
            patch("benchbox.cli.system.create_display_manager") as MockDM,
        ):
            MockReg.get_available_platforms.return_value = ["duckdb"]
            dm = MagicMock()
            MockDM.return_value = dm

            profiler = SystemProfiler()
            profiler.console = MagicMock()
            profiler.display_profile(mock_profile)

            printed = _printed_strings(profiler.console)
            assert any("duckdb" in arg for arg in printed)

    def test_no_available_dbs_prints_warning(self, mock_profile):
        from benchbox.cli.system import SystemProfiler

        with (
            patch("benchbox.core.platform_registry.PlatformRegistry") as MockReg,
            patch("benchbox.cli.system.create_display_manager") as MockDM,
            patch("benchbox.cli.system.console") as mock_console,
        ):
            MockReg.get_available_platforms.return_value = []
            MockDM.return_value = MagicMock()

            profiler = SystemProfiler()
            profiler.console = MagicMock()
            profiler.display_profile(mock_profile)

            printed = _printed_strings(mock_console)
            assert any("No database" in arg or "duckdb" in arg.lower() for arg in printed)

    def test_no_psutil_shows_install_note(self, mock_profile):
        from benchbox.cli.system import SystemProfiler

        with (
            patch("benchbox.cli.system.HAS_PSUTIL", False),
            patch("benchbox.core.platform_registry.PlatformRegistry") as MockReg,
            patch("benchbox.cli.system.create_display_manager") as MockDM,
            patch("benchbox.cli.system.console") as mock_console,
        ):
            MockReg.get_available_platforms.return_value = ["duckdb"]
            MockDM.return_value = MagicMock()

            profiler = SystemProfiler()
            profiler.console = MagicMock()
            profiler.display_profile(mock_profile)

            printed = _printed_strings(profiler.console) + _printed_strings(mock_console)
            assert any("psutil" in arg for arg in printed)

    def test_with_psutil_no_psutil_install_note(self, mock_profile):
        from benchbox.cli.system import SystemProfiler

        with (
            patch("benchbox.cli.system.HAS_PSUTIL", True),
            patch("benchbox.core.platform_registry.PlatformRegistry") as MockReg,
            patch("benchbox.cli.system.create_display_manager") as MockDM,
        ):
            MockReg.get_available_platforms.return_value = ["duckdb"]
            MockDM.return_value = MagicMock()

            profiler = SystemProfiler()
            profiler.console = MagicMock()
            profiler.display_profile(mock_profile)

            printed = _printed_strings(profiler.console)
            assert not any("Install psutil" in arg for arg in printed)

    def test_instantiation_uses_quiet_console(self):
        from benchbox.cli.system import SystemProfiler
        from benchbox.utils.printing import quiet_console

        profiler = SystemProfiler()
        assert profiler.console is quiet_console


