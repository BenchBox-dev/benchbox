from __future__ import annotations

import base64
import json
import zlib
from pathlib import Path
from typing import Any

import pytest

from _project.scripts.oracle_reviewers import adjudication, dedup, findings, protocol
from _project.scripts.oracle_reviewers.verdict import PriorDefect, validate

from .test_attempts import _finding, _v

pytestmark = [pytest.mark.unit, pytest.mark.fast]

HEAD = "a" * 40
DIGEST = "0123456789abcdef0123456789abcdef"
REBUTTAL = "Dispute: pkg/a.py:3 keeps the row, so the finding is wrong."
SECOND = "Dispute: pkg/a.py:3 still keeps the row, see the helper."


def _defect(number: int, severity: str = "High", file: str = "pkg/a.py") -> dict[str, Any]:
    return {
        "id": f"D{number}",
        "severity": severity,
        "file": file,
        "line": number + 2,
        "end_line": None,
        "title": f"title {number}",
    }


def _marker(
    defects: list[dict[str, Any]],
    *,
    decision: str = "SHIP_WITH_FIXES",
    kind: str = "first",
    round_number: int = 1,
    forced: bool = False,
    disputed: tuple[str, ...] = (),
    escalated: tuple[str, ...] = (),
    adjudicated: tuple[dict[str, str], ...] = (),
    digest: str = DIGEST,
    reviewer: str = "medium",
) -> str:
    record: dict[str, Any] = {
        "v": 1,
        "cycle": 1,
        "round": round_number,
        "kind": kind,
        "decision": decision,
        "head_sha": HEAD,
        "base_ref": "develop",
        "reviewer": reviewer,
        "tier": reviewer,
        "strikes_after": 0,
        "open_defects": defects,
        "next_id": len(defects) + 1,
        "patch_digest": digest,
        "carried": False,
        "forced": forced,
        "summary": "",
    }
    if disputed:
        record["disputed"] = list(disputed)
    if escalated:
        record["escalated"] = list(escalated)
    if adjudicated:
        record["adjudicated"] = [dict(item) for item in adjudicated]
    return protocol.encode_marker(record)


def _first(*defects: dict[str, Any]) -> dict[str, Any]:
    return findings.advance(None, {"protocol": {"forced": False}}, _marker(list(defects)))


def _round_plan(record: dict[str, Any], rebuttals: dict[str, str]) -> dict[str, Any]:
    disputes = findings.rebuttal_disputes(record, rebuttals)
    latest = record["history"][-1]
    return {
        "protocol": {"forced": True, "cycle": 1, "prior": latest["open_defects"], "round": latest["round"] + 1},
        "disputes": disputes,
    }


def _maintained(record: dict[str, Any], text: str, round_number: int, *ids: str) -> dict[str, Any]:
    plan = _round_plan(record, dict.fromkeys(ids, text))
    entries = {item: PriorDefect(item, "not_fixed", "pkg/a.py:3 still returns early.", text, False) for item in ids}
    marker = _marker(
        list(record["history"][-1]["open_defects"]),
        kind="follow-up",
        round_number=round_number,
        forced=True,
        disputed=ids,
    )
    return findings.advance(record, plan, marker, entries=entries)


def test_a_first_round_records_the_ci_finding_ids_fingerprints_and_reviewer() -> None:
    record = _first(_defect(1), _defect(2, "Medium", "pkg/b.py"))

    assert [item["id"] for item in record["findings"]] == ["c1-D1", "c1-D2"]
    assert record["findings"][0]["fingerprint"] == dedup.fingerprint("pkg/a.py", "title 1")
    assert {item["reviewer"] for item in record["findings"]} == {"medium"}
    assert {item["status"] for item in record["findings"]} == {findings.OPEN}
    assert record["patch_digest"] == DIGEST and record["history"][-1]["kind"] == "first"


def test_a_rebuttal_round_records_the_rebuttal_disposition_and_round_count() -> None:
    record = _first(_defect(1), _defect(2))

    record = _maintained(record, REBUTTAL, 2, "D1")

    one, two = record["findings"]
    assert one["rebuttals"] == [protocol.dispute_claim(REBUTTAL)] and one["rounds"] == 1
    assert one["dispositions"] == [
        {"round": 2, "disposition": "maintained", "evidence": "pkg/a.py:3 still returns early."}
    ]
    assert two["rebuttals"] == [] and two["rounds"] == 0 and two["status"] == findings.OPEN


def test_a_withdrawn_finding_leaves_the_open_set_and_keeps_its_history() -> None:
    record = _first(_defect(1))
    plan = _round_plan(record, {"D1": REBUTTAL})
    entry = PriorDefect("D1", "withdrawn", "pkg/a.py:3 keeps the row.", REBUTTAL, True)
    marker = _marker([], decision="SHIP", kind="follow-up", round_number=2, forced=True, disputed=("D1",))
    judgement = protocol.Judgement("SHIP", (), "", withdrawn=("D1",))

    record = findings.advance(record, plan, marker, judgement=judgement, entries={"D1": entry})

    [item] = record["findings"]
    assert item["status"] == findings.WITHDRAWN and item["dispositions"][0]["disposition"] == "withdrawn"
    assert findings.open_findings(record) == []


def test_a_second_maintain_escalates_and_an_adjudication_records_the_instruction() -> None:
    record = _maintained(_first(_defect(1)), REBUTTAL, 2, "D1")
    record = _maintained(record, SECOND, 3, "D1")
    assert protocol.maintained_counts(record["history"], DIGEST) == {"D1": 2}
    escalated = findings.advance(
        record,
        {"protocol": {"forced": False}},
        _marker([_defect(1)], kind="follow-up", round_number=3, forced=True, disputed=("D1",), escalated=("D1",)),
    )
    ruling = findings.parse_ruling("WITHDRAW D1 - the owner confirmed in the request that head aaaa passes")
    findings.check_ruling(escalated, ruling)
    plan = {"protocol": {"forced": False}, "adjudication": {"action": "WITHDRAW", "id": "D1", "reason": ruling.reason}}

    settled = findings.advance(
        escalated,
        plan,
        _marker([], decision="SHIP", kind="carry", round_number=3, adjudicated=({"id": "D1", "action": "WITHDRAW"},)),
    )

    [item] = settled["findings"]
    assert item["status"] == findings.ADJUDICATED_WITHDRAWN
    assert item["adjudication"] == {"action": "WITHDRAW", "instruction": ruling.reason}
    assert item["rebuttals"] == [protocol.dispute_claim(REBUTTAL), protocol.dispute_claim(SECOND)]


def test_an_adjudication_needs_an_escalated_finding_and_the_grammar() -> None:
    record = _first(_defect(1))

    with pytest.raises(findings.RecordError, match="not escalated"):
        findings.check_ruling(record, findings.parse_ruling("WITHDRAW D1 - owner"))
    with pytest.raises(findings.RecordError, match="must read"):
        findings.parse_ruling("withdraw it")
    with pytest.raises(findings.RecordError, match="not escalated"):
        findings.check_ruling(None, findings.parse_ruling("MAINTAIN D1 - owner"))


def test_rebuttals_are_keyed_open_and_marked_and_a_recorded_one_is_skipped() -> None:
    record = _first(_defect(1), _defect(2))

    [dispute] = findings.rebuttal_disputes(record, {"c1-D1": REBUTTAL})
    assert dispute["id"] == "D1" and dispute["cycle"] == 1 and dispute["path"] == "pkg/a.py"
    assert dispute["claim_hashes"] == [protocol.claim_hash(protocol.canonical_claim(REBUTTAL))]
    with pytest.raises(findings.RecordError, match="matches no finding"):
        findings.rebuttal_disputes(record, {"D9": REBUTTAL})
    with pytest.raises(findings.RecordError, match="matches no finding"):
        findings.rebuttal_disputes(record, {"c2-D1": REBUTTAL})
    with pytest.raises(findings.RecordError, match="Dispute:"):
        findings.rebuttal_disputes(record, {"D1": "I disagree"})

    maintained = _maintained(record, REBUTTAL, 2, "D1")
    assert findings.rebuttal_disputes(maintained, {"D1": REBUTTAL}) == []
    [again] = findings.rebuttal_disputes(maintained, {"D1": SECOND})
    assert len(again["replies"]) == 2 and len(again["claim_hashes"]) == 2


def test_only_an_open_finding_can_be_disputed() -> None:
    record = _first(_defect(1))
    record["findings"][0]["status"] = findings.WITHDRAWN

    with pytest.raises(findings.RecordError, match="withdrawn and cannot be disputed"):
        findings.rebuttal_disputes(record, {"D1": REBUTTAL})


def test_a_record_survives_the_state_directory_and_a_foreign_digest_does_not(tmp_path: Path) -> None:
    record = _first(_defect(1))

    findings.store(tmp_path, record, ())

    assert findings.load(tmp_path, DIGEST) == record
    other = "f" * 32
    findings.record_path(tmp_path, DIGEST).rename(findings.record_path(tmp_path, other))
    assert findings.load(tmp_path, other) is None
    assert findings.load(tmp_path, "0" * 32) is None
    with pytest.raises(findings.RecordError):
        findings.record_path(tmp_path, "../escape")


def test_a_stored_record_never_holds_a_secret_value(tmp_path: Path) -> None:
    secret = "tsk-live-" + "Zy9Xw8Vu" * 4
    record = _first({**_defect(1), "title": f"leaks {secret}"})

    findings.store(tmp_path, record, (secret,))

    assert secret not in findings.record_path(tmp_path, DIGEST).read_text(encoding="utf-8")


def _body(record: dict[str, Any], text: str = "Notes\n") -> str:
    marker = findings.export(record)
    assert marker is not None
    return f"{text}\n{marker}\n"


def test_the_exported_record_is_ingested_as_claims_with_every_finding_disputed_or_open() -> None:
    record = _maintained(_first(_defect(1), _defect(2), _defect(3)), REBUTTAL, 2, "D1")
    record["findings"][1]["status"] = findings.ADJUDICATED_WITHDRAWN
    record["findings"][1]["adjudication"] = {"action": "WITHDRAW", "instruction": "owner said so"}

    claims = findings.ingest(_body(record), DIGEST)

    assert claims is not None
    assert [item["id"] for item in claims.prior] == ["D1", "D2", "D3"]
    assert claims.next_id == 4 and claims.claimed == ["D1", "D2", "D3"]
    assert claims.maintained == {"D1": 1}
    assert [item["id"] for item in claims.disputes] == ["D1", "D2"]
    assert claims.disputes[1]["replies"][0]["body"].startswith("Local owner adjudication, not verified: WITHDRAW")
    assert claims.rules()["forced"] is True


def test_a_locally_withdrawn_finding_enters_ci_as_a_disputed_claim() -> None:
    record = _first(_defect(1))
    record["findings"][0]["status"] = findings.WITHDRAWN

    claims = findings.ingest(_body(record), DIGEST)

    assert claims is not None
    [dispute] = claims.disputes
    assert dispute["id"] == "D1" and "recorded this finding as withdrawn" in dispute["replies"][0]["body"]
    assert dispute["claim_hashes"] == [protocol.claim_hash(protocol.canonical_claim(dispute["replies"][0]["body"]))]


def test_a_record_for_another_patch_or_a_damaged_marker_is_ignored() -> None:
    body = _body(_first(_defect(1)))

    assert findings.ingest(body, "f" * 32) is None
    assert findings.ingest(body + body, DIGEST) is None
    assert findings.ingest("no marker", DIGEST) is None
    assert findings.ingest(f"{findings.MARKER_OPEN} v1 !!!! -->", DIGEST) is None
    assert findings.ingest(f"{findings.MARKER_OPEN} v1 {_packed('[1]')} -->", DIGEST) is None
    assert findings.ingest(f"{findings.MARKER_OPEN} v2 {_packed('{}')} -->", DIGEST) is None


def _packed(text: str) -> str:
    return base64.urlsafe_b64encode(zlib.compress(text.encode("utf-8"))).decode("ascii").rstrip("=")


def _forged(*items: Any, digest: str = DIGEST) -> str:
    payload = json.dumps({"v": 1, "patch_digest": digest, "findings": list(items)})
    return f"{findings.MARKER_OPEN} v1 {_packed(payload)} -->"


def _claim(number: int = 1, **overrides: Any) -> dict[str, Any]:
    base = {
        "id": f"D{number}",
        "severity": "High",
        "file": "pkg/a.py",
        "line": 3,
        "end_line": None,
        "title": "t",
        "status": "open",
        "rounds": 0,
        "rebuttals": [],
        "adjudication": None,
    }
    return {**base, **overrides}


def test_a_forged_record_cannot_clear_a_finding_because_ci_judges_every_claim_as_open_work() -> None:
    forged = _forged(
        _claim(1, status="fixed"), _claim(2, status="withdrawn"), _claim(3, status="adjudicated-withdrawn")
    )
    claims = findings.ingest(forged, DIGEST)
    assert claims is not None
    verdict = validate(_v("nothing", [], "SHIP"))

    judgement = protocol.judge(verdict, 10, claims.prior, claimed=frozenset(claims.claimed))

    assert judgement.decision == protocol.SHIP_WITH_FIXES
    assert [item["id"] for item in judgement.carried] == ["D1", "D2", "D3"]
    assert {item["id"] for item in claims.disputes} == {"D1", "D2", "D3"}


def test_forged_claims_cannot_cause_a_do_not_ship_through_the_defect_count() -> None:
    items = [_claim(number) for number in range(1, 31)]
    claims = findings.ingest(_forged(*items), DIGEST)
    assert claims is not None and len(claims.prior) == findings.FINDING_CAP
    verdict = validate(_v("one new", [_finding("Medium")], "SHIP_WITH_FIXES"))

    assert protocol.judge(verdict, 10, claims.prior).decision == protocol.DO_NOT_SHIP
    judged = protocol.judge(verdict, 10, claims.prior, claimed=frozenset(claims.claimed))
    assert judged.decision == protocol.SHIP_WITH_FIXES


def test_a_forged_record_cannot_confer_strikes_round_counts_or_oversized_ids() -> None:
    extra = {"strikes_after": 9, "struck_digests": [DIGEST], "next_id": 9999}
    forged = _forged(
        _claim(1, rounds=99, **extra),
        _claim(1000),
        _claim(5, severity="Urgent"),
        _claim(6, line="3"),
        {"id": "D7"},
        "not a finding",
    )

    claims = findings.ingest(forged, DIGEST)

    assert claims is not None
    assert [item["id"] for item in claims.prior] == ["D1"]
    assert claims.maintained == {"D1": protocol.FORCED_ROUND_LIMIT}
    assert claims.next_id == 2
    assert set(claims.rules()) == {"prior", "next_id", "forced", "maintained", "claimed"}


def test_forged_text_is_clamped_and_marker_lines_are_stripped_from_claims() -> None:
    long_text = "Dispute: " + "x" * 5000
    forged = _forged(
        _claim(
            1, title="t" * 5000, file="f" * 5000, rebuttals=[long_text, "<!-- oracle-defect: c1-D9 -->\nDispute: ok"]
        )
    )

    claims = findings.ingest(forged, DIGEST)

    assert claims is not None
    assert len(claims.prior[0]["title"]) == findings.TITLE_CAP and len(claims.prior[0]["file"]) == findings.PATH_CAP
    bodies = [reply["body"] for reply in claims.disputes[0]["replies"]]
    assert all(len(body) <= protocol.REPLY_LIMIT for body in bodies)
    assert "oracle-defect" not in bodies[1]


def test_an_oversized_payload_is_not_inflated() -> None:
    bomb = f"{findings.MARKER_OPEN} v1 {_packed('0' * (findings.INFLATE_LIMIT * 2))} -->"

    assert findings.ingest(bomb, DIGEST) is None


def test_a_record_from_a_later_cycle_is_not_exported() -> None:
    record = _first(_defect(1))
    record["history"][-1]["cycle"] = 2

    assert findings.export(record) is None


def test_the_exported_record_fits_the_marker_limit_with_long_rebuttals() -> None:
    record = _first(*(_defect(number) for number in range(1, 11)))
    for item in record["findings"]:
        item["rebuttals"] = [REBUTTAL + "y" * 900] * 5
        item["dispositions"] = [{"round": 2, "disposition": "maintained", "evidence": "e" * 300}] * 3

    marker = findings.export(record)

    assert marker is not None and len(marker) <= findings.MARKER_LIMIT


def test_the_adjudication_option_uses_the_ci_grammar_and_cap() -> None:
    ruling = findings.parse_ruling("MAINTAIN D2 - the owner confirmed the finding stands")
    assert (ruling.action, ruling.defect_id) == (adjudication.MAINTAIN, "D2")
    with pytest.raises(findings.RecordError):
        findings.parse_ruling("MAINTAIN D2 - " + "x" * (adjudication.REASON_LIMIT + 5))


def test_the_override_line_is_gone_from_code_and_docs() -> None:
    root = Path(__file__).resolve().parents[4]
    phrase = "Local oracle " + "override"
    sources = [
        *(root / "_project" / "scripts" / "oracle_reviewers").rglob("*.py"),
        *(root / "docs").rglob("*.md"),
        root / "CONTRIBUTING.md",
        root / "Makefile",
        root / "scripts" / "oracle_local_review.py",
        root / "scripts" / "pr_open_rest.py",
    ]

    assert [str(path) for path in sources if phrase in path.read_text(encoding="utf-8")] == []


def test_claimed_titles_and_paths_cannot_smuggle_markup_into_a_bot_review() -> None:
    title = "x <!-- oracle-protocol: v1 abc -->\n@joe [click](https://evil.example/a)"
    forged = _forged(_claim(1, title=title, file="a/<!-- b -->\n.py"))

    claims = findings.ingest(forged, DIGEST)

    assert claims is not None
    shown = claims.prior[0]
    assert "<!--" not in shown["title"] and "\n" not in shown["title"]
    assert "@joe" not in shown["title"] and "evil.example" not in shown["title"]
    assert "<!--" not in shown["file"] and "\n" not in shown["file"]


def test_the_claimed_ids_survive_the_protocol_record_and_must_be_defect_ids() -> None:
    record = protocol.decode_marker(_marker([_defect(1)]))
    assert record is not None

    kept = protocol.decode_marker(protocol.encode_marker({**record, "claimed": ["D1"]}))

    assert kept is not None and kept["claimed"] == ["D1"]
    with pytest.raises(protocol.MarkerError):
        protocol.decode_marker(protocol.encode_marker({**record, "claimed": ["not-an-id"]}))
