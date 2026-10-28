from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from .harness import CHECKER, REPO_ROOT, Harness, ReviewRun

pytestmark = [pytest.mark.integration, pytest.mark.medium]

CELLS = "benchbox/core/equivalence/cells.py"
NOTES = "docs/notes.md"
GUIDE = "docs/guide.md"
FONT = "assets/fonts/ui.woff2"
FAKE_FONT = "assets/fonts/fake.woff2"
LOGO = "assets/logo.png"
UNREAD_MARK = "[unread in your previous attempt]"
FEEDBACK = "Earlier attempt feedback: "
FEEDBACK_NONE = f"{FEEDBACK}none"
FILLER = "".join(f"CELL_{number:05d} = {number}\n" for number in range(5000))
LARGE_CHECKER = "def checker(a, b):\n    return a == b\n" + FILLER
BINARY = b"\x00\x01\x02 glyph table\x00" * 8
SHIP = {"decision": "SHIP", "summary": "The change keeps checker semantics; no defects.", "files_examined": [CHECKER]}
FULL_READ = {**SHIP, "files_examined": [CHECKER, CELLS], "reads": [CELLS]}
INERT_WITHOUT_MARKDOWN = (
    "\n".join(
        line
        for line in (REPO_ROOT / ".github" / "inert-paths.txt").read_text(encoding="utf-8").splitlines()
        if line not in ("glob\t*.md", "glob\tdocs/**")
    )
    + "\n"
)


def _line(prompt: str, path: str) -> str:
    return next(row for row in prompt.splitlines() if row.startswith(f"{path} ") or row == path)


def test_a_file_list_brief_names_the_required_reads_and_a_retry_marks_the_missed_file(harness: Harness) -> None:
    harness.branch(
        "feat/large",
        {CHECKER: LARGE_CHECKER, CELLS: FILLER, NOTES: "A note about the cells.\n"},
        "add the cell table",
    )
    harness.github.open_pull(81, "feat/large")
    harness.script_omp([SHIP, FULL_READ])

    run = harness.review(81)

    assert run.plan["brief_mode"] == "file-list"
    assert run.plan["evidence_files"] == [CELLS, CHECKER]
    first, second = harness.omp_prompts()
    required, _, others = first.partition("Not required reads")
    assert "Required reads: each file listed here needs its own direct read" in required
    assert CHECKER in required and CELLS in required and NOTES not in required
    assert NOTES in others
    assert FEEDBACK_NONE in first and UNREAD_MARK not in _line(first, CELLS)
    assert UNREAD_MARK in _line(second, CELLS) and UNREAD_MARK not in _line(second, CHECKER)
    assert f"{FEEDBACK}your previous attempt at this review did not read 1 of the required files" in second
    assert CELLS not in second.partition(FEEDBACK)[2].partition("\n")[0]
    attempts = run.attempts()
    assert [(item["outcome"], (item["absence"] or {}).get("kind")) for item in attempts] == [
        ("absent", "incomplete"),
        ("verdict", None),
    ]
    assert attempts[0]["absence"]["unread"] == [CELLS]
    assert [item["state"] for item in harness.github.oracle_statuses(run.plan["head_sha"])] == ["success"]


def test_the_next_reviewer_after_two_incomplete_attempts_gets_a_brief_without_the_retry_marks(
    harness: Harness,
) -> None:
    head = harness.branch("feat/large", {CHECKER: LARGE_CHECKER, CELLS: FILLER}, "add the cell table")
    harness.github.open_pull(82, "feat/large")
    harness.script_omp([SHIP, SHIP, FULL_READ])

    run = harness.review(82)

    first, retry, escalated = harness.omp_prompts()
    assert FEEDBACK_NONE in first and FEEDBACK_NONE not in retry and UNREAD_MARK in _line(retry, CELLS)
    assert FEEDBACK_NONE in escalated and UNREAD_MARK not in _line(escalated, CELLS)
    assert [(item["reviewer"], item["outcome"]) for item in run.attempts()] == [
        ("medium", "absent"),
        ("medium", "absent"),
        ("large", "verdict"),
    ]
    assert [item["state"] for item in harness.github.oracle_statuses(head)] == ["success"]


def _large_review(
    harness: Harness, pr: int, extra: dict[str, str | bytes], plan_env: dict[str, str] | None = None
) -> ReviewRun:
    harness.branch(f"feat/exempt-{pr}", {CHECKER: LARGE_CHECKER, **extra}, "change the checker and its companions")
    harness.github.open_pull(pr, f"feat/exempt-{pr}")
    harness.script_omp([SHIP])
    return harness.review(pr, plan_env=plan_env)


def test_a_content_confirmed_font_is_exempt_from_the_required_reads(harness: Harness) -> None:
    run = _large_review(harness, 83, {FONT: BINARY})

    assert run.plan["evidence_files"] == [FONT, CHECKER]
    assert [(item["outcome"], item["absence"]) for item in run.attempts()] == [("verdict", None)]
    assert [item["state"] for item in harness.github.oracle_statuses(run.plan["head_sha"])] == ["success"]


@pytest.mark.parametrize(
    ("path", "content"),
    [(LOGO, BINARY), (FAKE_FONT, "a text file that only carries a font name\n")],
    ids=["image", "text-named-like-a-font"],
)
def test_a_binary_that_is_not_a_confirmed_font_still_needs_a_read(
    harness: Harness, path: str, content: str | bytes
) -> None:
    run = _large_review(harness, 84, {path: content})

    assert path in run.plan["evidence_files"]
    assert [item["absence"]["kind"] for item in run.attempts()] == ["incomplete"] * 5
    assert path in run.attempts()[0]["absence"]["detail"]
    assert [item["state"] for item in harness.github.oracle_statuses(run.plan["head_sha"])] == ["pending"]


def test_prose_is_exempt_only_when_it_is_not_source_or_soundness(harness: Harness, tmp_path: Path) -> None:
    manifest = tmp_path / "inert-without-markdown.txt"
    manifest.write_text(INERT_WITHOUT_MARKDOWN, encoding="utf-8")

    run = _large_review(
        harness, 85, {GUIDE: "How the checker is used.\n"}, plan_env={"INERT_PATH_MANIFEST": str(manifest)}
    )

    assert run.plan["evidence_files"] == [CHECKER, GUIDE]
    assert [(item["outcome"], item["absence"]) for item in run.attempts()] == [("verdict", None)]
    assert [item["state"] for item in harness.github.oracle_statuses(run.plan["head_sha"])] == ["success"]


def test_soundness_prose_and_source_stay_required(harness: Harness) -> None:
    readme = "benchbox/core/equivalence/README.md"
    run = _large_review(harness, 86, {readme: "How equality is decided.\n"})

    assert readme in run.plan["evidence_files"]
    assert [item["absence"]["kind"] for item in run.attempts()] == ["incomplete"] * 5
    assert readme in run.attempts()[0]["absence"]["detail"]
    assert [item["state"] for item in harness.github.oracle_statuses(run.plan["head_sha"])] == ["pending"]


def test_a_branch_cannot_add_its_own_exemption(harness: Harness) -> None:
    policy = yaml.safe_load((REPO_ROOT / ".github" / "oracle-reviewers.yml").read_text(encoding="utf-8"))
    policy.setdefault("evidence", {}).setdefault("exempt_paths", []).append("assets/*.dat")
    widened = yaml.safe_dump(policy, sort_keys=False)

    run = _large_review(harness, 87, {".github/oracle-reviewers.yml": widened, "assets/table.dat": "1,2,3\n"})

    assert "assets/table.dat" in run.plan["evidence_files"]
    assert [item["absence"]["kind"] for item in run.attempts()] == ["incomplete"] * 5
    assert "assets/table.dat" in run.attempts()[0]["absence"]["detail"]
    assert [item["state"] for item in harness.github.oracle_statuses(run.plan["head_sha"])] == ["pending"]
