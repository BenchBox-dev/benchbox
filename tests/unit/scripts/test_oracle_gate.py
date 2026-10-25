from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "_project/scripts/oracle_gate.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("oracle_gate", SCRIPT)
assert SPEC and SPEC.loader
oracle_gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = oracle_gate
SPEC.loader.exec_module(oracle_gate)

oracle_review_check = sys.modules["oracle_review_check"]

pytestmark = [pytest.mark.unit, pytest.mark.fast]

HEAD = "a" * 40
AFTER_HEAD = "2026-10-01T13:00:00Z"
SOUNDNESS_FILES = ["benchbox/core/equivalence/checker.py", "docs/index.md"]
OTHER_FILES = ["docs/index.md", "notes.md"]


def _review(body: str, **over: Any) -> dict[str, Any]:
    review = {
        "login": "benchbox-oracle[bot]",
        "user_type": "Bot",
        "commit_id": HEAD,
        "state": "COMMENTED",
        "submitted_at": AFTER_HEAD,
        "body": body,
    }
    return {**review, **over}


def _success_review(**over: Any) -> dict[str, Any]:
    return _review(f"### oracle-verdict: success for `{HEAD}`", **over)


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
    reviews = [_success_review()]
    expected = oracle_review_check.decide(HEAD, SOUNDNESS_FILES, reviews, [], _matcher())
    assert expected[0] == oracle_review_check.PASS
    assert oracle_gate.decide(HEAD, SOUNDNESS_FILES, reviews, [], _matcher()) == expected


def test_ship_with_an_unresolved_oracle_thread_waits() -> None:
    reviews = [_success_review()]
    expected = oracle_review_check.decide(HEAD, SOUNDNESS_FILES, reviews, [_thread(False)], _matcher())
    assert expected[0] == oracle_review_check.WAITING
    assert "unresolved oracle review thread" in expected[1]
    assert oracle_gate.decide(HEAD, SOUNDNESS_FILES, reviews, [_thread(False)], _matcher()) == expected


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


def test_absent_review_waits() -> None:
    expected = oracle_review_check.decide(HEAD, SOUNDNESS_FILES, [], [], _matcher())
    assert expected[0] == oracle_review_check.WAITING
    assert oracle_gate.decide(HEAD, SOUNDNESS_FILES, [], [], _matcher()) == expected


def test_gate_state_maps_pass_to_success_and_everything_else_to_failure() -> None:
    assert oracle_gate.gate_state(oracle_gate.PASS) == "success"
    assert oracle_gate.gate_state(oracle_gate.WAITING) == "failure"
    assert oracle_gate.gate_state(oracle_gate.ERROR) == "failure"


def test_status_payload_posts_the_gate_context() -> None:
    assert oracle_gate.GATE_CONTEXT == "oracle-gate"
    payload = oracle_gate.status_payload(oracle_gate.PASS, "oracle-review: pass (oracle review of " + HEAD + ")")
    assert payload == {
        "state": "success",
        "context": "oracle-gate",
        "description": "oracle-review: pass (oracle review of " + HEAD + ")",
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


def test_main_reports_a_policy_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(
        oracle_gate,
        "fetch_pull",
        lambda repo, pr, token: {"base": {"ref": "develop"}, "head": {"sha": HEAD}, "changed_files": 1},
    )
    monkeypatch.setattr(oracle_gate, "fetch_files", lambda repo, pr, token: [{"filename": "x"}])

    def failing_rules() -> tuple[tuple[str, ...], tuple[str, ...]]:
        raise oracle_gate.CheckError("no policy")

    monkeypatch.setattr(oracle_gate, "load_policy_rules", failing_rules)
    assert oracle_gate.main(["--repo", "o/r", "--pr", "7"]) == oracle_gate.ERROR


def test_main_posts_nothing_for_a_pull_request_that_does_not_target_develop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(
        oracle_gate,
        "fetch_pull",
        lambda repo, pr, token: {"base": {"ref": "feature"}, "head": {"sha": HEAD}, "changed_files": 1},
    )

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("must not evaluate or post")

    for name in ("fetch_files", "fetch_reviews", "fetch_threads", "post_status"):
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
