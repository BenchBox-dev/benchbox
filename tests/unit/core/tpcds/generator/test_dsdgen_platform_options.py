# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchbox.core.tpcds import c_tools
from benchbox.core.tpcds.generator import runner

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.mark.parametrize(("platform", "expected"), [("win32", "/SCALE"), ("linux", "-SCALE"), ("darwin", "-SCALE")])
def test_option_prefix_follows_the_platform(monkeypatch, platform, expected):
    monkeypatch.setattr(sys, "platform", platform)
    assert c_tools.tpcds_option("scale") == expected


@pytest.mark.parametrize(("platform", "prefix"), [("win32", "/"), ("linux", "-")])
def test_file_based_dsdgen_passes_scale_and_terminate_with_the_platform_prefix(monkeypatch, tmp_path, platform, prefix):
    commands: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        commands.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    generator = SimpleNamespace(
        dsdgen_exe=Path("dsdgen"),
        scale_factor=0.01,
        verbose=False,
        _copy_distribution_files=lambda _output_dir: None,
    )
    try:
        runner.DsdgenRunnerMixin._run_file_based_dsdgen(generator, tmp_path)
    except Exception:
        pass

    assert commands, "dsdgen was not invoked"
    command = commands[0]
    assert command[command.index(f"{prefix}SCALE") + 1] == "0.01"
    assert command[command.index(f"{prefix}TERMINATE") + 1] == "n"
    assert not [arg for arg in command[1:] if arg[:1] in "-/" and not arg.startswith(prefix)]


