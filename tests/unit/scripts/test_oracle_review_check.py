from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "_project/scripts/oracle_review_check.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("oracle_review_check", SCRIPT)
assert SPEC and SPEC.loader
oracle_review_check = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = oracle_review_check
SPEC.loader.exec_module(oracle_review_check)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

HEAD = "a" * 40
OLDER = "b" * 40
HEAD_DATE = "2026-10-01T12:00:00Z"
BEFORE_HEAD = "2026-10-01T11:00:00Z"
AFTER_HEAD = "2026-10-01T13:00:00Z"
SOUNDNESS_FILES = ["benchbox/core/equivalence/checker.py", "README.md"]
OTHER_FILES = ["docs/index.md", "notes.md"]
CONNECTOR = "chatgpt-codex-connector[bot]"
RETARGETED = "2026-10-01T14:00:00Z"
AFTER_RETARGET = "2026-10-01T15:00:00Z"
ORACLE = "benchbox-oracle[bot]"


def _oracle_review(state: str = "success", submitted_at: str = AFTER_HEAD, **over: Any) -> dict[str, Any]:
    review = {
        "login": ORACLE,
        "user_type": "Bot",
        "commit_id": HEAD,
        "state": "COMMENTED",
        "submitted_at": submitted_at,
        "body": f"### oracle-verdict: {state} for `{over.get('commit_id', HEAD)}`\n\nDetails.",
    }
    return {**review, **over}


def _thread(resolved: bool, author: str = "benchbox-oracle", author_type: str = "Bot") -> dict[str, Any]:
    return {"resolved": resolved, "author": author, "author_type": author_type}


def _oracle(**kwargs: Any) -> tuple[int, str]:
    return oracle_review_check.decide(
        HEAD,
        kwargs.pop("files", SOUNDNESS_FILES),
        kwargs.pop("reviews", [_oracle_review()]),
        kwargs.pop("threads", []),
        kwargs.pop("paths", oracle_review_check.path_matcher(2, 2, *oracle_review_check._review_rules())),
        kwargs.pop("base_date", None),
        kwargs.pop("comments", []),
    )


def _decide(**kwargs: Any) -> tuple[int, str]:
    return _oracle(**{"reviews": [], **kwargs})


def test_exempt_only_change_skips() -> None:
    assert _decide(files=OTHER_FILES) == (0, "oracle-review: only exempt paths changed")


def test_machine_read_doc_needs_review() -> None:
    assert _decide(files=["README.md"])[0] == 1


def test_code_outside_the_soundness_list_needs_review() -> None:
    assert _decide(files=["benchbox/utils/row_compare.py"])[0] == 1


def test_gate_template_matching_the_markdown_exemption_needs_review() -> None:
    assert _decide(files=["_project/scripts/oracle_reviewers/templates/gate.md"])[0] == 1


def test_soundness_manifest_beats_the_markdown_exemption() -> None:
    assert _decide(files=[".github/PULL_REQUEST_TEMPLATE.md"])[0] == 1


def test_exempt_docs_are_skipped() -> None:
    assert _decide(files=["docs/foo.md"]) == (0, "oracle-review: only exempt paths changed")


def test_oracle_success_review_on_the_head_passes() -> None:
    assert _oracle() == (0, f"oracle-review: pass (oracle review of {HEAD})")


def test_no_review_waits_and_names_the_standin_marker() -> None:
    assert _decide() == (
        1,
        f"oracle-review: waiting for the oracle's review of {HEAD}, or a stand-in attestation "
        f"'Stand-in oracle review: APPROVE {HEAD}' from a listed attester; rerun this check after it lands",
    )


@pytest.mark.parametrize(
    "reviews",
    [
        [_oracle_review("failure")],
        [_oracle_review("pending")],
        [_oracle_review("success", submitted_at=BEFORE_HEAD), _oracle_review("failure")],
        [_oracle_review(body="No verdict header")],
        [_oracle_review(body=f"### oracle-verdict: success for `{OLDER}`")],
        [_oracle_review(body=f"Intro\n### oracle-verdict: success for `{HEAD}`")],
    ],
    ids=["failure", "pending", "latest-failure", "no-header", "other-sha", "header-not-first"],
)
def test_the_latest_oracle_review_must_report_success(reviews: list[dict[str, Any]]) -> None:
    status, message = _oracle(reviews=reviews)
    assert status == 1
    assert "the oracle's latest review" in message


def test_latest_oracle_success_wins() -> None:
    reviews = [_oracle_review("failure", submitted_at=BEFORE_HEAD), _oracle_review("success")]
    assert _oracle(reviews=reviews)[0] == 0


@pytest.mark.parametrize(
    "review",
    [
        _oracle_review(commit_id=OLDER),
        _oracle_review(state="PENDING"),
        _oracle_review(state="DISMISSED"),
        _oracle_review(login="benchbox-oracle", user_type="User"),
        _oracle_review(user_type=None),
        _oracle_review(login=CONNECTOR, user_type="Bot"),
        _oracle_review(login="joeharris76", user_type="User"),
    ],
    ids=["older-commit", "pending", "dismissed", "user-account", "no-type", "connector", "owner"],
)
def test_only_the_apps_review_of_the_head_counts(review: dict[str, Any]) -> None:
    assert _oracle(reviews=[review])[0] == 1


def test_open_oracle_threads_block_but_other_threads_do_not() -> None:
    status, message = _oracle(threads=[_thread(False)])
    assert status == 1
    assert "1 unresolved oracle review thread(s)" in message
    others = [
        _thread(True),
        _thread(False, author="benchbox-oracle", author_type="User"),
        _thread(False, author="chatgpt-codex-connector", author_type="Bot"),
        _thread(False, author="joeharris76", author_type="User"),
    ]
    assert _oracle(threads=others)[0] == 0


def test_resolving_the_last_oracle_thread_turns_a_success_verdict_into_a_pass() -> None:
    assert _oracle(threads=[_thread(True), _thread(False)])[0] == 1
    assert _oracle(threads=[_thread(True), _thread(True)]) == (0, f"oracle-review: pass (oracle review of {HEAD})")
    assert _oracle(threads=[_thread(True), _thread(False)])[0] == 1


def test_the_open_thread_message_names_the_recovery_command() -> None:
    message = _oracle(threads=[_thread(False)])[1]
    assert "make oracle-review-recover PR=<number>" in message


def test_resolved_threads_without_a_verdict_still_wait() -> None:
    assert _decide(threads=[_thread(True)])[0] == 1


def test_rename_with_a_non_exempt_side_needs_review() -> None:
    files = oracle_review_check.changed_paths(
        [{"filename": "docs/moved.py", "previous_filename": "benchbox/core/equivalence/checker.py"}]
    )
    assert files == ["docs/moved.py", "benchbox/core/equivalence/checker.py"]
    assert _decide(files=files)[0] == 1


def test_rename_within_exempt_paths_skips() -> None:
    files = oracle_review_check.changed_paths([{"filename": "docs/new.md", "previous_filename": "docs/old.md"}])
    assert _decide(files=files) == (0, "oracle-review: only exempt paths changed")


def test_changed_paths_without_a_rename_keeps_only_the_filename() -> None:
    assert oracle_review_check.changed_paths([{"filename": "README.md"}]) == ["README.md"]


def test_truncated_file_list_needs_review() -> None:
    matcher = oracle_review_check.path_matcher(listed_files=3000, changed_files=3001)
    assert matcher(OTHER_FILES) is True
    assert _decide(files=OTHER_FILES, paths=matcher)[0] == 1


def test_complete_file_list_uses_the_exempt_paths() -> None:
    matcher = oracle_review_check.path_matcher(listed_files=2, changed_files=2, exempt_paths=("docs/*", "*.md"))
    assert matcher(OTHER_FILES) is False
    assert matcher(SOUNDNESS_FILES) is True


def test_gate_paths_beat_the_exemption() -> None:
    matcher = oracle_review_check.path_matcher(
        listed_files=1, changed_files=1, exempt_paths=("*.md",), gate_paths=("gate/*",)
    )
    assert matcher(["gate/policy.md"]) is True
    assert matcher(["docs/notes.md"]) is False


def test_review_rules_come_from_the_reviewer_policy() -> None:
    exempt, gate = oracle_review_check._review_rules()
    assert exempt == ()
    assert "_project/scripts/oracle_reviewers/*" in gate


def test_unreadable_policy_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(path: object) -> object:
        raise ValueError("bad policy")

    monkeypatch.setattr("oracle_reviewers.policy.load_policy", broken)
    assert oracle_review_check._review_rules() == ((), ())
    assert oracle_review_check.path_matcher(1, 1)(["README.md"]) is True


def test_review_before_a_retarget_waits() -> None:
    assert _oracle(base_date=RETARGETED)[0] == oracle_review_check.WAITING


def test_review_after_a_retarget_passes() -> None:
    assert _oracle(reviews=[_oracle_review(submitted_at=AFTER_RETARGET)], base_date=RETARGETED)[0] == 0


def test_latest_base_change_picks_the_newest_retarget() -> None:
    events = [
        {"event": "base_ref_changed", "created_at": "2026-10-01T10:00:00Z"},
        {"event": "commented", "created_at": "2026-10-01T16:00:00Z"},
        {"event": "base_ref_changed", "created_at": RETARGETED},
    ]
    assert oracle_review_check.latest_base_change(events) == RETARGETED
    assert oracle_review_check.latest_base_change([{"event": "commented", "created_at": RETARGETED}]) is None


def _attestation(
    sha: str = HEAD,
    login: str = "joeharris76",
    created_at: str = AFTER_HEAD,
    prefix: str = "",
    updated_at: str | None = None,
    user_type: str = "User",
    body: str | None = None,
) -> dict[str, Any]:
    text = body or f"{prefix}Stand-in oracle review: APPROVE {sha}\n"
    return {
        "login": login,
        "user_type": user_type,
        "body": text,
        "created_at": created_at,
        "updated_at": updated_at or created_at,
    }


def test_standin_attestation_for_the_head_passes() -> None:
    status, message = _decide(comments=[_attestation()])
    assert status == oracle_review_check.PASS
    assert "stand-in review attested by joeharris76" in message


@pytest.mark.parametrize(
    "comment",
    [
        _attestation(sha=OLDER),
        _attestation(login="someone-else"),
        _attestation(prefix="> "),
        _attestation(body="Looks good to me"),
        _attestation(updated_at="2026-10-01T14:00:00Z"),
        _attestation(login="joeharris76[bot]", user_type="Bot"),
        _attestation(user_type="Bot"),
        _attestation(body=f"```\nStand-in oracle review: APPROVE {HEAD}\n```\n"),
        _attestation(body=f"- draft:\n  ```\nStand-in oracle review: APPROVE {HEAD}\n  ```\n"),
        _attestation(body=f"```\nStand-in oracle review: APPROVE {HEAD}\n"),
        _attestation(body=f"~~~\nStand-in oracle review: APPROVE {HEAD}\n~~~\n"),
        _attestation(body=f"<!--\nStand-in oracle review: APPROVE {HEAD}\n-->\n"),
        _attestation(body=f"<PRE>\nStand-in oracle review: APPROVE {HEAD}\n</PRE>\n"),
        _attestation(body=f"see `\nStand-in oracle review: APPROVE {HEAD}\n`\n"),
        _attestation(body=f'[a]: /u "\nStand-in oracle review: APPROVE {HEAD}\n"\n'),
        _attestation(body=f"<details>\nStand-in oracle review: APPROVE {HEAD}\n</details>\n"),
        _attestation(body=f"<code>\nStand-in oracle review: APPROVE {HEAD}\n</code>\n"),
        _attestation(body=f"No P0/P1 findings.\n\nStand-in oracle review: APPROVE {HEAD}\n"),
        _attestation(body=f"I would write Stand-in oracle review: APPROVE {HEAD} here"),
        _attestation(sha=HEAD.upper()),
    ],
    ids=[
        "older-commit",
        "not-an-attester",
        "quoted",
        "no-marker",
        "edited",
        "bot-suffix",
        "bot-account",
        "fenced",
        "indented-fence",
        "unclosed-fence",
        "tilde-fence",
        "html-comment",
        "pre-block",
        "inline-code",
        "link-title",
        "details-block",
        "code-block",
        "extra-text",
        "mid-line",
        "uppercase-sha",
    ],
)
def test_standin_attestation_must_be_exact_current_and_authorized(comment: dict[str, Any]) -> None:
    status, _ = _decide(comments=[comment])
    assert status == oracle_review_check.WAITING


def test_standin_attestation_does_not_override_open_oracle_threads() -> None:
    status, message = _decide(comments=[_attestation()], threads=[_thread(False)])
    assert status == oracle_review_check.WAITING
    assert "unresolved oracle review thread" in message


def test_standin_attestation_before_a_retarget_waits() -> None:
    assert _decide(comments=[_attestation(created_at=AFTER_HEAD)], base_date=RETARGETED)[0] == 1


def test_a_later_valid_attestation_counts_after_an_earlier_invalid_one() -> None:
    assert _decide(comments=[_attestation(sha=OLDER), _attestation()])[0] == oracle_review_check.PASS


def test_the_oracle_review_message_wins_over_a_standin() -> None:
    assert _oracle(comments=[_attestation()]) == (0, f"oracle-review: pass (oracle review of {HEAD})")


def test_fetch_comments_maps_author_type_and_edit_time(monkeypatch: pytest.MonkeyPatch) -> None:
    item = {
        "user": {"login": "joeharris76", "type": "User"},
        "body": "x",
        "created_at": AFTER_HEAD,
        "updated_at": AFTER_HEAD,
    }
    monkeypatch.setattr(oracle_review_check, "_paginate", lambda _token, _path: [item])
    assert oracle_review_check.fetch_comments("t", "o/r", 1) == [
        {"login": "joeharris76", "user_type": "User", "body": "x", "created_at": AFTER_HEAD, "updated_at": AFTER_HEAD}
    ]


def _standin_comment() -> dict[str, Any]:
    return {
        "login": "joeharris76",
        "user_type": "User",
        "body": f"Stand-in oracle review: APPROVE {HEAD}",
        "created_at": AFTER_HEAD,
        "updated_at": AFTER_HEAD,
    }


def test_standin_after_a_failing_oracle_review_overrides_it_but_not_open_threads() -> None:
    failing = [_oracle_review("failure", submitted_at=HEAD_DATE)]
    assert _oracle(reviews=failing, comments=[_standin_comment()]) == (
        0,
        f"oracle-review: pass (stand-in review attested by joeharris76 for {HEAD})",
    )
    early = {**_standin_comment(), "created_at": BEFORE_HEAD, "updated_at": BEFORE_HEAD}
    assert _oracle(reviews=failing, comments=[early])[0] == 1
    assert _oracle(reviews=[], comments=[early])[0] == 0
    threads = [{"resolved": False, "author": "benchbox-oracle", "author_type": "Bot"}]
    assert _oracle(reviews=failing, comments=[_standin_comment()], threads=threads)[0] == 1


LATER = "2026-10-01T14:00:00Z"
LATEST = "2026-10-01T15:00:00Z"


def _standin_at(created_at: str) -> dict[str, Any]:
    return {**_standin_comment(), "created_at": created_at, "updated_at": created_at}


def test_pending_reviews_do_not_void_a_standin_posted_after_the_last_verdict() -> None:
    reviews = [
        _oracle_review("failure", submitted_at=HEAD_DATE),
        _oracle_review("pending", submitted_at=LATER),
        _oracle_review("pending", submitted_at=LATEST),
    ]
    assert _oracle(reviews=reviews, comments=[_standin_at(AFTER_HEAD)])[0] == 0


def test_a_standin_before_the_last_verdict_still_waits_after_later_pending_reviews() -> None:
    reviews = [_oracle_review("failure", submitted_at=AFTER_HEAD), _oracle_review("pending", submitted_at=LATEST)]
    assert _oracle(reviews=reviews, comments=[_standin_at(HEAD_DATE)])[0] == 1


def test_a_review_without_a_verdict_does_not_advance_the_standin_window() -> None:
    bare = {**_oracle_review(submitted_at=LATER), "body": "Carried forward without a verdict."}
    reviews = [_oracle_review("failure", submitted_at=HEAD_DATE), bare]
    assert _oracle(reviews=reviews, comments=[_standin_at(AFTER_HEAD)])[0] == 0


def test_a_review_for_another_head_does_not_advance_the_standin_window() -> None:
    stale = {
        **_oracle_review("failure", submitted_at=LATER),
        "body": f"### oracle-verdict: failure for `{OLDER}`",
    }
    reviews = [_oracle_review("failure", submitted_at=HEAD_DATE), stale]
    assert _oracle(reviews=reviews, comments=[_standin_at(AFTER_HEAD)])[0] == 0


def test_a_later_decisive_review_voids_an_earlier_standin() -> None:
    reviews = [_oracle_review("failure", submitted_at=HEAD_DATE), _oracle_review("failure", submitted_at=LATER)]
    assert _oracle(reviews=reviews, comments=[_standin_at(AFTER_HEAD)])[0] == 1


def test_a_standin_after_pending_reviews_never_overrides_an_open_thread() -> None:
    reviews = [_oracle_review("failure", submitted_at=HEAD_DATE), _oracle_review("pending", submitted_at=LATER)]
    threads = [{"resolved": False, "author": "benchbox-oracle", "author_type": "Bot"}]
    assert _oracle(reviews=reviews, comments=[_standin_at(LATEST)], threads=threads)[0] == 1


def test_the_verdict_still_comes_from_the_latest_review() -> None:
    reviews = [_oracle_review("failure", submitted_at=HEAD_DATE), _oracle_review("success", submitted_at=LATER)]
    assert _oracle(reviews=reviews)[0] == 0
    reviews.reverse()
    assert _oracle(reviews=reviews)[0] == 0
    reviews = [_oracle_review("success", submitted_at=HEAD_DATE), _oracle_review("failure", submitted_at=LATER)]
    assert _oracle(reviews=reviews)[0] == 1


def test_force_push_back_to_an_attested_head_keeps_the_attestation() -> None:
    comment = {**_standin_comment(), "created_at": BEFORE_HEAD, "updated_at": BEFORE_HEAD}
    assert _decide(comments=[comment])[0] == 0


def _stub_main(monkeypatch: pytest.MonkeyPatch, reviews: list[dict[str, Any]]) -> None:
    check = oracle_review_check
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(check, "fetch_pull", lambda token, repo, pr: {"head": {"sha": HEAD}, "changed_files": 1})
    monkeypatch.setattr(check, "fetch_files", lambda token, repo, pr: [{"filename": SOUNDNESS_FILES[0]}])
    monkeypatch.setattr(check, "fetch_reviews", lambda token, repo, pr: reviews)
    monkeypatch.setattr(check, "fetch_threads", lambda token, repo, pr: [])
    monkeypatch.setattr(check, "fetch_base_change_date", lambda token, repo, pr: None)
    monkeypatch.setattr(check, "fetch_comments", lambda token, repo, pr: [])


def test_main_requires_the_oracle_and_prints_no_parity(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    _stub_main(monkeypatch, [_oracle_review()])
    assert oracle_review_check.main(["--repo", "o/r", "--pr", "7", "--signal", "oracle"]) == 0
    assert capsys.readouterr().out.splitlines() == [f"oracle-review: pass (oracle review of {HEAD})"]
    _stub_main(monkeypatch, [{**_oracle_review(), "login": CONNECTOR}])
    assert oracle_review_check.main(["--repo", "o/r", "--pr", "7"]) == 1


@pytest.mark.parametrize(
    ("reviews", "expected_status"),
    [
        ([], oracle_review_check.WAITING),
        ([_oracle_review("pending")], oracle_review_check.WAITING),
        ([_oracle_review("failure")], oracle_review_check.WAITING),
        ([_oracle_review("success")], oracle_review_check.PASS),
    ],
    ids=["absent", "pending", "failure", "success"],
)
def test_main_requires_an_oracle_decision_for_verdict_workflow_only(
    monkeypatch: pytest.MonkeyPatch, reviews: list[dict[str, Any]], expected_status: int
) -> None:
    _stub_main(monkeypatch, reviews)
    monkeypatch.setattr(
        oracle_review_check,
        "fetch_files",
        lambda token, repo, pr: [{"filename": ".github/workflows/oracle-verdict.yml"}],
    )
    assert oracle_review_check.main(["--repo", "o/r", "--pr", "7"]) == expected_status


def test_the_connector_signal_is_no_longer_accepted() -> None:
    with pytest.raises(SystemExit):
        oracle_review_check.main(["--repo", "o/r", "--pr", "7", "--signal", "connector"])


def test_fetch_threads_and_reviews_keep_the_author_type(monkeypatch: pytest.MonkeyPatch) -> None:
    node = {"isResolved": False, "comments": {"nodes": [{"author": {"__typename": "Bot", "login": "benchbox-oracle"}}]}}
    page = {
        "data": {
            "repository": {"pullRequest": {"reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": [node]}}}
        }
    }
    monkeypatch.setattr(oracle_review_check, "_request", lambda token, url, body=None: page)
    assert oracle_review_check.fetch_threads("t", "o/r", 7) == [
        {"resolved": False, "author": "benchbox-oracle", "author_type": "Bot"}
    ]
    review = {
        "user": {"login": ORACLE, "type": "Bot"},
        "commit_id": HEAD,
        "state": "COMMENTED",
        "submitted_at": AFTER_HEAD,
        "body": "b",
    }
    monkeypatch.setattr(oracle_review_check, "_paginate", lambda token, path: [review])
    fetched = oracle_review_check.fetch_reviews("t", "o/r", 7)[0]
    assert (fetched["user_type"], fetched["body"]) == ("Bot", "b")


def _refusal_body() -> str:
    from _project.scripts.oracle_reviewers import protocol, report

    patches = {"aaaaaaaa": "11111111"}
    plan = {
        "status_context": "oracle-verdict",
        "head_sha": HEAD,
        "mode": "shadow",
        "tier": "very-high",
        "tier_reasons": [],
        "findings_delivery": "review",
        "protocol": {
            "cycle": 3,
            "round": 0,
            "base_ref": "develop",
            "patch_map": patches,
            "patch_digest": "d" * 32,
            "strikes": 3,
            "max_do_not_ship": 3,
            "next_id": 1,
        },
    }
    return report.refused(plan).body


def test_a_refused_pull_request_is_told_to_open_a_new_one() -> None:
    status, message = _oracle(reviews=[_oracle_review(body=_refusal_body())])
    assert status == oracle_review_check.WAITING
    assert "refused to review" in message and "close this pull request and open a new one" in message
    failure_status, failure_message = _oracle(reviews=[_oracle_review("failure")])
    assert failure_status == oracle_review_check.WAITING and "refused" not in failure_message


def test_a_refusal_header_keeps_the_verdict_line_the_check_parses() -> None:
    assert _refusal_body().startswith(f"### oracle-verdict: failure for `{HEAD}`\n")


def test_a_standin_after_a_refusal_passes() -> None:
    reviews = [_oracle_review(body=_refusal_body(), submitted_at=AFTER_HEAD)]
    later = "2026-10-08T23:59:59Z"
    status, message = _oracle(reviews=reviews, comments=[_attestation(created_at=later)])
    assert status == oracle_review_check.PASS and "stand-in review attested" in message
    early = _oracle(reviews=reviews, comments=[_attestation(created_at=BEFORE_HEAD)])
    assert early[0] == oracle_review_check.WAITING


def test_a_standin_never_overrides_an_open_oracle_thread_after_a_refusal() -> None:
    reviews = [_oracle_review(body=_refusal_body())]
    threads = [{"resolved": False, "author": "benchbox-oracle", "author_type": "Bot"}]
    status, message = _oracle(
        reviews=reviews, threads=threads, comments=[_attestation(created_at="2026-10-08T23:59:59Z")]
    )
    assert status == oracle_review_check.WAITING and "unresolved oracle review thread" in message


@pytest.mark.parametrize("context", ["oracle-verdict", "oracle-review-shadow"], ids=["current", "legacy"])
def test_the_check_reads_the_current_and_the_legacy_first_line(context: str) -> None:
    success = _oracle_review(body=f"### {context}: success for `{HEAD}`\n\nDetails.")
    failure = _oracle_review("failure", body=f"### {context}: failure for `{HEAD}`\n\nDetails.")
    assert _oracle(reviews=[success])[0] == 0
    status, message = _oracle(reviews=[failure])
    assert status != 0 and "the oracle's latest review" in message


def test_new_reviews_open_with_the_current_first_line() -> None:
    assert oracle_review_check.ORACLE_CONTEXT == "oracle-verdict"
    assert _refusal_body().startswith(f"### {oracle_review_check.ORACLE_CONTEXT}: failure for `{HEAD}`\n")


def test_a_review_carried_after_a_retarget_satisfies_the_gate_that_the_old_review_did_not() -> None:
    from _project.scripts.oracle_reviewers import protocol, report

    previous = {
        "v": 1,
        "cycle": 1,
        "round": 1,
        "kind": protocol.FIRST,
        "decision": protocol.SHIP,
        "head_sha": HEAD,
        "base_ref": "develop",
        "reviewer": "small",
        "tier": "small",
        "strikes_after": 0,
        "open_defects": [],
        "next_id": 1,
        "patch_digest": "d" * 32,
        "carried": False,
        "summary": "",
    }
    plan = {
        "head_sha": HEAD,
        "base_ref": "develop",
        "reviewed_head": HEAD,
        "status_context": "oracle-verdict",
        "pr": 1,
        "repo": "o/r",
        "run_id": "1",
        "protocol": {
            "cycle": 1,
            "round": 1,
            "base_ref": "develop",
            "patch_map": {},
            "patch_digest": "d" * 32,
            "strikes": 0,
            "max_do_not_ship": 3,
            "previous": previous,
        },
    }
    assert _oracle(reviews=[_oracle_review()], base_date=RETARGETED)[0] == oracle_review_check.WAITING
    carried = report.carried(plan)
    assert protocol.decode_marker(carried.body) is not None
    fresh = _oracle_review(submitted_at=AFTER_RETARGET, body=carried.body)
    assert oracle_review_check.verdict_state(carried.body, HEAD) == "success"
    assert _oracle(reviews=[_oracle_review(), fresh], base_date=RETARGETED)[0] == 0
