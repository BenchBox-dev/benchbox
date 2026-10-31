from __future__ import annotations

from pathlib import Path

import pytest

from _project.scripts.oracle_reviewers import evidence
from _project.scripts.oracle_reviewers.verdict import MAX_EVIDENCE, validate

pytestmark = [pytest.mark.unit, pytest.mark.fast]

HIDDEN = ".github/workflows/oracle-verdict.yml"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / HIDDEN).write_text("name: x\n", encoding="utf-8")
    (tmp_path / "Makefile").write_text("all:\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    ("text", "path"),
    [
        (f"{HIDDEN}:1 shows it", HIDDEN),
        (f"see `{HIDDEN}:1`", HIDDEN),
        (f"see ./{HIDDEN}:1.", HIDDEN),
        ("Makefile:1 builds", "Makefile"),
        ("pkg/mod.py:1.", "pkg/mod.py"),
    ],
)
def test_withdrawal_citations_parse_the_full_repository_path(workspace: Path, text: str, path: str) -> None:
    assert evidence._withdrawal_verified(text, workspace, frozenset({path}))


@pytest.mark.parametrize("text", ["../pkg/mod.py:1", "/etc/passwd:1", "github/workflows/oracle-verdict.yml:1"])
def test_withdrawal_citations_outside_the_read_files_stay_unverified(workspace: Path, text: str) -> None:
    assert not evidence._withdrawal_verified(text, workspace, frozenset({HIDDEN, "Makefile", "pkg/mod.py"}))


def test_a_withdrawal_citation_counts_only_for_a_path_in_the_read_set() -> None:
    assert evidence.withdrawal_cited("pkg/mod.py:1 shows it", frozenset({"pkg/mod.py"}))
    assert not evidence.withdrawal_cited("pkg/mod.py:1 shows it", frozenset({"Makefile"}))
    assert not evidence.withdrawal_cited("no citation here", frozenset({"pkg/mod.py"}))
    assert evidence.withdrawal_cited("pkg/mod.py:1 shows it")


def test_a_hidden_path_reproduction_is_verified(workspace: Path) -> None:
    assert evidence._repro_verified(f"repro: {HIDDEN}", workspace, frozenset({HIDDEN}))
    assert evidence._repro_verified("repro: Makefile.", workspace, frozenset({"Makefile"}))
    assert not evidence._repro_verified("repro: ../Makefile", workspace, frozenset({"Makefile"}))


def test_replaying_a_trusted_artifact_keeps_the_appended_note() -> None:
    note = "disputed finding D1 has no concedes boolean; the defect stays open"
    data = {
        "id": "D1",
        "status": "not_fixed",
        "evidence": "e" * MAX_EVIDENCE + " " + note,
        "claim": "",
        "concedes": None,
        "demoted": "disposition",
    }
    verdict = validate(
        {
            "status": "complete",
            "incomplete_reason": "",
            "decision": "SHIP_WITH_FIXES",
            "summary": "open",
            "files_examined": [],
            "defects": [],
            "prior_defects": [data],
        },
        trusted=True,
    )
    assert verdict.prior_defects[0].evidence.endswith(note)
