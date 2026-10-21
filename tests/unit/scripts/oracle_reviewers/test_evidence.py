from __future__ import annotations

import json
from pathlib import Path

import pytest

from _project.scripts.oracle_reviewers.evidence import (
    CITATION_PREFIX,
    INLINE,
    UNVERIFIED_WITHDRAWAL,
    Trace,
    check,
    mark_citations,
    relative,
    trace,
    unread,
)
from _project.scripts.oracle_reviewers.verdict import Finding, PriorDefect, Verdict

pytestmark = [pytest.mark.unit, pytest.mark.fast]

OMP_STREAM = (
    Path(__file__).resolve().parents[3] / "fixtures" / "oracle_reviewers" / "omp_multi_turn_review.jsonl"
).read_text(encoding="utf-8")


def _tool_events(name: str, path: str, *, is_error: bool, output: str = "match") -> str:
    call = {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [{"type": "toolCall", "id": "c1", "name": name, "arguments": {"path": path}}],
        },
    }
    result = {
        "type": "message_end",
        "message": {
            "role": "toolResult",
            "toolCallId": "c1",
            "toolName": name,
            "isError": is_error,
            "content": [{"type": "text", "text": output}],
            "details": {"files": ["pkg/unrelated.py"]},
        },
    }
    return f"{json.dumps(call)}\n{json.dumps(result)}\n"


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


def test_omp_trace_reads_the_recorded_review_stream() -> None:
    checker = "/workspace/benchbox/core/equivalence/checker.py"
    run = trace("omp", OMP_STREAM)
    assert run.reads == (checker,)
    assert run.greps == ("/workspace",)
    assert unread("omp", ["benchbox/core/equivalence/checker.py", "pkg/b.py"], run, Path("/workspace")) == ["pkg/b.py"]


@pytest.mark.parametrize(
    ("name", "is_error", "output", "reads", "greps"),
    [
        ("read", False, "line", ("pkg/a.py:10-40",), ()),
        ("grep", False, "pkg/a.py\n*12|def f", (), ("pkg/a.py:10-40",)),
        ("grep", False, "No matches found", (), ()),
        ("read", True, "Path not found", (), ()),
        ("glob", False, "pkg/a.py", (), ()),
    ],
)
def test_omp_trace_counts_only_reads_that_returned_content(
    name: str, is_error: bool, output: str, reads: tuple[str, ...], greps: tuple[str, ...]
) -> None:
    events = _tool_events(name, "pkg/a.py:10-40", is_error=is_error, output=output)
    run = trace("omp", events)
    assert (run.reads, run.greps) == (reads, greps)


def test_multi_path_grep_credits_nothing(tmp_path: Path) -> None:
    events = _tool_events("grep", "pkg/a.py;pkg/b.py", is_error=False, output="pkg/a.py\n*3|x")
    assert unread("omp", ["pkg/a.py", "pkg/b.py"], trace("omp", events), tmp_path) == ["pkg/a.py", "pkg/b.py"]


def test_single_file_grep_credits_the_requested_file_not_the_reported_matches(tmp_path: Path) -> None:
    target = tmp_path / "pkg" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n", encoding="utf-8")
    events = _tool_events("grep", "pkg/a.py", is_error=False, output="pkg/a.py\n*1|x")
    assert unread("omp", ["pkg/a.py", "pkg/unrelated.py"], trace("omp", events), tmp_path) == ["pkg/unrelated.py"]


def test_single_file_grep_with_absolute_path_credits_the_file(tmp_path: Path) -> None:
    target = tmp_path / "pkg" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n", encoding="utf-8")
    events = _tool_events("grep", str(target), is_error=False, output="match")
    assert unread("omp", ["pkg/a.py"], trace("omp", events), tmp_path) == []


def test_directory_grep_credits_nothing(tmp_path: Path) -> None:
    target = tmp_path / "pkg" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n", encoding="utf-8")
    events = _tool_events("grep", "pkg", is_error=False, output="pkg/a.py\n*1|x")
    assert unread("omp", ["pkg/a.py"], trace("omp", events), tmp_path) == ["pkg/a.py"]


def test_unread_strips_omp_read_selectors_and_splits_path_lists(tmp_path: Path) -> None:
    run = Trace(("pkg/a.py:10-40", "pkg/b.py:raw", "pkg/c.py:5+20:raw", f"{tmp_path}/pkg/e.py; pkg/f.py:1-9"))
    required = ["pkg/a.py", "pkg/b.py", "pkg/c.py", "pkg/d.py", "pkg/e.py", "pkg/f.py"]
    assert unread("omp", required, run, tmp_path) == ["pkg/d.py"]


def test_untraced_harnesses_have_no_trace() -> None:
    assert trace("text", OMP_STREAM) == Trace()
    assert unread("text", ["pkg/a.py"], Trace(), Path("/workspace")) == []


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
            run=Trace(("pkg/a.py",)),
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
        harness="text",
        brief_mode="summary",
        workspace=workspace,
        required=["pkg/a.py"],
        run=Trace(),
    )
    assert problem == "the verdict would ship but did not examine pkg/a.py"


@pytest.mark.parametrize(
    ("reads", "problem"),
    [
        ((), "the verdict would ship but its trace shows no successful file read"),
        (("pkg/a.py",), "the verdict would ship but its trace shows no read of pkg/b.py"),
        (("pkg/a.py", "pkg/b.py:3-9"), None),
    ],
)
def test_check_requires_an_omp_read_of_every_required_file(
    tmp_path: Path, reads: tuple[str, ...], problem: str | None
) -> None:
    verdict = _verdict(examined=("pkg/a.py", "pkg/b.py"))
    found = check(
        verdict,
        ships=True,
        harness="omp",
        brief_mode="summary",
        workspace=tmp_path,
        required=["pkg/a.py", "pkg/b.py"],
        run=Trace(reads),
    )
    assert found == problem


def test_check_accepts_a_single_file_grep_for_each_required_file(tmp_path: Path) -> None:
    target = tmp_path / "pkg" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n", encoding="utf-8")
    verdict = _verdict(examined=("pkg/a.py", "pkg/b.py"))
    assert (
        check(
            verdict,
            ships=True,
            harness="omp",
            brief_mode="summary",
            workspace=tmp_path,
            required=["pkg/a.py", "pkg/b.py"],
            run=Trace((), ("pkg/a.py", "pkg/a.py;pkg/b.py", "pkg")),
        )
        == "the verdict would ship but its trace shows no read of pkg/b.py"
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
            run=Trace(("pkg/a.py",)),
        )
        is None
    )


def test_check_truncates_long_missing_lists(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    verdict = _verdict(examined=())
    required = [f"pkg/{name}.py" for name in ("a", "b", "c", "d", "e", "f", "g")]
    problem = check(
        verdict,
        ships=True,
        harness="omp",
        brief_mode="summary",
        workspace=workspace,
        required=required,
        run=Trace(tuple(required)),
    )
    assert problem is not None
    assert problem.endswith("and 2 more")
    assert "pkg/g.py" not in problem


def _withdrawn_verdict(evidence: str, examined: tuple[str, ...] = ("pkg/a.py",)) -> Verdict:
    return Verdict(
        status="complete",
        incomplete_reason="",
        decision="SHIP",
        summary="summary",
        files_examined=examined,
        defects=(),
        prior_defects=(PriorDefect(id="D1", status="withdrawn", evidence=evidence),),
    )


def test_mark_citations_keeps_a_withdrawal_with_a_head_commit_citation(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "a.py").write_text("one\ntwo\nthree\n")
    marked = mark_citations(_withdrawn_verdict("never a defect: pkg/a.py:2 keeps the row"), workspace)
    assert marked.prior_defects[0].status == "withdrawn"


def test_mark_citations_reopens_a_withdrawal_without_a_verifiable_citation(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "a.py").write_text("one\ntwo\nthree\n")
    marked = mark_citations(_withdrawn_verdict("never a defect"), workspace)
    assert marked.prior_defects[0].status == "not_fixed"
    assert UNVERIFIED_WITHDRAWAL in marked.prior_defects[0].evidence


def test_mark_citations_reopens_a_withdrawal_citing_a_missing_line(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "a.py").write_text("one\ntwo\n")
    marked = mark_citations(_withdrawn_verdict("never a defect: pkg/a.py:9"), workspace)
    assert marked.prior_defects[0].status == "not_fixed"


def test_mark_citations_reopens_a_withdrawal_citing_an_unexamined_file(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "a.py").write_text("one\ntwo\nthree\n")
    marked = mark_citations(_withdrawn_verdict("never a defect: pkg/a.py:2", ()), workspace)
    assert marked.prior_defects[0].status == "not_fixed"
