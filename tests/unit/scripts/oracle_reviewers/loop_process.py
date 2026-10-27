from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from _project.scripts.oracle_reviewers import cli, processes, runner
from _project.scripts.oracle_reviewers.absence import INVALID, Absence
from _project.scripts.oracle_reviewers.commands import Invocation
from _project.scripts.oracle_reviewers.policy import Reviewer


class Journal:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, item: Any) -> None:
        with self.path.open("a", encoding="utf-8") as sink:
            sink.write(json.dumps(item) + "\n")

    def items(self) -> list[Any]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]


def _unparseable() -> runner.ReviewOutcome:
    return runner.ReviewOutcome(None, Absence(INVALID, "unparseable"), "")


def recording_review(journal: str, workspace: str) -> None:
    log = Journal(Path(journal))
    staged = Path(workspace) / runner.STAGED_DIFF_NAME

    def review(reviewer: Reviewer, **kwargs: Any) -> runner.ReviewOutcome:
        home = Path(os.environ["HOME"])
        models = home / ".omp" / "agent" / "models.yml"
        log.append(
            {
                "home": str(home),
                "tmpdir": os.environ["TMPDIR"],
                "earlier_home_exists": any(Path(entry["home"]).exists() for entry in log.items()),
                "models_name_the_gateway": models.is_file()
                and os.environ["ORACLE_GATEWAY_URL"] in models.read_text(encoding="utf-8"),
                "staged_diff_present": staged.exists(),
            }
        )
        staged.write_text("diff", encoding="utf-8")
        return _unparseable()

    runner.review = review


def crashing_review() -> None:
    def review(reviewer: Reviewer, **kwargs: Any) -> runner.ReviewOutcome:
        raise RuntimeError(f"boom {os.environ['OMP_GATEWAY_TOKEN']}")

    runner.review = review


def forbidden_review(marker: str) -> None:
    def review(reviewer: Reviewer, **kwargs: Any) -> runner.ReviewOutcome:
        Path(marker).write_text("a reviewer ran", encoding="utf-8")
        return _unparseable()

    runner.review = review


def script_reviewer(script: str, cwd: str) -> None:
    def build(reviewer: Reviewer, workspace: Path, prompt: str, scratch: Path) -> Invocation:
        return Invocation((sys.executable, "-c", script), Path(cwd), None, dict(os.environ))

    runner.build = build


def home_recording_build(journal: str) -> None:
    log = Journal(Path(journal))

    def build(reviewer: Reviewer, workspace: Path, prompt: str, scratch: Path) -> Invocation:
        log.append(os.environ["HOME"])
        return Invocation((sys.executable, "-c", "print('not a verdict')"), scratch, None, dict(os.environ))

    runner.build = build


def short_timeout(seconds: int) -> None:
    original = runner.execute
    runner.execute = lambda invocation, timeout_seconds: original(invocation, seconds)


def unreadable_snapshot() -> None:
    def unreadable() -> list[processes.Process]:
        raise processes.HarnessLeakError((), "the process list could not be read")

    processes.snapshot = unreadable


def cannot_adopt_orphans() -> None:
    processes.IS_LINUX = True
    processes.become_subreaper = lambda: False


def deleted_tempdir(path: str) -> None:
    tempfile.tempdir = path


FAKES: dict[str, Callable[..., None]] = {
    "recording_review": recording_review,
    "crashing_review": crashing_review,
    "forbidden_review": forbidden_review,
    "script_reviewer": script_reviewer,
    "home_recording_build": home_recording_build,
    "short_timeout": short_timeout,
    "unreadable_snapshot": unreadable_snapshot,
    "cannot_adopt_orphans": cannot_adopt_orphans,
    "deleted_tempdir": deleted_tempdir,
}


def main(spec_path: Path, argv: list[str]) -> int:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for fake in spec["fakes"]:
        FAKES[fake["name"]](**fake["params"])
    status = cli.main(argv)
    Path(spec["environ_after"]).write_text(
        json.dumps({"HOME": os.environ.get("HOME"), "TMPDIR": os.environ.get("TMPDIR")}), encoding="utf-8"
    )
    return status


def _detached_grandchild(cwd: Path, script: str) -> list[int]:
    invocation = Invocation((sys.executable, "-c", script), cwd, None, dict(os.environ))
    try:
        runner.execute(invocation, 30)
    except processes.HarnessLeakError as raised:
        return list(raised.survivors)
    return []


def _bystander(cwd: Path) -> dict[str, Any]:
    bystander = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        invocation = Invocation((sys.executable, "-c", "print('done')"), cwd, None, dict(os.environ))
        result = runner.execute(invocation, 30)
        return {"exit_code": result.exit_code, "bystander_alive": bystander.poll() is None}
    finally:
        bystander.kill()
        bystander.wait()


def _reap_after_leak(cwd: Path) -> dict[str, bool]:
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    for _ in range(100):
        rows = [row for row in processes.snapshot() if row.pid == finished.pid]
        if rows and rows[0].state.startswith(processes.ZOMBIE):
            break
        time.sleep(0.05)
    script = (
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'],"
        " stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    )
    invocation = Invocation((sys.executable, "-c", script), cwd, None, dict(os.environ))
    leaked = False
    try:
        runner.execute(invocation, 30)
    except processes.HarnessLeakError:
        leaked = True
    return {"leaked": leaked, "sibling_waitable": os.waitpid(finished.pid, 0)[0] == finished.pid}


def adopt(mode: str, args: list[str]) -> int:
    assert processes.become_subreaper()
    try:
        cwd = Path(args[0])
        if mode == "detached-grandchild":
            report: Any = _detached_grandchild(cwd, args[1])
        elif mode == "bystander":
            report = _bystander(cwd)
        else:
            report = _reap_after_leak(cwd)
    finally:
        processes.release_subreaper()
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    if sys.argv[1] == "adopt":
        sys.exit(adopt(sys.argv[2], sys.argv[3:]))
    sys.exit(main(Path(sys.argv[1]), sys.argv[2:]))
