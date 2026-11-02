from __future__ import annotations

import shutil
import sys

import pytest

from benchbox.utils.host_lock import EXIT_ADMISSION
from tests.unit.test_heavy_choke_points import _calls, _make_up

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX process semantics"),
    pytest.mark.skipif(shutil.which("make") is None, reason="make is required"),
]


def test_a_refused_compose_bring_up_never_reaches_the_engine_and_tears_nothing_down(tmp_path) -> None:
    result = _make_up(tmp_path, "COMPOSE_UP_NEED_GIB=1000000")
    assert result.returncode != 0
    assert f"Error {EXIT_ADMISSION}" in result.stderr + result.stdout, result.stdout + result.stderr
    assert _calls(tmp_path) == []
    assert not list((tmp_path / "state").glob("*.project"))


def test_an_already_tracked_stack_skips_admission_and_is_not_torn_down(tmp_path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "postgresql.project").write_text("benchbox-postgresql-test-tracked\n", encoding="utf-8")
    result = _make_up(tmp_path, "COMPOSE_UP_NEED_GIB=1000000")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = _calls(tmp_path)
    assert len(calls) == 1 and calls[0].startswith("compose -p benchbox-postgresql-test-tracked ")
    assert (state / "postgresql.project").exists()
