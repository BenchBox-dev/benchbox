from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import pr_base

pytestmark = [pytest.mark.unit, pytest.mark.fast]


@pytest.mark.parametrize("base", ["develop", "release/0.5.x", "release/10.20.x"])
def test_develop_and_maintenance_lines_are_accepted(base: str) -> None:
    assert pr_base.validate(base) == base


def test_an_unset_base_means_develop() -> None:
    assert pr_base.validate("") == "develop"
    assert pr_base.validate(None) == "develop"


@pytest.mark.parametrize(
    "base",
    [
        "main",
        "release",
        "release/0.5",
        "release/0.5.x/extra",
        "release/a.b.x",
        "release/0.5.x ",
        "xrelease/0.5.x",
        "feature/x",
        "published-results",
        "origin/develop",
    ],
)
def test_any_other_base_is_refused(base: str) -> None:
    with pytest.raises(ValueError, match="not 'develop' or a release/X.Y.x maintenance line"):
        pr_base.validate(base)


def test_the_command_prints_the_base_and_refuses_others(capsys: pytest.CaptureFixture[str]) -> None:
    assert pr_base.main(["release/0.5.x"]) == 0
    assert capsys.readouterr().out == "release/0.5.x\n"
    assert pr_base.main([""]) == 0
    assert capsys.readouterr().out == "develop\n"
    assert pr_base.main(["feature/x"]) == 2
    assert "not 'develop' or a release/X.Y.x" in capsys.readouterr().err
    assert pr_base.main([]) == 2
