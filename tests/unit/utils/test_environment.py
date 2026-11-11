from __future__ import annotations

from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from benchbox.utils.environment import _detect_windows_cpu, detect_cpu_info

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_windows_cpu_uses_cim_hardware_inventory() -> None:
    completed = CompletedProcess(
        args=["powershell.exe"],
        returncode=0,
        stdout='{"Name":"AMD Ryzen 9 7950X 16-Core Processor","Manufacturer":"AuthenticAMD"}',
        stderr="",
    )

    with patch("benchbox.utils.environment.subprocess.run", return_value=completed) as run:
        model, vendor = _detect_windows_cpu()

    assert model == "AMD Ryzen 9 7950X 16-Core Processor"
    assert vendor == "AMD"
    assert run.call_args.args[0][0] == "powershell.exe"


def test_windows_cpu_does_not_promote_platform_processor_to_measured() -> None:
    with (
        patch("benchbox.utils.environment.platform.system", return_value="Windows"),
        patch("benchbox.utils.environment.platform.processor", return_value="Intel(R) Core(TM) i7-9750H"),
        patch("benchbox.utils.environment.subprocess.run", side_effect=FileNotFoundError("PowerShell unavailable")),
    ):
        model, vendor = detect_cpu_info()

    assert model is None
    assert vendor is None


