from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import zlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .evidence import withdrawal_cited
from .policy import SEVERITIES
from .verdict import COMPLETE, DO_NOT_SHIP, FIXED, NOT_FIXED, SHIP, SHIP_WITH_FIXES, WITHDRAWN, Finding, Verdict

DO_NOT_SHIP_SUMMARY = 1500
REFUSED = "REFUSED"
LABELS = {SHIP: "SHIP", SHIP_WITH_FIXES: "SHIP WITH FIXES", DO_NOT_SHIP: "DO NOT SHIP", REFUSED: "REFUSED"}
RECORD_DECISIONS = (SHIP, SHIP_WITH_FIXES, DO_NOT_SHIP, REFUSED)

FIRST = "first"
FOLLOW_UP = "follow-up"
CARRY = "carry"
SKIP = "skip"
REFUSAL = "refused"
KINDS = (FIRST, FOLLOW_UP, CARRY, REFUSAL)

MARKER_VERSION = 1
MARKER_LIMIT = 4096
MARKER_OPEN = "<!-- oracle-protocol:"
_MARKER = re.compile(r"<!-- oracle-protocol: v1 (?P<data>[A-Za-z0-9_-]+) -->\s*\Z")
_HUNK = re.compile(
    r"^@@ -(?P<start>\d+)(?:,(?P<count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<section>.*)$"
)
DEFECT_ID = re.compile(r"D([1-9][0-9]{0,3})")
NOT_COUNTED_LIMIT = 5
PATCH_HEX = 16
MISSING = "missing"
RECORD_KEYS = frozenset(
    {
        "v",
        "cycle",
        "round",
        "kind",
        "decision",
        "head_sha",
        "base_ref",
        "reviewer",
        "tier",
        "strikes_after",
        "open_defects",
        "next_id",
        "patch_digest",
        "carried",
        "summary",
    }
)
DEFECT_KEYS = ("id", "severity", "file", "line", "end_line", "title")


class MarkerError(ValueError):
    pass


@dataclass(frozen=True)
class Judgement:
    decision: str
    defects: tuple[Finding, ...]
    summary: str
    carried: tuple[dict[str, Any], ...] = ()
    fixed: tuple[str, ...] = ()
    not_counted: tuple[Finding, ...] = ()
    withdrawn: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return LABELS[self.decision]

    @property
    def open_count(self) -> int:
        return len(self.carried) + len(self.defects)


@dataclass(frozen=True)
class Round:
    kind: str
    reason: str
    cycle: int = 1
    number: int = 1
    previous: Mapping[str, Any] | None = None
    changed: frozenset[str] | None = None
    forced: bool = False


@dataclass
class History:
    records: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    @property
    def latest(self) -> dict[str, Any] | None:
        return self.records[-1] if self.records else None


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _short(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _heading_gap(section: str, new_start: int, new_count: int, content: str | None, old_start: int) -> str:
    heading = section.strip()
    if content is None or not heading:
        return f"at{old_start}"
    lines = content.split("\n")
    begin = new_start + (1 if new_count == 0 else 0)
    for index in range(min(begin - 2, len(lines) - 1), -1, -1):
        if lines[index].strip().startswith(heading):
            return f"fn{begin - 1 - index}"
    return f"at{old_start}"


def file_patches(diff_text: str, read: Callable[[str], str | None] | None = None) -> dict[str, str]:
    patches: dict[str, str] = {}
    for section in re.split(r"(?m)^(?=diff --git )", diff_text):
        header, _, body = section.partition("\n")
        match = re.fullmatch(r"diff --git a/(?P<old>.+) b/(?P<new>.+)", header)
        if match is None:
            continue
        binary = "\nBinary files " in f"\n{body}" or "\nGIT binary patch" in f"\n{body}"
        deleted = "\ndeleted file mode " in f"\n{body}"
        kept = []
        previous_end: int | None = None
        lines = body.split("\n")
        if lines[-1] == "":
            lines.pop()
        for line in lines:
            if not binary and line.startswith("index "):
                continue
            hunk = _HUNK.match(line)
            if hunk is None:
                kept.append(line)
                continue
            count = 1 if hunk["count"] is None else int(hunk["count"])
            new_count = 1 if hunk["new_count"] is None else int(hunk["new_count"])
            first = int(hunk["start"]) + (1 if count == 0 else 0)
            if previous_end is not None:
                gap = str(first - previous_end)
            elif read is None or deleted or not hunk["section"].strip():
                gap = "first"
            else:
                gap = _heading_gap(
                    hunk["section"], int(hunk["new_start"]), new_count, read(match.group("new")), int(hunk["start"])
                )
            previous_end = first + count
            kept.append(f"@@ gap={gap} old={count} new={new_count}{hunk['section']}")
        patches[match.group("new")] = hashlib.sha256("\n".join(kept).encode("utf-8")).hexdigest()
    return patches


def patch_map(patches: Mapping[str, str], paths: Iterable[str]) -> dict[str, str]:
    wanted = sorted(set(paths))
    collided = len({_short(path) for path in wanted}) < len(wanted)
    return {_short(path): MISSING if collided or path not in patches else patches[path][:PATCH_HEX] for path in wanted}


def patch_digest(patches: Mapping[str, str], paths: Iterable[str]) -> str | None:
    wanted = sorted(set(paths))
    if any(path not in patches for path in wanted):
        return None
    full = {path: patches[path] for path in wanted}
    return hashlib.sha256(json.dumps(full, sort_keys=True).encode("utf-8")).hexdigest()[:32]


def changed_paths(previous: Mapping[str, str] | None, current: Mapping[str, str], paths: Iterable[str]) -> frozenset:
    every = frozenset(paths)
    if previous is None or MISSING in previous.values() or MISSING in current.values() or set(previous) - set(current):
        return every
    return frozenset(path for path in every if previous.get(_short(path)) != current.get(_short(path)))


def encode_marker(record: Mapping[str, Any], patches: Mapping[str, str] | None = None) -> str:
    def render(data: Mapping[str, Any]) -> str:
        raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        packed = base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=")
        return f"{MARKER_OPEN} v{MARKER_VERSION} {packed} -->"

    full = render({**record, "patch_map": dict(patches)}) if patches is not None else ""
    if full and len(full) <= MARKER_LIMIT:
        return full
    trimmed = render(record)
    if len(trimmed) > MARKER_LIMIT:
        trimmed = render({**record, "summary": ""})
    if len(trimmed) > MARKER_LIMIT:
        raise MarkerError("the protocol record does not fit in a review marker")
    return trimmed


def _valid_defect(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and set(item) == set(DEFECT_KEYS)
        and isinstance(item["id"], str)
        and DEFECT_ID.fullmatch(item["id"]) is not None
        and item["severity"] in SEVERITIES
        and isinstance(item["file"], str)
        and isinstance(item["line"], int)
        and (item["end_line"] is None or isinstance(item["end_line"], int))
        and isinstance(item["title"], str)
    )


def _valid_patches(patches: Any) -> bool:
    return isinstance(patches, dict) and all(isinstance(v, str) for v in patches.values())


def _valid_record(record: Any) -> bool:
    if not isinstance(record, dict) or not RECORD_KEYS <= set(record) <= RECORD_KEYS | {"patch_map", "forced"}:
        return False
    return (
        record["v"] == MARKER_VERSION
        and record["kind"] in KINDS
        and record["decision"] in RECORD_DECISIONS
        and all(isinstance(record[key], int) and record[key] >= 0 for key in ("cycle", "round", "strikes_after"))
        and isinstance(record["next_id"], int)
        and re.fullmatch(r"[0-9a-f]{40}", str(record["head_sha"])) is not None
        and all(isinstance(record[key], str) for key in ("base_ref", "reviewer", "tier", "patch_digest", "summary"))
        and isinstance(record["carried"], bool)
        and ("forced" not in record or isinstance(record["forced"], bool))
        and isinstance(record["open_defects"], list)
        and all(_valid_defect(item) for item in record["open_defects"])
        and (record.get("patch_map") is None or _valid_patches(record["patch_map"]))
    )


def decode_marker(body: str) -> dict[str, Any] | None:
    count = body.count(MARKER_OPEN)
    if count == 0:
        return None
    match = _MARKER.search(body)
    if count > 1 or match is None:
        raise MarkerError("a review carries a malformed or repeated protocol marker")
    data = match.group("data")
    try:
        raw = zlib.decompress(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)))
        record = json.loads(raw)
    except (binascii.Error, ValueError, zlib.error) as exc:
        raise MarkerError(f"a protocol marker cannot be decoded: {exc}") from exc
    if not _valid_record(record):
        raise MarkerError("a protocol marker has an unexpected shape")
    return record


def history(reviews: Iterable[Mapping[str, Any]], bot_login: str) -> History:
    own = [
        review
        for review in reviews
        if (review.get("login") or "") == f"{bot_login}[bot]"
        and review.get("user_type") == "Bot"
        and review.get("state") != "DISMISSED"
    ]
    found = History()
    for review in sorted(own, key=lambda item: (str(item.get("submitted_at") or ""), int(item.get("id") or 0))):
        try:
            record = decode_marker(review.get("body") or "")
        except MarkerError as exc:
            return History([], str(exc))
        if record is not None:
            found.records.append(record)
    return found


def struck_digests(records: Iterable[Mapping[str, Any]]) -> frozenset[str]:
    return frozenset(
        record["patch_digest"]
        for record in records
        if record["decision"] == DO_NOT_SHIP and record["kind"] in (FIRST, FOLLOW_UP) and not record["carried"]
    )


def strikes(records: Iterable[Mapping[str, Any]]) -> int:
    return len(struck_digests(records))


FORCED_ROUND_LIMIT = 2


def forced_rounds(records: Iterable[Mapping[str, Any]], digest: str) -> int:
    return sum(
        1
        for record in records
        if record.get("patch_digest") == digest and record.get("kind") == FOLLOW_UP and record.get("forced")
    )


def _forced_round(
    records: Iterable[Mapping[str, Any]],
    *,
    digest: str,
    latest: Mapping[str, Any],
    paths: Sequence[str],
    reason: str,
) -> Round:
    if forced_rounds(records, digest) >= FORCED_ROUND_LIMIT:
        return Round(
            SKIP,
            f"the patch already had {FORCED_ROUND_LIMIT} forced re-reviews;"
            " change the patch or ask the owner for a stand-in",
        )
    return Round(
        FOLLOW_UP,
        reason,
        latest["cycle"],
        latest["round"] + 1,
        latest,
        frozenset(paths),
        True,
    )


def plan_round(
    found: History,
    *,
    head_sha: str,
    base_ref: str,
    digest: str,
    patches: Mapping[str, str],
    paths: Sequence[str],
    strike_count: int,
    max_do_not_ship: int,
    force: bool = False,
) -> Round:
    latest = found.latest
    if strike_count >= max_do_not_ship:
        if latest is not None and latest["kind"] == REFUSAL and latest["head_sha"] == head_sha:
            return Round(SKIP, f"this head already has the refusal after {strike_count} DO NOT SHIP decisions")
        cycle = latest["cycle"] if latest else 1
        return Round(REFUSAL, f"{strike_count} DO NOT SHIP decisions on this pull request", cycle, 0, latest)
    if latest is None:
        return Round(FIRST, "first review of this pull request")
    if latest["head_sha"] == head_sha:
        if force and latest["decision"] in (SHIP, SHIP_WITH_FIXES):
            return _forced_round(
                found.records,
                digest=digest,
                latest=latest,
                paths=paths,
                reason=(
                    f"forced re-review after {LABELS[latest['decision']]}: an author rebuttal disputes an open finding"
                ),
            )
        return Round(SKIP, f"head {head_sha} already has a {LABELS[latest['decision']]} decision")
    restart = latest["cycle"] + 1
    if latest["base_ref"] != base_ref:
        return Round(FIRST, f"the base changed from {latest['base_ref']} to {base_ref}", restart, 1, latest)
    if latest["decision"] == REFUSED:
        return Round(FIRST, "the strike limit no longer applies", restart, 1, latest)
    if latest["patch_digest"] == digest:
        if force and latest["decision"] in (SHIP, SHIP_WITH_FIXES):
            return _forced_round(
                found.records,
                digest=digest,
                latest=latest,
                paths=paths,
                reason=(
                    f"the patch is unchanged since head {latest['head_sha']},"
                    " but an author rebuttal disputes an open finding: forced re-review"
                ),
            )
        return Round(
            CARRY, f"the patch is unchanged since head {latest['head_sha']}", latest["cycle"], latest["round"], latest
        )
    if latest["decision"] == DO_NOT_SHIP:
        return Round(FIRST, "the patch changed after DO NOT SHIP", restart, 1, latest)
    changed = changed_paths(latest.get("patch_map"), patches, paths) or frozenset(paths)
    return Round(
        FOLLOW_UP,
        f"the patch changed after {LABELS[latest['decision']]}",
        latest["cycle"],
        latest["round"] + 1,
        latest,
        changed,
    )


def judge(
    verdict: Verdict,
    max_defects: int,
    prior: Sequence[Mapping[str, Any]] = (),
    changed: Iterable[str] | None = None,
    rejudge_fixed: bool = False,
) -> Judgement:
    if verdict.status != COMPLETE:
        raise ValueError("only a complete review can be judged")
    statuses = {item.id: item.status for item in verdict.prior_defects}
    cited = {item.id: item.evidence for item in verdict.prior_defects}
    if rejudge_fixed:
        statuses = {
            key: (
                NOT_FIXED if value == FIXED or (value == WITHDRAWN and not withdrawal_cited(cited.get(key))) else value
            )
            for key, value in statuses.items()
        }
    carried = tuple(item for item in prior if statuses.get(item["id"], NOT_FIXED) == NOT_FIXED)
    fixed = tuple(item["id"] for item in prior if statuses.get(item["id"], NOT_FIXED) == FIXED)
    withdrawn = tuple(item["id"] for item in prior if statuses.get(item["id"]) == WITHDRAWN)
    scope = None if changed is None else frozenset(changed)
    counted = tuple(item for item in verdict.defects if scope is None or item.file in scope)
    outside = tuple(item for item in verdict.defects if item not in counted)[:NOT_COUNTED_LIMIT]
    uncounted = verdict.listed - len(verdict.defects)
    total = len(carried) + len(counted) + uncounted
    if verdict.decision == DO_NOT_SHIP or total > max_defects:
        note = ""
        if total > max_defects:
            note = f" The review left {total} defects open, more than the {max_defects} allowed."
        summary = _clip(verdict.summary, DO_NOT_SHIP_SUMMARY - len(note)) + note
        return Judgement(DO_NOT_SHIP, (), summary, fixed=fixed, withdrawn=withdrawn)
    if carried or counted:
        return Judgement(SHIP_WITH_FIXES, counted, verdict.summary, carried, fixed, outside, withdrawn)
    return Judgement(SHIP, (), verdict.summary, (), fixed, outside, withdrawn)


def judge_planned(verdict: Verdict, plan: Mapping[str, Any]) -> Judgement:
    rules = plan.get("protocol") or {}
    changed = rules.get("changed") if rules.get("kind") == FOLLOW_UP else None
    return judge(verdict, int(plan["max_defects"]), rules.get("prior", ()), changed, bool(rules.get("forced")))


def contradiction(verdict: Verdict, plan: Mapping[str, Any]) -> str | None:
    if verdict.status != COMPLETE or judge_planned(verdict, plan).decision != SHIP:
        return None
    if verdict.incomplete_reason.strip():
        return "the verdict would ship but reports that the review could not be finished"
    priors = (plan.get("protocol") or {}).get("prior") or ()
    known = {item.get("id") for item in priors if isinstance(item, Mapping)}
    unknown = {item.id for item in verdict.prior_defects} - known
    if unknown:
        return f"the verdict would ship but cites {len(unknown)} prior defect ID(s) the plan does not list"
    unsupported = sorted(
        item.id for item in verdict.prior_defects if item.status in (FIXED, WITHDRAWN) and not item.evidence.strip()
    )
    if unsupported:
        return f"the verdict would ship but clears prior defect(s) {', '.join(unsupported)} without evidence"
    if verdict.decision == SHIP_WITH_FIXES and not verdict.defects and verdict.listed == 0:
        return "SHIP_WITH_FIXES names no current or open prior defects"
    return None


def defect_entry(defect_id: str, finding: Finding) -> dict[str, Any]:
    return {
        "id": defect_id,
        "severity": finding.severity,
        "file": finding.file,
        "line": finding.line,
        "end_line": finding.end_line,
        "title": finding.title,
    }
