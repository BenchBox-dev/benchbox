from __future__ import annotations

import sys

import pytest

from _project.scripts.oracle_reviewers import omp_pin

from .test_attempt_loop import Loop, loop

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_real_execute_survives_deleted_cached_tempdir_across_slots(loop: Loop, monkeypatch: pytest.MonkeyPatch) -> None:
    cached = loop.tmp_path / "deleted-cache"
    cached.mkdir()
    cached.rmdir()
    journal = loop.journal("homes")
    loop.fake("deleted_tempdir", path=str(cached))
    loop.fake("home_recording_build", journal=str(journal.path))
    monkeypatch.setenv(omp_pin.BINARY_ENV, sys.executable)
    assert loop.run() == 0
    homes = journal.items()
    assert len(homes) == 2 and homes[0] != homes[1]
    for slot in (1, 2):
        artifact = loop.artifact(slot)
        assert artifact["absence"]["kind"] == "invalid"
        assert "job did not finish" not in artifact["absence"]["detail"]
