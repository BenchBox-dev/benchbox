from __future__ import annotations

import base64
import binascii
import json
import os
import re
import uuid
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import adjudication, dedup, protocol, redaction_check, report
from .policy import SEVERITIES
from .verdict import MAX_LINE, PriorDefect

RECORD_VERSION = 1
FINDING_CAP = 20
NUMBER_CAP = 999
TEXT_CAP = protocol.REPLY_LIMIT
TITLE_CAP = 300
PATH_CAP = 300
EVIDENCE_CAP = 300
RECORDED_DISPOSITIONS = 3
RECORDED_REBUTTALS = 5
MARKER_OPEN = "<!-- oracle-local-record:"
MARKER_LIMIT = 30000
INFLATE_LIMIT = 1 << 20
AUTHOR = "local-review"
OPEN = "open"
WITHDRAWN = "withdrawn"
FIXED = "fixed"
ADJUDICATED_WITHDRAWN = "adjudicated-withdrawn"
ADJUDICATED_MAINTAINED = "adjudicated-maintained"
STATUSES = (OPEN, WITHDRAWN, FIXED, ADJUDICATED_WITHDRAWN, ADJUDICATED_MAINTAINED)
ADJUDICATION_GRAMMAR = (
    "WITHDRAW|MAINTAIN D<n> - <the owner's instruction naming the head, the finding and the decision>"
)
_MARKER = re.compile(rf"{re.escape(MARKER_OPEN)} v{RECORD_VERSION} (?P<data>[A-Za-z0-9_-]+) -->")
_ID = re.compile(r"c([0-9]+)-(D[1-9][0-9]{0,3})")
_SHORT = re.compile(r"D[1-9][0-9]{0,3}")
_DIGEST = re.compile(r"[0-9a-f]{32}")


class RecordError(ValueError):
    pass


def finding_id(cycle: int, short: str) -> str:
    return f"c{cycle}-{short}"


def _short(key: str) -> str:
    match = _ID.fullmatch(key)
    if match is None:
        raise RecordError(f"{key} is not a finding id")
    return match.group(2)


def _number(short: str) -> int:
    return int(short[1:])


def record_path(state: Path, digest: str) -> Path:
    if _DIGEST.fullmatch(digest) is None:
        raise RecordError("the patch digest is not a record name")
    return state / "records" / f"{digest}.json"


def _valid_finding(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and _ID.fullmatch(item["id"]) is not None
        and isinstance(item.get("cycle"), int)
        and item.get("severity") in SEVERITIES
        and isinstance(item.get("file"), str)
        and isinstance(item.get("line"), int)
        and (item.get("end_line") is None or isinstance(item["end_line"], int))
        and isinstance(item.get("title"), str)
        and isinstance(item.get("fingerprint"), str)
        and isinstance(item.get("reviewer"), str)
        and item.get("status") in STATUSES
        and isinstance(item.get("rounds"), int)
        and isinstance(item.get("rebuttals"), list)
        and all(isinstance(text, str) for text in item["rebuttals"])
        and isinstance(item.get("dispositions"), list)
        and all(isinstance(entry, dict) for entry in item["dispositions"])
        and (item.get("adjudication") is None or isinstance(item["adjudication"], dict))
    )


def _checked(data: Any, digest: str) -> dict[str, Any] | None:
    if not isinstance(data, dict) or data.get("v") != RECORD_VERSION or data.get("patch_digest") != digest:
        return None
    raw = data.get("history")
    findings = data.get("findings")
    if not isinstance(raw, list) or not raw or not isinstance(findings, list):
        return None
    history = [protocol.checked_record(item) for item in raw]
    if any(item is None for item in history) or not all(_valid_finding(item) for item in findings):
        return None
    return {**data, "history": history}


def load(state: Path, digest: str) -> dict[str, Any] | None:
    try:
        return _checked(json.loads(record_path(state, digest).read_text(encoding="utf-8")), digest)
    except (OSError, ValueError):
        return None


def store(state: Path, record: Mapping[str, Any], secrets: tuple[str, ...]) -> None:
    path = record_path(state, record["patch_digest"])
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    scratch.write_text(
        json.dumps(redaction_check.redact(dict(record), secrets), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(scratch, path)


def history_of(record: Mapping[str, Any] | None) -> protocol.History:
    return protocol.History(records=list(record["history"])) if record else protocol.History()


def latest_cycle(record: Mapping[str, Any]) -> int:
    return int(record["history"][-1]["cycle"])


def _new_finding(cycle: int, entry: Mapping[str, Any], reviewer: str) -> dict[str, Any]:
    return {
        "id": finding_id(cycle, entry["id"]),
        "cycle": cycle,
        "severity": entry["severity"],
        "file": entry["file"],
        "line": entry["line"],
        "end_line": entry["end_line"],
        "title": entry["title"],
        "fingerprint": dedup.fingerprint(entry["file"], entry["title"]),
        "reviewer": reviewer,
        "status": OPEN,
        "rounds": 0,
        "rebuttals": [],
        "dispositions": [],
        "adjudication": None,
    }


def advance(
    previous: Mapping[str, Any] | None,
    plan: Mapping[str, Any],
    marker: str,
    *,
    judgement: protocol.Judgement | None = None,
    entries: Mapping[str, PriorDefect] | None = None,
) -> dict[str, Any]:
    latest = protocol.decode_marker(marker)
    if latest is None:
        raise RecordError("the round left no protocol record")
    cycle = latest["cycle"]
    history = [*(previous["history"] if previous else ()), latest]
    kept = {item["id"]: dict(item) for item in (previous["findings"] if previous else ())}
    open_now = {item["id"]: item for item in latest["open_defects"]}
    for short, entry in open_now.items():
        kept.setdefault(finding_id(cycle, short), _new_finding(cycle, entry, latest["reviewer"]))
    forced = bool((plan.get("protocol") or {}).get("forced"))
    disputed = {item["id"]: item for item in protocol.bound_disputes(plan)} if forced else {}
    settled = {item["id"]: item["action"] for item in latest.get("adjudicated", ())}
    ruling = plan.get("adjudication") or {}
    withdrawn = set(judgement.withdrawn) if judgement else set()
    fixed = set(judgement.fixed) if judgement else set()
    counts = protocol.maintained_counts(history, latest["patch_digest"])
    for key, item in kept.items():
        if item["cycle"] != cycle:
            continue
        short = _short(key)
        item["rounds"] = counts.get(short, 0)
        dispute = disputed.get(short)
        if dispute is not None:
            for reply in dispute["replies"]:
                if reply["body"] not in item["rebuttals"]:
                    item["rebuttals"].append(reply["body"])
            entry = (entries or {}).get(short)
            item["dispositions"].append(
                {
                    "round": latest["round"],
                    "disposition": (
                        "none" if entry is None else report.disposition_label(report.effective_status(plan, entry))
                    ),
                    "evidence": "" if entry is None else entry.evidence[:EVIDENCE_CAP],
                }
            )
        if short in settled:
            action = settled[short]
            item["status"] = ADJUDICATED_WITHDRAWN if action == adjudication.WITHDRAW else ADJUDICATED_MAINTAINED
            instruction = (
                ruling["reason"] if ruling.get("id") == short else (item["adjudication"] or {}).get("instruction", "")
            )
            item["adjudication"] = {"action": action, "instruction": instruction}
        elif short in open_now:
            item["status"] = OPEN
        elif short in withdrawn:
            item["status"] = WITHDRAWN
        elif short in fixed:
            item["status"] = FIXED
    return {
        "v": RECORD_VERSION,
        "patch_digest": latest["patch_digest"],
        "base_ref": latest["base_ref"],
        "head_sha": latest["head_sha"],
        "history": history,
        "findings": list(kept.values()),
    }


def open_findings(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    cycle = latest_cycle(record)
    return [item for item in record["findings"] if item["cycle"] == cycle and item["status"] == OPEN]


def parse_ruling(text: str) -> adjudication.Adjudication:
    ruling = adjudication.parse(adjudication.PREFIX + text.strip())
    if ruling is None:
        raise RecordError(f"the adjudication must read: {ADJUDICATION_GRAMMAR}")
    return ruling


def check_ruling(record: Mapping[str, Any] | None, ruling: adjudication.Adjudication) -> None:
    latest = record["history"][-1] if record else None
    if (
        latest is None
        or latest["decision"] != protocol.SHIP_WITH_FIXES
        or ruling.defect_id not in latest.get("escalated", ())
    ):
        raise RecordError(
            f"{ruling.defect_id} is not escalated to the owner for this patch; an owner adjudication applies only "
            f"after {protocol.ESCALATION_ROUNDS} dispute re-reviews leave a finding open"
        )


def load_rebuttals(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecordError(f"the rebuttal file {path} cannot be read as JSON: {exc}") from exc
    if not isinstance(data, dict) or not all(
        isinstance(key, str) and isinstance(text, str) for key, text in data.items()
    ):
        raise RecordError("the rebuttal file must be a JSON object of finding id to rebuttal text")
    return data


def _claim_hash(text: str) -> str:
    return protocol.claim_hash(protocol.canonical_claim(text))


def rebuttal_disputes(record: Mapping[str, Any], rebuttals: Mapping[str, str]) -> list[dict[str, Any]]:
    cycle = latest_cycle(record)
    current = {_short(item["id"]): item for item in record["findings"] if item["cycle"] == cycle}
    disputes = []
    for key, text in sorted(rebuttals.items()):
        match = _ID.fullmatch(key)
        short = key if _SHORT.fullmatch(key) else (match.group(2) if match and int(match.group(1)) == cycle else "")
        item = current.get(short)
        if item is None:
            raise RecordError(f"the rebuttal key {key} matches no finding of this patch")
        if item["status"] != OPEN:
            raise RecordError(f"{item['id']} is {item['status']} and cannot be disputed")
        if not protocol.is_dispute(text):
            raise RecordError(f"the rebuttal for {key} needs a line that starts with '{protocol.DISPUTE_MARKER}'")
        claim = protocol.dispute_claim(text)
        known = [protocol.dispute_claim(old) for old in item["rebuttals"]]
        if claim in known:
            continue
        replies = [{"author": AUTHOR, "body": body} for body in (*known, claim)]
        disputes.append(
            {
                "id": short,
                "cycle": cycle,
                "path": item["file"],
                "title": item["title"][:TITLE_CAP],
                "replies": replies,
                "claim_hashes": [_claim_hash(reply["body"]) for reply in replies],
            }
        )
    return disputes


def _exported(item: Mapping[str, Any], rebuttals: int, dispositions: int, text_cap: int) -> dict[str, Any]:
    return {
        "id": _short(item["id"]),
        "severity": item["severity"],
        "file": item["file"][-PATH_CAP:],
        "line": item["line"],
        "end_line": item["end_line"],
        "title": item["title"][:TITLE_CAP],
        "fingerprint": item["fingerprint"],
        "reviewer": item["reviewer"],
        "status": item["status"],
        "rounds": item["rounds"],
        "rebuttals": [text[:text_cap] for text in item["rebuttals"][-rebuttals:]] if rebuttals else [],
        "dispositions": [
            {
                "round": entry.get("round", 0),
                "disposition": str(entry.get("disposition", ""))[:40],
                "evidence": str(entry.get("evidence", ""))[:text_cap],
            }
            for entry in item["dispositions"][-dispositions:]
        ]
        if dispositions
        else [],
        "adjudication": (
            None
            if not item["adjudication"]
            else {
                "action": item["adjudication"].get("action", ""),
                "instruction": str(item["adjudication"].get("instruction", ""))[:text_cap],
            }
        ),
    }


def _encode(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    packed = base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=")
    return f"{MARKER_OPEN} v{RECORD_VERSION} {packed} -->"


def export(record: Mapping[str, Any]) -> str | None:
    cycle = latest_cycle(record)
    if cycle != 1:
        return None
    ranked = sorted(
        (item for item in record["findings"] if item["cycle"] == cycle),
        key=lambda item: (item["status"] not in (OPEN, ADJUDICATED_MAINTAINED), _number(_short(item["id"]))),
    )[:FINDING_CAP]
    if not ranked:
        return None
    ranked.sort(key=lambda item: _number(_short(item["id"])))
    for rebuttals, dispositions, text_cap in (
        (RECORDED_REBUTTALS, RECORDED_DISPOSITIONS, TEXT_CAP),
        (RECORDED_REBUTTALS, 0, TEXT_CAP),
        (2, 0, 300),
        (1, 0, 120),
    ):
        marker = _encode(
            {
                "v": RECORD_VERSION,
                "patch_digest": record["patch_digest"],
                "findings": [_exported(item, rebuttals, dispositions, text_cap) for item in ranked],
            }
        )
        if len(marker) <= MARKER_LIMIT:
            return marker
    raise RecordError("the findings record does not fit in a pull request body marker")


def decode_body(body: str) -> dict[str, Any] | None:
    if (body or "").count(MARKER_OPEN) != 1:
        return None
    match = _MARKER.search(body)
    if match is None:
        return None
    data = match.group("data")
    try:
        inflater = zlib.decompressobj()
        raw = inflater.decompress(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)), INFLATE_LIMIT)
        payload = json.loads(raw) if inflater.eof and not inflater.unconsumed_tail else None
    except (binascii.Error, ValueError, zlib.error, RecursionError):
        return None
    return payload if isinstance(payload, dict) else None


def _text(value: Any, cap: int) -> str | None:
    return value[:cap] if isinstance(value, str) else None


def _claim(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    short = raw.get("id")
    severity, path, title = raw.get("severity"), _text(raw.get("file"), PATH_CAP), _text(raw.get("title"), TITLE_CAP)
    line, end = raw.get("line"), raw.get("end_line")
    if (
        not isinstance(short, str)
        or _SHORT.fullmatch(short) is None
        or _number(short) > NUMBER_CAP
        or severity not in SEVERITIES
        or not path
        or not title
        or not isinstance(line, int)
        or isinstance(line, bool)
        or not (end is None or (isinstance(end, int) and not isinstance(end, bool)))
        or raw.get("status") not in STATUSES
    ):
        return None
    rounds = raw.get("rounds")
    rebuttals = raw.get("rebuttals")
    ruling = raw.get("adjudication")
    texts = [
        protocol.dispute_claim(text)
        for text in (rebuttals if isinstance(rebuttals, list) else ())
        if isinstance(text, str)
    ]
    if (
        isinstance(ruling, dict)
        and ruling.get("action") in adjudication.ACTIONS
        and isinstance(ruling.get("instruction"), str)
    ):
        texts.append(
            f"Local owner adjudication, not verified: {ruling['action']} - "
            f"{protocol.dispute_claim(ruling['instruction'])[: adjudication.REASON_LIMIT]}"
        )
    return {
        "defect": {
            "id": short,
            "severity": severity,
            "file": path,
            "line": max(min(line, MAX_LINE), 1),
            "end_line": None if end is None else max(min(end, MAX_LINE), 1),
            "title": title,
        },
        "status": raw["status"],
        "rounds": max(min(rounds, protocol.FORCED_ROUND_LIMIT), 0)
        if isinstance(rounds, int) and not isinstance(rounds, bool)
        else 0,
        "texts": [text for text in texts if text.strip()],
    }


@dataclass(frozen=True)
class Claims:
    prior: list[dict[str, Any]]
    disputes: list[dict[str, Any]]
    maintained: dict[str, int]
    next_id: int
    claimed: list[str]

    def rules(self) -> dict[str, Any]:
        return {
            "prior": self.prior,
            "next_id": self.next_id,
            "forced": True,
            "maintained": self.maintained,
            "claimed": self.claimed,
        }


def ingest(body: str, digest: str, max_defects: int) -> Claims | None:
    payload = decode_body(body)
    if payload is None or payload.get("v") != RECORD_VERSION or payload.get("patch_digest") != digest:
        return None
    raw = payload.get("findings")
    if not isinstance(raw, list):
        return None
    unique: dict[str, dict[str, Any]] = {}
    for item in raw:
        claim = _claim(item)
        if claim is not None:
            unique.setdefault(claim["defect"]["id"], claim)
    claims = sorted(unique.values(), key=lambda item: _number(item["defect"]["id"]))[: max(FINDING_CAP, max_defects)]
    if not claims:
        return None
    prior, disputes, maintained = [], [], {}
    for claim in claims:
        defect = claim["defect"]
        prior.append(defect)
        texts = claim["texts"]
        if not texts and (claim["status"] != OPEN):
            texts = [f"The local review recorded this finding as {claim['status'].replace('-', ' ')}."]
        if claim["rounds"]:
            maintained[defect["id"]] = claim["rounds"]
        if texts:
            disputes.append(
                {
                    "id": defect["id"],
                    "cycle": 1,
                    "path": defect["file"],
                    "title": defect["title"],
                    "replies": [{"author": AUTHOR, "body": text} for text in texts],
                    "claim_hashes": [_claim_hash(text) for text in texts],
                }
            )
    return Claims(
        prior=prior,
        disputes=disputes,
        maintained=maintained,
        next_id=max(_number(item["id"]) for item in prior) + 1,
        claimed=[item["id"] for item in prior],
    )
