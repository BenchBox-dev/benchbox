from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import protocol, report
from _project.scripts.oracle_reviewers.retry import State

from .test_cli_plan import (
    DEFECT,
    HEAD,
    TWO_FILE_DIFF,
    TWO_FILES,
    FakeGitHub,
    _acked_thread,
    _disputed_thread,
    _plan,
    _pull,
    _record,
    _review,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

RUN = "1" * 32
COMMENT = {"action": "created", "issue": {"number": 7, "pull_request": {}}, "comment": {"body": "/oracle-review"}}


def _decided(decision: str = "SHIP_WITH_FIXES", **over: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {"head_sha": HEAD, "run_uuid": RUN, **over}
    if decision == "SHIP_WITH_FIXES":
        fields.update(open_defects=[DEFECT], next_id=2)
    return _record(TWO_FILE_DIFF, decision, **fields)


def _fake(record: dict[str, Any], threads: list[dict[str, Any]] | None = None, **kwargs: Any) -> FakeGitHub:
    fake = FakeGitHub(_pull(), TWO_FILES, threads=threads or [], diff=TWO_FILE_DIFF, **kwargs)
    fake.reviews = [_review(record)]
    return fake


def _comment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: FakeGitHub
) -> tuple[dict[str, str], dict[str, Any]]:
    _, values, plan = _plan(monkeypatch, tmp_path, fake, event_name="issue_comment", event=COMMENT)
    return values, plan


def test_a_decided_head_with_no_dispute_runs_no_reviewer_and_reposts_its_decision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    values, plan = _comment(monkeypatch, tmp_path, _fake(_decided(), [_acked_thread()]))
    assert values["decision"] == "repost" and values["post"] == "true"
    assert plan["chain"] == []
    assert plan["repost"]["state"] == "failure" and plan["repost"]["label"] == "SHIP WITH FIXES"
    assert plan["run_uuid"] == RUN


def test_the_repost_returns_the_existing_status_and_says_how_to_get_a_rejudgement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, plan = _comment(monkeypatch, tmp_path, _fake(_decided("SHIP", run_uuid=RUN)))
    final = report.reposted(plan)
    assert final.state == "success" and final.review is None
    assert final.description.endswith(report.REPOSTED_MARK)
    assert "Dispute:" in final.body and "push a change" in final.body
    payload = report.status_payload(plan, final, "https://example.test/run")
    assert payload["description"].endswith(f"{report.REPOSTED_MARK} [run {RUN}]")


def test_a_repost_is_free_even_when_the_retry_budget_is_used(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    now = datetime.now(UTC)
    fake = _fake(_decided(), [_acked_thread()])
    fake.state = State(7, HEAD, "failure", now, tuple(now - timedelta(minutes=m) for m in range(1, 12)))
    values, _ = _comment(monkeypatch, tmp_path, fake)
    assert values["decision"] == "repost"


def test_an_open_dispute_takes_the_dispute_path_not_the_repost(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    values, plan = _comment(monkeypatch, tmp_path, _fake(_decided(), [_disputed_thread()]))
    assert values["decision"] == "review"
    assert plan["protocol"]["forced"] is True


def test_an_absent_head_still_reruns_normally(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeGitHub(_pull(), TWO_FILES, threads=[], diff=TWO_FILE_DIFF)
    values, plan = _comment(monkeypatch, tmp_path, fake)
    assert values["decision"] == "review"
    assert plan["protocol"]["kind"] == "first"


def test_a_pending_head_with_no_record_still_reruns_normally(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeGitHub(
        _pull(),
        TWO_FILES,
        State(7, HEAD, "pending", datetime.now(UTC) - timedelta(hours=3), pending_cause="all-absent"),
        threads=[],
        diff=TWO_FILE_DIFF,
    )
    values, _ = _comment(monkeypatch, tmp_path, fake)
    assert values["decision"] == "review"


def test_a_push_event_on_a_decided_head_still_skips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _, values, _ = _plan(monkeypatch, tmp_path, _fake(_decided(), [_acked_thread()]), event={"action": "synchronize"})
    assert values["decision"] == "skip" and values["post"] == "false"


def test_a_record_without_a_run_id_is_carried_bound_to_the_new_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = _decided("SHIP")
    del record["run_uuid"]
    values, plan = _comment(monkeypatch, tmp_path, _fake(record))
    assert values["decision"] == "carry" and values["post"] == "true"
    final = report.carried(plan)
    assert final.review is not None or final.body
    payload = report.status_payload(plan, final, "https://example.test/run")
    assert payload["state"] == "success" and payload["description"].endswith(f"[run {plan['run_uuid']}]")
    assert protocol.decode_marker(final.body)["run_uuid"] == plan["run_uuid"]


def test_an_edited_record_without_a_run_id_holds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    record = _decided("SHIP")
    del record["run_uuid"]
    fake = _fake(record)
    fake.edit_states = {"PRR_1": ("2026-10-09T10:00:00Z", False)}
    values, plan = _comment(monkeypatch, tmp_path, fake)
    assert values["decision"] == "hold"
    assert "edited" in plan["decision_reason"]


def test_a_status_that_disagrees_with_the_record_holds_instead_of_reposting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _fake(_decided())
    fake.derive_verdict_statuses = False
    values, _ = _comment(monkeypatch, tmp_path, fake)
    assert values["decision"] == "hold"


def test_a_reposted_failure_keeps_a_long_escalation_list_whole() -> None:
    ids = ",".join(f"D{n}" for n in range(1, 13))
    description = f"large: SHIP WITH FIXES, 12 defect(s) to fix; escalated {ids}"
    plan = {"head_sha": HEAD, "repost": {"state": "failure", "description": description, "label": "SHIP WITH FIXES"}}
    final = report.reposted(plan)
    assert final.description == description + report.REPOSTED_MARK
