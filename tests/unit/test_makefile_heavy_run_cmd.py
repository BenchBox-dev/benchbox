from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_FAKE_UV_SCRIPT = """#!/bin/sh
for last in "$@"; do :; done
printf '%s' "$last" > "$FAKE_UV_OUT"
"""


def _run_heavy_run(tmp_path: Path, cmd: str) -> str:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uv = fake_bin / "uv"
    uv.write_text(_FAKE_UV_SCRIPT, encoding="utf-8")
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    out = tmp_path / "captured"
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["FAKE_UV_OUT"] = str(out)
    env.pop("HEAVY_ADMIT_WAIT", None)
    subprocess.run(
        ["make", "-C", str(REPO_ROOT), "heavy-run", f"CMD={cmd}"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.read_text(encoding="utf-8")


def test_heavy_run_passes_shell_variables_to_shell_unexpanded(tmp_path: Path) -> None:
    cmd = "BENCHBOX_OUTPUT_DIR=$HOME/x run > $HOME/y.log 2>&1"
    assert _run_heavy_run(tmp_path, cmd) == cmd


def test_heavy_run_keeps_dollar_dollar_and_make_syntax_verbatim(tmp_path: Path) -> None:
    cmd = "echo $$ $(date) ${X} $@"
    assert _run_heavy_run(tmp_path, cmd) == cmd
