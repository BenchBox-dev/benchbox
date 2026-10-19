from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from _project.scripts.oracle_reviewers import absence, cli, runner
from _project.scripts.oracle_reviewers.brief import (
    FILE_LIST_DIFF_LINE,
    FULL_DIFF_LINE,
    READ_RULE_PLACEHOLDER,
    READ_RULES,
)
from _project.scripts.oracle_reviewers.commands import Invocation
from _project.scripts.oracle_reviewers.policy import Policy

pytestmark = [pytest.mark.unit, pytest.mark.medium]
skip_on_windows = pytest.mark.skipif(
    sys.platform == "win32",
    reason="fake reviewer CLIs are POSIX shebang scripts, PATH is colon-joined, and runner.execute uses os.killpg",
)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
VERDICT = {
    "status": "complete",
    "incomplete_reason": "",
    "decision": "SHIP",
    "summary": "fine",
    "files_examined": ["a.txt"],
    "defects": [],
    "prior_defects": [],
}


def _workspace(tmp_path: Path) -> tuple[Path, str]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("a\n", encoding="utf-8")
    for args in (
        ("init", "-q"),
        ("add", "a.txt"),
        ("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "init"),
    ):
        subprocess.run(["git", "-C", str(workspace), *args], check=True)
    head = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return workspace, head


def _fake_muse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, name: str = "muse") -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / name
    script.write_text(f"#!{sys.executable}\nimport sys\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")


def _review(
    policy: Policy,
    tmp_path: Path,
    workspace: Path,
    head: str,
    prompt: str = "brief",
    *,
    reviewer: str = "muse-routine",
    brief_mode: str = "inline",
    required: tuple[str, ...] = (),
    rules: dict | None = None,
):
    plan = {"brief_mode": brief_mode, "evidence_files": list(required), "max_defects": 10, "protocol": rules or {}}
    return runner.review(
        policy.reviewers[reviewer],
        workspace=workspace,
        head_sha=head,
        prompt=prompt,
        scratch=tmp_path / "s",
        now=NOW,
        plan=plan,
    )


def _muse_says(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **over: object) -> None:
    _fake_muse(tmp_path, monkeypatch, f"print({json.dumps(json.dumps({**VERDICT, **over}))})")


def _claude_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reads: list[tuple[str, bool]], **over: object
) -> None:
    events: list[dict] = []
    for index, (path, error) in enumerate(reads):
        use = {"type": "tool_use", "id": f"t{index}", "name": "Read", "input": {"file_path": path}}
        events.append({"type": "assistant", "message": {"content": [use]}})
        result = {"type": "tool_result", "tool_use_id": f"t{index}", "is_error": error, "content": "x"}
        events.append({"type": "user", "message": {"content": [result]}})
    structured = {"type": "tool_use", "id": "s", "name": "StructuredOutput", "input": {}}
    events.append({"type": "assistant", "message": {"content": [structured]}})
    events.append(
        {"type": "result", "is_error": False, "num_turns": len(reads) + 2, "structured_output": {**VERDICT, **over}}
    )
    lines = "\n".join(json.dumps(event) for event in events)
    _fake_muse(tmp_path, monkeypatch, f"print({json.dumps(lines)})", name="claude")


def _codex_says(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, events: list[dict], **over: object) -> None:
    body = (
        f"open(sys.argv[sys.argv.index('-o') + 1], 'w').write({json.dumps(json.dumps({**VERDICT, **over}))})\n"
        f"for event in {events!r}:\n    print(__import__('json').dumps(event))"
    )
    _fake_muse(tmp_path, monkeypatch, body, name="codex")


def _command(text: str, output: str = "", exit_code: int = 0) -> dict:
    item = {"type": "command_execution", "command": text, "aggregated_output": output, "exit_code": exit_code}
    return {"type": "item.completed", "item": {**item, "status": "completed"}}


DEFECT = {"severity": "High", "file": "a.txt", "line": 1, "end_line": None, "title": "Wrong", "detail": "d"}


@skip_on_windows
def test_clean_verdict_is_recorded(policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, head = _workspace(tmp_path)
    _fake_muse(tmp_path, monkeypatch, f"print({json.dumps(json.dumps(VERDICT))})")
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing is None
    assert outcome.verdict == {**VERDICT, "defect_count": 0}


@skip_on_windows
def test_workspace_change_makes_the_reviewer_absent(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    body = f"open('{workspace}/b.txt', 'w').write('x')\nprint({json.dumps(json.dumps(VERDICT))})"
    _fake_muse(tmp_path, monkeypatch, body)
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing is not None and outcome.missing.kind == absence.INVALID
    assert "changed the workspace" in outcome.missing.detail


@skip_on_windows
def test_wrong_head_is_refused_before_running(policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, _ = _workspace(tmp_path)
    _fake_muse(tmp_path, monkeypatch, "raise SystemExit('must not run')")
    outcome = _review(policy, tmp_path, workspace, "f" * 40)
    assert outcome.missing is not None and "not at the reviewed head" in outcome.missing.detail


@skip_on_windows
def test_truncated_or_empty_brief_is_absent(policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, head = _workspace(tmp_path)
    _fake_muse(tmp_path, monkeypatch, "raise SystemExit('must not run')")
    outcome = _review(policy, tmp_path, workspace, head, prompt="")
    assert outcome.missing is not None and outcome.missing.kind == absence.INVALID


@skip_on_windows
def test_missing_cli_is_an_error_absence(policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, head = _workspace(tmp_path)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing is not None and outcome.missing.kind == absence.ERROR


@skip_on_windows
def test_prose_output_is_invalid(policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, head = _workspace(tmp_path)
    _fake_muse(tmp_path, monkeypatch, "print('Looks good to me!')")
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing is not None and outcome.missing.kind == absence.INVALID


@skip_on_windows
def test_diagnostic_is_sanitized(policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, head = _workspace(tmp_path)
    _fake_muse(tmp_path, monkeypatch, "sys.stderr.write('token sk-ant-oat01-' + 'A' * 40 + '\\n')\nsys.exit(3)")
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing is not None and outcome.missing.detail == "exit code 3"
    assert "AAAAAAAAAA" not in outcome.diagnostic and "[redacted]" in outcome.diagnostic


@skip_on_windows
def test_diagnostic_is_an_excerpt_not_the_raw_output(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    body = "print('model said private things')\nsys.stderr.write('trace 1\\ntrace 2\\nfatal: quota\\n')\nsys.exit(1)"
    _fake_muse(tmp_path, monkeypatch, body)
    monkeypatch.setenv("META_API_KEY", "should-not-appear")
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.diagnostic == "fatal: quota"


@skip_on_windows
def test_execute_kills_on_timeout(tmp_path: Path) -> None:
    invocation = Invocation((sys.executable, "-c", "import time; time.sleep(30)"), tmp_path, None)
    result = runner.execute(invocation, 1)
    assert result.timed_out is True


def _status(workspace: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(workspace), "status", "--porcelain", "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_staged_diff_is_readable_in_the_workspace_and_leaves_it_clean(tmp_path: Path) -> None:
    workspace, _ = _workspace(tmp_path)
    source = tmp_path / "diff.patch"
    source.write_text("diff --git a/a.txt b/a.txt\n+changed\n", encoding="utf-8")
    staged = runner.stage_pull_request_diff(source, workspace)
    assert staged == workspace / runner.STAGED_DIFF_NAME
    assert staged.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert staged.resolve().is_relative_to(workspace.resolve())
    assert _status(workspace) == ""


@pytest.mark.parametrize("content", [None, "", "  \n"])
def test_nothing_is_staged_without_a_diff(tmp_path: Path, content: str | None) -> None:
    workspace, _ = _workspace(tmp_path)
    source = tmp_path / "diff.patch"
    if content is not None:
        source.write_text(content, encoding="utf-8")
    assert runner.stage_pull_request_diff(source, workspace) is None
    assert not (workspace / runner.STAGED_DIFF_NAME).exists()


def test_a_pull_request_file_with_the_staging_name_is_never_overwritten(tmp_path: Path) -> None:
    workspace, _ = _workspace(tmp_path)
    (workspace / runner.STAGED_DIFF_NAME).write_text("from the pull request\n", encoding="utf-8")
    source = tmp_path / "diff.patch"
    source.write_text("+x\n", encoding="utf-8")
    assert runner.stage_pull_request_diff(source, workspace) is None
    assert (workspace / runner.STAGED_DIFF_NAME).read_text(encoding="utf-8") == "from the pull request\n"


def test_a_symlink_with_the_staging_name_is_never_followed(tmp_path: Path) -> None:
    workspace, _ = _workspace(tmp_path)
    outside = tmp_path / "outside.txt"
    (workspace / runner.STAGED_DIFF_NAME).symlink_to(outside)
    source = tmp_path / "diff.patch"
    source.write_text("+x\n", encoding="utf-8")
    assert runner.stage_pull_request_diff(source, workspace) is None
    assert not outside.exists()


def test_a_directory_that_is_not_a_repository_stages_nothing(tmp_path: Path) -> None:
    source = tmp_path / "diff.patch"
    source.write_text("+x\n", encoding="utf-8")
    plain = tmp_path / "plain"
    plain.mkdir()
    assert runner.stage_pull_request_diff(source, plain) is None


def _plan_dir(policy: Policy, tmp_path: Path, head: str, scope: str, brief: str, diff: str | None) -> Path:
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    plan = {
        "pr": 7,
        "head_sha": head,
        "brief_mode": "inline",
        "max_defects": 10,
        "scope": scope,
        "chain": [policy.reviewers["muse-routine"].to_json()],
    }
    (plan_dir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (plan_dir / "brief.md").write_text(brief, encoding="utf-8")
    if diff is not None:
        (plan_dir / "diff.patch").write_text(diff, encoding="utf-8")
    return plan_dir / "plan.json"


def _run_review(
    plan_path: Path,
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict:
    monkeypatch.setenv("GITHUB_RUN_ID", "99")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out.txt"))
    cli.main(
        [
            "review",
            "--plan",
            str(plan_path),
            "--slot",
            "1",
            "--reviewer",
            "muse-routine",
            "--harness",
            "muse",
            "--workspace",
            str(workspace),
            "--scratch",
            str(tmp_path / "scratch"),
            "--out-dir",
            str(tmp_path / "attempt"),
        ]
    )
    return json.loads((tmp_path / "attempt" / "attempt-1.json").read_text(encoding="utf-8"))


def _capture_prompt_body(tmp_path: Path) -> str:
    return f"open('{tmp_path}/prompt.txt', 'w').write(sys.argv[-1])\nprint({json.dumps(json.dumps(VERDICT))})"


@skip_on_windows
def test_a_scoped_review_gives_the_reviewer_a_readable_full_diff(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    brief = f"Intro.\n{FULL_DIFF_LINE}Files changed.\n"
    plan_path = _plan_dir(policy, tmp_path, head, "changed", brief, "diff --git a/a.txt b/a.txt\n+whole\n")
    _fake_muse(tmp_path, monkeypatch, _capture_prompt_body(tmp_path))
    artifact = _run_review(plan_path, workspace, tmp_path, monkeypatch)
    prompt = (tmp_path / "prompt.txt").read_text(encoding="utf-8")
    staged = workspace / runner.STAGED_DIFF_NAME
    assert f"The whole pull request diff is at {staged};" in prompt
    assert "+whole" in staged.read_text(encoding="utf-8")
    assert artifact["outcome"] == "verdict" and artifact["absence"] is None
    assert artifact["verdict"]["summary"] == VERDICT["summary"]


@skip_on_windows
def test_a_full_review_stages_no_diff(policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, head = _workspace(tmp_path)
    plan_path = _plan_dir(policy, tmp_path, head, "full", "Intro.\n", "diff --git a/a.txt b/a.txt\n+whole\n")
    _fake_muse(tmp_path, monkeypatch, _capture_prompt_body(tmp_path))
    _run_review(plan_path, workspace, tmp_path, monkeypatch)
    assert not (workspace / runner.STAGED_DIFF_NAME).exists()
    assert (tmp_path / "prompt.txt").read_text(encoding="utf-8") == "Intro.\n"


@skip_on_windows
def test_a_scoped_review_without_a_diff_drops_the_diff_line(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    plan_path = _plan_dir(policy, tmp_path, head, "changed", f"Intro.\n{FULL_DIFF_LINE}Files.\n", None)
    _fake_muse(tmp_path, monkeypatch, _capture_prompt_body(tmp_path))
    _run_review(plan_path, workspace, tmp_path, monkeypatch)
    assert (tmp_path / "prompt.txt").read_text(encoding="utf-8") == "Intro.\nFiles.\n"


@skip_on_windows
def test_a_reviewer_that_reports_incomplete_is_absent_with_its_reason(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    _muse_says(tmp_path, monkeypatch, status="incomplete", decision="NONE", incomplete_reason="could not read a.txt")
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.verdict is None
    assert outcome.missing == absence.Absence(absence.INCOMPLETE, "could not read a.txt")


@skip_on_windows
@pytest.mark.parametrize(
    ("defect", "note"),
    [
        (
            {"file": "invented.py"},
            "Citation not found in the head commit: invented.py is not a file in the head commit.",
        ),
        ({"line": 2}, "Citation not found in the head commit: a.txt has 1 lines, so line 2 does not exist."),
        ({"end_line": 9}, "Citation not found in the head commit: a.txt has 1 lines, so line 9 does not exist."),
    ],
)
def test_a_miscited_defect_is_kept_and_marked_so_the_change_still_fails(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: dict, note: str
) -> None:
    from _project.scripts.oracle_reviewers import protocol
    from _project.scripts.oracle_reviewers.verdict import validate

    workspace, head = _workspace(tmp_path)
    _muse_says(tmp_path, monkeypatch, decision="SHIP", defects=[{**DEFECT, **defect}])
    outcome = _review(policy, tmp_path, workspace, head, brief_mode="file-list", required=("a.txt",))
    assert outcome.missing is None and outcome.verdict is not None
    assert outcome.verdict["defects"][0]["detail"] == f"{note} d"
    judged = protocol.judge(validate(outcome.verdict, trusted=True), 10)
    assert judged.decision == "SHIP_WITH_FIXES"


@skip_on_windows
def test_a_path_outside_the_repository_is_rejected(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    _muse_says(tmp_path, monkeypatch, decision="SHIP_WITH_FIXES", defects=[{**DEFECT, "file": "../outside.txt"}])
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing == absence.Absence(absence.INVALID, "file must be a repository-relative path")


@skip_on_windows
def test_a_reviewer_cannot_supply_its_own_defect_count(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    _muse_says(tmp_path, monkeypatch, decision="SHIP_WITH_FIXES", defect_count=3)
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing == absence.Absence(absence.INVALID, "defect_count is set by the oracle, not the reviewer")


@skip_on_windows
def test_a_defect_citing_a_real_line_is_recorded(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    _muse_says(tmp_path, monkeypatch, decision="SHIP_WITH_FIXES", defects=[DEFECT])
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing is None and outcome.verdict is not None
    assert [item["file"] for item in outcome.verdict["defects"]] == ["a.txt"]


@skip_on_windows
def test_a_file_list_ship_must_examine_every_required_file(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    (workspace / "b.txt").write_text("b\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "b.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "b"],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    _muse_says(tmp_path, monkeypatch, files_examined=["a.txt"])
    short = _review(policy, tmp_path, workspace, head, brief_mode="file-list", required=("a.txt", "b.txt"))
    assert short.missing == absence.Absence(absence.INCOMPLETE, "the verdict would ship but did not examine b.txt")
    inline = _review(policy, tmp_path, workspace, head, brief_mode="inline", required=("a.txt", "b.txt"))
    assert inline.missing is None


@skip_on_windows
def test_absolute_examined_paths_inside_the_workspace_count(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    _muse_says(tmp_path, monkeypatch, files_examined=[str(workspace.resolve() / "a.txt")])
    outcome = _review(policy, tmp_path, workspace, head, brief_mode="file-list", required=("a.txt",))
    assert outcome.missing is None


@skip_on_windows
@pytest.mark.parametrize(
    ("reads", "brief_mode", "accepted"),
    [
        ([], "file-list", False),
        ([("a.txt", False)], "file-list", True),
        ([("{ws}/a.txt", False)], "file-list", True),
        ([("a.txt", True)], "file-list", False),
        ([("other.txt", False)], "file-list", False),
        ([], "inline", True),
    ],
    ids=["structured-output-only", "read", "absolute-read", "failed-read", "other-file", "inline"],
)
def test_claude_must_read_every_required_file_on_a_file_list_brief(
    policy: Policy,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reads: list[tuple[str, bool]],
    brief_mode: str,
    accepted: bool,
) -> None:
    workspace, head = _workspace(tmp_path)
    resolved = [(path.format(ws=workspace.resolve()), error) for path, error in reads]
    _claude_says(tmp_path, monkeypatch, resolved)
    outcome = _review(
        policy, tmp_path, workspace, head, reviewer="claude-deep", brief_mode=brief_mode, required=("a.txt",)
    )
    assert (outcome.missing is None and outcome.verdict is not None) is accepted
    if not accepted:
        assert outcome.missing is not None and outcome.missing.kind == absence.INCOMPLETE
        assert "trace shows no" in outcome.missing.detail


@skip_on_windows
def test_a_codex_ship_without_any_read_command_is_a_hollow_review(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    hollow = {"summary": "I could not review the files because I may not run commands.", "files_examined": ["a.txt"]}
    _codex_says(tmp_path, monkeypatch, [{"type": "turn.completed"}], **hollow)
    outcome = _review(
        policy, tmp_path, workspace, head, reviewer="codex-routine", brief_mode="file-list", required=("a.txt",)
    )
    assert outcome.verdict is None
    assert outcome.missing is not None and outcome.missing.kind == absence.INCOMPLETE
    assert outcome.missing.detail == "the verdict would ship but its trace shows no successful file read"


@skip_on_windows
@pytest.mark.parametrize(
    ("event", "accepted"),
    [
        (_command("/bin/zsh -lc 'cat -n a.txt'", "1 a"), True),
        (_command("/bin/zsh -lc 'rg -n x a.txt'", "1:a"), True),
        (_command("/bin/zsh -lc 'rg -n x .'", "./a.txt:1:a"), False),
        (_command("/bin/zsh -lc 'ls -R'", "a.txt"), False),
        (_command("/bin/zsh -lc 'cat .oracle-pull-request.diff'", "+++ b/a.txt"), False),
        (_command("/bin/zsh -lc 'cat -n a.txt'", "", exit_code=1), False),
    ],
    ids=["cat", "rg-on-file", "rg-on-directory", "listing", "staged-diff-only", "failed"],
)
def test_a_codex_file_list_review_needs_a_successful_read_of_each_required_file(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event: dict, accepted: bool
) -> None:
    workspace, head = _workspace(tmp_path)
    _codex_says(tmp_path, monkeypatch, [event])
    outcome = _review(
        policy, tmp_path, workspace, head, reviewer="codex-routine", brief_mode="file-list", required=("a.txt",)
    )
    assert (outcome.missing is None) is accepted


@skip_on_windows
def test_codex_quota_reported_in_the_event_stream_is_quota(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    error = {"type": "turn.failed", "error": {"message": "You've hit your usage limit. Resets in 2h"}}
    body = f"print(__import__('json').dumps({error!r}))\nraise SystemExit(1)"
    _fake_muse(tmp_path, monkeypatch, body, name="codex")
    outcome = _review(policy, tmp_path, workspace, head, reviewer="codex-routine")
    assert outcome.missing is not None and outcome.missing.kind == absence.QUOTA
    assert outcome.missing.reset_at == NOW + timedelta(hours=2)


def _codex_error_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, message: str) -> None:
    error = {"type": "error", "message": message}
    body = (
        f"print(__import__('json').dumps({error!r}))\n"
        "sys.stderr.write('Reading additional input from stdin...\\n')\nraise SystemExit(1)"
    )
    _fake_muse(tmp_path, monkeypatch, body, name="codex")


@skip_on_windows
def test_an_unclassified_codex_failure_names_its_error_event(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    _codex_error_event(tmp_path, monkeypatch, "stream disconnected before completion")
    outcome = _review(policy, tmp_path, workspace, head, reviewer="codex-routine")
    assert outcome.missing is not None and outcome.missing.detail == "exit code 1"
    assert outcome.diagnostic == "ERROR: stream disconnected before completion"


@skip_on_windows
def test_a_codex_quota_error_event_is_a_quota_absence(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    _codex_error_event(tmp_path, monkeypatch, "Quota exceeded. Check your plan and billing details.")
    outcome = _review(policy, tmp_path, workspace, head, reviewer="codex-routine")
    assert outcome.missing is not None and outcome.missing.kind == "quota"
    assert outcome.missing.detail == "ERROR: Quota exceeded. Check your plan and billing details."
    assert outcome.diagnostic == "ERROR: Quota exceeded. Check your plan and billing details."


@skip_on_windows
def test_repeated_muse_verdicts_are_recorded_once(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    text = json.dumps(VERDICT)
    _fake_muse(tmp_path, monkeypatch, f"print({json.dumps(text)})\nprint({json.dumps(text)})")
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing is None
    assert outcome.verdict == {**VERDICT, "defect_count": 0}


@skip_on_windows
def test_agy_without_its_api_key_is_an_auth_absence_before_running(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    _fake_muse(tmp_path, monkeypatch, "raise SystemExit('must not run')", name="agy")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    outcome = _review(policy, tmp_path, workspace, head, reviewer="gemini-routine")
    assert outcome.missing is not None and outcome.missing.kind == absence.AUTH
    assert outcome.missing.detail == "GEMINI_API_KEY is not set"


@skip_on_windows
def test_agy_runs_outside_the_pull_request_checkout(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    envelope = json.dumps({"status": "SUCCESS", "response": json.dumps(VERDICT), "num_turns": 2})
    body = f"import os\nassert not os.path.exists('a.txt'), os.getcwd()\nprint({json.dumps(envelope)})"
    _fake_muse(tmp_path, monkeypatch, body, name="agy")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-value")
    outcome = _review(policy, tmp_path, workspace, head, reviewer="gemini-routine")
    assert outcome.missing is None
    assert outcome.verdict == {**VERDICT, "defect_count": 0}


@skip_on_windows
def test_the_review_fills_the_read_rule_for_the_reviewers_harness(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    plan_path = _plan_dir(policy, tmp_path, head, "full", f"Rules: {READ_RULE_PLACEHOLDER}\n", None)
    _fake_muse(tmp_path, monkeypatch, _capture_prompt_body(tmp_path))
    _run_review(plan_path, workspace, tmp_path, monkeypatch)
    assert (tmp_path / "prompt.txt").read_text(encoding="utf-8") == f"Rules: {READ_RULES['muse']}\n"


@skip_on_windows
def test_a_file_list_review_stages_the_whole_diff(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    plan_path = _plan_dir(
        policy, tmp_path, head, "full", f"Intro. {FILE_LIST_DIFF_LINE}Read.\n", "diff --git a/a.txt b/a.txt\n+whole\n"
    )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan_path.write_text(json.dumps({**plan, "brief_mode": "file-list", "evidence_files": ["a.txt"]}), "utf-8")
    _fake_muse(tmp_path, monkeypatch, _capture_prompt_body(tmp_path))
    artifact = _run_review(plan_path, workspace, tmp_path, monkeypatch)
    staged = workspace / runner.STAGED_DIFF_NAME
    assert (tmp_path / "prompt.txt").read_text(encoding="utf-8") == (
        f"Intro. The whole pull request diff is at {staged}; read it with the changed files. Read.\n"
    )
    assert "+whole" in staged.read_text(encoding="utf-8")
    assert artifact["outcome"] == "verdict"


@skip_on_windows
@pytest.mark.parametrize("reviewer", ["codex-routine", "claude-deep", "muse-routine"])
def test_a_do_not_ship_is_never_discarded_for_missing_read_evidence(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reviewer: str
) -> None:
    workspace, head = _workspace(tmp_path)
    rework = {"decision": "DO_NOT_SHIP", "summary": "rework", "files_examined": []}
    if reviewer == "codex-routine":
        _codex_says(tmp_path, monkeypatch, [], **rework)
    elif reviewer == "claude-deep":
        _claude_says(tmp_path, monkeypatch, [], **rework)
    else:
        _muse_says(tmp_path, monkeypatch, **rework)
    outcome = _review(policy, tmp_path, workspace, head, reviewer=reviewer, brief_mode="file-list", required=("a.txt",))
    assert outcome.missing is None and outcome.verdict is not None
    assert outcome.verdict["decision"] == "DO_NOT_SHIP"


@skip_on_windows
def test_a_prior_defect_status_is_kept_in_the_recorded_verdict(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    answered = [{"id": "D1", "status": "fixed", "evidence": "line 1"}]
    _muse_says(tmp_path, monkeypatch, prior_defects=answered)
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing is None and outcome.verdict is not None
    assert [item["id"] for item in outcome.verdict["prior_defects"]] == ["D1"]


@skip_on_windows
@pytest.mark.parametrize(
    ("body", "changed"),
    [
        ("open(sys.argv[0].rsplit('/bin/', 1)[0] + '/workspace/.oracle-pull-request.diff', 'a').write('+x\\n')", True),
        ("", False),
    ],
    ids=["rewritten", "untouched"],
)
def test_a_reviewer_that_rewrites_the_staged_diff_is_invalid(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, changed: bool
) -> None:
    workspace, head = _workspace(tmp_path)
    source = tmp_path / "diff.patch"
    source.write_text("diff --git a/a.txt b/a.txt\n+whole\n", encoding="utf-8")
    staged = runner.stage_pull_request_diff(source, workspace)
    assert staged is not None
    _fake_muse(tmp_path, monkeypatch, f"{body}\nprint({json.dumps(json.dumps(VERDICT))})")
    outcome = _review(policy, tmp_path, workspace, head)
    if changed:
        assert outcome.missing == absence.Absence(absence.INVALID, "the reviewer changed the staged pull request diff")
    else:
        assert outcome.missing is None


FOLLOW_UP = {"kind": "follow-up", "changed": ["a.txt"], "prior": []}
OUTSIDE = {**DEFECT, "file": "b.txt", "severity": "Low", "title": "Unchanged"}
INSIDE = {**DEFECT, "title": "Changed"}


def _two_files(tmp_path: Path) -> tuple[Path, str]:
    workspace, _ = _workspace(tmp_path)
    (workspace / "b.txt").write_text("b\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "b.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "b"],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return workspace, head


def _judged(outcome: runner.ReviewOutcome, rules: dict):
    from _project.scripts.oracle_reviewers import protocol
    from _project.scripts.oracle_reviewers.verdict import validate

    assert outcome.missing is None and outcome.verdict is not None
    return protocol.judge_planned(validate(outcome.verdict, trusted=True), {"max_defects": 10, "protocol": rules})


@pytest.mark.parametrize("reviewer", ["codex-routine", "claude-deep"])
def test_a_follow_up_ship_with_only_an_unchanged_file_defect_still_needs_reads(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reviewer: str
) -> None:
    workspace, head = _two_files(tmp_path)
    if reviewer == "codex-routine":
        _codex_says(tmp_path, monkeypatch, [{"type": "turn.completed"}], defects=[OUTSIDE])
    else:
        _claude_says(tmp_path, monkeypatch, [], defects=[OUTSIDE])
    outcome = _review(
        policy,
        tmp_path,
        workspace,
        head,
        reviewer=reviewer,
        brief_mode="file-list",
        required=("a.txt",),
        rules=FOLLOW_UP,
    )
    assert outcome.verdict is None
    assert outcome.missing == absence.Absence(
        absence.INCOMPLETE, "the verdict would ship but its trace shows no successful file read"
    )


@pytest.mark.parametrize("reviewer", ["codex-routine", "claude-deep"])
def test_a_follow_up_that_read_the_changed_files_ships_with_the_unchanged_defect_not_counted(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reviewer: str
) -> None:
    workspace, head = _two_files(tmp_path)
    if reviewer == "codex-routine":
        _codex_says(tmp_path, monkeypatch, [_command("/bin/zsh -lc 'cat -n a.txt'", "1 a")], defects=[OUTSIDE])
    else:
        _claude_says(tmp_path, monkeypatch, [("a.txt", False)], defects=[OUTSIDE])
    outcome = _review(
        policy,
        tmp_path,
        workspace,
        head,
        reviewer=reviewer,
        brief_mode="file-list",
        required=("a.txt",),
        rules=FOLLOW_UP,
    )
    judged = _judged(outcome, FOLLOW_UP)
    assert judged.decision == "SHIP"
    assert [item.title for item in judged.not_counted] == ["Unchanged"]


def test_a_follow_up_defect_in_a_changed_file_needs_no_reads(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _two_files(tmp_path)
    _codex_says(tmp_path, monkeypatch, [{"type": "turn.completed"}], decision="SHIP", defects=[INSIDE, OUTSIDE])
    outcome = _review(
        policy,
        tmp_path,
        workspace,
        head,
        reviewer="codex-routine",
        brief_mode="file-list",
        required=("a.txt",),
        rules=FOLLOW_UP,
    )
    judged = _judged(outcome, FOLLOW_UP)
    assert judged.decision == "SHIP_WITH_FIXES"
    assert [item.title for item in judged.defects] == ["Changed"]


def test_a_follow_up_leaving_a_prior_defect_open_needs_no_reads(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _two_files(tmp_path)
    prior = {"id": "D1", "severity": "High", "file": "a.txt", "line": 1, "end_line": None, "title": "Old"}
    rules = {**FOLLOW_UP, "prior": [prior]}
    _codex_says(tmp_path, monkeypatch, [{"type": "turn.completed"}], defects=[OUTSIDE])
    outcome = _review(
        policy,
        tmp_path,
        workspace,
        head,
        reviewer="codex-routine",
        brief_mode="file-list",
        required=("a.txt",),
        rules=rules,
    )
    assert _judged(outcome, rules).decision == "SHIP_WITH_FIXES"


@pytest.mark.parametrize("defect", [INSIDE, OUTSIDE], ids=["changed-file", "other-file"])
def test_a_first_round_file_list_defect_needs_no_reads(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: dict
) -> None:
    workspace, head = _two_files(tmp_path)
    _codex_says(tmp_path, monkeypatch, [{"type": "turn.completed"}], decision="SHIP", defects=[defect])
    outcome = _review(
        policy, tmp_path, workspace, head, reviewer="codex-routine", brief_mode="file-list", required=("a.txt",)
    )
    assert _judged(outcome, {}).decision == "SHIP_WITH_FIXES"


@pytest.mark.parametrize(("examined", "accepted"), [([], False), (["a.txt"], True)], ids=["unlisted", "listed"])
def test_an_untraced_follow_up_ship_with_an_unchanged_file_defect_must_list_the_changed_files(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, examined: list[str], accepted: bool
) -> None:
    workspace, head = _two_files(tmp_path)
    _muse_says(tmp_path, monkeypatch, files_examined=examined, defects=[OUTSIDE])
    outcome = _review(policy, tmp_path, workspace, head, brief_mode="file-list", required=("a.txt",), rules=FOLLOW_UP)
    if accepted:
        assert _judged(outcome, FOLLOW_UP).decision == "SHIP"
    else:
        assert outcome.missing == absence.Absence(
            absence.INCOMPLETE, "the verdict would ship but did not examine a.txt"
        )
