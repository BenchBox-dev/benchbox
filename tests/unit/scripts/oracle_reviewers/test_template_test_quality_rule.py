from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

TEMPLATES = Path(__file__).resolve().parents[4] / "_project" / "scripts" / "oracle_reviewers" / "templates"


@pytest.mark.parametrize("name", ["gate", "standard", "deep"])
def test_test_quality_rule_still_reports_a_test_the_change_makes_vacuous(name: str) -> None:
    text = " ".join((TEMPLATES / f"{name}.md").read_text(encoding="utf-8").split())

    assert "never report an untouched existing test" not in text
    assert "untouched existing test only when the change under review bypasses its fake" in text


@pytest.mark.parametrize("name", ["gate", "standard", "deep"])
def test_hygiene_rule_still_reports_an_untouched_test_the_change_makes_leak_state(name: str) -> None:
    text = " ".join((TEMPLATES / f"{name}.md").read_text(encoding="utf-8").split())

    assert "applies only to tests the change adds or modifies" not in text
    assert "untouched existing test only when the change under review makes it leave such state behind" in text
