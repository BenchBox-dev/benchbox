from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

from _project.scripts.oracle_reviewers import runner
from _project.scripts.oracle_reviewers.commands import Invocation

from .test_attempt_loop import Loop, loop

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_real_execute_survives_deleted_cached_tempdir_across_slots(loop: Loop, monkeypatch: pytest.MonkeyPatch) -> None:
    cached = loop.tmp_path / "deleted-cache"
    cached.mkdir()
    cached.rmdir()
    monkeypatch.setattr(tempfile, "tempdir", str(cached))
    homes: list[str] = []

    def build(reviewer, workspace: Path, prompt: str, scratch: Path) -> Invocation:
        homes.append(os.environ["HOME"])
        return Invocation((sys.executable, "-c", "print('not a verdict')"), scratch, None, dict(os.environ))

    monkeypatch.setattr(runner, "build", build)
    monkeypatch.setattr(runner.shutil, "which", lambda name: sys.executable)
    assert loop.run() == 0
    assert len(homes) == 2 and homes[0] != homes[1]
    for slot in (1, 2):
        artifact = loop.artifact(slot)
        assert artifact["absence"]["kind"] == "invalid"
        assert "job did not finish" not in artifact["absence"]["detail"]
