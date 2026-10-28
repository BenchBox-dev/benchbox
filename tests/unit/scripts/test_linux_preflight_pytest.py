from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "linux_preflight_pytest.sh"


def _run(tmp_path: Path, *, path: str) -> subprocess.CompletedProcess[str]:
    test_list = tmp_path / "tests.txt"
    test_list.write_text("tests/unit/test_x.py\n", encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("BENCHBOX_LINUX")}
    env["PATH"] = path
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), str(test_list)], capture_output=True, text=True, env=env, check=False
    )


def _fake_container(tmp_path: Path, machines: str) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "container"
    fake.write_text(f"#!/bin/sh\nprintf '%s\\n' 'NAME'\nprintf '%s\\n' '{machines}'\n", encoding="utf-8")
    fake.chmod(0o755)
    return f"{bin_dir}:/usr/bin:/bin"


def test_missing_container_warns_and_succeeds(tmp_path: Path) -> None:
    result = _run(tmp_path, path="/usr/bin:/bin")

    assert result.returncode == 0
    assert "Linux not verified" in result.stderr


@pytest.mark.skipif(os.uname().sysname != "Darwin" or os.uname().machine != "arm64", reason="Apple silicon only")
def test_missing_machine_warns_with_setup_steps_and_succeeds(tmp_path: Path) -> None:
    result = _run(tmp_path, path=_fake_container(tmp_path, "other-machine"))

    assert result.returncode == 0
    assert "Linux not verified" in result.stderr
    assert "container machine create" in result.stderr
