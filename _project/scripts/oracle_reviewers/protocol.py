from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import unicodedata
import zlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from .edits import EDITED_REASON, EditStateError, is_edited
from .evidence import withdrawal_cited
from .policy import SEVERITIES
from .verdict import (
    COMPLETE,
    DEMOTED_DISPOSITION,
    DEMOTED_STATUSES,
    DO_NOT_SHIP,
    FIXED,
    MAX_CLAIM,
    MAX_LINE,
    NOT_FIXED,
    SHIP,
    SHIP_WITH_FIXES,
    WITHDRAWN,
    Finding,
    PriorDefect,
    Verdict,
    sanitize,
)

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

UNREAD_DIGEST = "unread-"
MARKER_VERSION = 1
MARKER_LIMIT = 4096
MARKER_TRIMS = ((120, 120), (80, 80), (50, 60), (32, 40), (24, 24))
MARKER_OPEN = "<!-- oracle-protocol:"
_MARKER = re.compile(r"<!-- oracle-protocol: v1 (?P<data>[A-Za-z0-9_-]+) -->\s*\Z")
_ANY_MARKER = re.compile(r"<!-- oracle-protocol: v[0-9]+ (?P<data>[A-Za-z0-9_-]+) -->")
MARKER_INFLATE_LIMIT = 1 << 20
DEFECT_ID = re.compile(r"D([1-9][0-9]{0,3})")
NOT_COUNTED_LIMIT = 5
PATCH_HEX = 16
MISSING = "missing"
ABSENT_MODE = "000000"
ABSENT_ID = "0" * 40
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
OPTIONAL_RECORD_KEYS = frozenset({"patch_map", "forced", "run_uuid"})
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
    merged: int = 0

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


@dataclass(frozen=True)
class FileState:
    old_path: str
    new_path: str
    old_mode: str
    new_mode: str
    old_id: str
    new_id: str

    def fields(self) -> list[str]:
        return [self.old_path, self.new_path, self.old_mode, self.new_mode, self.old_id, self.new_id]


@dataclass
class History:
    records: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    submitted: list[datetime | None] = field(default_factory=list)
    ids: list[int | None] = field(default_factory=list)

    @property
    def latest(self) -> dict[str, Any] | None:
        return self.records[-1] if self.records else None

    @property
    def latest_at(self) -> datetime | None:
        return self.submitted[-1] if len(self.submitted) == len(self.records) and self.records else None


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _short(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _fingerprint(entry: FileState) -> str:
    return hashlib.sha256(json.dumps(entry.fields(), separators=(",", ":")).encode("utf-8")).hexdigest()


def file_state(
    path: str,
    previous_path: str | None,
    old: tuple[str, str] | None,
    new: tuple[str, str] | None,
) -> FileState:
    return FileState(
        old_path=previous_path or path,
        new_path=path,
        old_mode=old[0] if old else ABSENT_MODE,
        new_mode=new[0] if new else ABSENT_MODE,
        old_id=old[1] if old else ABSENT_ID,
        new_id=new[1] if new else ABSENT_ID,
    )


def fingerprints(states: Iterable[FileState]) -> dict[str, str]:
    return {state.new_path: _fingerprint(state) for state in states}


def patch_map(prints: Mapping[str, str], paths: Iterable[str]) -> dict[str, str]:
    wanted = sorted(set(paths))
    collided = len({_short(path) for path in wanted}) < len(wanted)
    return {_short(path): MISSING if collided or path not in prints else prints[path][:PATCH_HEX] for path in wanted}


def patch_digest(prints: Mapping[str, str]) -> str:
    return hashlib.sha256(json.dumps(prints, sort_keys=True).encode("utf-8")).hexdigest()[:32]


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
    for stripped in ({**record}, {**record, "summary": ""}):
        trimmed = render(stripped)
        if len(trimmed) <= MARKER_LIMIT:
            return trimmed
    for title_limit, file_limit in MARKER_TRIMS:
        trimmed = render(
            {**record, "summary": "", "open_defects": _trim_defects(record["open_defects"], title_limit, file_limit)}
        )
        if len(trimmed) <= MARKER_LIMIT:
            return trimmed
    raise MarkerError("the protocol record does not fit in a review marker")


def _clip_head(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(limit - 1, 0)] + "…"[:limit]


def _clip_tail(text: str, limit: int) -> str:
    return text if len(text) <= limit else ("…" + text[len(text) - limit + 1 :])[:limit]


def _trim_defects(defects: Iterable[Mapping[str, Any]], title_limit: int, file_limit: int) -> list[dict[str, Any]]:
    return [
        {**item, "title": _clip_head(item["title"], title_limit), "file": _clip_tail(item["file"], file_limit)}
        for item in defects
    ]


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
    if not isinstance(record, dict) or not RECORD_KEYS <= set(record) <= RECORD_KEYS | OPTIONAL_RECORD_KEYS:
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
        and ("run_uuid" not in record or re.fullmatch(r"[0-9a-f]{32}|[0-9]+", str(record["run_uuid"])) is not None)
        and isinstance(record["open_defects"], list)
        and all(_valid_defect(item) for item in record["open_defects"])
        and (record.get("patch_map") is None or _valid_patches(record["patch_map"]))
    )


def _clamped(item: dict[str, Any]) -> dict[str, Any]:
    end = item["end_line"]
    return {
        **item,
        "line": max(min(item["line"], MAX_LINE), 1),
        "end_line": None if end is None else max(min(end, MAX_LINE), 1),
    }


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
    return {**record, "open_defects": [_clamped(item) for item in record["open_defects"]]}


def marker_payloads(text: str) -> list[Any]:
    found: list[Any] = []
    for match in _ANY_MARKER.finditer(text):
        data = match.group("data")
        try:
            raw = zlib.decompressobj().decompress(
                base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)), MARKER_INFLATE_LIMIT
            )
            found.append(json.loads(raw))
        except (binascii.Error, ValueError, zlib.error):
            continue
    return found


def _review_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def is_app_review(review: Mapping[str, Any], bot_login: str) -> bool:
    return (
        (review.get("login") or "") == f"{bot_login}[bot]"
        and review.get("user_type") == "Bot"
        and review.get("state") != "DISMISSED"
    )


def history(reviews: Iterable[Mapping[str, Any]], bot_login: str) -> History:
    own = [review for review in reviews if is_app_review(review, bot_login)]
    for review in own:
        if "last_edited_at" not in review or "includes_created_edit" not in review:
            raise EditStateError(f"oracle review {review.get('id')} has no readable edit state")
        if is_edited(review["last_edited_at"], review["includes_created_edit"]):
            return History([], EDITED_REASON)
    found = History()
    for review in sorted(own, key=lambda item: (str(item.get("submitted_at") or ""), int(item.get("id") or 0))):
        try:
            record = decode_marker(review.get("body") or "")
        except MarkerError as exc:
            return History([], str(exc))
        if record is not None:
            found.records.append(record)
            found.submitted.append(_review_time(review.get("submitted_at")))
            found.ids.append(review.get("id"))
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
    base_changed_at: datetime | None = None,
    base_change_unreadable: bool = False,
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
        decided_at = found.latest_at
        stale = latest["decision"] != REFUSED and (
            base_change_unreadable
            or (base_changed_at is not None and (decided_at is None or decided_at <= base_changed_at))
        )
        if not stale:
            return Round(SKIP, f"head {head_sha} already has a {LABELS[latest['decision']]} decision")
        if latest["patch_digest"] == digest and not digest.startswith(UNREAD_DIGEST):
            return Round(
                CARRY,
                f"the base changed after the {LABELS[latest['decision']]} decision on head {head_sha}",
                latest["cycle"],
                latest["round"],
                latest,
            )
    restart = latest["cycle"] + 1
    if latest["base_ref"] != base_ref:
        return Round(FIRST, f"the base changed from {latest['base_ref']} to {base_ref}", restart, 1, latest)
    if latest["decision"] == REFUSED:
        return Round(FIRST, "the strike limit no longer applies", restart, 1, latest)
    if latest["patch_digest"] == digest and not digest.startswith(UNREAD_DIGEST):
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


def _clean_path(path: str) -> str:
    norm = unicodedata.normalize("NFC", path)
    if norm.startswith("./"):
        norm = norm[2:]
    return norm


def _resolve_tree(path: str, workspace: Path | str | None) -> str:
    cleaned = _clean_path(path)
    if not cleaned or workspace is None:
        return cleaned
    try:
        root = os.path.realpath(str(workspace))
        candidate = os.path.normpath(os.path.join(root, path))
        if candidate == root or candidate.startswith(root + os.sep):
            real = os.path.realpath(candidate)
            if real == root or real.startswith(root + os.sep):
                rel = os.path.relpath(real, root).replace("\\", "/")
                return _clean_path(rel)
    except (OSError, ValueError):
        return cleaned
    return cleaned


def _exists_exact(path: str, workspace: Path | str | None) -> bool:
    if workspace is None or not path:
        return False
    try:
        root = os.path.realpath(str(workspace))
        current = root
        for part in path.split("/"):
            if part in ("", "."):
                continue
            if part not in os.listdir(current):
                return False
            current = os.path.join(current, part)
        return current != root
    except OSError:
        return False


def judge(
    verdict: Verdict,
    max_defects: int,
    prior: Sequence[Mapping[str, Any]] = (),
    changed: Iterable[str] | None = None,
    rejudge_fixed: bool = False,
    workspace: Path | str | None = None,
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
    if changed is None:
        in_scope = verdict.defects
        outside = ()
    else:
        changed_list = list(changed)
        scope = frozenset(changed_list)
        changed_map = []
        for path in changed_list:
            canonical = _resolve_tree(path, workspace)
            changed_map.append((path, canonical, canonical.casefold()))
        in_scope_list: list[Finding] = []
        outside_list: list[Finding] = []
        for item in verdict.defects:
            if item.file in scope:
                in_scope_list.append(item)
                continue
            f_canon = _resolve_tree(item.file, workspace)
            exact_matches = [c for c, c_canon, _ in changed_map if c_canon == f_canon]
            if exact_matches:
                chosen = f_canon if f_canon in exact_matches else exact_matches[0]
                in_scope_list.append(replace(item, file=chosen))
                continue
            if _exists_exact(f_canon, workspace):
                outside_list.append(item)
                continue
            f_fold = f_canon.casefold()
            case_matches = [c for c, _, c_fold in changed_map if c_fold == f_fold]
            if len(case_matches) == 1:
                in_scope_list.append(replace(item, file=case_matches[0]))
            elif len(case_matches) > 1:
                in_scope_list.append(item)
            else:
                outside_list.append(item)
        in_scope = tuple(in_scope_list)
        outside = tuple(outside_list)[:NOT_COUNTED_LIMIT]
    counted = tuple(dict.fromkeys(in_scope))
    merged = len(in_scope) - len(counted)
    uncounted = verdict.listed - len(verdict.defects)
    total = len(carried) + len(counted) + uncounted
    if verdict.decision == DO_NOT_SHIP or total > max_defects:
        note = ""
        if total > max_defects:
            note = f" The review left {total} defects open, more than the {max_defects} allowed."
        summary = _clip(verdict.summary, DO_NOT_SHIP_SUMMARY - len(note)) + note
        return Judgement(DO_NOT_SHIP, (), summary, fixed=fixed, withdrawn=withdrawn)
    if carried or counted:
        return Judgement(SHIP_WITH_FIXES, counted, verdict.summary, carried, fixed, outside, withdrawn, merged)
    return Judgement(SHIP, (), verdict.summary, (), fixed, outside, withdrawn)


def judge_planned(
    verdict: Verdict,
    plan: Mapping[str, Any],
    workspace: Path | str | None = None,
) -> Judgement:
    rules = plan.get("protocol") or {}
    changed = rules.get("changed") if rules.get("kind") == FOLLOW_UP else None
    ws = workspace if workspace is not None else plan.get("workspace")
    return judge(
        verdict,
        int(plan["max_defects"]),
        rules.get("prior", ()),
        changed,
        bool(rules.get("forced")),
        workspace=ws,
    )


THREAD_MARKER = re.compile(r"<!--\s*oracle-defect:\s*c([0-9]+)-(D[1-9][0-9]{0,3})\s*-->")
MARKER_LINE = re.compile(r"^\s*(?:<!--\s*oracle-[\s\S]*?-->\s*|oracle-defect\s*:.*)$")
REPLY_LIMIT = 1000


def thread_finding(body: str) -> tuple[int, str] | None:
    matches = THREAD_MARKER.findall(body or "")
    if not matches:
        return None
    cycle, defect_id = matches[-1]
    return int(cycle), defect_id


def thread_finding_id(body: str) -> str | None:
    found = thread_finding(body)
    return found[1] if found is not None else None


def strip_marker_lines(text: str) -> str:
    return "\n".join(line for line in (text or "").splitlines() if not MARKER_LINE.match(line))


def normalize_claim(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def canonical_claim(text: str) -> str:
    return sanitize(text or "", MAX_CLAIM)


def claim_hash(text: str) -> str:
    return hashlib.sha256(normalize_claim(text or "").encode("utf-8")).hexdigest()


def dispute_claim(text: str) -> str:
    return strip_marker_lines(text)[:REPLY_LIMIT]


def disputed_claims(plan: Mapping[str, Any]) -> dict[str, list[str]]:
    rules = plan.get("protocol") or {}
    prior_ids = {item.get("id") for item in rules.get("prior") or () if isinstance(item, Mapping) and item.get("id")}
    if not prior_ids:
        return {}
    cycle = rules.get("cycle")
    claims: dict[str, list[str]] = {}
    for item in plan.get("disputes") or ():
        if not isinstance(item, Mapping) or not item.get("id") or not item.get("claim_hashes"):
            continue
        if item["id"] not in prior_ids:
            continue
        if cycle is not None and item.get("cycle") is not None and item["cycle"] != cycle:
            continue
        claims.setdefault(item["id"], []).extend(
            [
                known
                for known in item["claim_hashes"]
                if isinstance(known, str) and known not in claims.get(item["id"], [])
            ]
        )
    return claims


def live_disputes(disputes: Any, latest: Any) -> list[dict[str, Any]]:
    if not isinstance(latest, Mapping):
        return []
    open_ids = {
        item.get("id") for item in latest.get("open_defects") or () if isinstance(item, Mapping) and item.get("id")
    }
    cycle = latest.get("cycle")
    return [
        item
        for item in disputes or ()
        if isinstance(item, Mapping)
        and item.get("id") in open_ids
        and (cycle is None or item.get("cycle") is None or item.get("cycle") == cycle)
    ]


def bound_disputes(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    allowed = disputed_claims(plan)
    cycle = (plan.get("protocol") or {}).get("cycle")
    return [
        item
        for item in plan.get("disputes") or ()
        if isinstance(item, Mapping)
        and item.get("id") in allowed
        and (cycle is None or item.get("cycle") is None or item.get("cycle") == cycle)
    ]


def dispute_problems(verdict: Verdict, plan: Mapping[str, Any]) -> dict[str, str]:
    claims = disputed_claims(plan)
    if verdict.status != COMPLETE or not claims:
        return {}
    entries = {item.id: item for item in verdict.prior_defects}
    problems: dict[str, str] = {}
    for defect_id in sorted(claims):
        entry = entries.get(defect_id)
        if entry is None:
            problems[defect_id] = f"disputed finding {defect_id} has no disposition, so the verdict is invalid"
            continue
        demoted = entry.demoted in DEMOTED_STATUSES
        if not isinstance(entry.concedes, bool) and not demoted:
            problems[defect_id] = f"disputed finding {defect_id} has no concedes boolean, so the verdict is invalid"
            continue
        if entry.status == NOT_FIXED and entry.concedes and not demoted:
            problems[defect_id] = (
                f"disputed finding {defect_id} concedes the rebuttal yet stays not fixed, so the verdict is invalid"
            )
        elif entry.status == WITHDRAWN and not entry.concedes:
            problems[defect_id] = (
                f"disputed finding {defect_id} is withdrawn without conceding the rebuttal, so the verdict is invalid"
            )
        elif (
            entry.status in (NOT_FIXED, WITHDRAWN)
            and not demoted
            and (not entry.claim.strip() or all(claim_hash(entry.claim) != known for known in claims[defect_id]))
        ):
            problems[defect_id] = (
                f"disputed finding {defect_id} does not restate one of its rebuttal replies, so the verdict is invalid"
            )
        elif entry.status == FIXED and not (entry.evidence or "").strip():
            problems[defect_id] = (
                f"disputed finding {defect_id} is marked fixed without evidence, so the verdict is invalid"
            )
    return problems


def dispute_problem(verdict: Verdict, plan: Mapping[str, Any]) -> str | None:
    problems = dispute_problems(verdict, plan)
    return problems[min(problems)] if problems else None


def keep_disputes_open(verdict: Verdict, plan: Mapping[str, Any]) -> Verdict:
    problems = dispute_problems(verdict, plan)
    if not problems:
        return verdict
    entries = {item.id: item for item in verdict.prior_defects}
    for defect_id, problem in problems.items():
        note = problem.removesuffix(", so the verdict is invalid") + "; the defect stays open"
        entry = entries.get(defect_id)
        if entry is None:
            entries[defect_id] = PriorDefect(defect_id, NOT_FIXED, note, demoted=DEMOTED_DISPOSITION)
        else:
            entries[defect_id] = replace(
                entry, status=NOT_FIXED, evidence=f"{entry.evidence} {note}".strip(), demoted=DEMOTED_DISPOSITION
            )
    return replace(verdict, prior_defects=tuple(entries.values()))


def contradiction(
    verdict: Verdict,
    plan: Mapping[str, Any],
    workspace: Path | str | None = None,
) -> str | None:
    if verdict.status != COMPLETE or judge_planned(verdict, plan, workspace=workspace).decision != SHIP:
        return None
    problem = dispute_problem(verdict, plan)
    if problem is not None:
        return problem
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


def unique_defects(entries: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for entry in entries:
        seen.setdefault(entry["id"], dict(entry))
    return list(seen.values())


def defect_entry(defect_id: str, finding: Finding) -> dict[str, Any]:
    return {
        "id": defect_id,
        "severity": finding.severity,
        "file": finding.file,
        "line": finding.line,
        "end_line": finding.end_line,
        "title": finding.title,
    }
