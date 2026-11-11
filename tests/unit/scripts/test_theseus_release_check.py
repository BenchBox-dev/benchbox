from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest
import theseus_release_check as check

pytestmark = [pytest.mark.unit, pytest.mark.fast]

LOCK = {"repository": "owner/engine", "source_commit": "a" * 40}
PR_HEAD = "b" * 40


def runs(*pairs: tuple[str, str], start: int = 1) -> dict[str, Any]:
    return {"check_runs": [{"id": start + i, "name": n, "conclusion": c} for i, (n, c) in enumerate(pairs)]}


GREEN = runs(*((name, "success") for name in check.REQUIRED_CHECKS))
EMPTY: dict[str, Any] = {"check_runs": []}


def api(routes: dict[str, Any]):
    seen: list[str] = []

    def fetch(path: str) -> Any:
        seen.append(path)
        return routes[path.split("?")[0]]

    fetch.seen = seen
    return fetch


TREE = "c" * 40


def routes(
    *, status="behind", direct=GREEN, pulls=(), pr_runs=EMPTY, locked_tree=TREE, head_tree=TREE
) -> dict[str, Any]:
    repo = "/repos/owner/engine"
    return {
        f"{repo}/compare/main...{'a' * 40}": {"status": status},
        f"{repo}/commits/{'a' * 40}/check-runs": direct,
        f"{repo}/commits/{'a' * 40}/pulls": list(pulls),
        f"{repo}/commits/{PR_HEAD}/check-runs": pr_runs,
        f"{repo}/git/commits/{'a' * 40}": {"tree": {"sha": locked_tree}},
        f"{repo}/git/commits/{PR_HEAD}": {"tree": {"sha": head_tree}},
    }


def pull(merged_at="2026-01-01T00:00:00Z", base="main") -> dict[str, Any]:
    return {"number": 7, "merged_at": merged_at, "base": {"ref": base}, "head": {"sha": PR_HEAD}}


@pytest.mark.parametrize("status", ["identical", "behind"])
def test_a_main_ancestor_with_green_checks_on_the_commit_passes(status: str) -> None:
    assert check.verify(LOCK, api(routes(status=status))) == []


@pytest.mark.parametrize("status", ["ahead", "diverged"])
def test_a_commit_not_on_main_fails_before_any_check_is_read(status: str) -> None:
    fetch = api(routes(status=status))

    problems = check.verify(LOCK, fetch)

    assert len(problems) == 1 and "not reachable" in problems[0]
    assert len(fetch.seen) == 1


def test_a_failed_required_check_on_the_commit_fails_without_the_pull_fallback() -> None:
    bad = runs(
        ("test (py3.11)", "success"), ("test (py3.13)", "failure"), ("build and installed-wheel smoke", "success")
    )
    fetch = api(routes(direct=bad, pulls=[pull()], pr_runs=GREEN))

    problems = check.verify(LOCK, fetch)

    assert problems == [f"test (py3.13): concluded 'failure' on {'a' * 40}"]
    assert not any("pulls" in path for path in fetch.seen)


def test_a_rerun_that_succeeded_supersedes_an_earlier_failure() -> None:
    reran = runs(
        ("test (py3.11)", "failure"),
        ("test (py3.13)", "success"),
        ("build and installed-wheel smoke", "success"),
        ("test (py3.11)", "success"),
    )

    assert check.verify(LOCK, api(routes(direct=reran))) == []


def test_unrelated_check_runs_on_the_commit_leave_the_pull_head_fallback_open() -> None:
    other = runs(("lint", "success"))

    problems = check.verify(LOCK, api(routes(direct=other, pulls=[pull()], pr_runs=GREEN)))

    assert problems == []


def test_a_merged_pull_request_head_supplies_the_checks_when_the_commit_has_none() -> None:
    assert check.verify(LOCK, api(routes(direct=EMPTY, pulls=[pull()], pr_runs=GREEN))) == []


def test_pull_head_checks_that_failed_are_reported_with_the_pull_number() -> None:
    bad = runs(*((name, "cancelled") for name in check.REQUIRED_CHECKS))

    problems = check.verify(LOCK, api(routes(direct=EMPTY, pulls=[pull()], pr_runs=bad)))

    assert any(item.startswith("pull 7: test (py3.11): concluded 'cancelled'") for item in problems)


@pytest.mark.parametrize("pr", [pull(merged_at=None), pull(base="release")])
def test_unmerged_or_off_main_pull_requests_are_ignored(pr: dict[str, Any]) -> None:
    problems = check.verify(LOCK, api(routes(direct=EMPTY, pulls=[pr], pr_runs=GREEN)))

    assert problems
    assert all(not item.startswith("pull") for item in problems)


def test_no_evidence_anywhere_fails() -> None:
    assert check.verify(LOCK, api(routes(direct=EMPTY))) != []


def test_main_exit_codes_follow_the_decision(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "theseus-lock.json").write_text(json.dumps(LOCK), encoding="utf-8")

    assert check.main(["x", str(tmp_path)], api(routes())) == 0
    assert check.main(["x", str(tmp_path)], api(routes(status="ahead"))) == 1
    assert "not reachable" in capsys.readouterr().err


def test_an_api_failure_is_a_refusal_not_a_crash(tmp_path: Path) -> None:
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "theseus-lock.json").write_text(json.dumps(LOCK), encoding="utf-8")

    def down(path: str) -> Any:
        raise check.ReleaseCheckError("offline")

    assert check.main(["x", str(tmp_path)], down) == 1


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.github.com/x", code, "error", {}, None)


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def test_a_transient_server_error_is_retried_with_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    outcomes: list[Any] = [_http_error(500), _http_error(502), _Response(b'{"ok": true}')]
    sleeps: list[float] = []

    def opener(request: Any, timeout: float) -> Any:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    assert check._fetch("/x", sleep=sleeps.append, opener=opener) == {"ok": True}
    assert sleeps == [check.BACKOFF_SECONDS, check.BACKOFF_SECONDS * 2]


def test_a_client_error_is_not_retried_and_persistent_server_errors_give_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    calls: list[int] = []

    def not_found(request: Any, timeout: float) -> Any:
        calls.append(1)
        raise _http_error(404)

    with pytest.raises(check.ReleaseCheckError, match="404"):
        check._fetch("/x", sleep=lambda _: None, opener=not_found)
    assert len(calls) == 1

    def down(request: Any, timeout: float) -> Any:
        calls.append(1)
        raise _http_error(503)

    with pytest.raises(check.ReleaseCheckError, match="503"):
        check._fetch("/x", sleep=lambda _: None, opener=down)
    assert len(calls) == 1 + check.ATTEMPTS


def test_the_backoff_is_capped_and_spans_about_a_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    sleeps: list[float] = []

    def down(request: Any, timeout: float) -> Any:
        raise _http_error(500)

    with pytest.raises(check.ReleaseCheckError, match="500"):
        check._fetch("/x", sleep=sleeps.append, opener=down)
    assert len(sleeps) == check.ATTEMPTS - 1
    assert max(sleeps) == check.BACKOFF_CAP_SECONDS
    assert 45 <= sum(sleeps) <= 120


def _authorizations(outcomes: list[Any]) -> tuple[list[Any], Any]:
    seen: list[Any] = []

    def opener(request: Any, timeout: float) -> Any:
        seen.append(request.get_header("Authorization"))
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return seen, opener


def test_a_server_error_with_a_token_is_retried_once_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    seen, opener = _authorizations([_http_error(500), _Response(b'{"ok": true}')])
    sleeps: list[float] = []
    assert check._fetch("/x", sleep=sleeps.append, opener=opener) == {"ok": True}
    assert seen == ["Bearer secret", None]
    assert sleeps == []


def test_the_unauthenticated_retry_happens_once_per_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    seen, opener = _authorizations([_http_error(500)] * (check.ATTEMPTS + 1))
    with pytest.raises(check.ReleaseCheckError, match="500"):
        check._fetch("/x", sleep=lambda _: None, opener=opener)
    assert seen.count(None) == 1
    assert len(seen) == check.ATTEMPTS + 1


def test_a_client_error_with_a_token_is_neither_retried_nor_tried_unauthenticated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    seen, opener = _authorizations([_http_error(403)])
    with pytest.raises(check.ReleaseCheckError, match="403"):
        check._fetch("/x", sleep=lambda _: None, opener=opener)
    assert seen == ["Bearer secret"]


def test_every_required_check_failed_on_the_commit_refuses_despite_a_green_pull_head() -> None:
    bad = runs(*((name, "failure") for name in check.REQUIRED_CHECKS))

    problems = check.verify(LOCK, api(routes(direct=bad, pulls=[pull()], pr_runs=GREEN)))

    assert len(problems) == len(check.REQUIRED_CHECKS)
    assert all("concluded 'failure'" in item for item in problems)


def test_a_mixed_result_on_the_commit_refuses_despite_a_green_pull_head() -> None:
    mixed = runs(("test (py3.11)", "success"), ("test (py3.13)", "failure"))

    problems = check.verify(LOCK, api(routes(direct=mixed, pulls=[pull()], pr_runs=GREEN)))

    assert any("test (py3.13)" in item for item in problems)
    assert any("build and installed-wheel smoke: no check run" in item for item in problems)


def test_no_runs_on_the_commit_and_no_merged_pull_refuses() -> None:
    assert check.verify(LOCK, api(routes(direct=EMPTY, pulls=[pull(merged_at=None)], pr_runs=GREEN))) != []
    assert check.verify(LOCK, api(routes(direct=EMPTY, pulls=[], pr_runs=GREEN))) != []


def test_a_pull_head_with_a_different_tree_than_the_locked_commit_is_refused_despite_green_checks() -> None:
    fetch = api(routes(direct=EMPTY, pulls=[pull()], pr_runs=GREEN, head_tree="d" * 40))

    problems = check.verify(LOCK, fetch)

    assert any(item.startswith("pull 7: head") and "not the locked commit's tree" in item for item in problems)
    assert not any("check-runs" in path and PR_HEAD in path for path in fetch.seen)


def test_a_locked_commit_with_no_readable_tree_refuses_the_pull_fallback() -> None:
    fetch = api(routes(direct=EMPTY, pulls=[pull()], pr_runs=GREEN, locked_tree="", head_tree=""))

    assert check.verify(LOCK, fetch) != []


