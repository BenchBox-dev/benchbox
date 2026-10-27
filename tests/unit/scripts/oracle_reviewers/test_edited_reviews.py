from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import cli, edits, github, protocol
from _project.scripts.oracle_reviewers.dedup import fingerprint, open_fingerprints

from .conftest import POLICY_PATH
from .test_cli_plan import (
    CAPTURE,
    CAPTURE_CHANGED,
    CHECKER,
    DEFECT,
    OLD_HEAD,
    TWO_FILE_DIFF,
    _decided_head,
    _disputed_thread,
    _env,
    _fake,
    _plan,
    _record,
    _review,
    _thread_fake,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

BOT = "benchbox-oracle"
CREATED_EDIT = (None, True)
LATER_EDIT = ("2026-10-09T00:00:00Z", False)
EDITED = [CREATED_EDIT, LATER_EDIT]
EDITED_IDS = ["created-edit", "edited-later"]
RUN = "4242"
APP_CREATOR = {"login": "benchbox-oracle[bot]", "type": "Bot"}
USER_CREATOR = {"login": "joe", "type": "User"}


def _app_review(body: str = "Decision.\n", **over: Any) -> dict[str, Any]:
    review = {
        "id": 1,
        "node_id": "PRR_1",
        "login": "benchbox-oracle[bot]",
        "user_type": "Bot",
        "state": "COMMENTED",
        "body": body,
        "submitted_at": "2026-10-08T10:00:00Z",
        "last_edited_at": None,
        "includes_created_edit": False,
    }
    review.update(over)
    return review


def _marked_body(decision: str = "SHIP", **over: Any) -> str:
    record = _record(TWO_FILE_DIFF, decision, **over)
    return "Body.\n\n" + protocol.encode_marker({k: v for k, v in record.items() if k != "patch_map"})


@pytest.mark.parametrize(("edited_at", "created"), EDITED, ids=EDITED_IDS)
def test_history_holds_on_an_edited_marked_app_review(edited_at: Any, created: bool) -> None:
    found = protocol.history(
        [_app_review(_marked_body(), last_edited_at=edited_at, includes_created_edit=created)], BOT
    )
    assert found.error == "an oracle review was edited"
    assert found.records == [] and found.latest is None


def test_history_keeps_an_unedited_marked_app_review() -> None:
    found = protocol.history([_app_review(_marked_body())], BOT)
    assert found.error is None and [record["decision"] for record in found.records] == ["SHIP"]


def test_history_holds_when_any_one_review_among_several_was_edited() -> None:
    clean = _app_review(_marked_body(), id=1, node_id="PRR_1")
    edited = _app_review(_marked_body("DO_NOT_SHIP"), id=2, node_id="PRR_2", includes_created_edit=True)
    assert protocol.history([clean, edited], BOT).error == "an oracle review was edited"


@pytest.mark.parametrize("missing", ["last_edited_at", "includes_created_edit"])
def test_history_refuses_a_marked_app_review_without_edit_state(missing: str) -> None:
    review = _app_review(_marked_body())
    del review[missing]
    with pytest.raises(edits.EditStateError):
        protocol.history([review], BOT)


def test_history_ignores_the_edit_state_of_reviews_that_carry_no_marker_or_are_not_the_apps() -> None:
    unmarked = _app_review("Plain review.", includes_created_edit=True)
    human = _app_review(_marked_body(), login="joe", user_type="User", includes_created_edit=True)
    dismissed = _app_review(_marked_body(), state="DISMISSED", includes_created_edit=True)
    assert protocol.history([unmarked, human, dismissed], BOT).error is None


def _states(*pairs: tuple[str, Any, bool]) -> dict[str, Any]:
    return {
        "data": {
            "nodes": [{"id": node, "lastEditedAt": at, "includesCreatedEdit": created} for node, at, created in pairs]
        }
    }


def test_parse_review_edits_returns_each_requested_state() -> None:
    payload = _states(("A", None, False), ("B", "2026-10-09T00:00:00Z", True))
    assert edits.parse_review_edits(payload, ["A", "B"]) == {"A": (None, False), "B": ("2026-10-09T00:00:00Z", True)}


@pytest.mark.parametrize(
    "payload",
    [
        {"errors": [{"message": "no"}], "data": None},
        {"data": {"nodes": [None, None]}},
        {"data": {"nodes": [{"id": "A", "lastEditedAt": None}]}},
        _states(("A", None, False)),
        {"data": None},
        [],
    ],
    ids=["errors", "null-nodes", "missing-field", "missing-node", "no-data", "not-an-object"],
)
def test_parse_review_edits_raises_for_anything_unreadable(payload: Any) -> None:
    with pytest.raises(edits.EditStateError):
        edits.parse_review_edits(payload, ["A", "B"])


def _status(
    state: str,
    description: str = "d",
    at: str = "2026-10-08T10:00:00Z",
    creator: dict[str, str] = APP_CREATOR,
    id_: int = 1,
) -> dict[str, Any]:
    return {
        "id": id_,
        "context": "oracle-verdict",
        "state": state,
        "description": description,
        "creator": creator,
        "created_at": at,
    }


@pytest.mark.parametrize(
    ("decision", "state", "agrees"),
    [
        ("SHIP", "success", True),
        ("SHIP", "failure", False),
        ("SHIP_WITH_FIXES", "failure", True),
        ("SHIP_WITH_FIXES", "success", False),
        ("DO_NOT_SHIP", "failure", True),
        ("DO_NOT_SHIP", "success", False),
        ("REFUSED", "failure", True),
        ("REFUSED", "success", False),
    ],
)
def test_a_record_agrees_only_with_the_status_its_decision_requires(decision: str, state: str, agrees: bool) -> None:
    record = {"decision": decision}
    assert edits.status_agrees([_status(state)], "oracle-verdict", BOT, record) is agrees


def test_a_non_app_status_and_other_contexts_and_pending_never_satisfy_the_check() -> None:
    record = {"decision": "SHIP"}
    statuses = [
        _status("success", creator=USER_CREATOR),
        {**_status("success"), "context": "other"},
        _status("pending"),
        _status("success", creator={"login": "benchbox-oracle[bot]", "type": "User"}),
    ]
    assert not edits.status_agrees(statuses, "oracle-verdict", BOT, record)


def test_the_status_is_chosen_by_the_records_run_id() -> None:
    record = {"decision": "SHIP", "run_uuid": "111"}
    statuses = [
        _status("success", "ok [run 111]", "2026-10-08T10:00:00Z", id_=1),
        _status("failure", "later verdict", "2026-10-08T11:00:00Z", id_=2),
        _status("success", "ok [run 222]", "2026-10-08T12:00:00Z", id_=3),
    ]
    chosen = edits.chosen_status(statuses, "oracle-verdict", BOT, "111", "SHIP")
    assert chosen is not None and chosen["id"] == 1
    assert edits.status_agrees(statuses, "oracle-verdict", BOT, record)
    assert not edits.status_agrees(statuses[1:2] + statuses[2:], "oracle-verdict", BOT, record)


def test_a_record_without_a_run_id_uses_the_latest_app_decisive_status() -> None:
    statuses = [
        _status("success", at="2026-10-08T10:00:00Z", id_=1),
        _status("failure", at="2026-10-08T11:00:00Z", id_=2),
    ]
    assert not edits.status_agrees(statuses, "oracle-verdict", BOT, {"decision": "SHIP"})
    assert edits.status_agrees(statuses, "oracle-verdict", BOT, {"decision": "DO_NOT_SHIP"})


def test_a_non_ship_record_with_a_run_id_matches_the_run_free_failure_status() -> None:
    statuses = [_status("failure", "Carried findings")]
    assert edits.status_agrees(statuses, "oracle-verdict", BOT, {"decision": "SHIP_WITH_FIXES", "run_uuid": "111"})
    assert not edits.status_agrees(
        [_status("success", "ok [run 222]")], "oracle-verdict", BOT, {"decision": "SHIP_WITH_FIXES", "run_uuid": "111"}
    )


def test_a_ship_record_with_a_run_id_never_matches_a_run_free_success() -> None:
    assert not edits.status_agrees(
        [_status("success", "ok")], "oracle-verdict", BOT, {"decision": "SHIP", "run_uuid": "111"}
    )


def test_no_status_at_all_never_agrees() -> None:
    assert not edits.status_agrees([], "oracle-verdict", BOT, {"decision": "SHIP"})


def _held(values: dict[str, str], plan: dict[str, Any]) -> bool:
    return values["decision"] == "hold" and values["post"] == "true" and plan["decision"] == "hold"


@pytest.mark.parametrize(("edited_at", "created"), EDITED, ids=EDITED_IDS)
def test_an_edited_prior_review_whose_digest_matches_the_new_head_holds_instead_of_carrying(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, edited_at: Any, created: bool
) -> None:
    fake = _fake(_review(_record(TWO_FILE_DIFF, "SHIP")))
    fake.edit_states = {"PRR_1": (edited_at, created)}
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert _held(values, plan)
    assert plan["decision_reason"] == "an oracle review was edited"
    assert plan.get("reviewed_head") is None


@pytest.mark.parametrize(("edited_at", "created"), EDITED, ids=EDITED_IDS)
def test_an_edited_patch_map_never_narrows_a_follow_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, edited_at: Any, created: bool
) -> None:
    record = _record(TWO_FILE_DIFF, "SHIP_WITH_FIXES", open_defects=[DEFECT], next_id=2)
    fake = _fake(_review(record), diff=CAPTURE_CHANGED)
    fake.edit_states = {"PRR_1": (edited_at, created)}
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert _held(values, plan)
    assert not plan.get("protocol")


def test_an_unedited_history_still_carries_after_the_edit_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _fake(_review(_record(TWO_FILE_DIFF, "SHIP")), base={"ref": "develop", "sha": "f" * 40})
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "carry" and plan["reviewed_head"] == OLD_HEAD


def _assert_nothing_posted(code: int, values: dict[str, str], plan: dict[str, Any], fake: Any) -> None:
    assert code == 2
    assert values == {}
    assert fake.posted == [] and fake.dispatched == []
    assert "decision" not in plan


def test_a_graphql_failure_fails_the_plan_step_and_leaves_a_success_status_the_latest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _fake(_review(_record(TWO_FILE_DIFF, "SHIP", head_sha="a" * 40)))
    fake.statuses = [_status("success", "oracle-verdict: success [run 77]")]
    fake.edit_states = edits.EditStateError("GitHub GraphQL returned errors")
    code, values, plan = _plan(monkeypatch, tmp_path, fake)
    _assert_nothing_posted(code, values, plan, fake)
    latest = max(fake.statuses, key=lambda status: status["created_at"])
    assert latest["state"] == "success"


def test_a_marked_review_without_a_node_id_fails_the_plan_step(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _fake(_review(_record(TWO_FILE_DIFF, "SHIP"), node_id=None))
    code, values, plan = _plan(monkeypatch, tmp_path, fake)
    _assert_nothing_posted(code, values, plan, fake)


def _plan_patched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: Any, **patches: Any
) -> tuple[int, dict[str, str]]:
    fake.install(monkeypatch)
    for name, replacement in patches.items():
        monkeypatch.setattr(github, name, replacement)
    output = _env(monkeypatch, tmp_path, "pull_request_target", {"action": "synchronize"}, "refs/heads/develop")
    code = cli.main(["plan", "--policy", str(POLICY_PATH), "--pr", "7", "--out", str(tmp_path / "plan")])
    return code, dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())


def test_a_missing_edit_state_for_a_review_fails_the_plan_step(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _fake(_review(_record(TWO_FILE_DIFF, "SHIP")))
    code, values = _plan_patched(monkeypatch, tmp_path, fake, review_edit_states=lambda ids: {})
    assert code == 2 and values == {} and fake.posted == []


def test_an_unreadable_review_list_still_holds_rather_than_failing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _fake()
    fake.reviews = None
    _, values, _ = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "hold"


def test_a_history_with_no_marked_review_reads_no_edit_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _fake(_review(None))
    fake.edit_states = edits.EditStateError("must not be read")
    _, values, _ = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "review"


def _carry_fake(decision: str, statuses: list[dict[str, Any]], **record: Any) -> Any:
    fake = _fake(
        _review(_record(TWO_FILE_DIFF, decision, **record)),
        base={"ref": "develop", "sha": "f" * 40},
    )
    fake.derive_verdict_statuses = False
    fake.statuses = statuses
    return fake


@pytest.mark.parametrize(
    ("decision", "state"),
    [("SHIP", "success"), ("SHIP_WITH_FIXES", "failure"), ("DO_NOT_SHIP", "failure")],
)
def test_a_decision_over_the_status_it_requires_carries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, decision: str, state: str
) -> None:
    fake = _carry_fake(decision, [_status(state)])
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "carry" and plan["reviewed_head"] == OLD_HEAD


def test_a_ship_record_over_an_app_failure_status_holds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _, values, plan = _plan(monkeypatch, tmp_path, _carry_fake("SHIP", [_status("failure")]))
    assert _held(values, plan)
    assert "does not match the oracle's own status" in plan["decision_reason"]


def test_a_ship_with_fixes_record_over_an_app_success_status_holds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, values, plan = _plan(monkeypatch, tmp_path, _carry_fake("SHIP_WITH_FIXES", [_status("success")]))
    assert _held(values, plan)


def test_a_non_app_success_status_never_satisfies_the_carry_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _carry_fake("SHIP", [_status("success", creator=USER_CREATOR)])
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert _held(values, plan)


def test_no_status_on_the_previous_head_holds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _, values, plan = _plan(monkeypatch, tmp_path, _carry_fake("SHIP", []))
    assert _held(values, plan)


def test_the_carry_check_picks_the_status_by_the_records_run_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    statuses = [
        _status("success", f"ok [run {RUN}]", "2026-10-08T10:00:00Z", id_=1),
        _status("failure", "a later bare failure", "2026-10-08T11:00:00Z", id_=2),
    ]
    _, values, plan = _plan(monkeypatch, tmp_path, _carry_fake("SHIP", statuses, run_uuid=RUN))
    assert values["decision"] == "carry"
    other = _carry_fake("SHIP", [_status("success", "ok [run 999]")], run_uuid=RUN)
    (tmp_path / "other").mkdir()
    _, values, plan = _plan(monkeypatch, tmp_path / "other", other)
    assert _held(values, plan)


def test_an_unreadable_status_list_fails_the_plan_step_instead_of_carrying(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _carry_fake("SHIP", [_status("success")])

    def broken(repo: str, sha: str) -> list[dict[str, Any]]:
        raise github.GitHubError("statuses down")

    code, values = _plan_patched(monkeypatch, tmp_path, fake, commit_statuses=broken)
    assert code == 2 and values == {}
    assert fake.posted == []


def _edited(thread: dict[str, Any]) -> dict[str, Any]:
    return {**thread, "edited": True}


def test_dedup_ignores_the_fingerprint_of_an_edited_app_comment() -> None:
    body = "**High**: Drops a row"
    kept = {"resolved": False, "path": CHECKER, "author": BOT, "author_type": "Bot", "body": body, "edited": False}
    assert open_fingerprints([kept], BOT) == {fingerprint(CHECKER, "Drops a row"): "High"}
    assert open_fingerprints([_edited(kept)], BOT) == {}


def test_a_dispute_under_an_edited_app_first_comment_is_not_bound(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _thread_fake(_decided_head(), [_edited(_disputed_thread())])
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "skip"
    assert plan.get("disputes", []) == []


def test_a_dispute_under_an_unedited_app_first_comment_still_binds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _thread_fake(
        _record(TWO_FILE_DIFF, "SHIP_WITH_FIXES", open_defects=[DEFECT], next_id=2), [_disputed_thread()]
    )
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "review" and plan["protocol"]["forced"] is True


def test_thread_unreadable_edit_state_deduplicates_and_binds_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _thread_fake(_record(TWO_FILE_DIFF, "SHIP_WITH_FIXES", open_defects=[DEFECT], next_id=2), None)
    _, values, plan = _plan(monkeypatch, tmp_path, fake)
    assert values["decision"] == "carry"
    assert plan.get("disputes", []) == [] and plan.get("open_findings", {}) == {}
