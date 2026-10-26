from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import attempts, cli, credentials, disk_scan, processes, runner
from _project.scripts.oracle_reviewers.absence import ERROR, INVALID, TIMEOUT, Absence
from _project.scripts.oracle_reviewers.commands import OMP_BASE_ENV, Invocation, omp_env
from _project.scripts.oracle_reviewers.policy import Policy, Reviewer
from _project.scripts.oracle_reviewers.selection import JOB_FAILURE_DETAIL

from .test_attempts import RUN_ID, _classification, _plan

pytestmark = [pytest.mark.unit, pytest.mark.fast]

GATEWAY_URL = "https://gateway-host.example.ts.net:4000"
GATEWAY_TOKEN = "gwtok-" + "a1B2c3D4" * 6
TYPESAFE_KEY = "tsk-live-" + "Zy9Xw8Vu" * 4
JOB_TOKEN = "ghs_" + "Q1w2E3r4" * 5
SECRET_ENV = {
    "OMP_GATEWAY_TOKEN": GATEWAY_TOKEN,
    "TYPESAFE_API_KEY": TYPESAFE_KEY,
    "ORACLE_GATEWAY_URL": GATEWAY_URL,
    "TS_OAUTH_CLIENT_ID": "kXq7Lm2NpR4tVw9",
    "TS_AUDIENCE": "api.tailscale.com/kXq7Lm2NpR4tVw9",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "eyJhbGciOi" + "Pq7Rs8Tu" * 6,
}


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class Loop:
    def __init__(self, tmp_path: Path, policy: Policy, monkeypatch: pytest.MonkeyPatch, max_attempts: int = 2) -> None:
        self.tmp_path = tmp_path
        self.workspace = tmp_path / "workspace"
        self.workspace.mkdir()
        _git(self.workspace, "init", "--quiet")
        (self.workspace / "a.py").write_text("a = 1\n", encoding="utf-8")
        _git(self.workspace, "add", "a.py")
        _git(self.workspace, "commit", "--quiet", "-m", "seed")
        head = _git(self.workspace, "rev-parse", "HEAD")
        self.plan = {**_plan(policy, ("small", "medium")), "head_sha": head, "max_attempts": max_attempts}
        self.plan_path = tmp_path / "plan" / "plan.json"
        self.plan_path.parent.mkdir()
        self.plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
        self.classification = tmp_path / "classification.json"
        self.classification.write_text(json.dumps(_classification("small").to_json()), encoding="utf-8")
        self.attempts = tmp_path / "attempts"
        self.output = tmp_path / "github-output.txt"
        self.output.write_text("", encoding="utf-8")
        monkeypatch.setenv("GITHUB_RUN_ID", RUN_ID)
        monkeypatch.setenv("GITHUB_OUTPUT", str(self.output))
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        monkeypatch.setenv("ORACLE_GATEWAY_URL", GATEWAY_URL)
        monkeypatch.setenv("OMP_GATEWAY_TOKEN", GATEWAY_TOKEN)
        monkeypatch.setattr(cli, "_assemble_prompt", lambda *args, **kwargs: "review this")

    def run(self) -> int:
        return cli.main(
            [
                "attempts",
                "--plan",
                str(self.plan_path),
                "--attempts-dir",
                str(self.attempts),
                "--classification",
                str(self.classification),
                "--workspace",
                str(self.workspace),
                "--home-root",
                str(self.tmp_path),
            ]
        )

    def artifact(self, slot: int) -> dict[str, Any]:
        return json.loads((self.attempts / attempts.artifact_name(slot)).read_text(encoding="utf-8"))

    def ran(self) -> str:
        values = dict(line.split("=", 1) for line in self.output.read_text(encoding="utf-8").splitlines())
        return values["attempts"]


@pytest.fixture
def loop(tmp_path: Path, policy: Policy, monkeypatch: pytest.MonkeyPatch) -> Loop:
    return Loop(tmp_path, policy, monkeypatch)


def _script_invocation(tmp_path: Path, script: str) -> Any:
    def build(reviewer: Reviewer, workspace: Path, prompt: str, scratch: Path) -> Invocation:
        return Invocation((sys.executable, "-c", script), tmp_path, None, dict(os.environ))

    return build


def _use_script_reviewer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, script: str) -> None:
    monkeypatch.setattr(runner, "build", _script_invocation(tmp_path, script))
    monkeypatch.setattr(runner.shutil, "which", lambda name: sys.executable)


def _gone(pid: int) -> bool:
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def test_each_attempt_gets_a_fresh_home_and_the_previous_one_is_deleted(
    loop: Loop, monkeypatch: pytest.MonkeyPatch
) -> None:
    homes: list[Path] = []
    seen_before: list[bool] = []
    original_home = os.environ.get("HOME")

    def fake_review(reviewer: Reviewer, **kwargs: Any) -> runner.ReviewOutcome:
        home = Path(os.environ["HOME"])
        seen_before.append(any(previous.exists() for previous in homes))
        assert (home / ".omp" / "agent" / "models.yml").is_file()
        assert GATEWAY_URL in (home / ".omp" / "agent" / "models.yml").read_text(encoding="utf-8")
        homes.append(home)
        return runner.ReviewOutcome(None, Absence(INVALID, "unparseable"), "")

    monkeypatch.setattr(runner, "review", fake_review)
    assert loop.run() == 0
    assert len(homes) == 2
    assert homes[0] != homes[1]
    assert seen_before == [False, False]
    assert not homes[0].exists() and not homes[1].exists()
    assert os.environ.get("HOME") == original_home
    assert loop.ran() == "2"
    assert loop.artifact(1)["absence"]["kind"] == INVALID and loop.artifact(2)["absence"]["kind"] == INVALID


def test_a_reviewer_that_crashes_still_leaves_a_result_file_for_its_slot(
    loop: Loop, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(reviewer: Reviewer, **kwargs: Any) -> runner.ReviewOutcome:
        raise RuntimeError(f"boom {GATEWAY_TOKEN}")

    monkeypatch.setattr(runner, "review", crash)
    assert loop.run() == 0
    data = loop.artifact(1)
    assert data["outcome"] == "absent"
    assert (data["absence"]["kind"], data["absence"]["detail"]) == (ERROR, JOB_FAILURE_DETAIL)
    assert loop.artifact(2)["absence"]["detail"] == JOB_FAILURE_DETAIL
    assert GATEWAY_TOKEN not in json.dumps(data)
    assert loop.ran() == "2"


def test_a_reviewer_that_times_out_still_leaves_a_result_file_for_its_slot(
    loop: Loop, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _use_script_reviewer(monkeypatch, tmp_path, "import time; time.sleep(60)")
    original = runner.execute
    monkeypatch.setattr(runner, "execute", lambda invocation, timeout_seconds: original(invocation, 1))
    assert loop.run() == 0
    data = loop.artifact(1)
    assert data["outcome"] == "absent"
    assert data["absence"]["kind"] == TIMEOUT


def test_a_reviewer_that_leaves_a_child_running_fails_the_review_closed_and_the_child_is_killed(
    loop: Loop, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pid_file = tmp_path / "child.pid"
    script = (
        "import subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'],"
        " stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "print('{}')\n"
    )
    _use_script_reviewer(monkeypatch, tmp_path, script)
    assert loop.run() == 1
    child = int(pid_file.read_text(encoding="utf-8"))
    assert _gone(child)
    data = loop.artifact(1)
    assert data["outcome"] == "absent"
    assert data["absence"]["detail"] == cli.LEAK_DETAIL
    assert not (loop.attempts / attempts.artifact_name(2)).exists()
    assert loop.ran() == "1"


def test_a_reviewer_that_exits_cleanly_leaves_nothing_to_kill(tmp_path: Path) -> None:
    invocation = Invocation((sys.executable, "-c", "print('done')"), tmp_path, None, dict(os.environ))
    result = runner.execute(invocation, 30)
    assert result.exit_code == 0 and not result.timed_out


def test_execute_raises_for_a_survivor_and_kills_it(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    script = (
        "import subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'],"
        " stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
    )
    invocation = Invocation((sys.executable, "-c", script), tmp_path, None, dict(os.environ))
    with pytest.raises(processes.HarnessLeakError) as raised:
        runner.execute(invocation, 30)
    assert int(pid_file.read_text(encoding="utf-8")) in raised.value.survivors
    assert _gone(int(pid_file.read_text(encoding="utf-8")))


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="orphan adoption needs a Linux subreaper")
def test_a_detached_grandchild_is_found_through_the_subreaper(tmp_path: Path) -> None:
    script = (
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import os, time; os.setsid(); time.sleep(120)'],"
        " stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    )
    assert processes.become_subreaper()
    try:
        invocation = Invocation((sys.executable, "-c", script), tmp_path, None, dict(os.environ))
        with pytest.raises(processes.HarnessLeakError) as raised:
            runner.execute(invocation, 30)
    finally:
        processes.release_subreaper()
    assert raised.value.survivors
    assert all(_gone(pid) for pid in raised.value.survivors)


def test_the_workspace_is_reset_between_attempts(loop: Loop, monkeypatch: pytest.MonkeyPatch) -> None:
    staged = loop.workspace / runner.STAGED_DIFF_NAME
    states: list[bool] = []

    def fake_review(reviewer: Reviewer, **kwargs: Any) -> runner.ReviewOutcome:
        states.append(staged.exists())
        staged.write_text("diff", encoding="utf-8")
        return runner.ReviewOutcome(None, Absence(INVALID, "unparseable"), "")

    monkeypatch.setattr(runner, "review", fake_review)
    assert loop.run() == 0
    assert states == [False, False]


def test_the_loop_stops_when_the_classification_is_unreadable(loop: Loop, monkeypatch: pytest.MonkeyPatch) -> None:
    loop.classification.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(runner, "review", lambda *args, **kwargs: pytest.fail("no reviewer may run"))
    assert loop.run() == 0
    assert loop.ran() == "0"
    assert not loop.attempts.joinpath(attempts.artifact_name(1)).exists()


def test_a_disk_hit_on_the_typesafe_value_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "home"
    (root / "deep").mkdir(parents=True)
    (root / "deep" / "notes.txt").write_text(f"prefix {TYPESAFE_KEY} suffix", encoding="utf-8")
    (tmp_path / "clean").mkdir()
    monkeypatch.setenv("TYPESAFE_API_KEY", TYPESAFE_KEY)
    monkeypatch.setenv("JOB_TOKEN", JOB_TOKEN)
    base = ["scan-disk", "--env", "TYPESAFE_API_KEY", "--env", "JOB_TOKEN"]
    assert cli.main([*base, "--root", str(tmp_path / "clean")]) == 0
    assert cli.main([*base, "--root", str(root), "--root", str(tmp_path / "clean")]) == 1


def test_a_disk_hit_on_the_job_token_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "script.sh").write_text(f"export GH_TOKEN={JOB_TOKEN}\n", encoding="utf-8")
    monkeypatch.setenv("TYPESAFE_API_KEY", TYPESAFE_KEY)
    monkeypatch.setenv("JOB_TOKEN", JOB_TOKEN)
    assert cli.main(["scan-disk", "--root", str(tmp_path), "--env", "TYPESAFE_API_KEY", "--env", "JOB_TOKEN"]) == 1


def test_the_disk_scan_finds_a_value_split_across_read_chunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(disk_scan, "CHUNK_BYTES", 16)
    data = b"x" * 13 + TYPESAFE_KEY.encode() + b"y" * 40
    (tmp_path / "blob.bin").write_bytes(data)
    hits = disk_scan.scan([tmp_path], {"TYPESAFE_API_KEY": TYPESAFE_KEY.encode()})
    assert [(name, path.name) for name, path in hits] == [("TYPESAFE_API_KEY", "blob.bin")]


def test_the_disk_scan_does_not_follow_symlinks_and_ignores_an_unset_value(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text(TYPESAFE_KEY, encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    assert disk_scan.scan([root], {"TYPESAFE_API_KEY": TYPESAFE_KEY.encode()}) == []
    assert disk_scan.secret_values(["TYPESAFE_API_KEY"], {}) == {}
    assert disk_scan.scan([outside], {}) == []


def _control_split(value: str) -> str:
    return value[:5] + "\x01\x02" + value[5:]


def _results(tmp_path: Path, planted: str) -> Path:
    directory = tmp_path / "results"
    directory.mkdir()
    (directory / "attempt-1.json").write_text(
        json.dumps(
            {
                "verdict": {"summary": f"seen {planted} here", "defects": [{"detail": planted}]},
                "diagnostic": f"stderr: {planted}",
                "absence": {"kind": "error", "detail": f"detail {planted}"},
                "slot": 1,
            }
        ),
        encoding="utf-8",
    )
    return directory


@pytest.mark.parametrize("name", [*SECRET_ENV, "JOB_TOKEN"])
@pytest.mark.parametrize("split", [False, True])
def test_every_secret_the_job_held_is_redacted_from_the_results(
    name: str, split: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = JOB_TOKEN if name == "JOB_TOKEN" else SECRET_ENV[name]
    monkeypatch.setenv(name, value)
    planted = _control_split(value) if split else value
    directory = _results(tmp_path, planted)
    assert cli.main(["redact-check", "--path", str(directory), "--env", "JOB_TOKEN"]) == 0
    text = (directory / "attempt-1.json").read_text(encoding="utf-8")
    assert value not in text
    assert credentials.REDACTED in text
    assert not credentials.contains(json.loads(text)["verdict"]["summary"])


def test_a_value_that_cannot_be_redacted_fails_the_review_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", TYPESAFE_KEY)
    directory = tmp_path / "results"
    directory.mkdir()
    (directory / "attempt-1.json").write_text(json.dumps({TYPESAFE_KEY: "key name"}), encoding="utf-8")
    assert cli.main(["redact-check", "--path", str(directory)]) == 1


def test_a_results_file_that_is_not_json_fails_the_review_closed(tmp_path: Path) -> None:
    directory = tmp_path / "results"
    directory.mkdir()
    (directory / "attempt-1.json").write_text("{not json", encoding="utf-8")
    assert cli.main(["redact-check", "--path", str(directory)]) == 1


def test_clean_results_are_left_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", TYPESAFE_KEY)
    directory = _results(tmp_path, "nothing secret")
    before = (directory / "attempt-1.json").read_text(encoding="utf-8")
    assert cli.main(["redact-check", "--path", str(directory)]) == 0
    assert (directory / "attempt-1.json").read_text(encoding="utf-8") == before


def test_the_reviewer_environment_stays_the_allowlist_while_the_loop_holds_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in SECRET_ENV.items():
        monkeypatch.setenv(name, value)
    assert set(omp_env()) <= {*OMP_BASE_ENV, "OMP_GATEWAY_TOKEN"}


def test_a_leftover_that_keeps_the_output_open_is_found_without_waiting_for_the_timeout(tmp_path: Path) -> None:
    script = (
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], stdin=subprocess.DEVNULL)\n"
    )
    invocation = Invocation((sys.executable, "-c", script), tmp_path, None, dict(os.environ))
    started = time.monotonic()
    with pytest.raises(processes.HarnessLeakError) as raised:
        runner.execute(invocation, 60)
    assert time.monotonic() - started < 30
    assert raised.value.survivors
    assert all(_gone(pid) for pid in raised.value.survivors)


def test_the_loop_fails_closed_when_the_process_list_cannot_be_read(
    loop: Loop, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _use_script_reviewer(monkeypatch, tmp_path, "print('{}')")

    def unreadable() -> list[processes.Process]:
        raise processes.HarnessLeakError((), "the process list could not be read")

    monkeypatch.setattr(processes, "snapshot", unreadable)
    assert loop.run() == 1
    assert loop.artifact(1)["absence"]["detail"] == cli.LEAK_DETAIL
    assert not (loop.attempts / attempts.artifact_name(2)).exists()


def test_the_process_list_failing_to_start_is_reported_as_a_failed_exit_check(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError("ps")

    monkeypatch.setattr(processes.subprocess, "Popen", refuse)
    with pytest.raises(processes.HarnessLeakError, match="process list"):
        processes.snapshot()


def test_the_loop_fails_closed_on_linux_when_it_cannot_adopt_orphans(
    loop: Loop, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(processes, "IS_LINUX", True)
    monkeypatch.setattr(processes, "become_subreaper", lambda: False)
    monkeypatch.setattr(runner, "review", lambda *args, **kwargs: pytest.fail("no reviewer may run"))
    assert loop.run() == 1
    assert not (loop.attempts / attempts.artifact_name(1)).exists()


def test_each_attempt_gets_its_own_temporary_directory_inside_its_home(
    loop: Loop, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[Path, Path]] = []
    original = os.environ.get("TMPDIR")

    def fake_review(reviewer: Reviewer, **kwargs: Any) -> runner.ReviewOutcome:
        seen.append((Path(os.environ["HOME"]), Path(os.environ["TMPDIR"])))
        return runner.ReviewOutcome(None, Absence(INVALID, "unparseable"), "")

    monkeypatch.setattr(runner, "review", fake_review)
    assert loop.run() == 0
    assert all(tmp == home / "tmp" for home, tmp in seen)
    assert seen[0][1] != seen[1][1]
    assert os.environ.get("TMPDIR") == original


def test_the_disk_scan_finds_the_job_token_inside_a_basic_auth_header(tmp_path: Path) -> None:
    header = base64.b64encode(f"x-access-token:{JOB_TOKEN}".encode()).decode()
    (tmp_path / "config").write_text(f"http.extraheader = AUTHORIZATION: basic {header}\n", encoding="utf-8")
    needles = disk_scan.secret_values(["JOB_TOKEN"], {"JOB_TOKEN": JOB_TOKEN})
    hits = disk_scan.scan([tmp_path], needles)
    assert [name for name, _ in hits] == ["JOB_TOKEN (as a basic-auth header)"]


def test_nested_scan_roots_are_walked_once(tmp_path: Path) -> None:
    inner = tmp_path / "work" / "checkout"
    inner.mkdir(parents=True)
    (inner / "leak.txt").write_text(TYPESAFE_KEY, encoding="utf-8")
    hits = disk_scan.scan([tmp_path, inner, tmp_path / "work"], {"TYPESAFE_API_KEY": TYPESAFE_KEY.encode()})
    assert len(hits) == 1
