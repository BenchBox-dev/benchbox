from __future__ import annotations

import io
import json
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
    "ca66bf1", MERGED_AT, "chore(oracle): pin omp 18.13.3 (#3713)", "18.13.3", "18.12.1", PREVIOUS_POLICY
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
    assert (mix.attempts, mix.counted) == (1209, 156)
    assert mix.rate == pytest.approx(0.129, abs=0.001)
    assert {kind: mix.kinds[kind] for kind in ("quota", "auth", "incomplete", "invalid", "empty", "error")} == {
        "quota": 10,
        "auth": 2,
        "incomplete": 44,
        "invalid": 105,
        "empty": 1,
        "error": 50,
    }


def test_the_reviews_observed_after_the_real_pin_change_are_pending_and_clean() -> None:
    result = omp_watch.assess(CAPTURE, CHANGE, datetime(2026, 10, 29, 12, 50, tzinfo=timezone.utc))
    assert result.state == omp_watch.PENDING and not result.reasons
    assert result.reviews == 7 and result.window.attempts == 7 and result.window.counted == 0
    assert result.baseline.attempts == 1209


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
    "subject", ['Revert "chore(oracle): pin omp 18.13.3" (#3679) (#3707)', "revert(oracle): restore omp 18.12.1"]
)
def test_a_bump_that_is_itself_a_revert_is_flagged_so_it_is_never_watched(subject: str) -> None:
    change = omp_watch.latest_pin_change(_history("18.12.1", "18.13.3", subjects=[subject]))
    assert change is not None and change.is_revert


def test_an_ordinary_bump_is_not_a_revert() -> None:
    assert not CHANGE.is_revert


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


def _artifact(identifier: int, created: str, run: int = 40900000001, **fields: Any) -> dict[str, Any]:
    return {"id": identifier, "created_at": created, "expired": False, "workflow_run": {"id": run}, **fields}


def test_an_attempt_artifact_becomes_a_record_with_its_absence_kind_and_a_clipped_detail() -> None:
    record = omp_watch.parse_attempt(_artifact(7, "2026-10-29T05:00:00Z"), _attempt())
    assert record is not None
    assert (record.artifact_id, record.run_id, record.reviewer, record.slot) == (7, 40900000001, "large", 1)
    assert (record.kind, record.counted) == ("error", True)
    assert len(record.detail) == omp_watch.DETAIL_LIMIT
    verdict = omp_watch.parse_attempt(_artifact(8, "2026-10-29T05:00:00Z"), _attempt(outcome="verdict", absence=None))
    assert verdict is not None and verdict.kind == absence.OK and not verdict.counted


@pytest.mark.parametrize(
    ("artifact", "payload"),
    [
        (_artifact(1, "not a time"), _attempt()),
        ({"id": 1, "created_at": "2026-10-29T05:00:00Z"}, _attempt()),
        (_artifact(1, "2026-10-29T05:00:00Z"), {"slot": 1}),
    ],
)
def test_an_artifact_that_does_not_parse_is_skipped(artifact: dict[str, Any], payload: dict[str, Any]) -> None:
    assert omp_watch.parse_attempt(artifact, payload) is None


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
    def __init__(self, artifacts: dict[str, list[dict[str, Any]]], payloads: dict[int, dict[str, Any]]) -> None:
        self.artifacts = artifacts
        self.payloads = payloads
        self.downloads: list[str] = []

    def getter(self, path: str) -> dict[str, Any]:
        name = path.split("name=")[1].split("&")[0]
        page = int(path.split("&page=")[1])
        items = self.artifacts.get(name, [])
        return {"artifacts": items[(page - 1) * omp_watch.ARTIFACT_PAGE : page * omp_watch.ARTIFACT_PAGE]}

    def downloader(self, path: str) -> bytes:
        self.downloads.append(path)
        identifier = int(path.split("/")[-2])
        return _zip("attempt-1.json", self.payloads[identifier])


def test_collection_reads_only_live_unseen_artifacts_inside_the_horizon_for_omp_reviewers() -> None:
    horizon = datetime(2026, 10, 22, tzinfo=timezone.utc)
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
    records = omp_watch.collect(
        "o/r", horizon, [6], reviewers=frozenset({"large"}), getter=host.getter, downloader=host.downloader
    )
    assert sorted(record.artifact_id for record in records) == [1, 4]
    assert sorted(path.split("/")[-2] for path in host.downloads) == ["1", "2", "4"]


def test_collection_follows_pages_until_one_reaches_past_the_horizon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(omp_watch, "ARTIFACT_PAGE", 2)
    horizon = datetime(2026, 10, 22, tzinfo=timezone.utc)
    items = [_artifact(index, f"2026-10-{28 - index:02d}T05:00:00Z") for index in range(1, 8)]
    host = _Host({"oracle-verdict-attempt-1": items}, {index: _attempt() for index in range(1, 8)})
    records = omp_watch.collect(
        "o/r", horizon, [], reviewers=frozenset({"large"}), getter=host.getter, downloader=host.downloader
    )
    assert sorted(record.artifact_id for record in records) == [1, 2, 3, 4, 5, 6]


def test_an_artifact_that_is_not_an_archive_with_one_json_document_is_refused() -> None:
    with pytest.raises(omp_watch.WatchError):
        omp_watch._unzip(_zip("notes.txt", "x"), ".json")
    with pytest.raises(omp_watch.WatchError):
        omp_watch._unzip(b"not a zip", ".json")
    host = _Host({"oracle-verdict-attempt-1": [_artifact(1, "2026-10-29T05:00:00Z")]}, {})
    host.payloads[1] = "{"
    with pytest.raises(omp_watch.WatchError):
        omp_watch.collect(
            "o/r",
            datetime(2026, 10, 22, tzinfo=timezone.utc),
            [],
            reviewers=frozenset({"large"}),
            getter=host.getter,
            downloader=host.downloader,
        )


def test_the_ledger_is_restored_from_the_newest_live_artifact_or_starts_empty() -> None:
    records = CAPTURE[:3]
    listing = {
        "artifacts": [
            {"id": 1, "created_at": "2026-10-27T00:00:00Z", "expired": False},
            {"id": 2, "created_at": "2026-10-28T00:00:00Z", "expired": False},
            {"id": 3, "created_at": "2026-10-29T00:00:00Z", "expired": True},
        ]
    }
    asked: list[str] = []

    def downloader(path: str) -> bytes:
        asked.append(path)
        return _zip(omp_watch.LEDGER_FILE, omp_watch.dump_ledger(records))

    assert omp_watch.restore_ledger("o/r", lambda _: listing, downloader) == records
    assert asked == ["repos/o/r/actions/artifacts/2/zip"]
    assert omp_watch.restore_ledger("o/r", lambda _: {"artifacts": []}, downloader) == []


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
    reverted = replace(CHANGE, subject='Revert "chore(oracle): pin omp 18.13.3"')
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
