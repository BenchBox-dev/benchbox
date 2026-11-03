from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from scripts.sqlglot_fork_refresh import (
    BUNDLE_FILE,
    MONITOR_TITLE,
    OPEN_FILE,
    STATE_FILE,
    authorization,
    open_pull_request,
    prepare,
    prepare_or_report,
    sync_issue,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

LATEST = {"current": {"tag": "v30.27.0"}, "latest_tag": "v30.29.0", "refresh_available": True}
UPSTREAM_SHA = "2" * 40


class FakeRunner:
    def __init__(self, handlers: dict[str, Any]) -> None:
        self.handlers = handlers
        self.calls: list[list[str]] = []
        self.environments: list[dict[str, str] | None] = []

    def __call__(
        self, args: Sequence[str], cwd: Path | None = None, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess:
        command = list(args)
        self.calls.append(command)
        self.environments.append(env)
        text = " ".join(command)
        for needle, response in self.handlers.items():
            if needle in text:
                result = response(command) if callable(response) else response
                if isinstance(result, subprocess.CompletedProcess):
                    return result
                return subprocess.CompletedProcess(command, 0, result, "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def ran(self, needle: str) -> list[list[str]]:
        return [call for call in self.calls if needle in " ".join(call)]


def lines(*items: dict[str, Any]) -> str:
    return "".join(json.dumps(item) + "\n" for item in items)


def refresh_handlers(latest: dict[str, Any], report: dict[str, Any] | None, exit_code: int = 0) -> dict[str, Any]:
    def auto(command: list[str]) -> subprocess.CompletedProcess:
        if report is not None:
            Path(command[command.index("--report") + 1]).write_text(json.dumps(report), encoding="utf-8")
        return subprocess.CompletedProcess(command, exit_code, "", "boom" if report is None else "")

    return {"downstream.refresh latest": json.dumps(latest), "downstream.refresh auto": auto}


def test_a_fork_already_on_the_newest_tag_needs_nothing(tmp_path: Path) -> None:
    runner = FakeRunner(refresh_handlers({**LATEST, "refresh_available": False}, None))
    assert prepare(tmp_path / "fork", tmp_path / "out", runner)["state"] == "current"
    assert not runner.ran("downstream.refresh auto")


def test_a_clean_refresh_is_bundled_for_the_pull_request_job(tmp_path: Path) -> None:
    report = {"status": "refreshed", "version": "30.29.0.1", "upstream_sha": UPSTREAM_SHA, "entries": 54}
    runner = FakeRunner(refresh_handlers(LATEST, report))
    state = prepare(tmp_path / "fork", tmp_path / "out", runner)
    assert state["state"] == "ready"
    assert (state["tag"], state["branch"], state["version"], state["entries"]) == (
        "v30.29.0",
        "refresh/upstream-30.29.0",
        "30.29.0.1",
        54,
    )
    (bundle,) = runner.ran("git bundle create")
    assert bundle[-2:] == [str(tmp_path / "out" / BUNDLE_FILE), "refresh/upstream-30.29.0"]


def test_a_conflicting_patch_is_reported_with_its_files_and_nothing_is_bundled(tmp_path: Path) -> None:
    report = {
        "status": "conflict",
        "step": "candidate",
        "entry": "sqlite-date-day-arithmetic",
        "paths": ["tests/dialects/test_sqlite.py"],
        "detail": "Carried patch sqlite-date-day-arithmetic does not apply cleanly",
    }
    runner = FakeRunner(refresh_handlers(LATEST, report, exit_code=3))
    state = prepare(tmp_path / "fork", tmp_path / "out", runner)
    assert (state["state"], state["entry"], state["paths"]) == (
        "conflict",
        "sqlite-date-day-arithmetic",
        ["tests/dialects/test_sqlite.py"],
    )
    assert not runner.ran("git bundle")


def test_a_refresh_command_that_crashes_without_a_report_is_a_failure_with_its_output(tmp_path: Path) -> None:
    runner = FakeRunner(refresh_handlers(LATEST, None, exit_code=1))
    state = prepare(tmp_path / "fork", tmp_path / "out", runner)
    assert (state["state"], state["step"], state["detail"]) == ("failed", "auto", "boom")


def test_a_failure_before_the_tag_is_known_is_a_failed_check_without_a_tag(tmp_path: Path) -> None:
    runner = FakeRunner({"downstream.refresh latest": subprocess.CompletedProcess([], 2, "", "no network")})
    state = prepare_or_report(tmp_path / "fork", tmp_path / "out", runner)
    assert state["state"] == "check-failed" and "tag" not in state
    assert "no network" in state["detail"]


def test_a_transient_failure_after_the_tag_is_known_keeps_the_tag_and_never_looks_like_a_refresh_problem(
    tmp_path: Path,
) -> None:
    broken = subprocess.CompletedProcess([], 1, "", "HTTP 502")
    runner = FakeRunner({**refresh_handlers(LATEST, None), "/pulls?state=all": broken})
    state = prepare_or_report(tmp_path / "fork", tmp_path / "out", runner)
    assert (state["state"], state["tag"]) == ("check-failed", "v30.29.0")
    assert "502" in state["detail"]
    assert not runner.ran("downstream.refresh auto")


@pytest.mark.parametrize("tag", ["30.29.0", "v30.29.0-rc1", "v30.29.0; rm -rf", "refresh/x"])
def test_a_tag_that_is_not_a_final_release_is_refused_before_it_names_a_branch(tmp_path: Path, tag: str) -> None:
    runner = FakeRunner(refresh_handlers({**LATEST, "latest_tag": tag}, None))
    state = prepare_or_report(tmp_path / "fork", tmp_path / "out", runner)
    assert state["state"] == "check-failed" and "tag" not in state
    assert not runner.ran("/pulls") and not runner.ran("downstream.refresh auto")


def test_a_pushed_branch_without_a_pull_request_is_reported_and_never_rebuilt(tmp_path: Path) -> None:
    sha = "a" * 40
    runner = FakeRunner(
        {
            **refresh_handlers(LATEST, None),
            "/pulls?state=all": "",
            "ls-remote --heads origin refs/heads/refresh/upstream-30.29.0": f"{sha}\trefs/heads/refresh/upstream-30.29.0\n",
        }
    )
    state = prepare(tmp_path / "fork", tmp_path / "out", runner)
    assert (state["state"], state["sha"]) == ("orphan-branch", sha)
    assert not runner.ran("downstream.refresh auto")


@pytest.mark.parametrize(
    ("pull", "checks", "expected"),
    [
        ({"state": "closed", "merged": False}, [], "declined"),
        (
            {"state": "open", "merged": False},
            [{"name": "run-checks (3.9)", "status": "completed", "conclusion": "failure"}],
            "ci-failing",
        ),
        (
            {"state": "open", "merged": False},
            [{"name": "build", "status": "in_progress", "conclusion": None}],
            "pending",
        ),
        ({"state": "open", "merged": False}, [], "pending"),
        (
            {"state": "open", "merged": False},
            [{"name": "build", "status": "completed", "conclusion": "success"}],
            "awaiting-review",
        ),
    ],
)
def test_an_existing_refresh_pull_request_is_inspected_and_never_rebuilt(
    tmp_path: Path, pull: dict[str, Any], checks: list[dict[str, Any]], expected: str
) -> None:
    pull = {**pull, "number": 81, "url": "https://example.test/pull/81", "sha": "f" * 40}
    runner = FakeRunner(
        {
            **refresh_handlers(LATEST, None),
            "/pulls?state=all": lines(pull),
            "/check-runs": lines(*checks),
        }
    )
    state = prepare(tmp_path / "fork", tmp_path / "out", runner)
    assert state["state"] == expected
    assert state["pull_request"] == "https://example.test/pull/81"
    assert not runner.ran("downstream.refresh auto")


def write_state(out_dir: Path, **state: Any) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / STATE_FILE).write_text(json.dumps(state), encoding="utf-8")


READY = {
    "state": "ready",
    "tag": "v30.29.0",
    "branch": "refresh/upstream-30.29.0",
    "version": "30.29.0.1",
    "upstream_sha": UPSTREAM_SHA,
    "entries": 54,
}


def test_without_the_token_nothing_is_pushed(tmp_path: Path) -> None:
    write_state(tmp_path, **READY)
    runner = FakeRunner({})
    opened = open_pull_request(tmp_path / "fork", tmp_path, "", "https://run", runner)
    assert opened["status"] == "unpushed"
    assert runner.calls == []


def test_the_pull_request_is_pushed_with_the_token_and_older_refresh_pull_requests_are_closed(tmp_path: Path) -> None:
    write_state(tmp_path, **READY)
    runner = FakeRunner(
        {
            "--method POST": "https://github.com/BenchBox-dev/benchbox-sqlglot/pull/90\n",
            "startswith": lines({"number": 81}),
        }
    )
    opened = open_pull_request(tmp_path / "fork", tmp_path, "s3cret", "https://run", runner)
    assert opened == {
        "status": "opened",
        "url": "https://github.com/BenchBox-dev/benchbox-sqlglot/pull/90",
        "superseded": [81],
    }
    (fetch,) = runner.ran("git fetch")
    assert fetch[-1] == "refs/heads/refresh/upstream-30.29.0:refs/heads/refresh/upstream-30.29.0"
    (push,) = runner.ran("push origin")
    push_environment = runner.environments[runner.calls.index(push)]
    assert push_environment == authorization("s3cret")
    assert all(authorization("s3cret")["GIT_CONFIG_VALUE_0"] not in argument for argument in push)
    assert all("s3cret" not in argument for call in runner.calls for argument in call)
    (create,) = runner.ran("--method POST")
    assert "head=refresh/upstream-30.29.0" in create and "base=main" in create
    body = (tmp_path / "pull-request.md").read_text(encoding="utf-8")
    assert "v30.29.0" in body and "54 retained" in body and "https://run" in body
    assert runner.ran("gh pr close 81")


def test_a_rejected_push_is_recorded_with_its_reason_and_never_carries_the_credential(tmp_path: Path) -> None:
    write_state(tmp_path, **READY)
    failure = subprocess.CompletedProcess(["git", "push", "origin", "branch"], 1, "", "remote: Permission denied")
    runner = FakeRunner({"push origin": failure})
    opened = open_pull_request(tmp_path / "fork", tmp_path, "s3cret", "https://run", runner)
    assert opened["status"] == "unpushed"
    assert "Permission denied" in opened["reason"] and "pushing" in opened["reason"]
    assert "s3cret" not in opened["reason"]
    assert authorization("s3cret")["GIT_CONFIG_VALUE_0"] not in opened["reason"]
    assert not runner.ran("--method POST")


def test_a_failed_pull_request_after_the_push_is_recorded_so_the_branch_is_not_left_unexplained(
    tmp_path: Path,
) -> None:
    write_state(tmp_path, **READY)
    failure = subprocess.CompletedProcess([], 1, "", "HTTP 502")
    opened = open_pull_request(
        tmp_path / "fork", tmp_path, "s3cret", "https://run", FakeRunner({"--method POST": failure})
    )
    assert opened["status"] == "unpushed"
    assert "was pushed" in opened["reason"] and "502" in opened["reason"]


def test_failing_to_close_an_older_refresh_does_not_undo_an_opened_pull_request(tmp_path: Path) -> None:
    write_state(tmp_path, **READY)
    runner = FakeRunner(
        {
            "--method POST": "https://example.test/pull/90\n",
            "startswith": subprocess.CompletedProcess([], 1, "", "HTTP 403"),
        }
    )
    opened = open_pull_request(tmp_path / "fork", tmp_path, "s3cret", "https://run", runner)
    assert opened["status"] == "opened" and opened["url"] == "https://example.test/pull/90"
    assert "403" in opened["superseded_error"]


def issue_handlers(existing: list[dict[str, Any]]) -> dict[str, Any]:
    return {"gh issue list": json.dumps(existing)}


CONFLICT = {
    "state": "conflict",
    "tag": "v30.29.0",
    "step": "candidate",
    "entry": "sqlite-date-day-arithmetic",
    "paths": ["tests/dialects/test_sqlite.py"],
    "detail": "does not apply",
}


def test_a_conflict_opens_one_issue_naming_the_patch_and_files(tmp_path: Path) -> None:
    write_state(tmp_path, **CONFLICT)
    runner = FakeRunner(issue_handlers([]))
    assert sync_issue(tmp_path, "https://run", runner)["action"] == "created"
    (create,) = runner.ran("gh issue create")
    assert create[create.index("--title") + 1] == "SQLGlot fork refresh to v30.29.0 needs follow-up"
    body = create[create.index("--body") + 1]
    assert "sqlite-date-day-arithmetic" in body and "tests/dialects/test_sqlite.py" in body


def test_a_changed_reason_updates_the_issue_once_and_later_nights_stay_quiet(tmp_path: Path) -> None:
    write_state(tmp_path, **CONFLICT)
    created = FakeRunner(issue_handlers([]))
    sync_issue(tmp_path, "https://run", created)
    body = next(c for c in created.calls if "issue" in c and "create" in c)[-1]
    title = "SQLGlot fork refresh to v30.29.0 needs follow-up"
    same = FakeRunner(issue_handlers([{"number": 7, "title": title, "body": body}]))
    assert sync_issue(tmp_path, "https://run/2", same)["action"] == "none"
    write_state(tmp_path, **{**CONFLICT, "entry": "another-patch"})
    changed = FakeRunner(issue_handlers([{"number": 7, "title": title, "body": body}]))
    assert sync_issue(tmp_path, "https://run/3", changed)["action"] == "updated"
    (edit,) = changed.ran("gh issue edit 7")
    assert changed.ran("gh issue comment 7")
    next_night = FakeRunner(issue_handlers([{"number": 7, "title": title, "body": edit[-1]}]))
    assert sync_issue(tmp_path, "https://run/4", next_night)["action"] == "none"
    assert not next_night.ran("gh issue comment")


def test_a_refresh_pull_request_closed_without_merging_still_asks_for_follow_up(tmp_path: Path) -> None:
    write_state(
        tmp_path,
        state="declined",
        tag="v30.29.0",
        current="v30.27.0",
        pull_request="https://example.test/pull/81",
    )
    runner = FakeRunner(issue_handlers([]))
    assert sync_issue(tmp_path, "https://run", runner)["action"] == "created"
    assert "closed without merging" in runner.ran("gh issue create")[0][-1]


def test_issues_for_other_tags_close_when_the_target_moves_on(tmp_path: Path) -> None:
    write_state(tmp_path, **CONFLICT)
    existing = [{"number": 4, "title": "SQLGlot fork refresh to v30.28.0 needs follow-up", "body": ""}]
    runner = FakeRunner(issue_handlers(existing))
    outcome = sync_issue(tmp_path, "https://run", runner)
    assert outcome["closed"] == [4] and outcome["action"] == "created"


@pytest.mark.parametrize("state", ["current", "pending", "awaiting-review"])
def test_a_healthy_state_closes_every_open_refresh_issue(tmp_path: Path, state: str) -> None:
    write_state(tmp_path, state=state, tag="v30.29.0")
    existing = [{"number": 9, "title": "SQLGlot fork refresh to v30.29.0 needs follow-up", "body": ""}]
    runner = FakeRunner(issue_handlers(existing))
    outcome = sync_issue(tmp_path, "https://run", runner)
    assert outcome["closed"] == [9]
    assert not runner.ran("gh issue create")


def test_a_ready_refresh_that_could_not_be_pushed_asks_for_follow_up_and_an_opened_one_does_not(tmp_path: Path) -> None:
    write_state(tmp_path, **READY)
    (tmp_path / OPEN_FILE).write_text(json.dumps({"status": "unpushed", "reason": "the token is missing"}))
    unpushed = FakeRunner(issue_handlers([]))
    assert sync_issue(tmp_path, "https://run", unpushed)["action"] == "created"
    assert "the token is missing" in unpushed.ran("gh issue create")[0][-1]
    (tmp_path / OPEN_FILE).write_text(json.dumps({"status": "opened", "url": "https://pr"}))
    opened = FakeRunner(issue_handlers([]))
    assert sync_issue(tmp_path, "https://run", opened)["action"] == "none"


def test_a_failed_check_opens_one_monitor_issue_and_leaves_per_tag_issues_alone(tmp_path: Path) -> None:
    write_state(tmp_path, state="check-failed", tag="v30.29.0", detail="HTTP 502")
    per_tag = {"number": 3, "title": "SQLGlot fork refresh to v30.29.0 needs follow-up", "body": "x"}
    runner = FakeRunner(issue_handlers([per_tag]))
    outcome = sync_issue(tmp_path, "https://run", runner)
    assert outcome == {"state": "check-failed", "closed": [], "action": "created"}
    (create,) = runner.ran("gh issue create")
    assert create[create.index("--title") + 1] == MONITOR_TITLE
    assert "HTTP 502" in create[create.index("--body") + 1]
    assert not runner.ran("gh issue close")


def test_a_repeated_failed_check_stays_quiet_even_when_its_error_text_differs(tmp_path: Path) -> None:
    write_state(tmp_path, state="check-failed", tag=None, detail="HTTP 502")
    created = FakeRunner(issue_handlers([]))
    sync_issue(tmp_path, "https://run", created)
    body = created.ran("gh issue create")[0][-1]
    write_state(tmp_path, state="check-failed", tag="v30.29.0", detail="timeout after 120s")
    again = FakeRunner(issue_handlers([{"number": 5, "title": MONITOR_TITLE, "body": body}]))
    assert sync_issue(tmp_path, "https://run/2", again)["action"] == "none"


def test_a_missing_state_file_means_the_preparation_job_failed_and_is_reported(tmp_path: Path) -> None:
    runner = FakeRunner(issue_handlers([]))
    assert sync_issue(tmp_path / "absent", "https://run", runner)["action"] == "created"
    (create,) = runner.ran("gh issue create")
    assert create[create.index("--title") + 1] == MONITOR_TITLE
    assert "did not produce a state file" in create[create.index("--body") + 1]


def test_a_good_run_closes_the_monitor_issue(tmp_path: Path) -> None:
    write_state(tmp_path, state="current", tag="v30.29.0")
    runner = FakeRunner(issue_handlers([{"number": 5, "title": MONITOR_TITLE, "body": ""}]))
    assert sync_issue(tmp_path, "https://run", runner)["closed"] == [5]


def test_an_orphan_branch_and_a_merged_but_unadopted_pull_request_are_explained(tmp_path: Path) -> None:
    write_state(tmp_path, state="orphan-branch", tag="v30.29.0", branch="refresh/upstream-30.29.0", sha="a" * 40)
    orphan = FakeRunner(issue_handlers([]))
    sync_issue(tmp_path, "https://run", orphan)
    body = orphan.ran("gh issue create")[0][-1]
    assert "refresh/upstream-30.29.0" in body and "no pull request" in body and "does not overwrite" in body
    write_state(
        tmp_path,
        state="declined",
        tag="v30.29.0",
        current="v30.27.0",
        pull_request="https://example.test/pull/81",
        merged=True,
    )
    merged = FakeRunner(issue_handlers([]))
    sync_issue(tmp_path, "https://run", merged)
    assert "was merged, but the fork's main is still on" in merged.ran("gh issue create")[0][-1]


def test_the_command_in_a_not_pushed_issue_has_the_arguments_it_needs(tmp_path: Path) -> None:
    write_state(tmp_path, **READY)
    (tmp_path / OPEN_FILE).write_text(json.dumps({"status": "unpushed", "reason": "no token"}))
    runner = FakeRunner(issue_handlers([]))
    sync_issue(tmp_path, "https://run", runner)
    body = runner.ran("gh issue create")[0][-1]
    assert "--tag v30.29.0 --work-dir" in body and "--report" in body
