from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from _project.scripts.oracle_reviewers.diff import binary_files as diff_binary_files
from _project.scripts.oracle_reviewers.evidence import (
    CITATION_PREFIX,
    INLINE,
    UNVERIFIED_WITHDRAWAL,
    Gap,
    Trace,
    check,
    describe,
    glob_match,
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


def _tool_events(name: str, path: str, *, is_error: bool, output: str = "match", call_id: str = "c1") -> str:
    call = {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [{"type": "toolCall", "id": call_id, "name": name, "arguments": {"path": path}}],
        },
    }
    result = {
        "type": "message_end",
        "message": {
            "role": "toolResult",
            "toolCallId": call_id,
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
    assert problem == Gap("the verdict would ship but did not examine pkg/a.py", ("pkg/a.py",))


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
    assert (found.reason if found else None) == problem


def test_check_accepts_a_single_file_grep_for_each_required_file(tmp_path: Path) -> None:
    target = tmp_path / "pkg" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n", encoding="utf-8")
    verdict = _verdict(examined=("pkg/a.py", "pkg/b.py"))
    found = check(
        verdict,
        ships=True,
        harness="omp",
        brief_mode="summary",
        workspace=tmp_path,
        required=["pkg/a.py", "pkg/b.py"],
        run=Trace((), ("pkg/a.py", "pkg/a.py;pkg/b.py", "pkg")),
    )
    assert found == Gap("the verdict would ship but its trace shows no read of pkg/b.py", ("pkg/b.py",))


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
    assert problem.reason.endswith("and 2 more")
    assert "pkg/g.py" not in problem.reason
    assert problem.files == tuple(required)


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
    marked = mark_citations(
        _withdrawn_verdict("never a defect: pkg/a.py:2 keeps the row"), workspace, read=frozenset({"pkg/a.py"})
    )
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
    marked = mark_citations(_withdrawn_verdict("never a defect: pkg/a.py:9"), workspace, read=frozenset({"pkg/a.py"}))
    assert marked.prior_defects[0].status == "not_fixed"


def test_mark_citations_reopens_a_withdrawal_citing_a_file_examined_but_never_read(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "a.py").write_text("one\ntwo\nthree\n")
    marked = mark_citations(_withdrawn_verdict("never a defect: pkg/a.py:2"), workspace)
    assert marked.prior_defects[0].status == "not_fixed"
    assert UNVERIFIED_WITHDRAWAL in marked.prior_defects[0].evidence


def test_mark_citations_reopens_a_withdrawal_citing_an_unexamined_file(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "a.py").write_text("one\ntwo\nthree\n")
    marked = mark_citations(_withdrawn_verdict("never a defect: pkg/a.py:2", ()), workspace, read=frozenset())
    assert marked.prior_defects[0].status == "not_fixed"


@pytest.mark.parametrize(
    "selector",
    [
        "",
        ":raw",
        ":5",
        ":L5",
        ":5-",
        ":5..",
        ":5-10",
        ":5..10",
        ":L5-L10",
        ":5+20",
        ":L5+L20",
        ":-30",
        ":1-12,-15",
        ":1-30,:-30",
        ":5-16,960-973",
        ":5-10:raw",
        ":raw:5-10",
        ":17:raw",
        ":5-:raw",
        ":5+20:raw",
    ],
)
def test_every_documented_line_and_raw_selector_credits_the_exact_path(tmp_path: Path, selector: str) -> None:
    run = Trace((f"pkg/a.py{selector}",))
    assert unread("omp", ["pkg/a.py", "pkg/ab.py"], run, tmp_path) == ["pkg/ab.py"]


@pytest.mark.parametrize(
    "path",
    [
        "pkg/a.py?q=.version",
        "pkg/a.py:conflicts",
        "pkg/a.py:img",
        "pkg/a.py:5:7",
        "pkg/a.py:0x",
        "pkg/a.pyx",
        "pkg/a.py.bak:5",
        "pkg/sub/../ab.py",
        "../outside/pkg/a.py",
        "pkg/a.py:../../../pkg/ab.py",
        "pkg",
        "pkg/*.py",
    ],
)
def test_unrecognized_selectors_siblings_and_traversal_credit_nothing(tmp_path: Path, path: str) -> None:
    assert unread("omp", ["pkg/a.py"], Trace((path,)), tmp_path) == ["pkg/a.py"]


def test_a_computed_query_read_stays_uncredited_while_a_plain_read_credits(tmp_path: Path) -> None:
    queried = trace("omp", _tool_events("read", "pkg/a.py?q=.version", is_error=False, output="1"))
    plain = trace("omp", _tool_events("read", "pkg/a.py", is_error=False, output="def f(): ..."))
    assert unread("omp", ["pkg/a.py"], queried, tmp_path) == ["pkg/a.py"]
    assert unread("omp", ["pkg/a.py"], plain, tmp_path) == []


@pytest.mark.parametrize(
    "path",
    ["dist/x.whl", "dist/x.whl:", "dist/x.whl:pkg/mod.py", "dist/x.whl:pkg/mod.py:5-9", "dist/x.tar.gz:a/b.txt"],
)
def test_a_successful_archive_read_credits_its_container_only(tmp_path: Path, path: str) -> None:
    container = path.split(":", 1)[0]
    required = [container, "pkg/mod.py", "a/b.txt"]
    run = trace("omp", _tool_events("read", path, is_error=False, output="pkg/\nmod.py (12)"))
    assert unread("omp", required, run, tmp_path) == ["pkg/mod.py", "a/b.txt"]


@pytest.mark.parametrize(
    ("path", "is_error", "output"),
    [
        ("dist/x.whl:pkg/mod.py", True, "Archive entry not found"),
        ("dist/x.whl:pkg/mod.py", False, "[Cannot read binary archive entry 'pkg/mod.so' (12 bytes)]"),
        ("dist/x.whl:../../pkg/mod.py", False, "x"),
        ("dist/x.whl:pkg/mod.py?q=.x", False, "x"),
        ("dist/x.txt:pkg/mod.py", False, "x"),
        ("other/x.whl:pkg/mod.py", False, "x"),
        ("pkg/a.py;dist/x.whl:pkg/mod.so", False, "def f(): ...\n[Cannot read binary archive entry 'pkg/mod.so']"),
        ("pkg/a.py;dist/x.whl:pkg/mod.py", False, "x"),
        ("dist/x.whl:pkg/mod.py;pkg/a.py", False, "x"),
    ],
)
def test_failed_unsafe_or_unrelated_archive_reads_do_not_credit_the_container(
    tmp_path: Path, path: str, is_error: bool, output: str
) -> None:
    run = trace("omp", _tool_events("read", path, is_error=is_error, output=output))
    assert unread("omp", ["dist/x.whl"], run, tmp_path) == ["dist/x.whl"]


def test_grep_credit_accepts_the_documented_selector_grammar_for_one_file_only(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "ab.py").write_text("x = 1\n", encoding="utf-8")
    for path in ("pkg/a.py:1-20", "pkg/a.py:raw", "pkg/a.py:5+20:raw"):
        run = trace("omp", _tool_events("grep", path, is_error=False, output="pkg/a.py\n*1|x"))
        assert unread("omp", ["pkg/a.py", "pkg/ab.py"], run, tmp_path) == ["pkg/ab.py"]
    run = trace("omp", _tool_events("grep", "pkg/*.py", is_error=False, output="pkg/a.py\n*1|x"))
    assert unread("omp", ["pkg/a.py"], run, tmp_path) == ["pkg/a.py"]


def _summarized_read(path: str, spans: int, lines: int) -> str:
    events = [json.loads(line) for line in _tool_events("read", path, is_error=False, output="x").splitlines()]
    events[1]["message"]["details"] = {"summary": {"lines": 64, "elidedSpans": spans, "elidedLines": lines}}
    return "".join(json.dumps(event) + "\n" for event in events)


def test_summarized_reads_stay_credited_and_their_elisions_are_recorded(tmp_path: Path) -> None:
    run = trace("omp", _summarized_read("pkg/a.py", 7, 107))
    assert run.elisions == (("pkg/a.py", 7, 107),)
    assert unread("omp", ["pkg/a.py"], run, tmp_path) == []
    assert trace("omp", _summarized_read("pkg/a.py", 0, 0)).elisions == ()


def test_describe_reports_bounded_counts_not_raw_streams(tmp_path: Path) -> None:
    failed = _tool_events("read", "pkg/gone.py", is_error=True, call_id="c2")
    events = _summarized_read("pkg/a.py", 7, 107) + failed
    required = [f"pkg/f{index}.py" for index in range(12)] + ["pkg/a.py"]
    text = describe(trace("omp", events), required, tmp_path)
    assert "reads=1" in text and "failed=1" in text and "elided_spans=7" in text and "elided_lines=107" in text
    assert "required=13" in text and "and 7 more" in text
    assert len(text) <= 700


def test_describe_redacts_credentials_in_requested_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "fakecred-Aa1Bb2Cc3Dd4"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    run = trace("omp", _tool_events("read", f"pkg/{secret}.py", is_error=False, output="x"))
    assert secret not in describe(run, [], tmp_path)


def test_a_symlink_leaving_the_checkout_credits_nothing_for_read_archive_or_grep(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x\n", encoding="utf-8")
    (outside / "x.whl").write_text("z", encoding="utf-8")
    workspace = tmp_path / "ws"
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "inside.py").write_text("x\n", encoding="utf-8")
    (workspace / "link.txt").symlink_to(outside / "secret.txt")
    (workspace / "link.whl").symlink_to(outside / "x.whl")
    for name, path in (("read", "link.txt"), ("read", "link.whl:pkg/m.py"), ("grep", "link.txt")):
        run = trace("omp", _tool_events(name, path, is_error=False, output="x"))
        assert unread("omp", ["link.txt", "link.whl"], run, workspace) == ["link.txt", "link.whl"]


def test_a_symlink_staying_inside_the_checkout_is_still_credited(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "inside.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "pkg" / "alias.py").symlink_to(tmp_path / "pkg" / "inside.py")
    run = trace("omp", _tool_events("read", "pkg/alias.py", is_error=False, output="x"))
    assert unread("omp", ["pkg/alias.py"], run, tmp_path) == []


@pytest.mark.parametrize(
    "literal",
    ["a.py:raw", "a.py:1-2", "a.py:5", "a.py:conflicts", "a.py:img", "a.py?q=.x", "a.py:table", "a.py:pkg/m.py"],
)
def test_a_literal_filename_that_collides_with_a_selector_credits_nothing(tmp_path: Path, literal: str) -> None:
    (tmp_path / "a.py").write_text("base\n", encoding="utf-8")
    (tmp_path / literal).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / literal).write_text("literal\n", encoding="utf-8")
    for name in ("read", "grep"):
        run = trace("omp", _tool_events(name, literal, is_error=False, output="literal"))
        assert unread("omp", ["a.py", literal], run, tmp_path) == ["a.py", literal]


@pytest.mark.parametrize("literal", ["a.py:raw", "a.py:1-2"])
def test_a_literal_filename_without_a_base_file_is_credited_exactly(tmp_path: Path, literal: str) -> None:
    (tmp_path / literal).write_text("literal\n", encoding="utf-8")
    run = trace("omp", _tool_events("read", literal, is_error=False, output="literal"))
    assert unread("omp", [literal], run, tmp_path) == []


def test_a_selector_credits_the_base_file_when_no_such_literal_file_exists(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("base\n", encoding="utf-8")
    run = trace("omp", _tool_events("read", "a.py:raw", is_error=False, output="base"))
    assert unread("omp", ["a.py"], run, tmp_path) == []


def test_required_symlink_inside_checkout_is_credited_by_real_path(tmp_path):
    (tmp_path / "real.md").write_text("x\n")
    (tmp_path / "link.md").symlink_to("real.md")
    assert unread("omp", ["link.md"], Trace(("real.md",)), tmp_path) == []


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


def _head_repo(tmp_path: Path, files: dict[str, bytes], attributes: str = "") -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(root, "add", "seed.txt")
    _git(root, "commit", "-q", "-m", "seed")
    base = _git(root, "rev-parse", "HEAD").strip()
    if attributes:
        (root / ".gitattributes").write_text(attributes, encoding="utf-8")
    for name, data in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "change")
    return root, _git(root, "diff", "--no-color", "--no-ext-diff", "--no-textconv", base, "HEAD")


def _exposure(workspace: Path, required: list[str], run: Trace | None = None, **kwargs: object) -> str | None:
    diff = kwargs.pop("diff", "")
    if diff:
        hinted = kwargs.get("binary_files", ())
        assert isinstance(hinted, (list, tuple))
        kwargs["binary_files"] = [*hinted, *diff_binary_files(str(diff))]
    gap = check(
        _verdict(examined=()),
        ships=True,
        harness="omp",
        brief_mode="summary",
        workspace=workspace,
        required=required,
        run=Trace() if run is None else run,
        **kwargs,
    )
    return gap.reason if gap else None


def _policy_exemptions() -> tuple[str, ...]:
    from _project.scripts.oracle_reviewers.policy import load_policy

    return load_policy(Path(__file__).resolve().parents[4] / ".github" / "oracle-reviewers.yml").evidence.exempt_paths


READ = Trace(("seed.txt",))
NO_READ = "the verdict would ship but its trace shows no successful file read"


def _unread(path: str) -> str:
    return f"the verdict would ship but its trace shows no read of {path}"


def test_binary_file_and_readme_no_longer_make_attempt_absent(tmp_path: Path) -> None:
    root, diff = _head_repo(tmp_path, {"vendor/package.woff2": b"PK\x03\x04\x00\x00data", "pkg/README.md": b"prose\n"})
    assert (
        _exposure(
            root,
            ["vendor/package.woff2", "pkg/README.md"],
            READ,
            diff=diff,
            exempt_paths=["README*.md", "**/README*.md"],
        )
        is None
    )


def test_source_file_still_requires_read(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    required = ["pkg/README.md", "benchbox/core/checker.py"]
    assert _exposure(workspace, required, exempt_paths=["README*.md", "**/README*.md"]) == NO_READ
    problem = _exposure(workspace, required, Trace(("other.py",)), exempt_paths=["README*.md", "**/README*.md"])
    assert problem == "the verdict would ship but its trace shows no read of benchbox/core/checker.py"


def test_soundness_path_prose_file_still_requires_read(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    soundness_prose = ".github/PULL_REQUEST_TEMPLATE.md"
    assert _exposure(workspace, [soundness_prose], exempt_paths=["*.md"]) == NO_READ
    problem = _exposure(workspace, [soundness_prose], Trace(("other.py",)), exempt_paths=["*.md"])
    assert problem == f"the verdict would ship but its trace shows no read of {soundness_prose}"


def test_an_archive_stays_required_even_after_a_binary_entry_notice(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / "dist").mkdir(parents=True)
    (workspace / "dist" / "x.whl").write_bytes(b"PK\x03\x04\x00\x00")
    events = _tool_events(
        "read",
        "dist/x.whl:pkg/mod.so",
        is_error=False,
        output="[Cannot read binary archive entry 'pkg/mod.so' (12 bytes)]",
    )
    run = trace("omp", events)
    assert run.failed == 1
    assert _exposure(workspace, ["dist/x.whl"], run, binary_files=["dist/x.whl"]) == NO_READ


def test_diff_detected_binary_files_are_exempt(tmp_path: Path) -> None:
    root, diff = _head_repo(tmp_path, {"bin.woff2": b"head\x00tail"})
    assert "Binary files /dev/null and b/bin.woff2 differ" in diff
    assert _exposure(root, ["bin.woff2"], READ, diff=diff) is None


def test_binary_notice_without_a_nul_byte_does_not_exempt(tmp_path: Path) -> None:
    root, _ = _head_repo(tmp_path, {"pkg/mod.woff2": b"value = 1\n"})
    notice = "diff --git a/pkg/mod.woff2 b/pkg/mod.woff2\nBinary files a/pkg/mod.woff2 and b/pkg/mod.woff2 differ\n"
    assert _exposure(root, ["pkg/mod.woff2"], READ, diff=notice, binary_files=["pkg/mod.woff2"]) == _unread(
        "pkg/mod.woff2"
    )


def test_nul_byte_after_the_probe_window_does_not_confirm_binary(tmp_path: Path) -> None:
    root, diff = _head_repo(tmp_path, {"late.woff2": b"a" * 8000 + b"\x00"})
    assert _exposure(root, ["late.woff2"], READ, diff=diff, binary_files=["late.woff2"]) == _unread("late.woff2")


def test_a_python_file_marked_not_diffable_stays_required_and_fails_when_unread(tmp_path: Path) -> None:
    root, diff = _head_repo(
        tmp_path,
        {"benchbox/core/checker.py": b"def verify():\n    return True\n"},
        attributes="*.py -diff\n",
    )
    assert "Binary files /dev/null and b/benchbox/core/checker.py differ" in diff
    assert _exposure(root, ["benchbox/core/checker.py"], diff=diff) == NO_READ
    problem = _exposure(root, ["benchbox/core/checker.py"], Trace(("benchbox/core/checker.py",)), diff=diff)
    assert problem == "the verdict would ship but did not examine benchbox/core/checker.py"


def test_a_symlinked_binary_outside_the_checkout_is_not_confirmed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"\x00\x01")
    (workspace / "link.woff2").symlink_to(outside)
    assert _exposure(workspace, ["link.woff2"], READ, binary_files=["link.woff2"]) == _unread("link.woff2")


def test_a_binary_absent_from_the_head_tree_is_not_confirmed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    assert _exposure(workspace, ["gone.woff2"], READ, binary_files=["gone.woff2"]) == _unread("gone.woff2")


@pytest.mark.parametrize("path", ["package.whl", "picture.woff2", "data.zip"])
def test_binary_extension_alone_does_not_exempt_a_file(tmp_path: Path, path: str) -> None:
    (tmp_path / path).write_bytes(b"\x00\x01")
    assert _exposure(tmp_path, [path]) == NO_READ


@pytest.mark.parametrize("path", [".github/PULL_REQUEST_TEMPLATE.md", ".gitattributes", "docs/.gitattributes"])
def test_binary_soundness_file_still_requires_exposure(tmp_path: Path, path: str) -> None:
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\x00\x01")
    problem = _exposure(tmp_path, [path], binary_files=[path], exempt_paths=["*.md", "*"])
    assert problem == NO_READ


@pytest.mark.parametrize(
    "path",
    [
        "data.sql",
        "golden.csv",
        "fixture.json",
        "mod.py",
        "notes.txt",
        "blob.dat",
        "noext",
        "_native.so",
        "mod.pyc",
        "tool.exe",
        "data.parquet",
    ],
)
def test_a_nul_byte_in_a_text_type_or_unknown_file_never_exempts(tmp_path: Path, path: str) -> None:
    root, diff = _head_repo(tmp_path, {path: b"payload\x00hidden"})
    assert "differ" in diff
    assert _exposure(root, [path], diff=diff, binary_files=[path]) == NO_READ


def test_a_machine_read_doc_stays_required_despite_a_recursive_docs_glob(tmp_path: Path) -> None:
    path = "docs/benchmarks/sf1-calibrated-sizes.md"
    assert _exposure(tmp_path, [path], READ, exempt_paths=["docs/**/*.md"]) == _unread(path)
    assert _exposure(tmp_path, ["docs/guide/page.md"], READ, exempt_paths=["docs/**/*.md"]) is None


def test_the_docs_exemption_covers_only_direct_children_of_docs(tmp_path: Path) -> None:
    exempt = list(_policy_exemptions())
    assert _exposure(tmp_path, ["docs/index.md"], READ, exempt_paths=exempt) is None
    for path in ("docs/benchmarks/tpc-h.md", "docs/development/result-validation.md"):
        assert _exposure(tmp_path, [path], READ, exempt_paths=exempt) == _unread(path)


def test_readme_and_changelog_exemptions_apply_at_any_depth(tmp_path: Path) -> None:
    exempt = list(_policy_exemptions())
    for path in ("pkg/README.md", "pkg/sub/README.md", "pkg/CHANGELOG.md", "a/b/c/CHANGELOG-1.md"):
        assert _exposure(tmp_path, [path], READ, exempt_paths=exempt) is None


def test_a_protected_binary_stays_required(tmp_path: Path) -> None:
    root, diff = _head_repo(tmp_path, {"vendor/pack.woff2": b"PK\x00\x00"})
    assert _exposure(root, ["vendor/pack.woff2"], READ, diff=diff) is None
    protected = _exposure(root, ["vendor/pack.woff2"], diff=diff, is_protected=lambda path: path == "vendor/pack.woff2")
    assert protected == NO_READ


def test_an_unread_all_exempt_change_still_needs_one_successful_read(tmp_path: Path) -> None:
    root, diff = _head_repo(tmp_path, {"pic.woff2": b"wOF2\x00"})
    assert _exposure(root, [], diff=diff) == NO_READ
    assert _exposure(root, ["pic.woff2"], diff=diff) == NO_READ
    assert _exposure(root, ["pic.woff2"], READ, diff=diff) is None


def test_a_gitattributes_change_is_a_soundness_path_change() -> None:
    from _project.scripts.soundness_paths import is_soundness_path

    assert is_soundness_path(".gitattributes")
    assert is_soundness_path("docs/.gitattributes")
    assert not is_soundness_path("docs/gitattributes.md")


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "pkg/README.md",
        "README-dev.md",
        "CHANGELOG.md",
        "pkg/CHANGELOG-1.md",
        "docs/guide.md",
    ],
)
def test_prose_globs_cover_prose_files(path: str) -> None:
    assert any(glob_match(path, pattern) for pattern in _policy_exemptions())


@pytest.mark.parametrize(
    "path",
    [
        "benchbox/README_helper.py",
        "README/loader.py",
        "pkg/README/loader.py",
        "CHANGELOG/gen.py",
        "benchbox/core/CHANGELOG_parser.py",
        "docs/gen.py",
        "docs/a/conf.yaml",
        "docs/a/b/guide.md",
        "CONTRIBUTING.md",
        "notes.md",
    ],
)
def test_prose_globs_do_not_cover_other_files(path: str) -> None:
    assert not any(glob_match(path, pattern) for pattern in _policy_exemptions())


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Binary files a/blob and b/blob differ\n", frozenset({"blob"})),
        ("Binary files /dev/null and b/blob differ\n", frozenset({"blob"})),
        ("Binary files a/blob and /dev/null differ\n", frozenset({"blob"})),
        ("GIT binary patch\nliteral 3\nabc\n", frozenset({"blob"})),
        ("@@ -1 +1 @@\n+GIT binary patch\n", frozenset()),
        ("@@ -1 +1 @@\n Binary files a/blob and b/blob differ\n", frozenset()),
        ("Binary files a/other and b/other differ\n", frozenset()),
    ],
)
def test_binary_detection_uses_diff_metadata(body: str, expected: frozenset[str]) -> None:
    from _project.scripts.oracle_reviewers.diff import binary_files

    assert binary_files("diff --git a/blob b/blob\n" + body) == expected


def test_exposure_exemption_does_not_bypass_withdrawal_citations(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("prose\n", encoding="utf-8")
    verdict = _withdrawn_verdict("never a defect: docs/guide.md:1", ())
    assert (
        check(
            verdict,
            ships=True,
            harness="omp",
            brief_mode="file-list",
            workspace=tmp_path,
            required=["docs/guide.md"],
            run=READ,
            exempt_paths=["docs/*.md"],
        )
        is None
    )
    marked = mark_citations(verdict, tmp_path)
    assert marked.prior_defects[0].status == "not_fixed"
    assert UNVERIFIED_WITHDRAWAL in marked.prior_defects[0].evidence
