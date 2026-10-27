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
LATER = "2026-10-01T14:00:00Z"
LATEST = "2026-10-01T15:00:00Z"
TEST_RUN_UUID = "1" * 32


def _marked_body(run_uuid: str | None = TEST_RUN_UUID, state: str = "success") -> str:
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
    }
    if run_uuid is not None:
        record["run_uuid"] = run_uuid
    return f"### oracle-verdict: {state} for `{HEAD}`\n\nDecision: **SHIP**.\n\n{protocol.encode_marker(record)}"


def _verdict(state: str = "success", created_at: str = AFTER_HEAD, **over: Any) -> dict[str, Any]:
    description = f"large: {state} [run {TEST_RUN_UUID}]" if state == "success" else f"large: {state}"
    status = {
        "id": 1,
        "context": "oracle-verdict",
        "state": state,
        "description": description,
        "login": ORACLE,
        "user_type": "Bot",
        "created_at": created_at,
    }
    return {**status, **over}


def _unbound_status(state: str = "success", created_at: str = AFTER_HEAD, **over: Any) -> dict[str, Any]:
    return _verdict(state=state, created_at=created_at, description=f"large: {state}", **over)


def _thread(resolved: bool, author: str = "benchbox-oracle", author_type: str = "Bot") -> dict[str, Any]:
    return {"resolved": resolved, "author": author, "author_type": author_type}


def _receipt_review(**over: Any) -> dict[str, Any]:
    review = {
        "login": ORACLE,
        "user_type": "Bot",
        "commit_id": HEAD,
        "state": "COMMENTED",
        "submitted_at": HEAD_DATE,
        "body": _marked_body(),
        "last_edited_at": None,
        "includes_created_edit": False,
    }
    return {**review, **over}


def _unmarked_receipt(**over: Any) -> dict[str, Any]:
    return _receipt_review(body=f"### oracle-verdict: success for `{HEAD}`\n\nDetails.", **over)


def _oracle(**kwargs: Any) -> tuple[int, str]:
    return oracle_review_check.decide(
        HEAD,
        kwargs.pop("files", SOUNDNESS_FILES),
        kwargs.pop("statuses", [_verdict()]),
        kwargs.pop("threads", []),
        kwargs.pop("paths", oracle_review_check.path_matcher(2, 2, *oracle_review_check._review_rules())),
        kwargs.pop("base_date", None),
        kwargs.pop("comments", []),
        reviews=kwargs.pop("reviews", [_receipt_review()]),
    )


def _decide(**kwargs: Any) -> tuple[int, str]:
    return _oracle(**{"statuses": [], **kwargs})


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


@pytest.mark.parametrize(
    "scenario",
    [
        {"statuses": [_verdict()], "reviews": []},
        {"statuses": [_verdict()], "reviews": [_receipt_review(last_edited_at=HEAD_DATE)]},
        {"statuses": [_verdict(description="only exempt paths changed")], "reviews": []},
    ],
)
def test_a_success_status_without_a_bound_unedited_receipt_names_the_recovery_not_a_verdict_run(
    scenario: dict[str, Any],
) -> None:
    status, message = _oracle(**scenario)
    assert status == oracle_review_check.WAITING
    assert "push a new head" in message
    assert "a byte-identical rebase is enough" in message
    assert "owner stand-in" in message
    assert "run the verdict" not in message


def test_the_exempt_success_marker_matches_the_report_builder() -> None:
    from _project.scripts.oracle_reviewers import report, retry

    assert oracle_review_check.EXEMPT_SUCCESS_DESCRIPTION == "only exempt paths changed"
    assert (
        report.fixed(retry.SUCCESS, "only exempt paths changed").description
        == oracle_review_check.EXEMPT_SUCCESS_DESCRIPTION
    )


def test_an_exempt_success_verdict_never_satisfies_a_gate_that_requires_a_review() -> None:
    exempt = _verdict("success", description=oracle_review_check.EXEMPT_SUCCESS_DESCRIPTION)
    status, message = _oracle(statuses=[exempt])
    assert status == 1
    assert "require a review of this head" in message
    assert _oracle(statuses=[exempt], files=OTHER_FILES)[0] == 0


def test_a_truncated_file_list_does_not_block_a_reviewed_success_verdict() -> None:
    assert _oracle(statuses=[_verdict("success")])[0] == 0


def test_the_apps_success_verdict_on_the_head_passes() -> None:
    assert _oracle() == (0, f"oracle-review: pass (oracle verdict for {HEAD})")


def test_no_verdict_waits_and_names_the_standin_marker() -> None:
    assert _decide() == (
        1,
        f"oracle-review: waiting for the oracle's verdict on {HEAD}, or a stand-in attestation "
        f"'Stand-in oracle review: APPROVE {HEAD}' from a listed attester; rerun this check after it lands",
    )


@pytest.mark.parametrize(
    "statuses",
    [
        [_verdict("failure")],
        [_verdict("pending")],
        [_verdict("success", created_at=BEFORE_HEAD), _verdict("failure")],
        [_verdict("failure", created_at=BEFORE_HEAD), _verdict("success"), _verdict("pending", created_at=LATEST)],
    ],
    ids=["failure", "pending", "latest-failure", "latest-pending"],
)
def test_the_latest_oracle_verdict_must_be_success(statuses: list[dict[str, Any]]) -> None:
    status, message = _oracle(statuses=statuses)
    assert status == 1
    assert "the oracle's latest verdict" in message


def test_latest_oracle_success_wins() -> None:
    statuses = [_verdict("failure", created_at=BEFORE_HEAD), _verdict("success")]
    assert _oracle(statuses=statuses)[0] == 0


def test_the_latest_verdict_is_chosen_by_time_not_by_listing_order() -> None:
    assert _oracle(statuses=[_verdict("success"), _verdict("failure", created_at=BEFORE_HEAD)])[0] == 0
    assert _oracle(statuses=[_verdict("failure"), _verdict("success", created_at=BEFORE_HEAD)])[0] == 1


def test_verdicts_posted_in_the_same_second_are_ordered_by_id() -> None:
    first = _verdict("failure", id=7)
    second = _verdict("success", id=8)
    assert _oracle(statuses=[second, first])[0] == 0
    assert _oracle(statuses=[_verdict("success", id=7), _verdict("failure", id=8)])[0] == 1


@pytest.mark.parametrize(
    "status",
    [
        _verdict(context="oracle-review-shadow"),
        _verdict(context="oracle-gate"),
        _verdict(context="ci/oracle-verdict"),
        _verdict(login="benchbox-oracle", user_type="User"),
        _verdict(user_type=None),
        _verdict(login=CONNECTOR, user_type="Bot"),
        _verdict(login="joeharris76", user_type="User"),
        _verdict(login=None, user_type=None),
        _verdict(created_at=None),
    ],
    ids=[
        "shadow-context",
        "gate-context",
        "other-context",
        "user-account",
        "no-type",
        "connector",
        "owner",
        "no-creator",
        "no-time",
    ],
)
def test_only_the_apps_verdict_status_counts(status: dict[str, Any]) -> None:
    assert _oracle(statuses=[status])[0] == 1


def test_a_forged_success_does_not_hide_the_apps_failure() -> None:
    forged = _verdict("success", created_at=LATEST, login="joeharris76", user_type="User")
    status, message = _oracle(statuses=[_verdict("failure"), forged])
    assert status == 1 and "the oracle's latest verdict" in message


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
    assert _oracle(threads=[_thread(True), _thread(True)]) == (0, f"oracle-review: pass (oracle verdict for {HEAD})")
    assert _oracle(threads=[_thread(True), _thread(False)])[0] == 1


def test_the_open_thread_message_names_the_required_gate_dispatch() -> None:
    message = _oracle(threads=[_thread(False)])[1]
    assert "gh workflow run oracle-gate.yml --ref develop -f pr=<number>" in message
    assert len(message) <= 140


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


def test_verdict_before_a_retarget_waits() -> None:
    assert _oracle(base_date=RETARGETED)[0] == oracle_review_check.WAITING


def test_verdict_after_a_retarget_passes() -> None:
    assert (
        _oracle(
            statuses=[_verdict(created_at=AFTER_RETARGET)],
            reviews=[_receipt_review(submitted_at=AFTER_RETARGET)],
            base_date=RETARGETED,
        )[0]
        == 0
    )


def test_a_success_status_needs_a_receipt_on_this_pull_request() -> None:
    assert _oracle(statuses=[_verdict()], reviews=[])[0] == 1


def test_a_receipt_from_another_head_does_not_count() -> None:
    assert _oracle(statuses=[_verdict()], reviews=[_receipt_review(commit_id=OLDER)])[0] == 1


def test_a_receipt_from_another_creator_does_not_count() -> None:
    assert _oracle(statuses=[_verdict()], reviews=[_receipt_review(login="joeharris76", user_type="User")])[0] == 1


def test_an_edited_receipt_does_not_count() -> None:
    edited = _receipt_review(last_edited_at=LATER, includes_created_edit=True)
    assert _oracle(statuses=[_verdict()], reviews=[edited])[0] == 1


def test_a_created_edit_flag_on_the_receipt_does_not_count() -> None:
    flagged = _receipt_review(last_edited_at=None, includes_created_edit=True)
    assert _oracle(statuses=[_verdict()], reviews=[flagged])[0] == 1


def test_a_receipt_that_disagrees_with_the_status_does_not_count() -> None:
    disagreeing = _receipt_review(body=f"### oracle-verdict: failure for `{HEAD}`\n\nDetails.")
    status, message = _oracle(statuses=[_verdict()], reviews=[disagreeing])
    assert status == 1
    assert "no unedited oracle review" in message


def test_a_bound_receipt_matching_the_status_run_passes() -> None:
    run = "a" * 32
    assert _oracle(statuses=[_uuid_status(run)], reviews=[_marked_receipt(run)])[0] == 0


def test_a_receipt_from_another_run_does_not_count() -> None:
    assert _oracle(statuses=[_uuid_status("a" * 32)], reviews=[_marked_receipt("b" * 32)])[0] == 1


def test_a_status_run_without_a_marked_receipt_does_not_count() -> None:
    assert _oracle(statuses=[_uuid_status("a" * 32)], reviews=[_unmarked_receipt()])[0] == 1


def test_a_marked_receipt_with_an_unbound_status_does_not_count() -> None:
    assert _oracle(statuses=[_unbound_status()], reviews=[_marked_receipt("a" * 32)])[0] == 1


def test_an_unmarked_receipt_with_a_bound_status_does_not_count() -> None:
    assert _oracle(statuses=[_uuid_status("a" * 32)], reviews=[_unmarked_receipt()])[0] == 1


def test_an_unbound_status_with_an_unmarked_receipt_does_not_count() -> None:
    assert _oracle(statuses=[_unbound_status()], reviews=[_unmarked_receipt()])[0] == 1


def test_an_exempt_status_keeps_its_meaning_with_a_run_suffix() -> None:
    exempt = _verdict("success", description=f"{oracle_review_check.EXEMPT_SUCCESS_DESCRIPTION} [run {'a' * 32}]")
    assert _oracle(statuses=[exempt])[0] == 1


def test_a_shadow_header_receipt_does_not_count() -> None:
    shadow = _receipt_review(body=f"### oracle-review-shadow: success for `{HEAD}`\n\nDetails.")
    assert _oracle(statuses=[_verdict()], reviews=[shadow])[0] == 1


def _marked_receipt(run_uuid: str | None, **over: Any) -> dict[str, Any]:
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
    }
    if run_uuid is not None:
        record["run_uuid"] = run_uuid
    body = f"### oracle-verdict: success for `{HEAD}`\n\nDecision: **SHIP**.\n\n{protocol.encode_marker(record)}"
    return _receipt_review(body=body, **over)


def _uuid_status(run_uuid: str | None, **over: Any) -> dict[str, Any]:
    description = "large: SHIP"
    if run_uuid is not None:
        description += f" [run {run_uuid}]"
    return _verdict("success", description=description, **over)


def test_a_receipt_without_edit_evidence_does_not_count() -> None:
    bare = {
        key: value for key, value in _receipt_review().items() if key not in ("last_edited_at", "includes_created_edit")
    }
    assert _oracle(statuses=[_verdict()], reviews=[bare])[0] == 1


def test_a_receipt_newer_than_the_status_still_counts() -> None:
    newer = _receipt_review(submitted_at=LATEST)
    assert _oracle(statuses=[_verdict()], reviews=[newer])[0] == 0


def test_a_receipt_before_a_retarget_does_not_count() -> None:
    assert _oracle(statuses=[_verdict(created_at=AFTER_RETARGET)], base_date=RETARGETED)[0] == 1


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


def test_the_oracle_verdict_message_wins_over_a_standin() -> None:
    assert _oracle(comments=[_attestation()]) == (0, f"oracle-review: pass (oracle verdict for {HEAD})")


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


def test_standin_after_a_failing_oracle_verdict_overrides_it_but_not_open_threads() -> None:
    failing = [_verdict("failure", created_at=HEAD_DATE)]
    assert _oracle(statuses=failing, comments=[_standin_comment()]) == (
        0,
        f"oracle-review: pass (stand-in review attested by joeharris76 for {HEAD})",
    )
    early = {**_standin_comment(), "created_at": BEFORE_HEAD, "updated_at": BEFORE_HEAD}
    assert _oracle(statuses=failing, comments=[early])[0] == 1
    assert _oracle(statuses=[], comments=[early])[0] == 0
    threads = [{"resolved": False, "author": "benchbox-oracle", "author_type": "Bot"}]
    assert _oracle(statuses=failing, comments=[_standin_comment()], threads=threads)[0] == 1


def _standin_at(created_at: str) -> dict[str, Any]:
    return {**_standin_comment(), "created_at": created_at, "updated_at": created_at}


def test_pending_verdicts_do_not_void_a_standin_posted_after_the_last_decisive_verdict() -> None:
    statuses = [
        _verdict("failure", created_at=HEAD_DATE),
        _verdict("pending", created_at=LATER),
        _verdict("pending", created_at=LATEST),
    ]
    assert _oracle(statuses=statuses, comments=[_standin_at(AFTER_HEAD)])[0] == 0


def test_a_standin_before_the_last_decisive_verdict_still_waits_after_later_pending_verdicts() -> None:
    statuses = [_verdict("failure", created_at=AFTER_HEAD), _verdict("pending", created_at=LATEST)]
    assert _oracle(statuses=statuses, comments=[_standin_at(HEAD_DATE)])[0] == 1


def test_a_status_from_another_creator_does_not_advance_the_standin_window() -> None:
    forged = _verdict("failure", created_at=LATER, login="joeharris76", user_type="User")
    statuses = [_verdict("failure", created_at=HEAD_DATE), forged]
    assert _oracle(statuses=statuses, comments=[_standin_at(AFTER_HEAD)])[0] == 0


def test_another_context_does_not_advance_the_standin_window() -> None:
    other = _verdict("failure", created_at=LATER, context="oracle-gate")
    statuses = [_verdict("failure", created_at=HEAD_DATE), other]
    assert _oracle(statuses=statuses, comments=[_standin_at(AFTER_HEAD)])[0] == 0


def test_a_later_decisive_verdict_voids_an_earlier_standin() -> None:
    statuses = [_verdict("failure", created_at=HEAD_DATE), _verdict("failure", created_at=LATER)]
    assert _oracle(statuses=statuses, comments=[_standin_at(AFTER_HEAD)])[0] == 1


def test_a_standin_after_pending_verdicts_never_overrides_an_open_thread() -> None:
    statuses = [_verdict("failure", created_at=HEAD_DATE), _verdict("pending", created_at=LATER)]
    threads = [{"resolved": False, "author": "benchbox-oracle", "author_type": "Bot"}]
    assert _oracle(statuses=statuses, comments=[_standin_at(LATEST)], threads=threads)[0] == 1


def test_the_verdict_still_comes_from_the_latest_status() -> None:
    statuses = [_verdict("failure", created_at=HEAD_DATE), _verdict("success", created_at=LATER)]
    assert _oracle(statuses=statuses)[0] == 0
    statuses.reverse()
    assert _oracle(statuses=statuses)[0] == 0
    statuses = [_verdict("success", created_at=HEAD_DATE), _verdict("failure", created_at=LATER)]
    assert _oracle(statuses=statuses)[0] == 1


def test_force_push_back_to_an_attested_head_keeps_the_attestation() -> None:
    comment = {**_standin_comment(), "created_at": BEFORE_HEAD, "updated_at": BEFORE_HEAD}
    assert _decide(comments=[comment])[0] == 0


def _stub_main(
    monkeypatch: pytest.MonkeyPatch,
    statuses: list[dict[str, Any]],
    reviews: list[dict[str, Any]] | None = None,
    edited: tuple[str | None, bool] | None = None,
) -> list[str]:
    check = oracle_review_check
    requested: list[str] = []
    raw_statuses = [
        {
            "id": item["id"],
            "context": item["context"],
            "state": item["state"],
            "description": item["description"],
            "creator": {"login": item["login"], "type": item["user_type"]},
            "created_at": item["created_at"],
        }
        for item in statuses
    ]

    def paginate(_token: str, path: str) -> list[dict[str, Any]]:
        requested.append(path)
        if path == f"/repos/o/r/commits/{HEAD}/statuses":
            return raw_statuses
        if path.endswith("/reviews"):
            return [_review(SHIP_BODY)] if reviews is None else reviews
        raise AssertionError(path)

    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(check, "fetch_pull", lambda token, repo, pr: {"head": {"sha": HEAD}, "changed_files": 1})
    monkeypatch.setattr(check, "fetch_files", lambda token, repo, pr: [{"filename": SOUNDNESS_FILES[0]}])
    monkeypatch.setattr(check, "_paginate", paginate)
    monkeypatch.setattr(check, "fetch_threads", lambda token, repo, pr: [])
    monkeypatch.setattr(check, "fetch_base_change_date", lambda token, repo, pr: None)
    monkeypatch.setattr(check, "fetch_comments", lambda token, repo, pr: [])
    monkeypatch.setattr(check, "fetch_review_edit", lambda token, node_id: (None, False) if edited is None else edited)
    return requested


def _review(body: str, **over: Any) -> dict[str, Any]:
    return {
        "user": {"login": ORACLE, "type": "Bot"},
        "commit_id": HEAD,
        "state": "COMMENTED",
        "submitted_at": AFTER_HEAD,
        "body": body,
        "node_id": "node_1",
        **over,
    }


def test_main_requires_the_oracle_and_prints_no_parity(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    _stub_main(monkeypatch, [_verdict()])
    assert oracle_review_check.main(["--repo", "o/r", "--pr", "7", "--signal", "oracle"]) == 0
    assert capsys.readouterr().out.splitlines() == [f"oracle-review: pass (oracle verdict for {HEAD})"]
    _stub_main(monkeypatch, [_verdict(login=CONNECTOR)])
    assert oracle_review_check.main(["--repo", "o/r", "--pr", "7"]) == 1


@pytest.mark.parametrize(
    ("statuses", "expected_status"),
    [
        ([], oracle_review_check.WAITING),
        ([_verdict("pending")], oracle_review_check.WAITING),
        ([_verdict("failure")], oracle_review_check.WAITING),
        ([_verdict("success")], oracle_review_check.PASS),
    ],
    ids=["absent", "pending", "failure", "success"],
)
def test_main_requires_an_oracle_decision_for_verdict_workflow_only(
    monkeypatch: pytest.MonkeyPatch, statuses: list[dict[str, Any]], expected_status: int
) -> None:
    _stub_main(monkeypatch, statuses)
    monkeypatch.setattr(
        oracle_review_check,
        "fetch_files",
        lambda token, repo, pr: [{"filename": ".github/workflows/oracle-verdict.yml"}],
    )
    assert oracle_review_check.main(["--repo", "o/r", "--pr", "7"]) == expected_status


SHIP_BODY = _marked_body()
EDITED_REVIEW = _review(SHIP_BODY, last_edited_at="2026-10-02T09:00:00Z", includes_created_edit=True, node_id=None)


def test_an_edited_ship_review_over_a_failure_verdict_does_not_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    requested = _stub_main(monkeypatch, [_verdict("failure")], reviews=[EDITED_REVIEW])
    assert oracle_review_check.main(["--repo", "o/r", "--pr", "7"]) == oracle_review_check.WAITING
    assert any(path.endswith("/reviews") for path in requested)


def test_deleting_a_later_do_not_ship_review_does_not_revive_an_earlier_ship(monkeypatch: pytest.MonkeyPatch) -> None:
    earlier_ship = _review(SHIP_BODY, submitted_at=BEFORE_HEAD)
    statuses = [_verdict("success", created_at=BEFORE_HEAD, id=1), _verdict("failure", created_at=AFTER_HEAD, id=2)]
    _stub_main(monkeypatch, statuses, reviews=[earlier_ship])
    assert oracle_review_check.main(["--repo", "o/r", "--pr", "7"]) == oracle_review_check.WAITING


def test_a_ship_review_with_no_app_verdict_does_not_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_main(monkeypatch, [], reviews=[_review(SHIP_BODY)])
    assert oracle_review_check.main(["--repo", "o/r", "--pr", "7"]) == oracle_review_check.WAITING


def test_a_ship_review_with_a_non_app_verdict_status_does_not_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    forged = _verdict("success", login="joeharris76", user_type="User")
    _stub_main(monkeypatch, [forged], reviews=[_review(SHIP_BODY)])
    assert oracle_review_check.main(["--repo", "o/r", "--pr", "7"]) == oracle_review_check.WAITING


def test_an_edited_receipt_over_a_success_verdict_does_not_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _review(SHIP_BODY, node_id="node_9", submitted_at=AFTER_HEAD)
    _stub_main(monkeypatch, [_verdict("success")], reviews=[raw], edited=("2026-10-02T09:00:00Z", True))
    assert oracle_review_check.main(["--repo", "o/r", "--pr", "7"]) == oracle_review_check.WAITING


def test_an_unreadable_receipt_check_waits_instead_of_erroring(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_token: str, _node_id: str) -> tuple[None, bool]:
        raise oracle_review_check.CheckError("GraphQL down")

    _stub_main(monkeypatch, [_verdict("success")], reviews=[_review(SHIP_BODY, node_id="node_9")])
    monkeypatch.setattr(oracle_review_check, "fetch_review_edit", broken)
    assert oracle_review_check.main(["--repo", "o/r", "--pr", "7"]) == oracle_review_check.WAITING


def test_fetch_review_edit_reads_the_graphql_edit_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def request(_token: str, url: str, body: dict[str, Any] | None = None) -> Any:
        seen.append({"url": url, "body": body})
        return {"data": {"node": {"lastEditedAt": None, "includesCreatedEdit": False}}}

    monkeypatch.setattr(oracle_review_check, "_request", request)
    assert oracle_review_check.fetch_review_edit("t", "node_9") == (None, False)
    assert seen[0]["url"].endswith("/graphql")
    assert seen[0]["body"]["variables"] == {"id": "node_9"}


def test_fetch_review_edit_fails_closed_on_graphql_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oracle_review_check, "_request", lambda *a, **k: {"errors": [{"message": "down"}]})
    with pytest.raises(oracle_review_check.CheckError):
        oracle_review_check.fetch_review_edit("t", "node_9")


def test_fetch_review_edit_fails_closed_on_a_null_node(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oracle_review_check, "_request", lambda *a, **k: {"data": {"node": None}})
    with pytest.raises(oracle_review_check.CheckError):
        oracle_review_check.fetch_review_edit("t", "node_9")


def test_fetch_review_edit_fails_closed_when_edit_fields_are_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oracle_review_check, "_request", lambda *a, **k: {"data": {"node": {}}})
    with pytest.raises(oracle_review_check.CheckError):
        oracle_review_check.fetch_review_edit("t", "node_9")


def test_annotate_receipt_marks_a_receipt_without_a_node_id_as_edited() -> None:
    reviews = [_receipt_review()]
    assert "node_id" not in reviews[0]
    oracle_review_check.annotate_receipt("t", reviews, HEAD)
    assert _oracle(statuses=[_verdict()], reviews=reviews)[0] == 1


def test_annotate_checks_the_same_latest_receipt_the_gate_decides_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _normalized(body: str, submitted_at: str, node_id: str) -> dict[str, Any]:
        return {
            "login": ORACLE,
            "user_type": "Bot",
            "commit_id": HEAD,
            "state": "COMMENTED",
            "submitted_at": submitted_at,
            "body": body,
            "node_id": node_id,
        }

    earlier = _normalized(SHIP_BODY, BEFORE_HEAD, "node_old")
    later = _normalized(SHIP_BODY, AFTER_HEAD, "node_new")
    seen: list[str] = []

    def edit(_token: str, node_id: str) -> tuple[None, bool]:
        seen.append(node_id)
        return None, False

    monkeypatch.setattr(oracle_review_check, "fetch_review_edit", edit)
    reviews = [earlier, later]
    oracle_review_check.annotate_receipt("t", reviews, HEAD)
    assert seen == ["node_new"]
    assert _oracle(statuses=[_verdict()], reviews=reviews)[0] == 0


def test_annotate_receipt_marks_an_unreadable_edit_check_as_edited(monkeypatch: pytest.MonkeyPatch) -> None:
    reviews = [_receipt_review()]
    reviews[0]["node_id"] = "node_9"

    def broken(_token: str, _node_id: str) -> tuple[None, bool]:
        raise oracle_review_check.CheckError("GraphQL down")

    monkeypatch.setattr(oracle_review_check, "fetch_review_edit", broken)
    oracle_review_check.annotate_receipt("t", reviews, HEAD)
    assert _oracle(statuses=[_verdict()], reviews=reviews)[0] == 1


def test_a_standin_still_counts_against_an_exempt_success_verdict() -> None:
    exempt = _verdict("success", description=oracle_review_check.EXEMPT_SUCCESS_DESCRIPTION)
    status, message = _oracle(statuses=[exempt], comments=[_attestation(created_at=LATER)])
    assert status == oracle_review_check.PASS
    assert "stand-in review attested" in message


def test_main_rejects_an_exempt_success_verdict_when_a_review_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exempt = _verdict("success", description=oracle_review_check.EXEMPT_SUCCESS_DESCRIPTION)
    _stub_main(monkeypatch, [exempt])
    assert oracle_review_check.main(["--repo", "o/r", "--pr", "7"]) == oracle_review_check.WAITING
    _stub_main(monkeypatch, [_verdict("success")])
    assert oracle_review_check.main(["--repo", "o/r", "--pr", "7"]) == oracle_review_check.PASS


def test_a_shadow_header_review_does_not_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    shadow = _review(f"### oracle-review-shadow: success for `{HEAD}`\n\nDecision: **SHIP**.\n")
    _stub_main(monkeypatch, [], reviews=[shadow])
    assert oracle_review_check.main(["--repo", "o/r", "--pr", "7"]) == oracle_review_check.WAITING
    _stub_main(monkeypatch, [_verdict(context="oracle-review-shadow")], reviews=[shadow])
    assert oracle_review_check.main(["--repo", "o/r", "--pr", "7"]) == oracle_review_check.WAITING


def test_the_connector_signal_is_no_longer_accepted() -> None:
    with pytest.raises(SystemExit):
        oracle_review_check.main(["--repo", "o/r", "--pr", "7", "--signal", "connector"])


def test_fetch_threads_keeps_the_author_type(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_fetch_statuses_reads_the_head_commit_and_keeps_the_creator_type(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = {
        "id": 9,
        "context": "oracle-verdict",
        "state": "success",
        "description": "large: SHIP",
        "creator": {"login": ORACLE, "type": "Bot"},
        "created_at": AFTER_HEAD,
        "target_url": "https://example.invalid",
    }
    seen: list[str] = []
    monkeypatch.setattr(oracle_review_check, "_paginate", lambda token, path: seen.append(path) or [raw])
    assert oracle_review_check.fetch_statuses("t", "o/r", HEAD) == [
        _verdict("success", id=9, description="large: SHIP")
    ]
    assert seen == [f"/repos/o/r/commits/{HEAD}/statuses"]


def _refusal_description() -> str:
    from _project.scripts.oracle_reviewers import report

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
    payload = report.status_payload(plan, report.refused(plan), "https://example.invalid/run")
    assert payload["context"] == oracle_review_check.ORACLE_CONTEXT and payload["state"] == "failure"
    return payload["description"]


def test_a_refused_pull_request_is_told_to_open_a_new_one() -> None:
    status, message = _oracle(statuses=[_verdict("failure", description=_refusal_description())])
    assert status == oracle_review_check.WAITING
    assert "refused to review" in message and "close this pull request and open a new one" in message
    failure_status, failure_message = _oracle(statuses=[_verdict("failure")])
    assert failure_status == oracle_review_check.WAITING and "refused" not in failure_message


def test_a_standin_after_a_refusal_passes() -> None:
    statuses = [_verdict("failure", description=_refusal_description(), created_at=AFTER_HEAD)]
    later = "2026-10-08T23:59:59Z"
    status, message = _oracle(statuses=statuses, comments=[_attestation(created_at=later)])
    assert status == oracle_review_check.PASS and "stand-in review attested" in message
    early = _oracle(statuses=statuses, comments=[_attestation(created_at=BEFORE_HEAD)])
    assert early[0] == oracle_review_check.WAITING


def test_a_standin_never_overrides_an_open_oracle_thread_after_a_refusal() -> None:
    statuses = [_verdict("failure", description=_refusal_description())]
    threads = [{"resolved": False, "author": "benchbox-oracle", "author_type": "Bot"}]
    status, message = _oracle(
        statuses=statuses, threads=threads, comments=[_attestation(created_at="2026-10-08T23:59:59Z")]
    )
    assert status == oracle_review_check.WAITING and "unresolved oracle review thread" in message


def test_new_verdicts_use_the_current_status_context() -> None:
    assert oracle_review_check.ORACLE_CONTEXT == "oracle-verdict"
    assert _refusal_description().startswith(oracle_review_check.REFUSED_DESCRIPTION_PREFIX)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", "https://api.github.com"),
        ("https://api.github.com", "https://api.github.com"),
        ("https://attacker.example", "https://api.github.com"),
        ("http://attacker.example", "https://api.github.com"),
        ("http://127.0.0.1:8123/", "http://127.0.0.1:8123"),
        ("http://localhost:8123", "http://localhost:8123"),
        ("http://127.0.0.1:8123/elsewhere", "https://api.github.com"),
    ],
)
def test_the_api_root_is_overridden_only_by_a_loopback_server(value: str, expected: str) -> None:
    assert oracle_review_check._api_root(value) == expected
