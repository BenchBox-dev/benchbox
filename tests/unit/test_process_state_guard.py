from __future__ import annotations

import ctypes

import pytest

from tests.utilities import process_state_guard

PR_SET_CHILD_SUBREAPER = 36

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def test_clean_process_reports_no_leak():
    cleared: list[bool] = []

    assert process_state_guard.subreaper_leak(lambda: False, lambda: cleared.append(True)) is None
    assert cleared == []


def test_subreaper_process_reports_the_leak_and_is_cleared():
    cleared: list[bool] = []

    message = process_state_guard.subreaper_leak(lambda: True, lambda: cleared.append(True))

    assert message == process_state_guard.LEAK_MESSAGE
    assert cleared == [True]


@pytest.mark.skipif(not process_state_guard.IS_LINUX, reason="child subreaper status exists on Linux only")
def test_real_subreaper_status_is_detected_and_cleared():
    libc = ctypes.CDLL(None, use_errno=True)

    def set_subreaper(enabled: int) -> int:
        return libc.prctl(PR_SET_CHILD_SUBREAPER, enabled, 0, 0, 0)

    assert process_state_guard.is_child_subreaper() is False
    assert set_subreaper(1) == 0
    try:
        assert process_state_guard.is_child_subreaper() is True
        assert process_state_guard.subreaper_leak() == process_state_guard.LEAK_MESSAGE
        assert process_state_guard.is_child_subreaper() is False
    finally:
        set_subreaper(0)


def test_non_linux_probe_never_reports_a_leak(monkeypatch):
    monkeypatch.setattr(process_state_guard, "IS_LINUX", False)

    assert process_state_guard.is_child_subreaper() is False
