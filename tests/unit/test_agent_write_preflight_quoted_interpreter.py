from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

import pytest

from tests.unit.test_agent_write_preflight import (
    _fake_uv,
    _linked_worktree,
    _run_in_clone,
    _write_generated_hook,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _preflight_with_hook_interpreter(tmp_path: Path, interpreter: str):
    primary, linked = _linked_worktree(tmp_path)
    _write_generated_hook(primary, interpreter)
    _fake_uv(tmp_path / "fake-bin", tmp_path / "uv-cwd.txt")
    return _run_in_clone(
        linked,
        extra_env={"PATH": f"{tmp_path / 'fake-bin'}{os.pathsep}{os.environ['PATH']}"},
    )


@pytest.mark.parametrize("quote", ["'", '"'])
def test_preflight_accepts_a_quoted_hook_interpreter(tmp_path: Path, quote: str) -> None:
    result = _preflight_with_hook_interpreter(tmp_path, f"{quote}{sys.executable}{quote}")

    assert result.returncode == 0, result.stdout + result.stderr


def test_preflight_accepts_a_shell_quoted_interpreter_path_with_a_space(tmp_path: Path) -> None:
    spaced = tmp_path / "python home" / "python"
    spaced.parent.mkdir()
    spaced.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    spaced.chmod(0o755)

    result = _preflight_with_hook_interpreter(tmp_path, shlex.quote(str(spaced)))

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("quote", ["'", '"'])
def test_preflight_still_refuses_a_quoted_interpreter_that_does_not_exist(tmp_path: Path, quote: str) -> None:
    dead = tmp_path / "BenchBox.wt-deleted" / ".venv" / "bin" / "python"

    result = _preflight_with_hook_interpreter(tmp_path, f"{quote}{dead}{quote}")

    assert result.returncode == 1
    assert "shared pre-commit hook is not usable" in result.stderr
    assert str(dead) in result.stderr
