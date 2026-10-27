from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from _project.scripts.oracle_reviewers import absence, cli, omp_pin, runner
from _project.scripts.oracle_reviewers.commands import Invocation
from _project.scripts.oracle_reviewers.policy import Policy
from _project.scripts.oracle_reviewers.verdict import RESPONSE_PLACEHOLDERS

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


def _fake_omp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "omp"
    script.write_text(f"#!{sys.executable}\nimport sys\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv(omp_pin.BINARY_ENV, str(script))


def _envelope(payload: object) -> str:
    return json.dumps(
        {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": payload}]}}
    )


def _reads(*paths: str) -> str:
    lines = []
    for index, path in enumerate(paths):
        call = {"type": "toolCall", "id": f"r{index}", "name": "read", "arguments": {"path": path}}
        lines.append(json.dumps({"type": "message_end", "message": {"role": "assistant", "content": [call]}}))
        result = {"role": "toolResult", "toolCallId": f"r{index}", "toolName": "read", "isError": False}
        lines.append(json.dumps({"type": "message_end", "message": result}))
    return "\n".join(lines)


def _review(
    policy: Policy,
    tmp_path: Path,
    workspace: Path,
    head: str,
    prompt: str = "brief",
    *,
    reviewer: str = "small",
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


def _omp_says(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, reads: tuple[str, ...] = (), **over: object) -> None:
    stdout = "\n".join([_reads(*reads), _envelope(json.dumps({**VERDICT, **over}))])
    _fake_omp(tmp_path, monkeypatch, f"print({json.dumps(stdout)})")


def _omp_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stderr: str, exit_code: int) -> None:
    _fake_omp(tmp_path, monkeypatch, f"sys.stderr.write({stderr!r})\nraise SystemExit({exit_code})")


DEFECT = {"severity": "High", "file": "a.txt", "line": 1, "end_line": None, "title": "Wrong", "detail": "d"}


@skip_on_windows
def test_clean_verdict_is_recorded(policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, head = _workspace(tmp_path)
    _omp_says(tmp_path, monkeypatch)
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing is None
    assert outcome.verdict == {**VERDICT, "defect_count": 0}


@skip_on_windows
def test_workspace_change_makes_the_reviewer_absent(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    body = f"open('{workspace}/b.txt', 'w').write('x')\nprint({json.dumps(_envelope(json.dumps(VERDICT)))})"
    _fake_omp(tmp_path, monkeypatch, body)
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing is not None and outcome.missing.kind == absence.INVALID
    assert "changed the workspace" in outcome.missing.detail


@skip_on_windows
def test_wrong_head_is_refused_before_running(policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, _ = _workspace(tmp_path)
    _fake_omp(tmp_path, monkeypatch, "raise SystemExit('must not run')")
    outcome = _review(policy, tmp_path, workspace, "f" * 40)
    assert outcome.missing is not None and "not at the reviewed head" in outcome.missing.detail


@skip_on_windows
def test_truncated_or_empty_brief_is_absent(policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, head = _workspace(tmp_path)
    _fake_omp(tmp_path, monkeypatch, "raise SystemExit('must not run')")
    outcome = _review(policy, tmp_path, workspace, head, prompt="")
    assert outcome.missing is not None and outcome.missing.kind == absence.INVALID


@skip_on_windows
def test_missing_pinned_binary_is_an_error_absence_even_with_omp_on_path(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    _fake_omp(tmp_path, monkeypatch, f"print({json.dumps(_envelope(json.dumps(VERDICT)))})")
    monkeypatch.delenv(omp_pin.BINARY_ENV)
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.verdict is None
    assert outcome.missing is not None and outcome.missing.kind == absence.ERROR
    assert "pinned omp is not installed" in outcome.missing.detail


@skip_on_windows
def test_prose_output_is_invalid(policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, head = _workspace(tmp_path)
    _fake_omp(tmp_path, monkeypatch, "print('Looks good to me!')")
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing is not None and outcome.missing.kind == absence.INVALID


@skip_on_windows
def test_envelope_thinking_blocks_are_ignored(policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, head = _workspace(tmp_path)
    envelope = json.dumps(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "hmm"},
                    {"type": "text", "text": json.dumps(VERDICT)},
                ],
            },
        }
    )
    _fake_omp(tmp_path, monkeypatch, f"print({json.dumps(envelope)})")
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing is None
    assert outcome.verdict == {**VERDICT, "defect_count": 0}


@skip_on_windows
def test_diagnostic_is_sanitized(policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, head = _workspace(tmp_path)
    _fake_omp(tmp_path, monkeypatch, "sys.stderr.write('token sk-ant-oat01-' + 'A' * 40 + '\\n')\nsys.exit(3)")
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing is not None and outcome.missing.detail == "exit code 3"
    assert "AAAAAAAAAA" not in outcome.diagnostic and "[redacted]" in outcome.diagnostic


@skip_on_windows
def test_diagnostic_is_an_excerpt_not_the_raw_output(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    body = "print('model said private things')\nsys.stderr.write('trace 1\\ntrace 2\\nfatal: quota\\n')\nsys.exit(1)"
    _fake_omp(tmp_path, monkeypatch, body)
    monkeypatch.setenv("META_API_KEY", "should-not-appear")
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.diagnostic == "fatal: quota"


@skip_on_windows
def test_an_unparsable_answer_keeps_the_end_of_the_final_message(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    monkeypatch.setenv("OMP_GATEWAY_TOKEN", "broker-secret-value")
    answer = "word " * 700 + json.dumps(VERDICT) + "\ntoken broker-secret-value\nAll done."
    _fake_omp(
        tmp_path, monkeypatch, f"print({json.dumps(_envelope('Let me look.'))})\nprint({json.dumps(_envelope(answer))})"
    )
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing is not None and outcome.missing.kind == absence.INVALID
    kept, _, trace = outcome.diagnostic.partition(" | trace: ")
    assert kept.startswith("\u2026") and kept.endswith("All done.")
    assert trace.startswith("reads=0 greps=0")
    assert "broker-secret-value" not in outcome.diagnostic and "[redacted]" in outcome.diagnostic
    assert "Let me look." not in outcome.diagnostic
    assert len(kept) <= absence.UNPARSED_LIMIT + 1


@skip_on_windows
def test_an_empty_final_message_keeps_the_stderr_excerpt(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    body = f"print({json.dumps(_reads('a.txt'))})\nsys.stderr.write('error: stream ended after a tool call\\n')"
    _fake_omp(tmp_path, monkeypatch, body)
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing == absence.Absence(absence.EMPTY, "the reviewer ended without a final answer")
    assert outcome.diagnostic.startswith("error: stream ended after a tool call | trace: reads=1 greps=0")


@skip_on_windows
def test_a_final_message_of_prose_is_invalid_not_empty(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    _fake_omp(
        tmp_path, monkeypatch, f"print({json.dumps(_reads('a.txt'))})\nprint({json.dumps(_envelope('All done.'))})"
    )
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing is not None and outcome.missing.kind == absence.INVALID
    assert outcome.missing.detail.startswith("the reviewer output is not JSON")


@skip_on_windows
def test_rate_limit_error_is_quota_with_its_reset(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    _omp_fails(
        tmp_path,
        monkeypatch,
        '{"type":"error","error":{"type":"rate_limit_error","message":"slow down"}} retry-after-ms=1000 Resets in 2h\n',
        1,
    )
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing is not None and outcome.missing.kind == absence.QUOTA
    assert outcome.missing.reset_at == NOW + timedelta(hours=2)


@skip_on_windows
def test_a_provider_rate_limit_event_on_stdout_is_quota_with_its_reset(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    message = {
        "role": "assistant",
        "content": [],
        "stopReason": "error",
        "errorStatus": 429,
        "errorMessage": "429 rate_limit_error: usage limit reached. Resets in 2h",
    }
    event = json.dumps({"type": "message_end", "message": message})
    _fake_omp(tmp_path, monkeypatch, f"print({json.dumps(event)})\nraise SystemExit(1)")
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing is not None and outcome.missing.kind == absence.QUOTA
    assert outcome.missing.reset_at == NOW + timedelta(hours=2)


@skip_on_windows
def test_a_provider_authentication_event_on_stdout_is_auth(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    message = {
        "role": "assistant",
        "content": [],
        "stopReason": "error",
        "errorStatus": 401,
        "errorMessage": "401 Unauthorized",
    }
    event = json.dumps({"type": "message_end", "message": message})
    _fake_omp(tmp_path, monkeypatch, f"print({json.dumps(event)})\nraise SystemExit(1)")
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing is not None and outcome.missing.kind == absence.AUTH


@skip_on_windows
def test_assistant_text_claiming_quota_or_auth_in_a_failed_run_is_an_error(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    claim = "429 rate_limit_error Resets in 2h 401 Unauthorized invalid_grant"
    message = {
        "role": "assistant",
        "content": [{"type": "text", "text": claim}],
        "stopReason": "error",
        "errorMessage": "stream disconnected",
    }
    event = json.dumps({"type": "message_end", "message": message})
    _fake_omp(tmp_path, monkeypatch, f"print({json.dumps(event)})\nraise SystemExit(1)")
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing == absence.Absence(absence.ERROR, "exit code 1")


@skip_on_windows
def test_signal_text_echoed_in_reviewer_output_is_an_error_not_an_absence_to_retry(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    echoed = {"type": "message_end", "message": {"text": "+| 401 | token mismatch | rate_limit_error invalid_grant"}}
    _fake_omp(
        tmp_path,
        monkeypatch,
        f"print({json.dumps(json.dumps(echoed))})\nsys.stderr.write('connection reset\\n')\nraise SystemExit(1)",
    )
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing == absence.Absence(absence.ERROR, "exit code 1")


@skip_on_windows
def test_unclassified_failure_names_its_exit_code(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    _omp_fails(tmp_path, monkeypatch, "stream disconnected before completion\n", 1)
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing is not None and outcome.missing.detail == "exit code 1"
    assert outcome.diagnostic == "stream disconnected before completion"


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


def test_staged_diff_keeps_carriage_returns_byte_for_byte(tmp_path: Path) -> None:
    workspace, _ = _workspace(tmp_path)
    source = tmp_path / "diff.patch"
    data = b"diff --git a/a.txt b/a.txt\n+one\r+two\r\n+three\n"
    source.write_bytes(data)
    staged = runner.stage_pull_request_diff(source, workspace)
    assert staged is not None
    assert staged.read_bytes() == data


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


def _classification() -> dict:
    return {
        "size_bucket": "small",
        "capability": "routine",
        "template": "standard",
        "confidence": 0.9,
        "gate_flags": [],
        "files": [],
    }


def _plan_dir(
    policy: Policy,
    tmp_path: Path,
    head: str,
    scope: str,
    brief_mode: str,
    diff: str | None,
    inline: str | None,
    prior: tuple[dict, ...] = (),
) -> Path:
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    reviewed = [
        {
            "path": "a.txt",
            "previous_path": None,
            "additions": 1,
            "deletions": 0,
            "status": "modified",
        }
    ]
    plan = {
        "repo": "o/n",
        "pr": 7,
        "base_sha": "a" * 40,
        "head_sha": head,
        "brief_mode": brief_mode,
        "brief_max_bytes": 100000,
        "max_defects": 10,
        "scope": scope,
        "reviewed_head": None,
        "restart_summary": None,
        "evidence_files": ["a.txt"],
        "max_attempts": policy.max_attempts,
        "chain": [policy.reviewers["small"].to_json()],
        "reviewed_files": reviewed,
        "unchanged_files": [],
        "untracked_files": [],
        "diff_inline": inline,
        "protocol": {"prior": list(prior)},
        "pool_blocked_until": {},
    }
    (plan_dir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (plan_dir / "classification.json").write_text(json.dumps(_classification()), encoding="utf-8")
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
            "small",
            "--harness",
            "omp",
            "--workspace",
            str(workspace),
            "--scratch",
            str(tmp_path / "scratch"),
            "--out-dir",
            str(tmp_path / "attempt"),
            "--classification",
            str(plan_path.parent / "classification.json"),
        ]
    )
    return json.loads((tmp_path / "attempt" / "attempt-1.json").read_text(encoding="utf-8"))


def _capture_prompt_body(tmp_path: Path) -> str:
    stdout = "\n".join([_reads("a.txt"), _envelope(json.dumps(VERDICT))])
    return f"open('{tmp_path}/prompt.txt', 'w').write(sys.argv[-1])\nprint({json.dumps(stdout)})"


@skip_on_windows
def test_a_file_list_review_stages_the_whole_diff(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    plan_path = _plan_dir(policy, tmp_path, head, "full", "file-list", "diff --git a/a.txt b/a.txt\n+whole\n", None)
    _fake_omp(tmp_path, monkeypatch, _capture_prompt_body(tmp_path))
    artifact = _run_review(plan_path, workspace, tmp_path, monkeypatch)
    staged = workspace / runner.STAGED_DIFF_NAME
    prompt = (tmp_path / "prompt.txt").read_text(encoding="utf-8")
    assert f"The whole pull request diff is at {staged};" in prompt
    assert "FILE PRIORITY" in prompt and "CHANGE DATA" in prompt
    assert "+whole" in staged.read_text(encoding="utf-8")
    assert artifact["outcome"] == "verdict"


@skip_on_windows
def test_a_file_list_review_records_the_reviewer_unavailable_when_the_diff_cannot_be_staged(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    (workspace / runner.STAGED_DIFF_NAME).write_text("from the pull request\n", encoding="utf-8")
    plan_path = _plan_dir(policy, tmp_path, head, "full", "file-list", "diff --git a/a.txt b/a.txt\n+whole\n", None)
    _fake_omp(tmp_path, monkeypatch, _capture_prompt_body(tmp_path))
    artifact = _run_review(plan_path, workspace, tmp_path, monkeypatch)
    assert artifact["outcome"] != "verdict"
    assert artifact["verdict"] is None
    assert "could not be staged" in json.dumps(artifact)
    assert not (tmp_path / "prompt.txt").exists()
    assert (workspace / runner.STAGED_DIFF_NAME).read_text(encoding="utf-8") == "from the pull request\n"


@skip_on_windows
def test_a_file_created_between_the_check_and_the_write_is_neither_followed_nor_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, _ = _workspace(tmp_path)
    outside = tmp_path / "outside.txt"
    source = tmp_path / "diff.patch"
    source.write_text("+x\n", encoding="utf-8")

    def racing_lexists(path: object) -> bool:
        (workspace / runner.STAGED_DIFF_NAME).symlink_to(outside)
        return False

    monkeypatch.setattr(runner.os.path, "lexists", racing_lexists)
    assert runner.stage_pull_request_diff(source, workspace) is None
    assert not outside.exists()
    assert (workspace / runner.STAGED_DIFF_NAME).is_symlink()


@skip_on_windows
def test_an_inline_review_embeds_the_diff_and_stages_nothing(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    plan_path = _plan_dir(policy, tmp_path, head, "full", "inline", "diff --git a/a.txt b/a.txt\n+whole\n", "+whole\n")
    _fake_omp(tmp_path, monkeypatch, _capture_prompt_body(tmp_path))
    _run_review(plan_path, workspace, tmp_path, monkeypatch)
    assert not (workspace / runner.STAGED_DIFF_NAME).exists()
    assert "+whole" in (tmp_path / "prompt.txt").read_text(encoding="utf-8")


@skip_on_windows
def test_a_reviewer_that_reports_incomplete_is_absent_with_its_reason(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    _omp_says(tmp_path, monkeypatch, status="incomplete", decision="NONE", incomplete_reason="could not read a.txt")
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
    _omp_says(tmp_path, monkeypatch, decision="SHIP", defects=[{**DEFECT, **defect}])
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
    _omp_says(tmp_path, monkeypatch, decision="SHIP_WITH_FIXES", defects=[{**DEFECT, "file": "../outside.txt"}])
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing == absence.Absence(absence.INVALID, "file must be a repository-relative path")


@skip_on_windows
def test_a_reviewer_cannot_supply_its_own_defect_count(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    _omp_says(tmp_path, monkeypatch, decision="SHIP_WITH_FIXES", defect_count=3)
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.missing == absence.Absence(absence.INVALID, "defect_count is set by the oracle, not the reviewer")


@skip_on_windows
def test_a_defect_citing_a_real_line_is_recorded(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    _omp_says(tmp_path, monkeypatch, decision="SHIP_WITH_FIXES", defects=[DEFECT])
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
    _omp_says(tmp_path, monkeypatch, reads=("a.txt", "b.txt"), files_examined=["a.txt"])
    short = _review(policy, tmp_path, workspace, head, brief_mode="file-list", required=("a.txt", "b.txt"))
    assert short.missing == absence.Absence(
        absence.INCOMPLETE, "the verdict would ship but did not examine b.txt", unread=("b.txt",)
    )
    inline = _review(policy, tmp_path, workspace, head, brief_mode="inline", required=("a.txt", "b.txt"))
    assert inline.missing is None


@skip_on_windows
def test_a_file_list_ship_must_have_read_every_file_it_claims(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _two_files(tmp_path)
    _omp_says(tmp_path, monkeypatch, reads=("a.txt:1-5",), files_examined=["a.txt", "b.txt"])
    outcome = _review(policy, tmp_path, workspace, head, brief_mode="file-list", required=("a.txt", "b.txt"))
    assert outcome.missing == absence.Absence(
        absence.INCOMPLETE, "the verdict would ship but its trace shows no read of b.txt", unread=("b.txt",)
    )
    _omp_says(tmp_path, monkeypatch, files_examined=["a.txt", "b.txt"])
    unread = _review(policy, tmp_path, workspace, head, brief_mode="file-list", required=("a.txt", "b.txt"))
    assert unread.missing == absence.Absence(
        absence.INCOMPLETE,
        "the verdict would ship but its trace shows no successful file read",
        unread=("a.txt", "b.txt"),
    )


@skip_on_windows
def test_absolute_examined_paths_inside_the_workspace_count(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    resolved = str(workspace.resolve() / "a.txt")
    _omp_says(tmp_path, monkeypatch, reads=(resolved,), files_examined=[resolved])
    outcome = _review(policy, tmp_path, workspace, head, brief_mode="file-list", required=("a.txt",))
    assert outcome.missing is None


@skip_on_windows
def test_a_do_not_ship_is_never_discarded_for_missing_read_evidence(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    rework = {"decision": "DO_NOT_SHIP", "summary": "rework", "files_examined": []}
    _omp_says(tmp_path, monkeypatch, **rework)
    outcome = _review(policy, tmp_path, workspace, head, brief_mode="file-list", required=("a.txt",))
    assert outcome.missing is None and outcome.verdict is not None
    assert outcome.verdict["decision"] == "DO_NOT_SHIP"


@skip_on_windows
def test_a_prior_defect_status_is_kept_in_the_recorded_verdict(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    answered = [{"id": "D1", "status": "fixed", "evidence": "line 1"}]
    _omp_says(tmp_path, monkeypatch, prior_defects=answered)
    outcome = _review(policy, tmp_path, workspace, head, rules={"prior": [{"id": "D1"}]})
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
    _fake_omp(tmp_path, monkeypatch, f"{body}\nprint({json.dumps(_envelope(json.dumps(VERDICT)))})")
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


@skip_on_windows
def test_a_follow_up_ship_with_an_unchanged_file_defect_counts_correctly(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _two_files(tmp_path)
    _omp_says(tmp_path, monkeypatch, reads=("a.txt",), defects=[OUTSIDE])
    outcome = _review(policy, tmp_path, workspace, head, brief_mode="file-list", required=("a.txt",), rules=FOLLOW_UP)
    judged = _judged(outcome, FOLLOW_UP)
    assert judged.decision == "SHIP"
    assert [item.title for item in judged.not_counted] == ["Unchanged"]


@skip_on_windows
def test_a_follow_up_defect_in_a_changed_file_needs_no_reads(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _two_files(tmp_path)
    _omp_says(tmp_path, monkeypatch, decision="SHIP", defects=[INSIDE, OUTSIDE])
    outcome = _review(policy, tmp_path, workspace, head, brief_mode="file-list", required=("a.txt",), rules=FOLLOW_UP)
    judged = _judged(outcome, FOLLOW_UP)
    assert judged.decision == "SHIP_WITH_FIXES"
    assert [item.title for item in judged.defects] == ["Changed"]


@skip_on_windows
def test_a_follow_up_leaving_a_prior_defect_open_needs_no_reads(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _two_files(tmp_path)
    prior = {"id": "D1", "severity": "High", "file": "a.txt", "line": 1, "end_line": None, "title": "Old"}
    rules = {**FOLLOW_UP, "prior": [prior]}
    _omp_says(tmp_path, monkeypatch, defects=[OUTSIDE])
    outcome = _review(policy, tmp_path, workspace, head, brief_mode="file-list", required=("a.txt",), rules=rules)
    assert _judged(outcome, rules).decision == "SHIP_WITH_FIXES"


@skip_on_windows
@pytest.mark.parametrize("defect", [INSIDE, OUTSIDE], ids=["changed-file", "other-file"])
def test_a_first_round_file_list_defect_needs_no_reads(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: dict
) -> None:
    workspace, head = _two_files(tmp_path)
    _omp_says(tmp_path, monkeypatch, decision="SHIP", defects=[defect])
    outcome = _review(policy, tmp_path, workspace, head, brief_mode="file-list", required=("a.txt",))
    assert _judged(outcome, {}).decision == "SHIP_WITH_FIXES"


@pytest.mark.parametrize(("examined", "accepted"), [([], False), (["a.txt"], True)], ids=["unlisted", "listed"])
def test_a_follow_up_ship_with_an_unchanged_file_defect_must_list_the_changed_files(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, examined: list[str], accepted: bool
) -> None:
    workspace, head = _two_files(tmp_path)
    _omp_says(tmp_path, monkeypatch, reads=("a.txt",), files_examined=examined, defects=[OUTSIDE])
    outcome = _review(policy, tmp_path, workspace, head, brief_mode="file-list", required=("a.txt",), rules=FOLLOW_UP)
    if accepted:
        assert _judged(outcome, FOLLOW_UP).decision == "SHIP"
    else:
        assert outcome.missing == absence.Absence(
            absence.INCOMPLETE, "the verdict would ship but did not examine a.txt", unread=("a.txt",)
        )


@skip_on_windows
def test_a_follow_up_prompt_carries_only_verified_prior_ids_in_an_unfilled_template(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    prior = {
        "id": "D4",
        "severity": "High",
        "file": "prior/path.py",
        "line": 3,
        "end_line": None,
        "title": "PRIOR-TITLE",
    }
    plan_path = _plan_dir(
        policy, tmp_path, head, "full", "inline", "diff --git a/a.txt b/a.txt\n+x\n", "+x\n", (prior,)
    )
    _fake_omp(tmp_path, monkeypatch, _capture_prompt_body(tmp_path))
    _run_review(plan_path, workspace, tmp_path, monkeypatch)
    prompt = (tmp_path / "prompt.txt").read_text(encoding="utf-8")
    nonce = re.search(r"([0-9a-f]{16})-BEGIN FILE PRIORITY", prompt).group(1)
    assert prompt.count(nonce) == 4
    change_start = prompt.index(f"{nonce}-BEGIN CHANGE DATA")
    change_end = prompt.index(f"{nonce}-END CHANGE DATA")
    section = prompt[change_start:change_end]
    after = prompt[change_end:]
    assert "PRIOR-TITLE" in section and "prior/path.py" in section
    assert "PRIOR-TITLE" not in prompt[:change_start] and "PRIOR-TITLE" not in after
    assert "prior/path.py" not in prompt[:change_start] and "prior/path.py" not in after
    template = json.loads(after[after.index("{") : after.rindex("}") + 1])
    assert [item["id"] for item in template["prior_defects"]] == ["D4"]
    placeholders = set(RESPONSE_PLACEHOLDERS.values())
    assert template["prior_defects"][0]["status"] in placeholders
    assert template["prior_defects"][0]["evidence"] in placeholders


@skip_on_windows
def test_an_evidence_failure_keeps_a_bounded_redacted_trace_diagnostic(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _two_files(tmp_path)
    _omp_says(tmp_path, monkeypatch, reads=("a.txt",), files_examined=["a.txt", "b.txt"])
    outcome = _review(policy, tmp_path, workspace, head, brief_mode="file-list", required=("a.txt", "b.txt"))
    assert outcome.missing is not None and outcome.missing.kind == absence.INCOMPLETE
    assert "trace: reads=1 greps=0" in outcome.diagnostic
    assert "required=2" in outcome.diagnostic and "missing=[b.txt]" in outcome.diagnostic
    assert len(outcome.diagnostic) < 1100


@skip_on_windows
def test_a_plain_read_and_single_file_grep_complete_a_file_list_verdict(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _two_files(tmp_path)
    _omp_says(tmp_path, monkeypatch, reads=("a.txt?q=.version", "a.txt"), files_examined=["a.txt", "b.txt"])
    missing = _review(policy, tmp_path, workspace, head, brief_mode="file-list", required=("a.txt", "b.txt"))
    assert missing.missing is not None and "no read of b.txt" in missing.missing.detail
    grep_call = {"type": "toolCall", "id": "g1", "name": "grep", "arguments": {"path": "b.txt"}}
    grep_events = [
        {"type": "message_end", "message": {"role": "assistant", "content": [grep_call]}},
        {
            "type": "message_end",
            "message": {
                "role": "toolResult",
                "toolCallId": "g1",
                "toolName": "grep",
                "isError": False,
                "content": [{"type": "text", "text": "b.txt\n*1|b"}],
            },
        },
    ]
    verdict = {**VERDICT, "files_examined": ["a.txt", "b.txt"]}
    stdout = "\n".join([_reads("a.txt"), *(json.dumps(event) for event in grep_events), _envelope(json.dumps(verdict))])
    _fake_omp(tmp_path, monkeypatch, f"print({json.dumps(stdout)})")
    done = _review(policy, tmp_path, workspace, head, brief_mode="file-list", required=("a.txt", "b.txt"))
    assert done.missing is None


@skip_on_windows
def test_a_credential_in_the_final_verdict_text_never_reaches_the_recorded_verdict(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "fakecred-Aa1Bb2Cc3Dd4"
    monkeypatch.setenv("OPENAI_CODEX_OAUTH_TOKEN", secret)
    workspace, head = _workspace(tmp_path)
    _omp_says(tmp_path, monkeypatch, summary=f"saw {secret} and {secret[:6]}\x01{secret[6:]}")
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.verdict is not None
    assert secret not in json.dumps(outcome.verdict)
    assert outcome.verdict["decision"] == "SHIP"
    _omp_says(tmp_path, monkeypatch, files_examined=[f"{secret}.txt"])
    invalid = _review(policy, tmp_path, workspace, head)
    assert invalid.missing is not None and invalid.missing.kind == absence.INVALID
    assert secret not in invalid.missing.detail + invalid.diagnostic


@skip_on_windows
def test_the_omp_event_stream_is_never_excerpted_into_the_diagnostic(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    _omp_says(tmp_path, monkeypatch, summary="tool output must stay out")
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.verdict is not None
    assert outcome.diagnostic.startswith("trace: reads=0 greps=0")
    assert "tool output must stay out" not in outcome.diagnostic


def test_capture_redacts_a_token_split_across_text_blocks(monkeypatch: pytest.MonkeyPatch, policy: Policy) -> None:
    token = "tok-" + "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH"
    monkeypatch.setenv("OMP_GATEWAY_TOKEN", token)
    blocks = [{"type": "text", "text": token[i : i + 16]} for i in range(0, len(token), 16)]
    event = {"type": "message_end", "message": {"role": "assistant", "content": blocks}}
    result = runner.RunResult(0, json.dumps(event) + "\n", "", False)
    captured = runner.capture(replace(policy.reviewers["small"], harness="omp"), result)
    message = captured["events"][0]["message"]
    assert token not in "".join(block["text"] for block in message["content"])
    assert token not in json.dumps(captured)
    assert "[redacted]" in json.dumps(captured)


@skip_on_windows
def test_a_capture_crash_keeps_the_completed_verdict(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, head = _workspace(tmp_path)
    _omp_says(tmp_path, monkeypatch)

    def explode(*_: object) -> dict[str, object]:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(runner, "capture", explode)
    outcome = _review(policy, tmp_path, workspace, head)
    assert outcome.verdict is not None
    assert outcome.verdict["decision"] == "SHIP"
    assert outcome.capture is None
