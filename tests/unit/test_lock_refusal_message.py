import pytest

from tests.conftest import _format_lock_refusal

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_refusal_lists_only_compliant_steps():
    text = _format_lock_refusal("/tmp/lock", "pid 1 pytest", "held", "")
    assert "BENCHBOX_SKIP_TEST_LOCK" not in text
    assert "Kill" not in text
    assert "kill" not in text
    assert "-n 0" not in text
    assert "make heavy-status" in text
    assert "make heavy-run" in text
    assert "docs/agent/heavy-work.md" in text
    assert "BENCHBOX_TEST_LOCK_WAIT_SECONDS" in text
    assert "/tmp/lock" in text and "pid 1 pytest" in text
