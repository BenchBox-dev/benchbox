from __future__ import annotations

from pathlib import Path

import pytest

from _project.scripts.oracle_reviewers.evidence import (
    CITATION_PREFIX,
    INLINE,
    TRACED,
    Trace,
    check,
    mark_citations,
    relative,
    trace,
    unread,
)
from _project.scripts.oracle_reviewers.verdict import Finding, Verdict

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _verdict(examined: tuple[str, ...] = (), defects: tuple[Finding, ...] = ()) -> Verdict:
    return Verdict(
        status="complete",
        incomplete_reason="",
        decision="SHIP",
        summary="summary",
        files_examined=examined,
        defects=defects,
    )


def _finding(file: str = "pkg/a.py", line: int = 2, end_line: int | None = None) -> Finding:
    return Finding(
        severity="High",
        file=file,
        line=line,
        title="title",
        detail="detail",
        end_line=end_line,
    )


@pytest.mark.parametrize("harness", ["codex", "Muse", "claude", "omp", "text", "other"])
@pytest.mark.parametrize("stdout", ["", "not json\n", '{"type": "item.completed"}\n'])
def test_trace_always_returns_empty(harness: str, stdout: str) -> None:
    assert trace(harness, stdout) == Trace()
    assert trace(harness, stdout).reads == ()


def test_traced_membership_is_empty() -> None:
    assert TRACED == ()


def test_unread_always_returns_empty(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    assert unread("codex", ["pkg/a.py"], Trace(("cat pkg/a.py",)), workspace) == []
    assert unread("omp", ["pkg/a.py"], Trace(), workspace) == []


def test_relative_maps_workspace_paths_and_leaves_outside_paths_absolute(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / "pkg").mkdir(parents=True)
    assert relative("pkg/a.py", workspace) == "pkg/a.py"
    assert relative("./pkg/a.py", workspace) == "pkg/a.py"
    assert relative(str(workspace.resolve() / "pkg" / "a.py"), workspace) == "pkg/a.py"
    assert relative("/etc/passwd", workspace) == "/etc/passwd"
    assert relative("pkg/a.py:12", workspace) == "pkg/a.py"
    assert relative("pkg/a.py:12-40", workspace) == "pkg/a.py"
    assert relative(str(workspace.resolve() / "pkg" / "a.py") + ":3", workspace) == "pkg/a.py"
    assert relative("../ws2/a.py", workspace).endswith("ws2/a.py")
    assert not relative("../ws2/a.py", workspace).startswith("ws2")


def test_mark_citations_leaves_valid_citation_unchanged(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "a.py").write_text("one\ntwo\nthree\n")
    marked = mark_citations(_verdict(defects=(_finding(),)), workspace)
    assert marked.defects[0].detail == "detail"


def test_mark_citations_flags_line_beyond_end_of_file(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "a.py").write_text("one\ntwo\n")
    marked = mark_citations(_verdict(defects=(_finding(line=9),)), workspace)
    assert marked.defects[0].detail.startswith(f"{CITATION_PREFIX}: pkg/a.py has 2 lines")
    assert marked.defects[0].detail.endswith("detail")


def test_mark_citations_flags_end_line_beyond_end_of_file(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "a.py").write_text("one\ntwo\n")
    marked = mark_citations(_verdict(defects=(_finding(line=1, end_line=7),)), workspace)
    assert "line 7 does not exist" in marked.defects[0].detail


def test_mark_citations_flags_path_missing_from_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    marked = mark_citations(_verdict(defects=(_finding(file="pkg/missing.py"),)), workspace)
    assert marked.defects[0].detail.startswith(f"{CITATION_PREFIX}: pkg/missing.py is not a file")


def test_check_passes_when_examined_covers_required(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    verdict = _verdict(examined=("pkg/a.py",))
    assert (
        check(
            verdict,
            ships=True,
            harness="omp",
            brief_mode="summary",
            workspace=workspace,
            required=["pkg/a.py"],
            run=Trace(),
        )
        is None
    )


def test_check_reports_examined_gap_without_trace_gate(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    verdict = _verdict(examined=("pkg/other.py",))
    problem = check(
        verdict,
        ships=True,
        harness="codex",
        brief_mode="summary",
        workspace=workspace,
        required=["pkg/a.py"],
        run=Trace(),
    )
    assert problem == "the verdict would ship but did not examine pkg/a.py"


def test_check_ignores_empty_trace_for_previously_traced_harnesses(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    verdict = _verdict(examined=("pkg/a.py",))
    for harness in ("codex", "Muse", "claude"):
        assert (
            check(
                verdict,
                ships=True,
                harness=harness,
                brief_mode="summary",
                workspace=workspace,
                required=["pkg/a.py"],
                run=Trace(),
            )
            is None
        )


def test_check_skips_inline_and_non_shipping_verdicts(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    verdict = _verdict(examined=())
    assert (
        check(
            verdict,
            ships=True,
            harness="omp",
            brief_mode=INLINE,
            workspace=workspace,
            required=["pkg/a.py"],
            run=Trace(),
        )
        is None
    )
    assert (
        check(
            verdict,
            ships=False,
            harness="omp",
            brief_mode="summary",
            workspace=workspace,
            required=["pkg/a.py"],
            run=Trace(),
        )
        is None
    )


def test_check_matches_examined_after_relative_normalization(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    verdict = _verdict(examined=("pkg/a.py:12-40",))
    assert (
        check(
            verdict,
            ships=True,
            harness="omp",
            brief_mode="summary",
            workspace=workspace,
            required=["pkg/a.py"],
            run=Trace(),
        )
        is None
    )


def test_check_truncates_long_missing_lists(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    verdict = _verdict(examined=())
    required = [f"pkg/{name}.py" for name in ("a", "b", "c", "d", "e", "f", "g")]
    problem = check(
        verdict, ships=True, harness="omp", brief_mode="summary", workspace=workspace, required=required, run=Trace()
    )
    assert problem is not None
    assert problem.endswith("and 2 more")
    assert "pkg/g.py" not in problem
