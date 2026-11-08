from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"
LOCK = json.loads((ROOT / ".github" / "theseus-lock.json").read_text(encoding="utf-8"))
PIN = LOCK["workflow_sha"]
SLUG = LOCK["repository"]
COMMIT = re.compile(r"[0-9a-f]{40}")
USES = re.compile(rf"^\s*(?:-\s+)?uses:\s*{re.escape(SLUG)}/\S+@(\S+)\s*$")
THESEUS_REF = re.compile(r"^\s*theseus_ref:\s*(\S+)\s*$")
CALLERS = ("review-verdict.yml", "review-gate.yml")


def _problems(workflows: dict[str, str], pin: str) -> list[str]:
    found: list[str] = []
    refs_by_file: dict[str, set[str]] = {}
    for name, text in sorted(workflows.items()):
        uses: list[str] = []
        theseus_refs: list[str] = []
        for line in text.splitlines():
            if uses_match := USES.match(line):
                uses.append(uses_match.group(1))
            elif ref_match := THESEUS_REF.match(line):
                theseus_refs.append(ref_match.group(1))
            elif SLUG in line:
                found.append(f"{name}: unrecognised reference to {SLUG}: {line.strip()}")
        if not uses and not theseus_refs:
            continue
        if len(uses) != len(theseus_refs):
            found.append(f"{name}: {len(uses)} `uses:` refs but {len(theseus_refs)} `theseus_ref` values")
        for kind, refs in (("uses", uses), ("theseus_ref", theseus_refs)):
            for ref in refs:
                if not COMMIT.fullmatch(ref):
                    found.append(f"{name}: {kind} is not a full commit sha: {ref}")
                elif ref != pin:
                    found.append(f"{name}: {kind} {ref} differs from the locked workflow_sha {pin}")
        refs_by_file[name] = {*uses, *theseus_refs}
    distinct = set().union(*refs_by_file.values()) if refs_by_file else set()
    if len(distinct) > 1:
        found.append(f"Theseus is referenced at {len(distinct)} different commits: {sorted(distinct)}")
    return found


def _tree() -> dict[str, str]:
    texts = {path.name: path.read_text(encoding="utf-8") for path in sorted(WORKFLOWS.glob("*.yml"))}
    return {name: text for name, text in texts.items() if SLUG in text or "theseus_ref" in text}


def _caller(pin: str, ref: str | None = None) -> str:
    return (
        "jobs:\n"
        "  gate:\n"
        f"    uses: {SLUG}/.github/workflows/gate.yml@{pin}\n"
        "    with:\n"
        f"      theseus_ref: {ref or pin}\n"
    )


OTHER = "f" * 40


def test_every_workflow_reference_to_theseus_is_the_locked_commit() -> None:
    tree = _tree()

    assert set(CALLERS) <= set(tree)
    assert _problems(tree, PIN) == []


def test_the_lock_names_a_full_commit_to_compare_against() -> None:
    assert COMMIT.fullmatch(PIN)


def test_agreeing_references_report_nothing() -> None:
    assert _problems({"a.yml": _caller(PIN), "b.yml": _caller(PIN)}, PIN) == []


def test_a_uses_ref_that_disagrees_with_theseus_ref_is_reported() -> None:
    problems = _problems({"a.yml": _caller(PIN, ref=OTHER)}, PIN)

    assert any("theseus_ref" in problem and OTHER in problem for problem in problems)


def test_a_theseus_ref_that_disagrees_with_the_uses_ref_is_reported() -> None:
    problems = _problems({"a.yml": _caller(OTHER, ref=PIN)}, PIN)

    assert any("uses" in problem and OTHER in problem for problem in problems)


def test_callers_pinned_to_different_commits_are_reported() -> None:
    problems = _problems({"a.yml": _caller(PIN), "b.yml": _caller(OTHER)}, PIN)

    assert any("different commits" in problem for problem in problems)
    assert any(problem.startswith("b.yml:") for problem in problems)


def test_both_callers_moved_together_without_the_lock_are_reported() -> None:
    problems = _problems({"a.yml": _caller(OTHER), "b.yml": _caller(OTHER)}, PIN)

    assert len(problems) == 4
    assert all("locked workflow_sha" in problem for problem in problems)


@pytest.mark.parametrize("ref", ["main", "v0.2.1", PIN[:12]])
def test_a_ref_that_is_not_a_full_commit_is_reported(ref: str) -> None:
    problems = _problems({"a.yml": _caller(ref, ref=ref)}, PIN)

    assert any("not a full commit sha" in problem for problem in problems)


def test_a_caller_that_loses_its_theseus_ref_is_reported() -> None:
    text = f"jobs:\n  gate:\n    uses: {SLUG}/.github/workflows/gate.yml@{PIN}\n"

    assert any("theseus_ref" in problem for problem in _problems({"a.yml": text}, PIN))


def test_a_second_workflow_that_references_theseus_in_another_form_is_reported() -> None:
    text = f"steps:\n  - uses: actions/checkout@v4\n    with:\n      repository: {SLUG}\n      ref: main\n"

    assert any("unrecognised reference" in problem for problem in _problems({"c.yml": text}, PIN))


def test_a_step_level_pin_in_another_workflow_is_held_to_the_lock() -> None:
    text = f"steps:\n  - uses: {SLUG}/.github/actions/x@{OTHER}\n"
    problems = _problems({"c.yml": text}, PIN)

    assert any("locked workflow_sha" in problem for problem in problems)
    assert any("`theseus_ref`" in problem for problem in problems)
