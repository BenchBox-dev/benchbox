from __future__ import annotations

import io
import json
import os
import subprocess
import zipfile
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import absence, omp_pin, omp_watch
from _project.scripts.oracle_reviewers.omp_watch import PinChange, PolicyCommit, Record

from .conftest import POLICY_PATH, REPO_ROOT

pytestmark = [pytest.mark.unit, pytest.mark.fast]

CAPTURED = json.loads(
    (REPO_ROOT / "tests" / "fixtures" / "oracle_reviewers" / "omp_watch" / "attempts.json").read_text(encoding="utf-8")
)
MERGED_AT = datetime(2026, 10, 29, 3, 17, 50, tzinfo=timezone.utc)
POLICY = POLICY_PATH.read_text(encoding="utf-8")
PREVIOUS_POLICY = POLICY.replace("18.13.3", "18.12.1")
CHANGE = PinChange(
    "ca66bf1",
    MERGED_AT,
    "chore(oracle): pin omp 18.13.3 (#3713)",
    "joeharris76@gmail.com",
    "18.13.3",
    "18.12.1",
    PREVIOUS_POLICY,
)


def _captured() -> list[Record]:
    columns = CAPTURED["columns"]
    records = []
    for index, row in enumerate(CAPTURED["rows"]):
        item = dict(zip(columns, row, strict=True))
        records.append(
            Record(
                index,
                item["run_id"],
                datetime.fromisoformat(item["created_at"].replace("Z", "+00:00")),
                item["reviewer"],
                item["slot"],
                item["outcome"],
                item["kind"],
                item["detail"],
            )
        )
    return records


CAPTURE = _captured()
BEFORE = [record for record in CAPTURE if record.created_at < MERGED_AT]


def _stretch(day: int, hour: int) -> list[Record]:
    start = datetime(2026, 10, day, hour, tzinfo=timezone.utc)
    runs: list[int] = []
    chosen: list[Record] = []
    for record in CAPTURE:
        if record.created_at < start:
            continue
        if record.run_id not in runs:
            if len(runs) == omp_watch.WINDOW_REVIEWS:
                break
            runs.append(record.run_id)
        chosen.append(record)
    return chosen


def _after_the_merge(stretch: Sequence[Record], gap: timedelta = timedelta(minutes=5)) -> list[Record]:
    shift = MERGED_AT + gap - stretch[0].created_at
    return [
        replace(record, artifact_id=1_000_000 + record.artifact_id, created_at=record.created_at + shift)
        for record in stretch
    ]


def _assess(window: Sequence[Record], now: datetime | None = None) -> omp_watch.Assessment:
    return omp_watch.assess([*BEFORE, *window], CHANGE, now or MERGED_AT + timedelta(hours=6))


def _mutate(stretch: Sequence[Record], kinds: Sequence[str], detail: str = "") -> list[Record]:
    ok = [index for index, record in enumerate(stretch) if record.kind == absence.OK]
    swapped = list(stretch)
    for index, kind in zip(ok, kinds, strict=False):
        swapped[index] = replace(swapped[index], outcome="absent", kind=kind, detail=detail)
    return swapped


def test_the_captured_week_before_the_pin_change_sets_the_baseline() -> None:
    mix = omp_watch._mix(BEFORE)
    assert (mix.attempts, mix.counted) == (1208, 156)
    assert mix.rate == pytest.approx(0.129, abs=0.001)
    assert {kind: mix.kinds[kind] for kind in ("quota", "auth", "incomplete", "invalid", "empty", "error")} == {
        "quota": 10,
        "auth": 2,
        "incomplete": 44,
        "invalid": 105,
        "empty": 1,
        "error": 50,
    }


def test_the_reviews_observed_after_the_real_pin_change_are_clean_and_settle_the_watch() -> None:
    result = omp_watch.assess(CAPTURE, CHANGE, datetime(2026, 10, 29, 13, 30, tzinfo=timezone.utc))
    assert result.state == omp_watch.CLEAR and not result.reasons
    assert result.reviews == omp_watch.WINDOW_REVIEWS and result.window.attempts == 10 and result.window.counted == 0
    assert result.baseline.attempts == 1208


@pytest.mark.parametrize(("day", "hour"), [(25, 0), (25, 12), (28, 0), (28, 12), (24, 18)])
def test_ordinary_variation_in_the_captured_reviews_never_raises_the_alert(day: int, hour: int) -> None:
    stretch = _stretch(day, hour)
    assert len(stretch) >= omp_watch.WINDOW_REVIEWS
    result = _assess(_after_the_merge(stretch))
    assert result.state == omp_watch.CLEAR, result.reasons
    assert result.reviews == omp_watch.WINDOW_REVIEWS


def test_a_window_inside_twice_the_baseline_stays_clear_even_with_several_failures() -> None:
    stretch = _mutate(_stretch(28, 0), [absence.INVALID, absence.INVALID])
    mix = omp_watch._mix(stretch)
    assert mix.counted == 2 and mix.rate < omp_watch.RATE_FACTOR * omp_watch._mix(BEFORE).rate
    assert _assess(_after_the_merge(stretch)).state == omp_watch.CLEAR


def test_a_shift_in_the_absence_mix_past_twice_the_baseline_raises_the_alert() -> None:
    stretch = _mutate(_stretch(28, 0), [absence.ERROR] * 4)
    result = _assess(_after_the_merge(stretch))
    assert result.state == omp_watch.REVERT
    assert result.window.kinds[absence.ERROR] == 4
    assert "more than 2 times the baseline" in result.reasons[0]
    assert not result.contract_errors


def test_the_alert_fires_before_the_window_closes() -> None:
    stretch = _mutate(_stretch(28, 0), [absence.ERROR] * 4)[:6]
    result = _assess(_after_the_merge(stretch), now=MERGED_AT + timedelta(hours=1))
    assert result.reviews < omp_watch.WINDOW_REVIEWS
    assert result.state == omp_watch.REVERT


def _flagged(records: Sequence[Record], counted: int) -> list[Record]:
    return [
        replace(record, kind=absence.ERROR, outcome="absent", detail="exit code 1")
        if index < counted
        else replace(record, kind=absence.OK, outcome="verdict", detail="")
        for index, record in enumerate(records)
    ]


def test_a_rate_of_exactly_twice_the_baseline_does_not_trigger_and_one_more_failure_does() -> None:
    week = _flagged(BEFORE[-100:], 10)
    assert omp_watch._mix(week).rate == pytest.approx(0.1)
    base = [replace(record, artifact_id=6_000_000 + index) for index, record in enumerate(week)]
    sample = _after_the_merge(_stretch(28, 0))[:10]
    runs = [replace(record, run_id=7_000_000 + index, slot=1) for index, record in enumerate(sample)]
    at_twice = omp_watch.assess([*base, *_flagged(runs, 2)], CHANGE, MERGED_AT + timedelta(hours=1))
    assert at_twice.window.rate == pytest.approx(0.2) and at_twice.state == omp_watch.CLEAR
    past_twice = omp_watch.assess([*base, *_flagged(runs, 3)], CHANGE, MERGED_AT + timedelta(hours=1))
    assert past_twice.state == omp_watch.REVERT


def test_one_failure_after_a_clean_week_is_not_enough() -> None:
    clean = [
        record
        for record in BEFORE
        if datetime(2026, 10, 28, tzinfo=timezone.utc)
        <= record.created_at
        < datetime(2026, 10, 29, tzinfo=timezone.utc)
    ]
    assert omp_watch._mix(clean).counted == 0
    stretch = _mutate(_stretch(28, 0), [absence.INVALID])
    result = omp_watch.assess([*clean, *_after_the_merge(stretch)], CHANGE, MERGED_AT + timedelta(hours=1))
    assert result.baseline.rate == 0.0 and result.window.counted == 1
    assert result.state == omp_watch.CLEAR
    two = _mutate(_stretch(28, 0), [absence.INVALID, absence.INVALID])
    assert omp_watch.assess([*clean, *_after_the_merge(two)], CHANGE, MERGED_AT).state == omp_watch.REVERT


@pytest.mark.parametrize(
    ("kind", "detail"),
    [
        (absence.EMPTY, "the reviewer ended without a final answer"),
        (absence.EMPTY, "exit code 0 with no output"),
        (absence.ERROR, "the reviewer could not be started"),
    ],
)
def test_a_single_harness_contract_error_raises_the_alert_whatever_the_rate(kind: str, detail: str) -> None:
    stretch = _mutate(_stretch(28, 0), [kind], detail)
    result = _assess(_after_the_merge(stretch))
    assert result.state == omp_watch.REVERT
    assert len(result.contract_errors) == 1 and result.window.counted == 1
    assert result.reasons[0].startswith("harness-contract error in run ")


def test_the_captured_empty_attempt_is_a_contract_error_and_ordinary_failures_are_not() -> None:
    empties = [record for record in BEFORE if record.kind == absence.EMPTY]
    assert [record.contract_error for record in empties] == [True]
    ordinary = [record for record in BEFORE if record.counted and not record.contract_error]
    assert len(ordinary) == 155
    assert not any(record.contract_error for record in BEFORE if record.kind in {absence.QUOTA, absence.AUTH})


def test_quota_auth_and_incomplete_shifts_are_reported_but_never_trigger() -> None:
    stretch = _mutate(_stretch(28, 0), [absence.QUOTA, absence.AUTH, absence.INCOMPLETE, absence.QUOTA, absence.AUTH])
    result = _assess(_after_the_merge(stretch))
    assert result.state == omp_watch.CLEAR and not result.reasons
    assert result.window.counted == 0
    rendered = result.render(CHANGE)
    assert "quota=2" in rendered and "auth=2" in rendered and "incomplete=1" in rendered


def test_only_the_first_ten_reviews_after_the_change_are_judged() -> None:
    quiet = _after_the_merge(_stretch(28, 0))
    last = max(quiet, key=lambda record: record.created_at)
    late = replace(last, artifact_id=3_000_000, run_id=last.run_id + 1, created_at=last.created_at + timedelta(hours=1))
    bad = [replace(late, artifact_id=3_000_000 + index, kind=absence.ERROR, outcome="absent") for index in range(3)]
    result = _assess([*quiet, *bad])
    assert result.reviews == omp_watch.WINDOW_REVIEWS
    assert result.state == omp_watch.CLEAR


def test_nothing_after_the_forty_eighth_hour_is_judged_and_the_window_then_closes() -> None:
    quiet = _after_the_merge(_stretch(28, 0))[:4]
    late = [
        replace(
            quiet[0],
            artifact_id=4_000_000 + index,
            run_id=9_000_000 + index,
            created_at=MERGED_AT + omp_watch.WINDOW + timedelta(minutes=index),
            kind=absence.ERROR,
            outcome="absent",
        )
        for index in range(4)
    ]
    still_open = _assess([*quiet, *late], now=MERGED_AT + timedelta(hours=47))
    assert still_open.state == omp_watch.PENDING
    closed = _assess([*quiet, *late], now=MERGED_AT + omp_watch.WINDOW + timedelta(hours=1))
    assert closed.state == omp_watch.CLEAR and closed.window.counted == 0


def test_attempts_older_than_seven_days_before_the_change_are_not_baseline() -> None:
    old = replace(BEFORE[0], artifact_id=5_000_000, created_at=MERGED_AT - omp_watch.BASELINE - timedelta(seconds=1))
    result = omp_watch.assess([old, *BEFORE[-5:]], CHANGE, MERGED_AT)
    assert result.baseline.attempts == 5


def test_a_window_with_no_reviews_yet_is_pending() -> None:
    result = omp_watch.assess(BEFORE, CHANGE, MERGED_AT + timedelta(minutes=1))
    assert result.state == omp_watch.PENDING and result.reviews == 0


def _history(*versions: str, subjects: Sequence[str] = ()) -> list[PolicyCommit]:
    commits = []
    for index, version in enumerate(versions):
        subject = subjects[index] if index < len(subjects) else f"commit {index}"
        commits.append(
            PolicyCommit(
                f"sha{index}",
                MERGED_AT - timedelta(days=index),
                subject,
                POLICY.replace("18.13.3", version),
            )
        )
    return commits


def test_the_newest_change_of_the_pinned_version_is_the_one_watched() -> None:
    change = omp_watch.latest_pin_change(_history("18.13.3", "18.13.3", "18.12.1", "18.12.1"))
    assert change is not None
    assert (change.sha, change.version, change.previous_version) == ("sha1", "18.13.3", "18.12.1")
    assert change.merged_at == MERGED_AT - timedelta(days=1)
    assert omp_pin.pin_from_text(change.previous_text).version == "18.12.1"


def test_a_history_without_a_version_change_or_a_prior_pin_is_not_watched() -> None:
    assert omp_watch.latest_pin_change(_history("18.13.3", "18.13.3")) is None
    assert omp_watch.latest_pin_change([]) is None
    without_pin = PolicyCommit("old", MERGED_AT - timedelta(days=2), "older", "version: 2\nmode: enforce\n")
    assert omp_watch.latest_pin_change([*_history("18.13.3"), without_pin]) is None


@pytest.mark.parametrize(
    ("author", "subject", "expected"),
    [
        (omp_watch.BOT_AUTHOR_EMAIL, "revert(oracle): restore omp 18.12.1", True),
        (omp_watch.BOT_AUTHOR_EMAIL, "chore(oracle): pin omp 18.13.4", False),
        ("joeharris76@gmail.com", "revert(oracle): restore omp 18.12.1", False),
        ("joeharris76@gmail.com", 'Revert "chore(oracle): pin omp 18.13.3" (#3679) (#3707)', False),
        ("joeharris76@gmail.com", "chore(oracle): pin omp 18.13.3 (#3713)", False),
    ],
)
def test_only_the_restoring_pull_request_the_watch_opens_is_exempt_from_being_watched(
    author: str, subject: str, expected: bool
) -> None:
    history = _history("18.12.1", "18.13.3")
    history[0] = replace(history[0], subject=subject, author_email=author)
    change = omp_watch.latest_pin_change(history)
    assert change is not None and change.is_revert is expected
    assert (change.subject, change.author_email) == (subject, author)


def test_the_revert_policy_restores_only_the_omp_section() -> None:
    restored = omp_watch.revert_policy(POLICY, CHANGE)
    assert restored == PREVIOUS_POLICY
    assert omp_pin.pin_from_text(restored).version == "18.12.1"
    assert restored.replace("18.12.1", "18.13.3") == POLICY


def test_the_revert_body_states_the_numbers_and_the_evidence_the_pin_gate_demands() -> None:
    result = _assess(_after_the_merge(_mutate(_stretch(28, 0), [absence.ERROR] * 4)))
    body = omp_watch.render_revert_body(CHANGE, result)
    assert "Restores the pinned omp build 18.12.1 after 18.13.3 merged" in body
    assert "make omp-candidate-check VERSION=18.12.1" in body
    assert "more than 2 times the baseline" in body and "oracle-gate" in body
    assert "does not mirror builds" in body


def _attempt(slot: int = 1, **fields: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": 3,
        "run_id": "40900000001",
        "pr": 3700,
        "head_sha": "a" * 40,
        "slot": slot,
        "reviewer": "large",
        "outcome": "absent",
        "absence": {"kind": "error", "detail": "x" * 400, "reset_at": None, "provider": "", "unread": []},
        "verdict": None,
        "diagnostic": "",
        "recovery": None,
    }
    payload.update(fields)
    return payload


def _artifact(identifier: int, created: str, run: int | None = None, **fields: Any) -> dict[str, Any]:
    return {
        "id": identifier,
        "created_at": created,
        "expired": False,
        "workflow_run": {"id": run or identifier},
        **fields,
    }


def _run(run_id: int, **fields: Any) -> dict[str, Any]:
    run: dict[str, Any] = {
        "id": run_id,
        "path": ".github/workflows/oracle-verdict.yml",
        "event": "issue_comment",
        "head_branch": "develop",
        "repository": {"full_name": "O/R"},
        "run_started_at": "2026-10-29T04:00:00Z",
        "created_at": "2026-10-29T03:59:00Z",
        "display_title": f"oracle-verdict (issue_comment pr=3700 head={'a' * 40})",
        "pull_requests": [],
    }
    run.update(fields)
    return run


def test_an_attempt_becomes_a_record_stamped_with_when_its_run_started_and_a_clipped_detail() -> None:
    record = omp_watch.parse_attempt(_artifact(7, "2026-10-29T05:00:00Z", 11), _attempt(), _run(11))
    assert record is not None
    assert (record.artifact_id, record.run_id, record.reviewer, record.slot) == (7, 11, "large", 1)
    assert record.created_at == datetime(2026, 10, 29, 4, 0, tzinfo=timezone.utc)
    assert (record.kind, record.counted) == ("error", True)
    assert len(record.detail) == omp_watch.DETAIL_LIMIT
    verdict = omp_watch.parse_attempt(
        _artifact(8, "2026-10-29T05:00:00Z", 12), _attempt(outcome="verdict", absence=None), _run(12)
    )
    assert verdict is not None and verdict.kind == absence.OK and not verdict.counted


def test_a_run_started_before_the_change_but_uploaded_after_it_belongs_to_the_old_pin() -> None:
    run = _run(13, run_started_at="2026-10-29T03:10:00Z")
    record = omp_watch.parse_attempt(_artifact(9, "2026-10-29T03:25:00Z", 13), _attempt(), run)
    assert record is not None and record.created_at < MERGED_AT
    assert omp_watch.assess([record], CHANGE, MERGED_AT + timedelta(hours=1)).window.attempts == 0


@pytest.mark.parametrize(
    ("artifact", "payload", "run"),
    [
        (_artifact(1, "2026-10-29T05:00:00Z"), _attempt(), _run(1, run_started_at="not a time", created_at="no")),
        (_artifact(1, "2026-10-29T05:00:00Z"), {"slot": 1}, _run(1)),
        ({"created_at": "2026-10-29T05:00:00Z"}, _attempt(), _run(1)),
    ],
)
def test_an_artifact_that_does_not_parse_is_skipped(
    artifact: dict[str, Any], payload: dict[str, Any], run: dict[str, Any]
) -> None:
    assert omp_watch.parse_attempt(artifact, payload, run) is None


def test_the_ledger_round_trips_and_refuses_an_unknown_shape() -> None:
    records = CAPTURE[:20]
    assert omp_watch.load_ledger(omp_watch.dump_ledger(reversed(records))) == records
    for text in ("{", json.dumps({"schema": 9, "records": []}), json.dumps({"schema": 1})):
        with pytest.raises(omp_watch.WatchError):
            omp_watch.load_ledger(text)
    with pytest.raises(omp_watch.WatchError):
        omp_watch.load_ledger(json.dumps({"schema": 1, "records": [{"artifact_id": 1}]}))


def test_merging_keeps_the_newest_copy_of_each_artifact_and_drops_what_is_too_old() -> None:
    first, second = CAPTURE[:2]
    changed = replace(first, kind=absence.ERROR)
    merged = omp_watch.merge([first, second], [changed], first.created_at)
    assert changed in merged and first not in merged and second in merged
    assert omp_watch.merge([first, second], [], second.created_at + timedelta(seconds=1)) == []


def _zip(name: str, payload: Any) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, json.dumps(payload) if not isinstance(payload, str) else payload)
    return buffer.getvalue()


class _Host:
    def __init__(
        self,
        artifacts: dict[str, list[dict[str, Any]]],
        payloads: dict[int, Any],
        runs: dict[int, dict[str, Any]] | None = None,
        pulls: dict[int, str] | None = None,
    ) -> None:
        self.artifacts = artifacts
        self.payloads = payloads
        self.runs = runs or {}
        self.pulls = pulls or {}
        self.downloads: list[str] = []

    def getter(self, path: str) -> dict[str, Any]:
        if "/actions/runs/" in path:
            run_id = int(path.rsplit("/", 1)[1])
            if self.runs.get(run_id) == {}:
                raise omp_watch.GitHubError("gone")
            return self.runs.get(run_id) or _run(run_id)
        if "/pulls/" in path:
            number = int(path.rsplit("/", 1)[1])
            if number not in self.pulls:
                raise omp_watch.GitHubError("gone")
            return {"base": {"ref": self.pulls[number]}}
        name = path.split("name=")[1].split("&")[0]
        page = int(path.split("&page=")[1])
        items = self.artifacts.get(name, [])
        return {"artifacts": items[(page - 1) * omp_watch.ARTIFACT_PAGE : page * omp_watch.ARTIFACT_PAGE]}

    def downloader(self, path: str) -> bytes:
        self.downloads.append(path)
        identifier = int(path.split("/")[-2])
        return _zip("attempt-1.json", self.payloads[identifier])


def _collect(host: _Host, known: Sequence[int] = ()) -> list[Record]:
    return omp_watch.collect(
        "o/r",
        datetime(2026, 10, 22, tzinfo=timezone.utc),
        known,
        reviewers=frozenset({"large"}),
        getter=host.getter,
        downloader=host.downloader,
    )


def test_collection_reads_only_live_unseen_artifacts_inside_the_horizon_for_omp_reviewers() -> None:
    items = [
        _artifact(1, "2026-10-29T05:00:00Z"),
        _artifact(2, "2026-10-28T05:00:00Z"),
        _artifact(3, "2026-10-27T05:00:00Z", expired=True),
        _artifact(4, "2026-10-26T05:00:00Z"),
        _artifact(5, "2026-10-21T05:00:00Z"),
        _artifact(6, "2026-10-25T05:00:00Z"),
    ]
    payloads = {
        1: _attempt(),
        2: _attempt(reviewer="other"),
        4: _attempt(outcome="verdict", absence=None),
        6: _attempt(),
    }
    host = _Host({"oracle-verdict-attempt-1": items}, payloads)
    records = _collect(host, [6])
    assert sorted(record.artifact_id for record in records) == [1, 4]
    assert sorted(path.split("/")[-2] for path in host.downloads) == ["1", "2", "4"]


def test_collection_follows_pages_until_one_reaches_past_the_horizon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(omp_watch, "ARTIFACT_PAGE", 2)
    items = [_artifact(index, f"2026-10-{28 - index:02d}T05:00:00Z") for index in range(1, 8)]
    host = _Host({"oracle-verdict-attempt-1": items}, {index: _attempt() for index in range(1, 8)})
    assert sorted(record.artifact_id for record in _collect(host)) == [1, 2, 3, 4, 5, 6]


def test_an_artifact_that_is_not_an_archive_with_one_json_document_is_refused() -> None:
    with pytest.raises(omp_watch.WatchError):
        omp_watch._unzip(_zip("notes.txt", "x"), ".json")
    with pytest.raises(omp_watch.WatchError):
        omp_watch._unzip(b"not a zip", ".json")
    with pytest.raises(omp_watch.WatchError):
        _collect(_Host({"oracle-verdict-attempt-1": [_artifact(1, "2026-10-29T05:00:00Z")]}, {1: "{"}))


@pytest.mark.parametrize(
    "fields",
    [
        {"path": ".github/workflows/evil.yml"},
        {"repository": {"full_name": "someone/else"}},
        {"event": "pull_request"},
        {"event": "workflow_dispatch", "head_branch": "feature/forged"},
        {"event": "push", "head_branch": "feature/forged"},
    ],
)
def test_artifacts_from_a_run_of_another_workflow_repository_event_or_branch_are_never_read(
    fields: dict[str, Any],
) -> None:
    host = _Host(
        {"oracle-verdict-attempt-1": [_artifact(1, "2026-10-29T05:00:00Z"), _artifact(2, "2026-10-29T05:00:00Z")]},
        {1: _attempt(), 2: _attempt()},
        {1: _run(1, **fields)},
    )
    assert [record.artifact_id for record in _collect(host)] == [2]
    assert [path.split("/")[-2] for path in host.downloads] == ["2"]


def test_an_artifact_whose_run_cannot_be_read_is_skipped_and_tried_again_on_the_next_watch() -> None:
    host = _Host({"oracle-verdict-attempt-1": [_artifact(1, "2026-10-29T05:00:00Z")]}, {1: _attempt()}, {1: {}})
    assert _collect(host) == []
    assert host.downloads == []


@pytest.mark.parametrize(
    ("listed", "titled", "trusted"),
    [
        ([{"base": {"ref": "develop"}}], None, True),
        ([{"base": {"ref": "develop"}}, {"base": {"ref": "release"}}], None, False),
        ([{"base": {"ref": "release"}}], None, False),
        ([], "develop", True),
        ([], "release", False),
        ([], None, False),
    ],
)
def test_a_pull_request_target_run_counts_only_when_its_pull_request_targets_develop(
    listed: list[dict[str, Any]], titled: str | None, trusted: bool
) -> None:
    host = _Host({}, {}, pulls={} if titled is None else {3700: titled})
    title = "oracle-verdict (pull_request_target pr=3700 head=" + "a" * 40 + ")"
    run = _run(1, event="pull_request_target", head_branch="feature/x", pull_requests=listed, display_title=title)
    assert omp_watch.produced_by(run, "o/r", omp_watch.ORACLE_WORKFLOW, omp_watch.ORACLE_EVENTS, host.getter) is trusted


def test_a_pull_request_target_run_whose_pull_request_cannot_be_read_is_not_trusted() -> None:
    host = _Host({}, {})
    title = "oracle-verdict (pull_request_target pr=3700 head=" + "a" * 40 + ")"
    run = _run(1, event="pull_request_target", head_branch="feature/x", display_title=title)
    assert not omp_watch.produced_by(run, "o/r", omp_watch.ORACLE_WORKFLOW, omp_watch.ORACLE_EVENTS, host.getter)


def test_the_ledger_is_restored_from_the_newest_live_artifact_of_a_trusted_run_or_starts_empty() -> None:
    records = CAPTURE[:3]
    listing = {
        "artifacts": [
            {"id": 1, "created_at": "2026-10-27T00:00:00Z", "expired": False, "workflow_run": {"id": 1}},
            {"id": 2, "created_at": "2026-10-28T00:00:00Z", "expired": False, "workflow_run": {"id": 2}},
            {"id": 3, "created_at": "2026-10-29T00:00:00Z", "expired": True, "workflow_run": {"id": 3}},
            {"id": 4, "created_at": "2026-10-29T01:00:00Z", "expired": False, "workflow_run": {"id": 4}},
        ]
    }
    watch = ".github/workflows/omp-pin-watch.yml"
    runs = {
        1: _run(1, path=watch, event="schedule"),
        2: _run(2, path=watch, event="workflow_dispatch"),
        4: _run(4, path=watch, event="workflow_dispatch", head_branch="feature/forged"),
    }
    host = _Host({}, {}, runs)
    asked: list[str] = []

    def getter(path: str) -> dict[str, Any]:
        return listing if "name=" in path else host.getter(path)

    def downloader(path: str) -> bytes:
        asked.append(path)
        return _zip(omp_watch.LEDGER_FILE, omp_watch.dump_ledger(records))

    assert omp_watch.restore_ledger("o/r", getter, downloader) == records
    assert asked == ["repos/o/r/actions/artifacts/2/zip"]
    assert omp_watch.restore_ledger("o/r", lambda _: {"artifacts": []}, downloader) == []
    forged = {"artifacts": [listing["artifacts"][3]]}
    assert (
        omp_watch.restore_ledger("o/r", lambda path: forged if "name=" in path else host.getter(path), downloader) == []
    )


def _evaluate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    window: Sequence[Record],
    change: PinChange | None = CHANGE,
) -> tuple[int, Path, dict[str, str]]:
    history = [PolicyCommit("sha", MERGED_AT, "subject", POLICY)]
    monkeypatch.setattr(omp_watch, "policy_history", lambda: history)
    monkeypatch.setattr(omp_watch, "latest_pin_change", lambda _: change)
    monkeypatch.setattr(omp_watch, "restore_ledger", lambda *_: list(BEFORE))
    monkeypatch.setattr(omp_watch, "collect", lambda *_args, **_kwargs: list(window))
    published: dict[str, str] = {}
    monkeypatch.setattr(omp_watch, "_publish", published.update)
    out = tmp_path / "out"
    code = omp_watch.main(["evaluate", "--repo", "o/r", "--policy", str(POLICY_PATH), "--out-dir", str(out)])
    return code, out, published


def test_an_anomalous_window_writes_the_restored_policy_the_report_and_the_ledger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    window = _after_the_merge(_mutate(_stretch(28, 0), [absence.ERROR] * 4))
    code, out, published = _evaluate(monkeypatch, tmp_path, window)
    assert code == 0
    assert published == {"state": "revert", "version": "18.13.3", "previous": "18.12.1"}
    assert omp_pin.pin_from_text((out / "policy.yml").read_text(encoding="utf-8")).version == "18.12.1"
    assert "make omp-candidate-check VERSION=18.12.1" in (out / "pr-body.md").read_text(encoding="utf-8")
    assert "revert" in (out / "assessment.txt").read_text(encoding="utf-8")
    assert len(omp_watch.load_ledger((out / omp_watch.LEDGER_FILE).read_text(encoding="utf-8"))) >= len(window)


def test_a_clean_window_writes_no_revert(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    code, out, published = _evaluate(monkeypatch, tmp_path, _after_the_merge(_stretch(28, 0)))
    assert code == 0 and published["state"] == omp_watch.CLEAR
    assert not (out / "policy.yml").exists() and not (out / "pr-body.md").exists()


def test_a_revert_is_not_watched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    reverted = replace(CHANGE, subject="revert(oracle): restore omp 18.12.1", author_email=omp_watch.BOT_AUTHOR_EMAIL)
    code, out, published = _evaluate(monkeypatch, tmp_path, [], reverted)
    assert code == 0 and published == {"state": omp_watch.IDLE, "version": ""}
    assert not (out / omp_watch.LEDGER_FILE).exists()


def test_a_ledger_that_cannot_be_restored_fails_the_run_instead_of_judging_a_partial_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def broken(*_args: Any) -> list[Record]:
        raise omp_watch.WatchError("the ledger is not JSON")

    monkeypatch.setattr(omp_watch, "policy_history", list)
    monkeypatch.setattr(omp_watch, "latest_pin_change", lambda _: CHANGE)
    monkeypatch.setattr(omp_watch, "restore_ledger", broken)
    monkeypatch.setattr(omp_watch, "_publish", lambda _: None)
    code = omp_watch.main(["evaluate", "--repo", "o/r", "--policy", str(POLICY_PATH), "--out-dir", str(tmp_path)])
    assert code == 1


def test_a_flood_of_foreign_ledger_artifacts_cannot_push_the_trusted_ledger_off_the_first_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(omp_watch, "ARTIFACT_PAGE", 3)
    records = CAPTURE[:3]
    watch = ".github/workflows/omp-pin-watch.yml"
    artifacts = [
        {"id": index, "created_at": f"2026-10-29T0{index}:00:00Z", "expired": False, "workflow_run": {"id": index}}
        for index in range(9, 0, -1)
    ]
    runs = {index: _run(index, path=".github/workflows/other.yml") for index in range(1, 9)}
    runs[1] = _run(1, path=watch, event="schedule")
    host = _Host({}, {}, runs)

    def getter(path: str) -> dict[str, Any]:
        if "name=" not in path:
            return host.getter(path)
        page = int(path.split("&page=")[1])
        return {"artifacts": artifacts[(page - 1) * 3 : page * 3]}

    asked: list[str] = []

    def downloader(path: str) -> bytes:
        asked.append(path)
        return _zip(omp_watch.LEDGER_FILE, omp_watch.dump_ledger(records))

    assert omp_watch.restore_ledger("o/r", getter, downloader) == records
    assert asked == ["repos/o/r/actions/artifacts/1/zip"]


def test_the_policy_history_carries_each_commit_author_subject_and_the_policy_it_introduced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / ".github" / "oracle-reviewers.yml"
    target.parent.mkdir()
    environment = {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_COMMITTER_DATE": "2026-10-29T03:17:50+00:00",
        "GIT_COMMITTER_NAME": "c",
        "GIT_COMMITTER_EMAIL": "c@example.com",
    }

    def run(*args: str, **extra: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            env={**os.environ, **environment, **extra},
        )

    run("init", "-q")
    for version, author, subject in (
        ("18.12.1", "a@example.com", "chore(oracle): pin omp 18.12.1"),
        ("18.13.3", omp_watch.BOT_AUTHOR_EMAIL, "chore(oracle): pin omp 18.13.3 (#1)"),
    ):
        target.write_text(POLICY.replace("18.13.3", version), encoding="utf-8")
        run("add", ".github/oracle-reviewers.yml")
        run("commit", "-q", "-m", subject, GIT_AUTHOR_NAME="a", GIT_AUTHOR_EMAIL=author)
    history = omp_watch.policy_history()
    assert [(commit.author_email, commit.subject) for commit in history] == [
        (omp_watch.BOT_AUTHOR_EMAIL, "chore(oracle): pin omp 18.13.3 (#1)"),
        ("a@example.com", "chore(oracle): pin omp 18.12.1"),
    ]
    change = omp_watch.latest_pin_change(history)
    assert change is not None and (change.version, change.previous_version) == ("18.13.3", "18.12.1")
    assert change.merged_at == MERGED_AT and not change.is_revert
