from __future__ import annotations

import base64
import hashlib
import json
import os
import unicodedata
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import protocol
from _project.scripts.oracle_reviewers.protocol import (
    DO_NOT_SHIP_SUMMARY,
    MarkerError,
    decode_marker,
    encode_marker,
    judge,
    judge_planned,
)
from _project.scripts.oracle_reviewers.verdict import MAX_LINE, validate

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _defect(line: int = 1, severity: str = "High", file: str = "a.py") -> dict[str, Any]:
    return {"severity": severity, "file": file, "line": line, "end_line": None, "title": f"t{line}", "detail": ""}


def _verdict(decision: str, defects: list[dict[str, Any]], summary: str = "s", **over: Any):
    return validate(
        {
            "status": "complete",
            "incomplete_reason": "",
            "decision": decision,
            "summary": summary,
            "files_examined": ["a.py"],
            "defects": defects,
            "prior_defects": [],
            **over,
        }
    )


@pytest.mark.parametrize(
    ("decision", "count", "expected"),
    [
        ("SHIP", 0, "SHIP"),
        ("SHIP", 1, "SHIP_WITH_FIXES"),
        ("SHIP_WITH_FIXES", 0, "SHIP"),
        ("SHIP_WITH_FIXES", 3, "SHIP_WITH_FIXES"),
        ("SHIP_WITH_FIXES", 10, "SHIP_WITH_FIXES"),
        ("SHIP_WITH_FIXES", 11, "DO_NOT_SHIP"),
        ("SHIP", 11, "DO_NOT_SHIP"),
        ("DO_NOT_SHIP", 0, "DO_NOT_SHIP"),
        ("DO_NOT_SHIP", 2, "DO_NOT_SHIP"),
    ],
)
def test_the_decision_is_computed_and_the_stricter_outcome_wins(decision: str, count: int, expected: str) -> None:
    judgement = judge(_verdict(decision, [_defect(line) for line in range(1, count + 1)]), 10)
    assert judgement.decision == expected
    assert len(judgement.defects) == (count if expected == "SHIP_WITH_FIXES" else 0)


@pytest.mark.parametrize("severity", ["Critical", "High", "Medium", "Low"])
def test_a_relabelled_severity_never_produces_ship(severity: str) -> None:
    assert judge(_verdict("SHIP", [_defect(severity=severity)]), 10).decision == "SHIP_WITH_FIXES"


def test_too_many_defects_become_do_not_ship_with_the_count_in_the_summary() -> None:
    verdict = _verdict("SHIP_WITH_FIXES", [_defect(line) for line in range(1, 61)], summary="x" * 3000)
    judgement = judge(verdict, 10)
    assert judgement.decision == "DO_NOT_SHIP" and judgement.defects == ()
    assert judgement.summary.endswith("The review left 60 defects open, more than the 10 allowed.")
    assert len(judgement.summary) <= DO_NOT_SHIP_SUMMARY


def test_do_not_ship_keeps_only_a_short_summary() -> None:
    judgement = judge(_verdict("DO_NOT_SHIP", [_defect()], summary="y" * 4000), 10)
    assert len(judgement.summary) == DO_NOT_SHIP_SUMMARY
    assert judgement.label == "DO NOT SHIP"


def test_an_incomplete_review_cannot_be_judged() -> None:
    verdict = _verdict("NONE", [], status="incomplete", incomplete_reason="no access")
    with pytest.raises(ValueError, match="only a complete review"):
        judge(verdict, 10)


def test_judge_lists_fixed_and_withdrawn_ids_separately() -> None:
    prior = [
        {"id": "D1", "severity": "High", "file": "a.py", "line": 1, "end_line": None, "title": "t1"},
        {"id": "D2", "severity": "High", "file": "a.py", "line": 2, "end_line": None, "title": "t2"},
    ]
    verdict = _verdict(
        "SHIP",
        [],
        prior_defects=[
            {"id": "D1", "status": "fixed", "evidence": "reworked"},
            {"id": "D2", "status": "withdrawn", "evidence": "never a defect: a.py:2"},
        ],
    )
    judgement = judge(verdict, 10, prior)
    assert judgement.decision == "SHIP"
    assert judgement.fixed == ("D1",) and judgement.withdrawn == ("D2",) and judgement.carried == ()


def test_judge_counts_an_unverified_withdrawal_as_open() -> None:
    prior = [{"id": "D1", "severity": "High", "file": "a.py", "line": 1, "end_line": None, "title": "t1"}]
    verdict = _verdict("SHIP", [], prior_defects=[{"id": "D1", "status": "not_fixed", "evidence": "still there"}])
    judgement = judge(verdict, 10, prior)
    assert judgement.decision == "SHIP_WITH_FIXES" and judgement.carried == (prior[0],)
    assert judgement.fixed == () and judgement.withdrawn == ()


def test_judge_rejudges_fixed_as_open_on_a_forced_round() -> None:
    prior = [{"id": "D1", "severity": "High", "file": "a.py", "line": 1, "end_line": None, "title": "t1"}]
    fixed = _verdict("SHIP_WITH_FIXES", [], prior_defects=[{"id": "D1", "status": "fixed", "evidence": "reworked"}])
    judgement = judge(fixed, 10, prior, rejudge_fixed=True)
    assert judgement.decision == "SHIP_WITH_FIXES" and judgement.carried == (prior[0],)
    assert judgement.fixed == () and judgement.withdrawn == ()


def test_judge_rejudges_an_uncited_withdrawal_as_open_on_a_forced_round() -> None:
    prior = [{"id": "D1", "severity": "High", "file": "a.py", "line": 1, "end_line": None, "title": "t1"}]
    verdict = _verdict(
        "SHIP_WITH_FIXES", [], prior_defects=[{"id": "D1", "status": "withdrawn", "evidence": "never a defect"}]
    )
    judgement = judge(verdict, 10, prior, rejudge_fixed=True)
    assert judgement.decision == "SHIP_WITH_FIXES" and judgement.carried == (prior[0],)
    assert judgement.withdrawn == ()


def test_judge_keeps_a_cited_withdrawal_on_a_forced_round() -> None:
    prior = [{"id": "D1", "severity": "High", "file": "a.py", "line": 1, "end_line": None, "title": "t1"}]
    verdict = _verdict(
        "SHIP", [], prior_defects=[{"id": "D1", "status": "withdrawn", "evidence": "never a defect: a.py:1"}]
    )
    judgement = judge(verdict, 10, prior, rejudge_fixed=True)
    assert judgement.decision == "SHIP" and judgement.withdrawn == ("D1",)


def test_judge_merges_identical_defects_and_counts_them_once() -> None:
    judgement = judge(_verdict("SHIP_WITH_FIXES", [_defect(1), _defect(1), _defect(2)]), 10)
    assert len(judgement.defects) == 2 and judgement.merged == 1


def _marker_record(**over: Any) -> dict[str, Any]:
    record = {
        "v": 1,
        "cycle": 1,
        "round": 1,
        "kind": "first",
        "decision": "SHIP",
        "head_sha": "a" * 40,
        "base_ref": "develop",
        "reviewer": "large",
        "tier": "large",
        "strikes_after": 0,
        "open_defects": [],
        "next_id": 1,
        "patch_digest": "d" * 32,
        "carried": False,
        "summary": "",
        **over,
    }
    return record


def test_a_marker_round_trips_an_optional_run_uuid() -> None:
    body = f"line\n{encode_marker(_marker_record(run_uuid='a' * 32))}"
    assert decode_marker(body)["run_uuid"] == "a" * 32


def test_a_marker_without_a_run_uuid_stays_valid() -> None:
    body = f"line\n{encode_marker(_marker_record())}"
    assert decode_marker(body).get("run_uuid") is None


def test_a_marker_rejects_a_malformed_run_uuid() -> None:
    body = f"line\n{encode_marker(_marker_record(run_uuid='xyz'))}"
    with pytest.raises(MarkerError):
        decode_marker(body)


def test_judge_counts_nfd_finding_on_nfc_changed_path() -> None:
    nfc_path = "benchbox/Café.py"
    nfd_path = unicodedata.normalize("NFD", nfc_path)
    assert nfc_path != nfd_path
    verdict = _verdict("SHIP", [_defect(1, file=nfd_path)])
    judgement = judge(verdict, 10, changed=[nfc_path])
    assert judgement.decision == "SHIP_WITH_FIXES"
    assert len(judgement.defects) == 1
    assert judgement.defects[0].file == nfc_path
    assert judgement.not_counted == ()


def test_judge_counts_case_variant_finding_matching_single_changed_path() -> None:
    verdict = _verdict("SHIP", [_defect(1, file="benchbox/café.py")])
    judgement = judge(verdict, 10, changed=["benchbox/Café.py"])
    assert judgement.decision == "SHIP_WITH_FIXES"
    assert len(judgement.defects) == 1
    assert judgement.defects[0].file == "benchbox/Café.py"
    assert judgement.not_counted == ()


def test_judge_counts_ambiguous_case_variant_against_decision() -> None:
    verdict = _verdict("SHIP", [_defect(1, file="benchbox/query.py")])
    judgement = judge(verdict, 10, changed=["benchbox/Query.py", "benchbox/QUERY.py"])
    assert judgement.decision == "SHIP_WITH_FIXES"
    assert len(judgement.defects) == 1
    assert judgement.defects[0].file == "benchbox/query.py"
    assert judgement.not_counted == ()


def _tree(tmp_path: Path, *names: str) -> None:
    (tmp_path / "benchbox").mkdir()
    for name in names:
        (tmp_path / "benchbox" / name).write_text(name, encoding="utf-8")


def test_judge_does_not_fold_citation_of_distinct_existing_file(tmp_path: Path) -> None:
    _tree(tmp_path, "query.py")
    verdict = _verdict("SHIP", [_defect(1, file="benchbox/query.py")])
    judgement = judge(verdict, 10, changed=["benchbox/Query.py"], workspace=tmp_path)
    assert judgement.decision == "SHIP"
    assert judgement.defects == ()
    assert [item.file for item in judgement.not_counted] == ["benchbox/query.py"]


def test_judge_folds_case_variant_of_nonexistent_path_to_single_changed_file(tmp_path: Path) -> None:
    _tree(tmp_path)
    verdict = _verdict("SHIP", [_defect(1, file="benchbox/query.py")])
    judgement = judge(verdict, 10, changed=["benchbox/Query.py"], workspace=tmp_path)
    assert [item.file for item in judgement.defects] == ["benchbox/Query.py"]
    assert judgement.not_counted == ()


def test_judge_leaves_ambiguous_fold_unrewritten_with_workspace(tmp_path: Path) -> None:
    _tree(tmp_path)
    verdict = _verdict("SHIP", [_defect(1, file="benchbox/query.py")])
    judgement = judge(verdict, 10, changed=["benchbox/Query.py", "benchbox/QUERY.py"], workspace=tmp_path)
    assert [item.file for item in judgement.defects] == ["benchbox/query.py"]


def test_judge_counts_symlink_alias_finding_in_workspace(tmp_path: Path) -> None:
    target = tmp_path / "benchbox" / "Café.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n", encoding="utf-8")
    alias = tmp_path / "benchbox" / "alias.py"
    os.symlink("Café.py", alias)
    verdict = _verdict("SHIP", [_defect(1, file="benchbox/alias.py")])
    judgement = judge(verdict, 10, changed=["benchbox/Café.py"], workspace=tmp_path)
    assert judgement.decision == "SHIP_WITH_FIXES"
    assert len(judgement.defects) == 1
    assert judgement.defects[0].file == "benchbox/Café.py"
    assert judgement.not_counted == ()


def test_judge_counts_symlink_changed_path_matching_target_finding(tmp_path: Path) -> None:
    target = tmp_path / "benchbox" / "Café.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n", encoding="utf-8")
    alias = tmp_path / "benchbox" / "alias.py"
    os.symlink("Café.py", alias)
    verdict = _verdict("SHIP", [_defect(1, file="benchbox/Café.py")])
    judgement = judge(verdict, 10, changed=["benchbox/alias.py"], workspace=tmp_path)
    assert judgement.decision == "SHIP_WITH_FIXES"
    assert len(judgement.defects) == 1
    assert judgement.defects[0].file == "benchbox/alias.py"
    assert judgement.not_counted == ()


def test_judge_keeps_genuinely_unchanged_file_out_of_scope() -> None:
    verdict = _verdict("SHIP", [_defect(1, file="benchbox/unchanged.py")])
    judgement = judge(verdict, 10, changed=["benchbox/Café.py"])
    assert judgement.decision == "SHIP"
    assert judgement.defects == ()
    assert len(judgement.not_counted) == 1
    assert judgement.not_counted[0].file == "benchbox/unchanged.py"


def test_judge_keeps_case_variant_of_unchanged_file_out_of_scope() -> None:
    verdict = _verdict("SHIP", [_defect(1, file="benchbox/UNCHANGED.py")])
    judgement = judge(verdict, 10, changed=["benchbox/Café.py"])
    assert judgement.decision == "SHIP"
    assert judgement.defects == ()
    assert len(judgement.not_counted) == 1
    assert judgement.not_counted[0].file == "benchbox/UNCHANGED.py"


@pytest.mark.parametrize(
    ("changed", "finding_file", "raw_in_scope", "canonical_in_scope", "expected_file"),
    [
        (["a.py"], "a.py", True, True, "a.py"),
        (["benchbox/Café.py"], unicodedata.normalize("NFD", "benchbox/Café.py"), False, True, "benchbox/Café.py"),
        (["benchbox/Café.py"], "benchbox/café.py", False, True, "benchbox/Café.py"),
        (["benchbox/Query.py", "benchbox/QUERY.py"], "benchbox/query.py", False, True, "benchbox/query.py"),
        (["benchbox/Café.py"], "benchbox/other.py", False, False, None),
    ],
)
def test_canonicalization_only_moves_findings_into_scope_never_out(
    changed: list[str],
    finding_file: str,
    raw_in_scope: bool,
    canonical_in_scope: bool,
    expected_file: str | None,
) -> None:
    raw_matches = finding_file in set(changed)
    assert raw_matches is raw_in_scope
    verdict = _verdict("SHIP", [_defect(1, file=finding_file)])
    judgement = judge(verdict, 10, changed=changed)
    is_counted = len(judgement.defects) == 1
    assert is_counted is canonical_in_scope
    if raw_in_scope:
        assert is_counted
    if expected_file is not None:
        assert judgement.defects[0].file == expected_file


def test_judge_planned_uses_workspace_and_changed_from_plan(tmp_path: Path) -> None:
    target = tmp_path / "benchbox" / "Café.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n", encoding="utf-8")
    alias = tmp_path / "benchbox" / "alias.py"
    os.symlink("Café.py", alias)
    plan = {
        "max_defects": 10,
        "workspace": str(tmp_path),
        "protocol": {"kind": "follow-up", "changed": ["benchbox/Café.py"]},
    }
    verdict = _verdict("SHIP", [_defect(1, file="benchbox/alias.py")])
    judgement = judge_planned(verdict, plan)
    assert judgement.decision == "SHIP_WITH_FIXES"
    assert len(judgement.defects) == 1
    assert judgement.defects[0].file == "benchbox/Café.py"


def test_judge_resolves_decomposed_symlink_before_unicode_normalization(tmp_path: Path) -> None:
    (tmp_path / "target.py").write_text("x = 1\n", encoding="utf-8")
    alias = unicodedata.normalize("NFD", "Café.py")
    (tmp_path / alias).symlink_to("target.py")
    judgement = judge(_verdict("SHIP", [_defect(file=alias)]), 10, changed=["target.py"], workspace=tmp_path)
    assert judgement.decision == "SHIP_WITH_FIXES"
    assert judgement.defects[0].file == "target.py"


@pytest.mark.parametrize("path", [" a.py", "a.py ", "unchanged.py"])
def test_judge_does_not_strip_real_filename_characters(path: str) -> None:
    judgement = judge(_verdict("SHIP", [_defect(file=path)]), 10, changed=["a.py"])
    assert judgement.decision == "SHIP"
    assert judgement.not_counted[0].file == path


def test_judge_does_not_match_symlinks_outside_head_checkout(tmp_path: Path) -> None:
    root = tmp_path / "head"
    root.mkdir()
    (tmp_path / "outside.py").write_text("x = 1\n", encoding="utf-8")
    (root / "changed.py").symlink_to("../outside.py")
    (root / "unchanged.py").symlink_to("../outside.py")
    verdict = _verdict("SHIP", [_defect(file="unchanged.py"), _defect(file="changed.py")])
    judgement = judge(verdict, 10, changed=["changed.py"], workspace=root)
    assert [item.file for item in judgement.defects] == ["changed.py"]
    assert [item.file for item in judgement.not_counted] == ["unchanged.py"]


HEAD = "a" * 40
OTHER = "b" * 40
DECIDED = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def _open(identifier: str = "D1", **over: Any) -> dict[str, Any]:
    return {"id": identifier, "severity": "High", "file": "a.py", "line": 3, "end_line": None, "title": "t", **over}


def _wire(record: dict[str, Any]) -> str:
    raw = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    packed = base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=")
    return f"<!-- oracle-protocol: v1 {packed} -->"


def _scrambled(length: int, seed: str) -> str:
    text = ""
    while len(text) < length:
        text += hashlib.sha256(f"{seed}{len(text)}".encode()).hexdigest()
    return text[:length]


def test_a_marker_encodes_the_same_whatever_the_key_order_and_round_trips() -> None:
    record = _marker_record(open_defects=[_open()], summary="checked")
    reordered = dict(reversed(list(record.items())))
    assert encode_marker(record) == encode_marker(reordered)
    assert decode_marker(f"line\n{encode_marker(record)}") == record


@pytest.mark.parametrize(
    "record",
    [
        _marker_record(open_defects=[_open(severity="Urgent")]),
        _marker_record(open_defects=[_open(line="3")]),
        _marker_record(open_defects=[_open(file=None)]),
        _marker_record(open_defects=[_open("X1")]),
        _marker_record(open_defects=[_open(end_line="4")]),
        _marker_record(open_defects=[{**_open(), "detail": "extra"}]),
        _marker_record(open_defects=[{key: value for key, value in _open().items() if key != "title"}]),
        _marker_record(unknown=True),
        _marker_record(cycle=-1),
        _marker_record(forced="yes"),
        _marker_record(patch_map={"0a1b2c3d": 1}),
        _marker_record(patch_map=["0a1b2c3d"]),
    ],
    ids=[
        "severity",
        "line",
        "file",
        "defect-id",
        "end-line",
        "extra-defect-key",
        "missing-defect-key",
        "extra-record-key",
        "negative-cycle",
        "forced",
        "patch-value",
        "patch-map",
    ],
)
def test_a_marker_with_a_malformed_field_is_refused(record: dict[str, Any]) -> None:
    with pytest.raises(MarkerError):
        decode_marker(f"line\n{_wire(record)}")


def test_a_marker_with_every_optional_field_decodes() -> None:
    record = _marker_record(patch_map={"0a1b2c3d": "e" * 16}, forced=True, run_uuid="1" * 32)
    assert decode_marker(f"line\n{encode_marker(record)}") == record


def test_a_marker_that_fits_the_limit_exactly_keeps_its_patch_map(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _marker_record(open_defects=[_open()])
    patches = {"0a1b2c3d": "e" * 16}
    full = encode_marker(record, patches)
    bare = encode_marker(record)
    assert decode_marker(f"line\n{full}")["patch_map"] == patches
    monkeypatch.setattr(protocol, "MARKER_LIMIT", len(full))
    assert encode_marker(record, patches) == full
    monkeypatch.setattr(protocol, "MARKER_LIMIT", len(bare))
    assert encode_marker(record, patches) == bare


def test_a_marker_drops_only_the_summary_when_that_is_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    title = _scrambled(200, "title")
    record = _marker_record(open_defects=[_open(title=title)], summary=_scrambled(600, "summary"))
    monkeypatch.setattr(protocol, "MARKER_LIMIT", len(encode_marker({**record, "summary": ""})))
    decoded = decode_marker(encode_marker(record))
    assert decoded["summary"] == "" and decoded["open_defects"][0]["title"] == title


def test_a_marker_trims_titles_and_paths_only_as_far_as_the_limit_needs(monkeypatch: pytest.MonkeyPatch) -> None:
    title = _scrambled(200, "title")
    path = "pkg/" + _scrambled(196, "path")
    record = _marker_record(open_defects=[_open(title=title, file=path)], summary=_scrambled(600, "summary"))
    trimmed = {**record, "summary": "", "open_defects": [_open(title=title[:119] + "…", file="…" + path[-119:])]}
    monkeypatch.setattr(protocol, "MARKER_LIMIT", len(encode_marker(trimmed)))
    assert decode_marker(encode_marker(record)) == trimmed


@pytest.mark.parametrize(
    ("title_length", "path_length"),
    [(120, 130), (130, 120)],
    ids=["title-fits", "path-fits"],
)
def test_a_marker_trim_leaves_a_title_or_path_that_fits_its_limit_untouched(
    monkeypatch: pytest.MonkeyPatch, title_length: int, path_length: int
) -> None:
    title = _scrambled(title_length, "title")
    path = _scrambled(path_length, "path")
    record = _marker_record(open_defects=[_open(title=title, file=path)], summary=_scrambled(600, "summary"))
    kept_title = title if title_length == 120 else title[:119] + "…"
    kept_path = path if path_length == 120 else "…" + path[-119:]
    trimmed = {**record, "summary": "", "open_defects": [_open(title=kept_title, file=kept_path)]}
    monkeypatch.setattr(protocol, "MARKER_LIMIT", len(encode_marker(trimmed)))
    assert decode_marker(encode_marker(record)) == trimmed


def _review(record: dict[str, Any], submitted_at: str | None, review_id: int) -> dict[str, Any]:
    return {
        "login": "benchbox-oracle[bot]",
        "user_type": "Bot",
        "state": "COMMENTED",
        "submitted_at": submitted_at,
        "id": review_id,
        "body": f"Decision.\n\n{encode_marker(record)}",
        "last_edited_at": None,
        "includes_created_edit": False,
    }


def test_history_orders_reviews_by_submission_time_then_id() -> None:
    early = _marker_record(round=1)
    late = _marker_record(round=2)
    reviews = [_review(late, "2026-10-05T13:00:00Z", 1), _review(early, "2026-10-05T12:00:00Z", 9)]
    assert protocol.history(reviews, "benchbox-oracle").latest == late
    tied = [_review(late, "2026-10-05T12:00:00Z", 9), _review(early, "2026-10-05T12:00:00Z", 1)]
    assert protocol.history(tied, "benchbox-oracle").latest == late
    undated = [_review(late, "2026-10-05T12:00:00Z", 1), _review(early, None, 9)]
    assert protocol.history(undated, "benchbox-oracle").latest == late


def test_history_with_a_malformed_marker_has_no_records() -> None:
    broken = {**_review(_marker_record(), "2026-10-05T12:00:00Z", 1), "body": "<!-- oracle-protocol: v1 !!! -->"}
    found = protocol.history([broken], "benchbox-oracle")
    assert found.records == [] and found.latest is None and found.error is not None


def _found(record: dict[str, Any], decided: datetime = DECIDED) -> protocol.History:
    return protocol.History(records=[record], submitted=[decided], ids=[1])


def _round(found: protocol.History, **over: Any) -> protocol.Round:
    arguments: dict[str, Any] = {
        "head_sha": HEAD,
        "base_ref": "develop",
        "digest": "d" * 32,
        "patches": {},
        "paths": ["a.py"],
        "strike_count": 0,
        "max_do_not_ship": 3,
        **over,
    }
    return protocol.plan_round(found, **arguments)


def test_the_strike_limit_refuses_with_the_first_cycle_when_there_is_no_history() -> None:
    step = _round(protocol.History(), strike_count=3)
    assert (step.kind, step.cycle, step.number, step.previous) == (protocol.REFUSAL, 1, 0, None)


def test_the_strike_limit_refuses_a_head_whose_latest_record_is_not_a_refusal() -> None:
    latest = _marker_record(decision="DO_NOT_SHIP", cycle=4, round=2)
    step = _round(_found(latest), strike_count=3)
    assert (step.kind, step.cycle, step.number, step.previous) == (protocol.REFUSAL, 4, 0, latest)


def test_a_base_change_at_the_decision_moment_makes_the_same_head_carry() -> None:
    latest = _marker_record(decision="SHIP", cycle=2, round=3)
    step = _round(_found(latest), base_changed_at=DECIDED)
    assert (step.kind, step.cycle, step.number, step.previous) == (protocol.CARRY, 2, 3, latest)


@pytest.mark.parametrize(
    ("latest", "over"),
    [
        (_marker_record(head_sha=OTHER, cycle=2, round=3, base_ref="main"), {}),
        (_marker_record(head_sha=OTHER, cycle=2, round=3, decision="REFUSED", kind="refused"), {}),
        (_marker_record(head_sha=OTHER, cycle=2, round=3, decision="DO_NOT_SHIP"), {"digest": "e" * 32}),
    ],
    ids=["base-ref", "refused", "do-not-ship"],
)
def test_a_restart_opens_the_next_cycle_at_round_one(latest: dict[str, Any], over: dict[str, Any]) -> None:
    step = _round(_found(latest), **over)
    assert (step.kind, step.cycle, step.number, step.previous) == (protocol.FIRST, 3, 1, latest)


def test_an_unchanged_patch_on_a_new_head_carries_the_latest_round() -> None:
    latest = _marker_record(head_sha=OTHER, cycle=2, round=3, decision="SHIP_WITH_FIXES")
    step = _round(_found(latest))
    assert (step.kind, step.cycle, step.number, step.previous) == (protocol.CARRY, 2, 3, latest)


def test_marker_lines_are_stripped_and_the_other_lines_kept() -> None:
    text = "keep one\n<!-- oracle-defect: c1-D2 -->\nkeep two"
    assert protocol.strip_marker_lines(text) == "keep one\nkeep two"
    assert protocol.strip_marker_lines("") == ""


def test_an_empty_claim_hashes_like_none_and_claims_differing_in_space_or_case_hash_alike() -> None:
    assert protocol.claim_hash(None) == protocol.claim_hash("")
    assert protocol.claim_hash("  Foo \n BAR\t") == protocol.claim_hash("foo bar")
    assert protocol.claim_hash("foo bar") != protocol.claim_hash("foo baz")


def test_unique_defects_keeps_the_first_entry_for_each_id() -> None:
    first, second, repeat = _open("D1"), _open("D2"), _open("D1", title="repeat")
    assert protocol.unique_defects([first, second, repeat]) == [first, second]


def test_live_disputes_need_an_open_defect_id_and_a_matching_or_missing_cycle() -> None:
    latest = {"cycle": 2, "open_defects": [_open("D1"), {"severity": "High"}, "D3"]}
    undated = {"id": "D1"}
    current = {"id": "D1", "cycle": 2}
    disputes = [undated, current, {"id": "D1", "cycle": 1}, {"cycle": 2}, {"id": "D3", "cycle": 2}]
    assert protocol.live_disputes(disputes, latest) == [undated, current]


def test_a_do_not_ship_summary_that_fits_is_kept_whole_and_a_longer_one_is_cut_with_an_ellipsis() -> None:
    fits = "z" * DO_NOT_SHIP_SUMMARY
    assert judge(_verdict("DO_NOT_SHIP", [_defect()], summary=fits), 10).summary == fits
    cut = judge(_verdict("DO_NOT_SHIP", [_defect()], summary=fits + "z"), 10).summary
    assert cut == fits[:-1] + "…"


def test_a_stored_marker_clamps_pre_bound_finding_coordinates() -> None:
    huge = 10**4000
    record = _marker_record(open_defects=[_open(line=huge, end_line=huge + 5), _open("D2", line=7)])
    decoded = decode_marker(f"line\n{encode_marker(record)}")
    assert decoded is not None
    first, second = decoded["open_defects"]
    assert first["line"] == first["end_line"] == MAX_LINE
    assert (second["line"], second["end_line"]) == (7, None)
