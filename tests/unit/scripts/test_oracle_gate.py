from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "_project/scripts/oracle_gate.py"
sys.path.insert(0, str(SCRIPT.parent))
oracle_review_check = importlib.import_module("_project.scripts.oracle_review_check")
sys.modules["oracle_review_check"] = oracle_review_check
oracle_gate = importlib.import_module("_project.scripts.oracle_gate")

pytestmark = [pytest.mark.unit, pytest.mark.fast]

HEAD = "a" * 40
AFTER_HEAD = "2026-10-01T13:00:00Z"
SOUNDNESS_FILES = ["benchbox/core/equivalence/checker.py", "docs/index.md"]
OTHER_FILES = ["docs/index.md", "notes.md"]


@pytest.fixture(autouse=True)
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(time, "sleep", recorded.append)
    return recorded


RUN_UUID = "1" * 32


def _receipt_body(run_uuid: str = RUN_UUID) -> str:
    from _project.scripts.oracle_reviewers import protocol

    record = {
        "v": protocol.MARKER_VERSION,
        "cycle": 1,
        "round": 1,
        "kind": protocol.FIRST,
        "decision": protocol.SHIP,
        "head_sha": HEAD,
        "base_ref": "develop",
        "reviewer": "large",
        "tier": "large",
        "strikes_after": 0,
        "open_defects": [],
        "next_id": 1,
        "patch_digest": "d" * 32,
        "carried": False,
        "summary": "",
        "run_uuid": run_uuid,
    }
    return f"### oracle-verdict: success for `{HEAD}`\n\n{protocol.encode_marker(record)}"


def _success_status(**over: Any) -> dict[str, Any]:
    status = {
        "id": 1,
        "context": "oracle-verdict",
        "state": "success",
        "description": f"large: SHIP [run {RUN_UUID}]",
        "login": "benchbox-oracle[bot]",
        "user_type": "Bot",
        "created_at": AFTER_HEAD,
    }
    return {**status, **over}


def _receipt(**over: Any) -> dict[str, Any]:
    review = {
        "login": "benchbox-oracle[bot]",
        "user_type": "Bot",
        "commit_id": HEAD,
        "state": "COMMENTED",
        "submitted_at": AFTER_HEAD,
        "body": _receipt_body(),
        "last_edited_at": None,
        "includes_created_edit": False,
    }
    return {**review, **over}


def _thread(resolved: bool) -> dict[str, Any]:
    return {"resolved": resolved, "author": "benchbox-oracle", "author_type": "Bot"}


def _attestation(body: str | None = None) -> dict[str, Any]:
    return {
        "login": "joeharris76",
        "user_type": "User",
        "created_at": AFTER_HEAD,
        "updated_at": AFTER_HEAD,
        "body": body if body is not None else f"Stand-in oracle review: APPROVE {HEAD}",
    }


def _matcher() -> Any:
    return oracle_review_check.path_matcher(2, 2, *oracle_review_check._review_rules())


def test_gate_reuses_the_checker_decision_and_helpers() -> None:
    assert oracle_gate.decide is oracle_review_check.decide
    assert oracle_gate.path_matcher is oracle_review_check.path_matcher
    assert oracle_gate.changed_paths is oracle_review_check.changed_paths
    assert (oracle_gate.PASS, oracle_gate.WAITING, oracle_gate.ERROR) == (
        oracle_review_check.PASS,
        oracle_review_check.WAITING,
        oracle_review_check.ERROR,
    )


def test_policy_rules_match_the_checker_rules() -> None:
    assert oracle_gate.load_policy_rules() == oracle_review_check._review_rules()


def test_policy_rules_fail_when_the_policy_file_is_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(oracle_gate.CheckError):
        oracle_gate.load_policy_rules()


def test_policy_rules_fail_when_the_package_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "oracle_reviewers", None)
    monkeypatch.setitem(sys.modules, "oracle_reviewers.policy", None)
    with pytest.raises(oracle_gate.CheckError):
        oracle_gate.load_policy_rules()


def test_exempt_only_change_passes() -> None:
    expected = oracle_review_check.decide(HEAD, OTHER_FILES, [], [], _matcher())
    assert expected == (oracle_review_check.PASS, "oracle-review: only exempt paths changed")
    assert oracle_gate.decide(HEAD, OTHER_FILES, [], [], _matcher()) == expected


def test_ship_without_unresolved_threads_passes() -> None:
    statuses = [_success_status()]
    expected = oracle_review_check.decide(HEAD, SOUNDNESS_FILES, statuses, [], _matcher(), reviews=[_receipt()])
    assert expected[0] == oracle_review_check.PASS
    assert oracle_gate.decide(HEAD, SOUNDNESS_FILES, statuses, [], _matcher(), reviews=[_receipt()]) == expected


def test_ship_without_a_same_pr_receipt_waits() -> None:
    statuses = [_success_status()]
    expected = oracle_review_check.decide(HEAD, SOUNDNESS_FILES, statuses, [], _matcher())
    assert expected[0] == oracle_review_check.WAITING
    assert "no unedited oracle review" in expected[1]
    assert oracle_gate.decide(HEAD, SOUNDNESS_FILES, statuses, [], _matcher()) == expected


def test_ship_with_an_unresolved_oracle_thread_waits() -> None:
    statuses = [_success_status()]
    expected = oracle_review_check.decide(
        HEAD, SOUNDNESS_FILES, statuses, [_thread(False)], _matcher(), reviews=[_receipt()]
    )
    assert expected[0] == oracle_review_check.WAITING
    assert "unresolved oracle review thread" in expected[1]
    assert (
        oracle_gate.decide(HEAD, SOUNDNESS_FILES, statuses, [_thread(False)], _matcher(), reviews=[_receipt()])
        == expected
    )


def test_exact_standin_for_the_head_passes() -> None:
    comments = [_attestation()]
    expected = oracle_review_check.decide(HEAD, SOUNDNESS_FILES, [], [], _matcher(), None, comments)
    assert expected[0] == oracle_review_check.PASS
    assert "stand-in review attested by joeharris76" in expected[1]
    assert oracle_gate.decide(HEAD, SOUNDNESS_FILES, [], [], _matcher(), None, comments) == expected


def test_standin_with_extra_text_waits() -> None:
    comments = [_attestation(f"note: Stand-in oracle review: APPROVE {HEAD} thanks")]
    expected = oracle_review_check.decide(HEAD, SOUNDNESS_FILES, [], [], _matcher(), None, comments)
    assert expected[0] == oracle_review_check.WAITING
    assert oracle_gate.decide(HEAD, SOUNDNESS_FILES, [], [], _matcher(), None, comments) == expected


def test_absent_verdict_waits() -> None:
    expected = oracle_review_check.decide(HEAD, SOUNDNESS_FILES, [], [], _matcher())
    assert expected[0] == oracle_review_check.WAITING
    assert oracle_gate.decide(HEAD, SOUNDNESS_FILES, [], [], _matcher()) == expected


def test_gate_state_maps_pass_to_success_and_everything_else_to_failure() -> None:
    assert oracle_gate.gate_state(oracle_gate.PASS) == "success"
    assert oracle_gate.gate_state(oracle_gate.WAITING) == "failure"
    assert oracle_gate.gate_state(oracle_gate.ERROR) == "failure"


def test_status_payload_posts_the_gate_context() -> None:
    assert oracle_gate.GATE_CONTEXT == "oracle-gate"
    payload = oracle_gate.status_payload(oracle_gate.PASS, "oracle-review: pass (oracle verdict for " + HEAD + ")")
    assert payload == {
        "state": "success",
        "context": "oracle-gate",
        "description": "oracle-review: pass (oracle verdict for " + HEAD + ")",
    }


def test_status_payload_clips_long_descriptions_to_the_api_limit() -> None:
    payload = oracle_gate.status_payload(oracle_gate.WAITING, "x" * 200, "https://example.invalid/run")
    assert payload["state"] == "failure"
    assert payload["context"] == "oracle-gate"
    assert len(payload["description"]) == oracle_gate.DESCRIPTION_LIMIT
    assert payload["target_url"] == "https://example.invalid/run"


def test_post_status_writes_the_gate_context_to_the_head(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def read(self) -> bytes:
            return b"{}"

    def fake_urlopen(request: Any) -> _Response:
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["body"] = json.loads(request.data.decode())
        seen["auth"] = request.get_header("Authorization")
        return _Response()

    monkeypatch.setattr(oracle_gate.urllib.request, "urlopen", fake_urlopen)
    oracle_gate.post_status("token", "o/r", HEAD, oracle_gate.PASS, "oracle-review: pass")
    assert seen["url"] == f"https://api.github.com/repos/o/r/statuses/{HEAD}"
    assert seen["method"] == "POST"
    assert seen["auth"] == "Bearer token"
    assert seen["body"]["context"] == "oracle-gate"
    assert seen["body"]["state"] == "success"


def test_post_status_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_urlopen(request: Any) -> Any:
        raise oracle_gate.urllib.error.URLError("down")

    monkeypatch.setattr(oracle_gate.urllib.request, "urlopen", failing_urlopen)
    with pytest.raises(oracle_gate.CheckError):
        oracle_gate.post_status("token", "o/r", HEAD, oracle_gate.PASS, "msg")


def _stub_evaluate(monkeypatch: pytest.MonkeyPatch, status: int, message: str) -> dict[str, Any]:
    posted: dict[str, Any] = {}
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    def fake_evaluate(repo: str, pr: int, token: str) -> tuple[str, int, str]:
        posted["repo"] = repo
        posted["pr"] = pr
        posted["token"] = token
        return HEAD, status, message

    def fake_post(token: str, repo: str, head_sha: str, post_status: int, post_message: str) -> None:
        posted["state"] = oracle_gate.gate_state(post_status)
        posted["context"] = oracle_gate.GATE_CONTEXT
        posted["head"] = head_sha
        posted["message"] = post_message

    monkeypatch.setattr(oracle_gate, "evaluate", fake_evaluate)
    monkeypatch.setattr(oracle_gate, "post_status", fake_post)
    return posted


def test_main_posts_success_and_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    posted = _stub_evaluate(monkeypatch, oracle_gate.PASS, "oracle-review: pass")
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7"]) == 0
    assert (posted["repo"], posted["pr"], posted["token"]) == ("o/r", 7, "token")
    assert posted["state"] == "success" and posted["head"] == HEAD


def test_main_posts_failure_and_exits_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_evaluate(monkeypatch, oracle_gate.WAITING, "oracle-review: waiting")
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7"]) == oracle_gate.WAITING


def test_main_no_post_skips_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(oracle_gate, "evaluate", lambda repo, pr, token: (HEAD, oracle_gate.PASS, "ok"))

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("must not post")

    monkeypatch.setattr(oracle_gate, "post_status", forbidden)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7", "--no-post"]) == 0


def test_main_needs_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7"]) == oracle_gate.ERROR


def test_main_rejects_a_bad_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    assert oracle_gate.main(["--repo", "oops", "--pr", "7"]) == oracle_gate.ERROR


def test_main_reports_a_fetch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    def failing_evaluate(repo: str, pr: int, token: str) -> tuple[str, int, str]:
        raise oracle_gate.CheckError("down")

    monkeypatch.setattr(oracle_gate, "evaluate", failing_evaluate)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7"]) == oracle_gate.ERROR


def _stub_pull(monkeypatch: pytest.MonkeyPatch, base: str = "develop") -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(
        oracle_gate,
        "fetch_pull",
        lambda repo, pr, token: {"base": {"ref": base}, "head": {"sha": HEAD}, "changed_files": 1},
    )
    monkeypatch.setattr(oracle_gate, "fetch_files", lambda repo, pr, token: [{"filename": "x"}])


def _record_posts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int, str]]:
    posts: list[tuple[str, int, str]] = []

    def fake_post(token: str, repo: str, head_sha: str, status: int, message: str) -> None:
        posts.append((head_sha, status, message))

    monkeypatch.setattr(oracle_gate, "post_status", fake_post)
    return posts


def _failing_rules() -> tuple[tuple[str, ...], tuple[str, ...]]:
    raise oracle_gate.CheckError("no policy")


def test_main_posts_failure_when_evaluation_errors_after_the_head_is_known(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pull(monkeypatch)
    monkeypatch.setattr(oracle_gate, "load_policy_rules", _failing_rules)
    posts = _record_posts(monkeypatch)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7"]) == oracle_gate.ERROR
    assert len(posts) == 1
    head, status, message = posts[0]
    assert head == HEAD and oracle_gate.gate_state(status) == "failure"
    assert "no policy" in message and len(oracle_gate.gate_description(message)) <= oracle_gate.DESCRIPTION_LIMIT


def test_main_posts_failure_when_a_status_fetch_raises_a_key_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pull(monkeypatch)
    monkeypatch.setattr(oracle_gate, "load_policy_rules", lambda: ((), ()))

    def broken(repo: str, pr: int, token: str) -> Any:
        raise KeyError("data")

    monkeypatch.setattr(oracle_gate, "fetch_statuses", broken)
    posts = _record_posts(monkeypatch)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7"]) == oracle_gate.ERROR
    assert [head for head, _, _ in posts] == [HEAD]


def test_main_posts_failure_when_the_fresh_reevaluation_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    calls: list[int] = []

    def flaky(repo: str, pr: int, token: str) -> tuple[str, int, str]:
        calls.append(1)
        if len(calls) == 1:
            return HEAD, oracle_gate.PASS, "ok"
        raise oracle_gate.EvaluationFailed(HEAD, oracle_gate.CheckError("down"))

    monkeypatch.setattr(oracle_gate, "evaluate", flaky)
    posts = _record_posts(monkeypatch)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7"]) == oracle_gate.ERROR
    assert len(posts) == 1 and posts[0][0] == HEAD and oracle_gate.gate_state(posts[0][1]) == "failure"


def test_main_returns_error_when_the_failure_post_also_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pull(monkeypatch)
    monkeypatch.setattr(oracle_gate, "load_policy_rules", _failing_rules)

    def failing_post(token: str, repo: str, head_sha: str, status: int, message: str) -> None:
        raise oracle_gate.CheckError("down")

    monkeypatch.setattr(oracle_gate, "post_status", failing_post)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7"]) == oracle_gate.ERROR


def test_main_no_post_never_posts_an_evaluation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pull(monkeypatch)
    monkeypatch.setattr(oracle_gate, "load_policy_rules", _failing_rules)
    posts = _record_posts(monkeypatch)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7", "--no-post"]) == oracle_gate.ERROR
    assert posts == []


def test_main_posts_nothing_when_the_pull_request_cannot_be_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    def down(repo: str, pr: int, token: str) -> Any:
        raise oracle_gate.CheckError("down")

    monkeypatch.setattr(oracle_gate, "fetch_pull", down)

    def head_unavailable(token: str, repo: str, pr: int) -> str:
        raise oracle_gate.CheckError("graphql down")

    monkeypatch.setattr(oracle_gate, "fetch_head_oid", head_unavailable)
    posts = _record_posts(monkeypatch)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7"]) == oracle_gate.ERROR
    assert posts == []


def test_main_posts_nothing_when_the_pull_request_has_no_head(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(oracle_gate, "fetch_pull", lambda repo, pr, token: {"base": {"ref": "develop"}})

    def head_unavailable(token: str, repo: str, pr: int) -> str:
        raise oracle_gate.CheckError("graphql down")

    monkeypatch.setattr(oracle_gate, "fetch_head_oid", head_unavailable)
    posts = _record_posts(monkeypatch)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7"]) == oracle_gate.ERROR
    assert posts == []


def test_main_posts_nothing_for_an_ungated_base_even_when_evaluation_would_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_pull(monkeypatch, base="feature")
    monkeypatch.setattr(oracle_gate, "load_policy_rules", _failing_rules)
    posts = _record_posts(monkeypatch)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7"]) == oracle_gate.PASS
    assert posts == []


def test_main_posts_nothing_for_a_pull_request_that_does_not_target_develop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(
        oracle_gate,
        "fetch_pull",
        lambda repo, pr, token: {"base": {"ref": "feature"}, "head": {"sha": HEAD}, "changed_files": 1},
    )

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("must not evaluate or post")

    for name in ("fetch_files", "fetch_statuses", "fetch_threads", "fetch_reviews", "annotate_receipt"):
        monkeypatch.setattr(oracle_gate, name, forbidden)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7"]) == oracle_gate.PASS


def test_main_posts_the_fresh_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    posted: dict[str, Any] = {}
    seen: list[str] = []

    def moving_evaluate(repo: str, pr: int, token: str) -> tuple[str, int, str]:
        seen.append(repo)
        if len(seen) == 1:
            return HEAD, oracle_gate.WAITING, "first"
        return HEAD, oracle_gate.PASS, "second"

    def fake_post(token: str, repo: str, head_sha: str, status: int, message: str) -> None:
        posted["head"] = head_sha
        posted["message"] = message

    monkeypatch.setattr(oracle_gate, "evaluate", moving_evaluate)
    monkeypatch.setattr(oracle_gate, "post_status", fake_post)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7"]) == 0
    assert posted == {"head": HEAD, "message": "second"}


def test_main_reports_a_post_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_evaluate(monkeypatch, oracle_gate.PASS, "ok")

    def failing_post(token: str, repo: str, head_sha: str, status: int, message: str) -> None:
        raise oracle_gate.CheckError("down")

    monkeypatch.setattr(oracle_gate, "post_status", failing_post)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7"]) == oracle_gate.ERROR


def test_target_url_points_at_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    assert oracle_gate.target_url("o/r") is None
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.delenv("GITHUB_SERVER_URL", raising=False)
    assert oracle_gate.target_url("o/r") == "https://github.com/o/r/actions/runs/123"


OTHER_HEAD = "b" * 40


def _test_pull(base: str = "develop", head: str = HEAD) -> dict[str, Any]:
    return {"base": {"ref": base}, "head": {"sha": head}, "changed_files": 1}


def _stub_unreadable_pull(monkeypatch: pytest.MonkeyPatch, failures: int) -> list[int]:
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(oracle_gate, "load_policy_rules", lambda: ((), ()))
    calls: list[int] = []

    def flaky(repo: str, pr: int, token: str) -> dict[str, Any]:
        calls.append(1)
        if len(calls) <= failures:
            raise oracle_gate.CheckError("read down")
        return _test_pull()

    monkeypatch.setattr(oracle_gate, "fetch_pull", flaky)
    monkeypatch.setattr(oracle_gate, "fetch_files", lambda repo, pr, token: [{"filename": "x"}])
    monkeypatch.setattr(oracle_gate, "fetch_statuses", lambda repo, pr, token: [])
    monkeypatch.setattr(oracle_gate, "fetch_threads", lambda repo, pr, token: [])
    monkeypatch.setattr(oracle_gate, "fetch_base_change_date", lambda repo, pr, token: None)
    monkeypatch.setattr(oracle_gate, "fetch_comments", lambda repo, pr, token: [])
    monkeypatch.setattr(oracle_gate, "fetch_reviews", lambda repo, pr, token: [])
    monkeypatch.setattr(oracle_gate, "annotate_receipt", lambda token, reviews, head_sha, base_date=None: None)
    return calls


@pytest.mark.parametrize(
    "error", [TypeError("pullRequest is null"), ConnectionResetError("reset"), AttributeError("x")]
)
def test_main_posts_failure_for_any_exception_after_the_head_is_known(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    _stub_pull(monkeypatch)
    monkeypatch.setattr(oracle_gate, "load_policy_rules", lambda: ((), ()))
    monkeypatch.setattr(oracle_gate, "fetch_statuses", lambda repo, pr, token: [])

    def broken(repo: str, pr: int, token: str) -> Any:
        raise error

    monkeypatch.setattr(oracle_gate, "fetch_threads", broken)
    posts = _record_posts(monkeypatch)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7"]) == oracle_gate.ERROR
    assert [(head, oracle_gate.gate_state(status)) for head, status, _ in posts] == [(HEAD, "failure")]


def test_main_posts_failure_when_the_fresh_reevaluation_raises_an_arbitrary_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_pull(monkeypatch)
    monkeypatch.setattr(oracle_gate, "load_policy_rules", lambda: ((), ()))
    monkeypatch.setattr(oracle_gate, "fetch_statuses", lambda repo, pr, token: [])
    monkeypatch.setattr(oracle_gate, "fetch_base_change_date", lambda repo, pr, token: None)
    monkeypatch.setattr(oracle_gate, "fetch_comments", lambda repo, pr, token: [])
    monkeypatch.setattr(oracle_gate, "fetch_reviews", lambda repo, pr, token: [])
    monkeypatch.setattr(oracle_gate, "annotate_receipt", lambda token, reviews, head_sha, base_date=None: None)
    calls: list[int] = []

    def threads(repo: str, pr: int, token: str) -> list[Any]:
        calls.append(1)
        if len(calls) > 1:
            raise ConnectionResetError("reset")
        return []

    monkeypatch.setattr(oracle_gate, "fetch_threads", threads)
    posts = _record_posts(monkeypatch)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7"]) == oracle_gate.ERROR
    assert [(head, oracle_gate.gate_state(status)) for head, status, _ in posts] == [(HEAD, "failure")]


def test_pull_read_that_fails_twice_then_succeeds_evaluates_normally(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    calls = _stub_unreadable_pull(monkeypatch, failures=2)
    posts = _record_posts(monkeypatch)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7"]) in (oracle_gate.PASS, oracle_gate.WAITING)
    assert len(calls) == 4
    assert sleeps == [2, 4]
    assert [head for head, _, _ in posts] == [HEAD]


def test_pull_read_failing_three_times_posts_failure_to_the_given_head(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    calls = _stub_unreadable_pull(monkeypatch, failures=99)
    posts = _record_posts(monkeypatch)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7", "--head-sha", OTHER_HEAD]) == oracle_gate.ERROR
    assert len(calls) == 3
    assert sleeps == [2, 4]
    assert [(head, oracle_gate.gate_state(status)) for head, status, _ in posts] == [(OTHER_HEAD, "failure")]


def _stub_graphql_head(monkeypatch: pytest.MonkeyPatch, result: Any) -> list[tuple[str, int]]:
    calls: list[tuple[str, int]] = []

    def head(token: str, repo: str, pr: int) -> Any:
        calls.append((repo, pr))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(oracle_gate, "fetch_head_oid", head)
    return calls


def test_pull_read_failing_three_times_with_a_graphql_head_posts_failure_to_it(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    calls = _stub_unreadable_pull(monkeypatch, failures=99)
    graphql = _stub_graphql_head(monkeypatch, OTHER_HEAD)
    posts = _record_posts(monkeypatch)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7"]) == oracle_gate.ERROR
    assert len(calls) == 3
    assert sleeps == [2, 4]
    assert graphql == [("o/r", 7)]
    assert [(head, oracle_gate.gate_state(status)) for head, status, _ in posts] == [(OTHER_HEAD, "failure")]


def test_pull_read_and_graphql_read_both_failing_posts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_unreadable_pull(monkeypatch, failures=99)
    graphql = _stub_graphql_head(monkeypatch, oracle_gate.CheckError("graphql down"))
    posts = _record_posts(monkeypatch)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7"]) == oracle_gate.ERROR
    assert graphql == [("o/r", 7)]
    assert posts == []


@pytest.mark.parametrize("value", [None, "", "abc", "A" * 40, 7])
def test_an_unusable_graphql_head_posts_nothing(monkeypatch: pytest.MonkeyPatch, value: Any) -> None:
    _stub_unreadable_pull(monkeypatch, failures=99)
    _stub_graphql_head(monkeypatch, value)
    posts = _record_posts(monkeypatch)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7"]) == oracle_gate.ERROR
    assert posts == []


def test_a_given_head_is_used_without_the_graphql_read(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_unreadable_pull(monkeypatch, failures=99)
    graphql = _stub_graphql_head(monkeypatch, HEAD)
    posts = _record_posts(monkeypatch)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7", "--head-sha", OTHER_HEAD]) == oracle_gate.ERROR
    assert graphql == []
    assert [head for head, _, _ in posts] == [OTHER_HEAD]


def test_no_post_never_reads_the_graphql_head(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_unreadable_pull(monkeypatch, failures=99)
    graphql = _stub_graphql_head(monkeypatch, HEAD)
    posts = _record_posts(monkeypatch)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7", "--no-post"]) == oracle_gate.ERROR
    assert graphql == []
    assert posts == []


def test_a_readable_pull_wins_over_the_graphql_head(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_unreadable_pull(monkeypatch, failures=0)
    graphql = _stub_graphql_head(monkeypatch, OTHER_HEAD)
    posts = _record_posts(monkeypatch)
    oracle_gate.main(["--repo", "o/r", "--pr", "7"])
    assert graphql == []
    assert [head for head, _, _ in posts] == [HEAD]


def test_pull_read_failure_with_a_head_never_posts_under_no_post(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_unreadable_pull(monkeypatch, failures=99)
    posts = _record_posts(monkeypatch)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7", "--head-sha", OTHER_HEAD, "--no-post"]) == oracle_gate.ERROR
    assert posts == []


def test_a_readable_pull_request_ignores_the_given_head(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_unreadable_pull(monkeypatch, failures=0)
    posts = _record_posts(monkeypatch)
    oracle_gate.main(["--repo", "o/r", "--pr", "7", "--head-sha", OTHER_HEAD])
    assert [head for head, _, _ in posts] == [HEAD]


def test_the_given_head_does_not_post_for_an_ungated_base(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pull(monkeypatch, base="feature")
    posts = _record_posts(monkeypatch)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7", "--head-sha", OTHER_HEAD]) == oracle_gate.PASS
    assert posts == []


def test_pull_read_failing_on_the_fresh_reevaluation_posts_failure_to_the_known_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    calls: list[int] = []

    def flaky(repo: str, pr: int, token: str) -> tuple[str, int, str]:
        calls.append(1)
        if len(calls) == 1:
            return HEAD, oracle_gate.PASS, "ok"
        raise oracle_gate.PullReadFailed("down")

    monkeypatch.setattr(oracle_gate, "evaluate", flaky)
    posts = _record_posts(monkeypatch)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7"]) == oracle_gate.ERROR
    assert [(head, oracle_gate.gate_state(status)) for head, status, _ in posts] == [(HEAD, "failure")]


@pytest.mark.parametrize("value", ["", "abc", "A" * 40, "g" * 40, "a" * 39, "a" * 41])
def test_an_invalid_head_sha_is_rejected(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    posts = _record_posts(monkeypatch)
    with pytest.raises(SystemExit) as raised:
        oracle_gate.main(["--repo", "o/r", "--pr", "7", "--head-sha", value])
    assert raised.value.code == 2
    assert posts == []


def test_fetch_head_oid_reads_the_pull_request_head_through_graphql(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, dict[str, Any]]] = []

    def request(token: str, url: str, body: dict[str, Any] | None = None) -> Any:
        assert body is not None
        sent.append((url, body))
        return {"data": {"repository": {"pullRequest": {"headRefOid": HEAD}}}}

    monkeypatch.setattr(oracle_review_check, "_request", request)
    assert oracle_review_check.fetch_head_oid("token", "o/r", 7) == HEAD
    assert sent[0][0].endswith("/graphql")
    assert sent[0][1]["variables"] == {"owner": "o", "name": "r", "number": 7}
    assert "headRefOid" in sent[0][1]["query"]


def test_fetch_head_oid_raises_on_graphql_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oracle_review_check, "_request", lambda token, url, body=None: {"errors": ["boom"]})
    with pytest.raises(oracle_review_check.CheckError):
        oracle_review_check.fetch_head_oid("token", "o/r", 7)


def test_process_exit_gives_waiting_a_code_distinct_from_crashes() -> None:
    assert oracle_gate.process_exit(oracle_gate.PASS) == 0
    assert oracle_gate.process_exit(oracle_gate.ERROR) == oracle_gate.ERROR
    assert oracle_gate.process_exit(oracle_gate.WAITING) == oracle_gate.WAITING_EXIT
    assert oracle_gate.WAITING_EXIT not in (0, 1, oracle_gate.ERROR)


def test_script_entry_point_exits_through_the_process_mapping() -> None:
    env = {key: value for key, value in os.environ.items() if key not in ("GITHUB_TOKEN", "GH_TOKEN")}
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", "o/r", "--pr", "7"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == oracle_gate.ERROR
    assert "GITHUB_TOKEN is not set" in result.stderr


@pytest.mark.parametrize(
    ("status", "expected"),
    [("PASS", "0"), ("WAITING", "3"), ("ERROR", "2")],
)
def test_run_reports_the_process_exit_in_the_result_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: str, expected: str
) -> None:
    target = tmp_path / "result"
    monkeypatch.setenv(oracle_gate.RESULT_FILE_ENV, str(target))
    monkeypatch.setattr(oracle_gate, "main", lambda argv=None: getattr(oracle_gate, status))
    assert oracle_gate.run([]) == int(expected)
    assert target.read_text(encoding="utf-8") == f"{expected}\n"


def test_run_writes_no_result_file_without_the_variable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(oracle_gate.RESULT_FILE_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(oracle_gate, "main", lambda argv=None: oracle_gate.WAITING)
    assert oracle_gate.run([]) == oracle_gate.WAITING_EXIT
    assert list(tmp_path.iterdir()) == []


def test_a_crash_before_the_exit_leaves_no_result(tmp_path: Path) -> None:
    target = tmp_path / "result"
    env = {key: value for key, value in os.environ.items() if key not in ("GITHUB_TOKEN", "GH_TOKEN")}
    env[oracle_gate.RESULT_FILE_ENV] = str(target)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", "o/r", "--pr", "not-a-number"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert not target.exists()
