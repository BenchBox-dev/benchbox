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

from _project.scripts.oracle_reviewers import attempts, cli, credentials, disk_scan, omp_pin, processes, runner
from _project.scripts.oracle_reviewers.absence import ERROR, INVALID, TIMEOUT, Absence
from _project.scripts.oracle_reviewers.commands import OMP_BASE_ENV, Invocation, omp_env
from _project.scripts.oracle_reviewers.policy import Policy, Reviewer
from _project.scripts.oracle_reviewers.selection import JOB_FAILURE_DETAIL

from .loop_process import Journal
from .test_attempts import RUN_ID, _classification, _plan

REPO_ROOT = Path(__file__).resolve().parents[4]

pytestmark = [pytest.mark.unit, pytest.mark.fast]

GATEWAY_URL = "https://gateway-host.example.ts.net:4000"
GATEWAY_TOKEN = "gwtok-" + "a1B2c3D4" * 6
TYPESAFE_KEY = "tsk-live-" + "Zy9Xw8Vu" * 4
JOB_TOKEN = "ghs_" + "Q1w2E3r4" * 5
SECRET_ENV = {
    "OMP_GATEWAY_TOKEN": GATEWAY_TOKEN,
    "TYPESAFE_API_KEY": TYPESAFE_KEY,
    "ORACLE_GATEWAY_URL": GATEWAY_URL,
    "TS_OAUTH_CI_CLIENT_ID": "kXq7Lm2NpR4tVw9",
    "TS_OAUTH_CI_SECRET": "tskey-client-" + "kXq7Lm2NpR4tVw9-Zb3Yc8Wd5Xe",
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
        self.fakes: list[dict[str, Any]] = []
        self.output_text = ""
        self.environ_after: dict[str, str | None] = {}
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

    def fake(self, name: str, **params: Any) -> None:
        self.fakes.append({"name": name, "params": params})

    def run(self, *command: str) -> int:
        spec = self.tmp_path / "loop-spec.json"
        environ_after = self.tmp_path / "environ-after.json"
        spec.write_text(json.dumps({"fakes": self.fakes, "environ_after": str(environ_after)}), encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.unit.scripts.oracle_reviewers.loop_process",
                str(spec),
                *(command or self.attempts_command()),
            ],
            cwd=REPO_ROOT,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT), "MUTANT_UNDER_TEST": "", "TYPESAFE_API_KEY": ""},
            capture_output=True,
            text=True,
            check=False,
        )
        self.output_text = result.stdout + result.stderr
        if environ_after.exists():
            self.environ_after = json.loads(environ_after.read_text(encoding="utf-8"))
        return result.returncode

    def attempts_command(self) -> list[str]:
        return [
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

    def recover_command(self) -> list[str]:
        return [
            "recover",
            "--plan",
            str(self.plan_path),
            "--attempts-dir",
            str(self.attempts),
            "--workspace",
            str(self.workspace),
        ]

    def run_with_recovery(self) -> int:
        status = self.run()
        while status == 0:
            before = len(list(self.attempts.glob("attempt-*.json")))
            if self.run(*self.recover_command()) != 0:
                return 1
            status = self.run()
            if len(list(self.attempts.glob("attempt-*.json"))) == before:
                break
        return status

    def journal(self, name: str) -> Journal:
        return Journal(self.tmp_path / f"{name}.journal")

    def artifact(self, slot: int) -> dict[str, Any]:
        return json.loads((self.attempts / attempts.artifact_name(slot)).read_text(encoding="utf-8"))

    def ran(self) -> str:
        values = dict(line.split("=", 1) for line in self.output.read_text(encoding="utf-8").splitlines())
        return values["attempts"]


@pytest.fixture
def loop(tmp_path: Path, policy: Policy, monkeypatch: pytest.MonkeyPatch) -> Loop:
    return Loop(tmp_path, policy, monkeypatch)


def _use_script_reviewer(loop: Loop, monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    loop.fake("script_reviewer", script=script, cwd=str(loop.tmp_path))
    monkeypatch.setenv(omp_pin.BINARY_ENV, sys.executable)


def _gone(pid: int) -> bool:
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def test_each_attempt_gets_a_fresh_home_and_the_previous_one_is_deleted(loop: Loop) -> None:
    journal = loop.journal("reviews")
    loop.fake("recording_review", journal=str(journal.path), workspace=str(loop.workspace))
    assert loop.run_with_recovery() == 0
    entries = journal.items()
    homes = [Path(entry["home"]) for entry in entries]
    assert len(homes) == 2
    assert homes[0] != homes[1]
    assert [entry["earlier_home_exists"] for entry in entries] == [False, False]
    assert [entry["models_name_the_gateway"] for entry in entries] == [True, True]
    assert not homes[0].exists() and not homes[1].exists()
    assert loop.environ_after == {"HOME": os.environ.get("HOME"), "TMPDIR": os.environ.get("TMPDIR")}
    assert loop.ran() == "2"
    assert loop.artifact(1)["absence"]["kind"] == INVALID and loop.artifact(2)["absence"]["kind"] == INVALID


def test_a_reviewer_that_crashes_still_leaves_a_result_file_for_its_slot(loop: Loop) -> None:
    loop.fake("crashing_review")
    assert loop.run() == 0
    data = loop.artifact(1)
    assert data["outcome"] == "absent"
    assert (data["absence"]["kind"], data["absence"]["detail"]) == (ERROR, JOB_FAILURE_DETAIL)
    assert loop.artifact(2)["absence"]["detail"] == JOB_FAILURE_DETAIL
    assert GATEWAY_TOKEN not in json.dumps(data)
    assert loop.ran() == "2"


def test_a_reviewer_that_times_out_still_leaves_a_result_file_for_its_slot(
    loop: Loop, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_script_reviewer(loop, monkeypatch, "import time; time.sleep(60)")
    loop.fake("short_timeout", seconds=1)
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
    _use_script_reviewer(loop, monkeypatch, script)
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


def _adopting_child(mode: str, *args: str) -> Any:
    result = subprocess.run(
        [sys.executable, "-m", "tests.unit.scripts.oracle_reviewers.loop_process", "adopt", mode, *args],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT), "MUTANT_UNDER_TEST": ""},
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="orphan adoption needs a Linux subreaper")
def test_a_detached_grandchild_is_found_through_the_subreaper(tmp_path: Path) -> None:
    script = (
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import os, time; os.setsid(); time.sleep(120)'],"
        " stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    )

    survivors = _adopting_child("detached-grandchild", str(tmp_path), script)
    assert survivors
    assert all(_gone(pid) for pid in survivors)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="orphan adoption needs a Linux subreaper")
def test_a_bystander_child_of_the_loop_is_not_a_leaked_reviewer_process(tmp_path: Path) -> None:
    report = _adopting_child("bystander", str(tmp_path))
    assert report == {"exit_code": 0, "bystander_alive": True}


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="orphan adoption needs a Linux subreaper")
def test_reaping_after_a_leak_leaves_the_loops_other_children_waitable(tmp_path: Path) -> None:
    report = _adopting_child("reap-after-leak", str(tmp_path))
    assert report == {"leaked": True, "sibling_waitable": True}


def test_the_loop_stops_after_an_invalid_attempt_until_its_recovery_is_recorded(loop: Loop) -> None:
    journal = loop.journal("reviews")
    loop.fake("recording_review", journal=str(journal.path), workspace=str(loop.workspace))
    assert loop.run() == 0
    assert loop.ran() == "1" and len(journal.items()) == 1
    assert loop.artifact(1)["recovery"] is None
    assert loop.run() == 0
    assert loop.ran() == "1" and len(journal.items()) == 1
    assert loop.run(*loop.recover_command()) == 0
    assert loop.artifact(1)["recovery"]["source"] == "fallback"
    assert loop.run() == 0
    assert loop.ran() == "2" and len(journal.items()) == 2


def test_the_workspace_is_reset_between_attempts(loop: Loop) -> None:
    journal = loop.journal("reviews")
    loop.fake("recording_review", journal=str(journal.path), workspace=str(loop.workspace))
    assert loop.run_with_recovery() == 0
    assert [entry["staged_diff_present"] for entry in journal.items()] == [False, False]


def test_the_loop_stops_when_the_classification_is_unreadable(loop: Loop) -> None:
    loop.classification.write_text("not json", encoding="utf-8")
    ran = loop.tmp_path / "reviewer-ran"
    loop.fake("forbidden_review", marker=str(ran))
    assert loop.run() == 0
    assert not ran.exists()
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
    loop: Loop, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_script_reviewer(loop, monkeypatch, "print('{}')")
    loop.fake("unreadable_snapshot")
    assert loop.run() == 1
    assert loop.artifact(1)["absence"]["detail"] == cli.LEAK_DETAIL
    assert not (loop.attempts / attempts.artifact_name(2)).exists()


def test_the_process_list_failing_to_start_is_reported_as_a_failed_exit_check(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError("ps")

    monkeypatch.setattr(processes.subprocess, "Popen", refuse)
    with pytest.raises(processes.HarnessLeakError, match="process list"):
        processes.snapshot()


def test_the_loop_fails_closed_on_linux_when_it_cannot_adopt_orphans(loop: Loop) -> None:
    ran = loop.tmp_path / "reviewer-ran"
    loop.fake("cannot_adopt_orphans")
    loop.fake("forbidden_review", marker=str(ran))
    assert loop.run() == 1
    assert "could not become a child subreaper" in loop.output_text
    assert not ran.exists()
    assert not (loop.attempts / attempts.artifact_name(1)).exists()


def test_each_attempt_gets_its_own_temporary_directory_inside_its_home(loop: Loop) -> None:
    journal = loop.journal("reviews")
    original = os.environ.get("TMPDIR")
    loop.fake("recording_review", journal=str(journal.path), workspace=str(loop.workspace))
    assert loop.run_with_recovery() == 0
    entries = journal.items()
    assert len(entries) == 2
    assert all(Path(entry["tmpdir"]) == Path(entry["home"]) / "tmp" for entry in entries)
    assert entries[0]["tmpdir"] != entries[1]["tmpdir"]
    assert loop.environ_after["TMPDIR"] == original


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
