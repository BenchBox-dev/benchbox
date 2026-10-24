from __future__ import annotations

import io
import urllib.error
from email.message import Message
from typing import Any

import pytest

from scripts import ci_text_edit_verdict as verdict
from scripts.ci_text_edit_verdict import (
    FAIL,
    PASS,
    TEXT_EDIT_RUN_NAME,
    WAIT,
    decide,
    main,
    newest_failed_text_edit_run,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

CURRENT = 500
REPO_RUNS = "https://github.com/o/r/actions/runs"


def run(run_id: int, status: str = "completed", title: str = "CI", conclusion: str | None = None) -> dict[str, Any]:
    return {"id": run_id, "status": status, "display_title": title, "conclusion": conclusion}


def check(
    run_id: int, conclusion: str | None, *, name: str = "core", started: str = "2026-10-21T10:00:00Z", check_id: int = 1
) -> dict[str, Any]:
    return {
        "id": check_id,
        "name": name,
        "status": "completed" if conclusion else "in_progress",
        "conclusion": conclusion,
        "started_at": started,
        "html_url": f"{REPO_RUNS}/{run_id}/job/{check_id}",
    }


def test_copies_success_from_the_full_run() -> None:
    assert (
        decide("core", CURRENT, [run(100), run(CURRENT, "in_progress", TEXT_EDIT_RUN_NAME)], [check(100, "success")])[0]
        == PASS
    )


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped", "timed_out"])
def test_anything_but_success_fails(conclusion: str) -> None:
    assert decide("core", CURRENT, [run(100)], [check(100, conclusion)])[0] == FAIL


def test_waits_while_a_full_run_is_in_progress_even_after_an_older_success() -> None:
    runs = [run(100), run(200, "in_progress")]
    assert decide("core", CURRENT, runs, [check(100, "success")])[0] == WAIT


def test_the_newest_full_run_decides() -> None:
    runs = [run(100), run(200)]
    assert (
        decide("core", CURRENT, runs, [check(100, "success", check_id=1), check(200, "failure", check_id=2)])[0] == FAIL
    )
    assert (
        decide("core", CURRENT, runs, [check(100, "failure", check_id=1), check(200, "success", check_id=2)])[0] == PASS
    )


def test_a_rerun_attempt_in_the_same_run_supersedes_the_first() -> None:
    checks = [
        check(100, "failure", started="2026-10-21T10:00:00Z", check_id=1),
        check(100, "success", started="2026-10-21T11:00:00Z", check_id=2),
    ]
    assert decide("core", CURRENT, [run(100)], checks)[0] == PASS


def test_text_edit_runs_never_count_as_full_runs() -> None:
    runs = [run(300, title=TEXT_EDIT_RUN_NAME)]
    assert decide("core", CURRENT, runs, [check(300, "success")])[0] == WAIT


def test_waits_when_no_full_run_exists() -> None:
    assert decide("core", CURRENT, [run(CURRENT, "in_progress", TEXT_EDIT_RUN_NAME)], [])[0] == WAIT


def test_fails_when_the_full_run_reported_no_such_check() -> None:
    assert decide("core", CURRENT, [run(100)], [check(100, "success", name="docs")])[0] == FAIL


def test_ignores_checks_from_other_workflows_and_unfinished_checks() -> None:
    checks = [check(999, "success", check_id=1), check(100, None, check_id=2)]
    assert decide("core", CURRENT, [run(100)], checks)[0] == FAIL


VERDICT_ARGS = ["verdict", "--repo", "o/r", "--sha", "abc", "--unit", "core", "--run-id", str(CURRENT)]
REFRESH_ARGS = ["refresh", "--repo", "o/r", "--sha", "abc"]


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    calls: list[float] = []
    monkeypatch.setenv("GH_TOKEN", "token")
    monkeypatch.setattr(verdict.time, "sleep", calls.append)
    return calls


def fake_api(monkeypatch: pytest.MonkeyPatch, runs: list[dict[str, Any]], checks: list[dict[str, Any]]) -> list[int]:
    calls: list[int] = []

    def fetch(*_: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        calls.append(1)
        return runs, checks

    monkeypatch.setattr(verdict, "_fetch", fetch)
    return calls


def test_wait_exits_nonzero_once_without_sleeping(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float], capsys: pytest.CaptureFixture[str]
) -> None:
    calls = fake_api(monkeypatch, [run(100, "in_progress")], [])
    assert main(VERDICT_ARGS) == 1
    assert len(calls) == 1
    assert sleeps == []
    assert "re-run automatically" in capsys.readouterr().err


def test_pass_exits_zero_and_fail_exits_one(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> None:
    fake_api(monkeypatch, [run(100)], [check(100, "success")])
    assert main(VERDICT_ARGS) == 0
    fake_api(monkeypatch, [run(100)], [check(100, "failure")])
    assert main(VERDICT_ARGS) == 1
    assert sleeps == []


def test_api_errors_retry_then_succeed(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> None:
    attempts: list[int] = []

    def fetch(*_: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        attempts.append(1)
        if len(attempts) < 3:
            raise urllib.error.URLError("boom")
        return [run(100)], [check(100, "success")]

    monkeypatch.setattr(verdict, "_fetch", fetch)
    assert main(VERDICT_ARGS) == 0
    assert len(attempts) == 3
    assert sleeps == [verdict.API_RETRY_SECONDS] * 2


def test_api_errors_retry_then_fail(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float], capsys: pytest.CaptureFixture[str]
) -> None:
    attempts: list[int] = []

    def fetch(*_: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        attempts.append(1)
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(verdict, "_fetch", fetch)
    assert main(VERDICT_ARGS) == 1
    assert len(attempts) == verdict.API_TRIES
    assert len(sleeps) == verdict.API_TRIES - 1
    assert "boom" in capsys.readouterr().err


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.github.com/x", code, f"status {code}", Message(), io.BytesIO(b""))


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_http_client_errors_fail_without_retry(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float], capsys: pytest.CaptureFixture[str], code: int
) -> None:
    attempts: list[int] = []

    def fetch(*_: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        attempts.append(1)
        raise http_error(code)

    monkeypatch.setattr(verdict, "_fetch", fetch)
    assert main(VERDICT_ARGS) == 1
    assert len(attempts) == 1
    assert sleeps == []
    assert f"status {code}" in capsys.readouterr().err


@pytest.mark.parametrize("code", [500, 502, 503])
def test_http_server_errors_retry_then_succeed(monkeypatch: pytest.MonkeyPatch, sleeps: list[float], code: int) -> None:
    attempts: list[int] = []

    def fetch(*_: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        attempts.append(1)
        if len(attempts) < 2:
            raise http_error(code)
        return [run(100)], [check(100, "success")]

    monkeypatch.setattr(verdict, "_fetch", fetch)
    assert main(VERDICT_ARGS) == 0
    assert len(attempts) == 2
    assert sleeps == [verdict.API_RETRY_SECONDS]


def test_timeouts_retry_then_succeed(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> None:
    attempts: list[int] = []

    def fetch(*_: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        attempts.append(1)
        if len(attempts) < 2:
            raise TimeoutError("slow")
        return [run(100)], [check(100, "success")]

    monkeypatch.setattr(verdict, "_fetch", fetch)
    assert main(VERDICT_ARGS) == 0
    assert sleeps == [verdict.API_RETRY_SECONDS]


def test_refresh_http_client_error_fails_without_retry(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> None:
    attempts: list[int] = []

    def fetch_runs(*_: Any) -> list[dict[str, Any]]:
        attempts.append(1)
        raise http_error(403)

    monkeypatch.setattr(verdict, "_fetch_runs", fetch_runs)
    assert main(REFRESH_ARGS) == 1
    assert len(attempts) == 1
    assert sleeps == []


def test_refresh_selects_the_newest_failed_text_edit_run() -> None:
    runs = [
        run(10, title=TEXT_EDIT_RUN_NAME, conclusion="failure"),
        run(30, title=TEXT_EDIT_RUN_NAME, conclusion="failure"),
        run(40, title=TEXT_EDIT_RUN_NAME, conclusion="success"),
        run(50, "in_progress", TEXT_EDIT_RUN_NAME),
        run(60, conclusion="failure"),
    ]
    assert newest_failed_text_edit_run(runs) == 30


def test_refresh_finds_nothing_without_a_failed_text_edit_run() -> None:
    runs = [run(60, conclusion="failure"), run(40, title=TEXT_EDIT_RUN_NAME, conclusion="success")]
    assert newest_failed_text_edit_run(runs) is None
    assert newest_failed_text_edit_run([]) is None


def test_refresh_reruns_only_the_failed_jobs_of_that_run(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> None:
    runs = [
        run(10, title=TEXT_EDIT_RUN_NAME, conclusion="failure"),
        run(30, title=TEXT_EDIT_RUN_NAME, conclusion="failure"),
    ]
    seen: dict[str, Any] = {}
    monkeypatch.setattr(verdict, "_fetch_runs", lambda repo, sha, token: seen.update(sha=sha) or runs)
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_: Any) -> Any:
        commands.append(command)
        return type("Done", (), {"returncode": 0})()

    monkeypatch.setattr(verdict.subprocess, "run", fake_run)
    assert main(REFRESH_ARGS) == 0
    assert seen["sha"] == "abc"
    assert commands == [["gh", "run", "rerun", "30", "--failed", "--repo", "o/r"]]


def test_refresh_does_nothing_when_no_run_failed(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> None:
    monkeypatch.setattr(verdict, "_fetch_runs", lambda repo, sha, token: [run(60, conclusion="failure")])
    commands: list[list[str]] = []
    monkeypatch.setattr(verdict.subprocess, "run", lambda command, **_: commands.append(command))
    assert main(REFRESH_ARGS) == 0
    assert commands == []


def test_refresh_waits_for_a_text_edit_run_that_is_still_finishing(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    finishing = [run(30, "in_progress", TEXT_EDIT_RUN_NAME)]
    finished = [run(30, title=TEXT_EDIT_RUN_NAME, conclusion="failure")]
    polls = iter([finishing, finishing, finished])
    monkeypatch.setattr(verdict, "_fetch_runs", lambda repo, sha, token: next(polls))
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_: Any) -> Any:
        commands.append(command)
        return type("Done", (), {"returncode": 0})()

    monkeypatch.setattr(verdict.subprocess, "run", fake_run)
    assert main(REFRESH_ARGS) == 0
    assert sleeps == [verdict.UNFINISHED_POLL_SECONDS] * 2
    assert commands == [["gh", "run", "rerun", "30", "--failed", "--repo", "o/r"]]


def test_refresh_stops_waiting_after_a_bounded_number_of_polls(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    monkeypatch.setattr(verdict, "_fetch_runs", lambda repo, sha, token: [run(30, "queued", TEXT_EDIT_RUN_NAME)])
    commands: list[list[str]] = []
    monkeypatch.setattr(verdict.subprocess, "run", lambda command, **_: commands.append(command))
    assert main(REFRESH_ARGS) == 0
    assert len(sleeps) == verdict.UNFINISHED_POLLS
    assert commands == []
