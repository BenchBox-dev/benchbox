from datetime import datetime
from io import StringIO

import pytest
from rich.console import Console

from benchbox.cli.display import StandardDisplays, show_system_info
from benchbox.core.schemas import SystemProfile

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _profile() -> SystemProfile:
    return SystemProfile(
        os_name="Linux",
        os_version="5.4.0",
        architecture="x86_64",
        cpu_model="Intel Core i7",
        cpu_cores_physical=4,
        cpu_cores_logical=8,
        memory_total_gb=16.0,
        memory_available_gb=12.0,
        python_version="3.11.0",
        disk_space_gb=500.0,
        timestamp=datetime(2025, 1, 15, 10, 30, 45),
        hostname="test-machine",
    )


def test_standard_displays_creation() -> None:
    console = Console(file=StringIO(), force_terminal=False)
    displays = StandardDisplays(console)

    assert displays.console == console


def test_show_system_profile_detailed() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False)
    displays = StandardDisplays(console)

    displays.show_system_profile(_profile(), detailed=True)

    output_text = output.getvalue()
    assert "System Information" in output_text
    assert "Linux" in output_text
    assert "Intel Core i7" in output_text
    assert "16.0 GB" in output_text
    assert "test-machine" in output_text
    assert "4 physical" in output_text
    assert "8 logical" in output_text
    assert "2025-01-15 10:30:45" in output_text


def test_show_system_info_wrapper() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False)

    show_system_info(_profile(), console, detailed=False)

    output_text = output.getvalue()
    assert "System Information" in output_text
    assert "Linux" in output_text
    assert "test-machine" not in output_text


def test_show_system_profile_marks_missing_cpu_as_not_recorded() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False)
    profile = _profile().model_copy(update={"cpu_model": None})

    StandardDisplays(console).show_system_profile(profile)

    assert "Not recorded" in output.getvalue()


