from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
GUIDE = ROOT / "docs/agent/todo-review.md"


def test_own_edit_target_freshness_rule_is_present() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    assert "own-edit-target" in text
    assert "edit target" in text or "own-edit-target" in text
    assert "w0" in text
    assert "Line-number" in text


