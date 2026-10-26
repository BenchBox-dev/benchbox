from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from . import absence, credentials, dedup, protocol, selection
from .retry import ALL_ABSENT, FAILURE, INTEGRITY, MARKER_LIMIT, OVERSIZE, PENDING, SUCCESS, UNREPORTED
from .selection import Attempt, Step
from .verdict import (
    MAX_DETAIL,
    MAX_EVIDENCE,
    Finding,
    Placement,
    PriorDefect,
    Verdict,
    comment_span,
    display_path,
    place,
)

DESCRIPTION_LIMIT = 140
BODY_LIMIT = 65536
TRUNCATED = "… [truncated]"
OVERSIZE_DESCRIPTION = "pending: too large for the oracle; split the pull request or ask the owner for a stand-in"
OVERSIZE_NOTE = (
    "This pull request is too large for the oracle: its brief exceeds the size cap even without the diff, "
    "so no reviewer can run and this head is not retried. Split it into smaller pull requests, or ask the "
    "owner for a stand-in review of this head."
)
MARKER_LIMIT_DESCRIPTION = "pending: the protocol record does not fit in a review marker"
MARKER_LIMIT_NOTE = (
    "The decision and its defects are listed in full in this review, but the protocol record they must be saved in "
    "exceeds the size limit of a review marker even after shortening titles and paths. This head has no recorded "
    "decision and is not retried; push a new head with fewer or shorter findings. The defects below carry no "
    "identifiers and open no tracked threads, so they are not carried into the next review."
)
STATES = {selection.PASS: SUCCESS, selection.FAIL: FAILURE, selection.PENDING: PENDING}


@dataclass(frozen=True)
class Final:
    state: str
    description: str
    body: str
    review: dict[str, Any] | None
    reviewer: str | None
    pending_cause: str | None = None
    strikes: int | None = None


def _clip(text: str, limit: int = DESCRIPTION_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + TRUNCATED


def _finding_line(finding: Finding, defect_id: str | None = None, cap: int = MAX_DETAIL) -> str:
    detail = f"\n  {_cut(finding.detail, cap)}" if finding.detail else ""
    span = f"{finding.line}-{finding.end_line}" if finding.end_line else str(finding.line)
    named = f"{defect_id}: " if defect_id else ""
    return f"- **{finding.severity}** `{display_path(finding.file)}:{span}`: {named}{finding.title}{detail}"


def _entry_line(entry: Mapping[str, Any]) -> str:
    span = f"{entry['line']}-{entry['end_line']}" if entry.get("end_line") else str(entry["line"])
    return f"- **{entry['severity']}** `{display_path(entry['file'])}:{span}`: {entry['id']}: {entry['title']}"


def _size_notice(listed: list[Finding], cap: int) -> str:
    trimmed = sum(len(finding.detail) > cap for finding in listed)
    return (
        f"Size limit: the detail of {trimmed} of {len(listed)} listed defect(s) was shortened so this review stays"
        f" under GitHub's {BODY_LIMIT}-character limit; every defect is still listed with its title and location."
    )


def _fit(render: Callable[[int | None, int | None], str]) -> str:
    body = render(None, None)
    if len(body) <= BODY_LIMIT:
        return body
    low, high, best = 0, MAX_DETAIL, render(0, None)
    while low <= high:
        mid = (low + high) // 2
        candidate = render(mid, None)
        if len(candidate) <= BODY_LIMIT:
            best, low = candidate, mid + 1
        else:
            high = mid - 1
    if len(best) <= BODY_LIMIT:
        return best
    low, high = 0, MAX_EVIDENCE
    best = render(0, 0)
    while low <= high:
        mid = (low + high) // 2
        candidate = render(0, mid)
        if len(candidate) <= BODY_LIMIT:
            best, low = candidate, mid + 1
        else:
            high = mid - 1
    return best


def _record(
    plan: Mapping[str, Any],
    *,
    kind: str,
    decision: str,
    reviewer: str,
    open_defects: list[dict[str, Any]],
    next_id: int,
    strikes_after: int,
    summary: str = "",
    carried: bool = False,
) -> str:
    rules = plan["protocol"]
    record = {
        "v": protocol.MARKER_VERSION,
        "cycle": rules["cycle"],
        "round": rules["round"],
        "kind": kind,
        "decision": decision,
        "head_sha": plan["head_sha"],
        "base_ref": rules["base_ref"],
        "reviewer": reviewer,
        "tier": reviewer,
        "strikes_after": strikes_after,
        "open_defects": open_defects,
        "next_id": next_id,
        "patch_digest": rules["patch_digest"],
        "carried": carried,
        "forced": bool(rules.get("forced", False)),
        "summary": summary if decision == protocol.DO_NOT_SHIP else "",
    }
    return protocol.encode_marker(record, rules.get("patch_map"))


def _strike_line(plan: Mapping[str, Any], strikes: int) -> list[str]:
    limit = (plan.get("protocol") or {}).get("max_do_not_ship")
    if not strikes or not limit:
        return []
    return [f"DO NOT SHIP decisions on this pull request: {strikes} of {limit}.", ""]


def _section(title: str, lines: list[str]) -> list[str]:
    return [f"**{title}**", "", *lines, ""] if lines else []


def _header(plan: Mapping[str, Any], state: str) -> list[str]:
    lines = [f"### {plan['status_context']}: {state} for `{plan['head_sha']}`", ""]
    if plan.get("mode") == "shadow":
        lines += [
            "Shadow mode: this result is advisory and does not replace the required oracle checks.",
            "",
        ]
    lines += ["This result supersedes those for earlier heads; their open review threads still apply.", ""]
    return lines


def _reviewer_line(plan: Mapping[str, Any], name: str | None) -> list[str]:
    spec = next((item for item in plan["chain"] if item["name"] == name), None)
    if spec is None:
        return []
    effort = f", effort {spec['effort']}" if spec.get("effort") else ""
    return [f"Reviewer: {name} (role {spec['role']}, {spec['harness']} {spec['model']}{effort}).", ""]


def _absences(attempts: list[Attempt], step: Step) -> list[str]:
    known = credentials.known()
    lines = []
    for attempt in attempts:
        if attempt.outcome != "absent":
            continue
        detail = absence.published_detail(attempt.detail, known)
        lines.append(
            f"- {attempt.reviewer} (attempt {attempt.slot}): absent ({attempt.absence})"
            + (f": {detail}" if detail else "")
        )
    attempted = {attempt.reviewer for attempt in attempts}
    lines += [f"- {reason}" for reason in step.reasons if reason.split(":", 1)[0] not in attempted]
    return lines


def _review_comment(
    finding: Finding, commentable: Mapping[str, frozenset[int]], defect_marker: str = ""
) -> dict[str, Any]:
    span = comment_span(finding, commentable)
    anchor: dict[str, Any] = {"line": finding.line, "side": "RIGHT"}
    if span is not None:
        anchor = {"start_line": span[0], "start_side": "RIGHT", "line": span[1], "side": "RIGHT"}
    return {
        "path": finding.file,
        **anchor,
        "body": f"**{finding.severity}**: {finding.title}\n\n{finding.detail}".rstrip()
        + (f"\n\n{defect_marker}" if defect_marker else "")
        + f"\n\n{dedup.marker(finding)}",
    }


def _review_payload(
    plan: Mapping[str, Any],
    body: str,
    placement: Placement,
    commentable: Mapping[str, frozenset[int]],
    markers: Mapping[Finding, str] | None = None,
) -> dict[str, Any]:
    return {
        "commit_id": plan["head_sha"],
        "event": "COMMENT",
        "body": body,
        "comments": [
            _review_comment(finding, commentable, (markers or {}).get(finding, "")) for finding in placement.inline
        ],
    }


def _settled(plan: Mapping[str, Any], step: Step) -> tuple[Step, str]:
    if step.kind == selection.REVIEW:
        reason = f"{step.reviewer.name if step.reviewer else 'a reviewer'}: selected but did not report"
        return Step(selection.PENDING, None, (*step.reasons, reason)), UNREPORTED
    if step.kind == selection.PENDING and plan.get("brief_mode") == "oversize":
        return step, OVERSIZE
    return step, ALL_ABSENT


def _marked(
    plan: Mapping[str, Any],
    judgement: protocol.Judgement,
    reviewer: str,
    ids: Mapping[Finding, str],
    next_id: int,
    strikes: int,
) -> str | None:
    open_defects = [*judgement.carried, *(protocol.defect_entry(ids[item], item) for item in judgement.defects)]
    try:
        return _record(
            plan,
            kind=plan["protocol"]["kind"],
            decision=judgement.decision,
            reviewer=reviewer,
            open_defects=open_defects,
            next_id=next_id + len(judgement.defects),
            strikes_after=strikes,
            summary=judgement.summary,
        )
    except protocol.MarkerError:
        return None


def _description(step: Step, judgement: protocol.Judgement | None, reviewer: str | None, cause: str) -> str:
    if cause == OVERSIZE:
        return OVERSIZE_DESCRIPTION
    if judgement is None:
        return "pending: " + "; ".join(step.reasons or ("no reviewer available",))
    if judgement.decision == protocol.SHIP_WITH_FIXES:
        return f"{reviewer}: {judgement.label}, {judgement.open_count} defect(s) to fix"
    return f"{reviewer}: {judgement.label}"


def _judgement_notes(judgement: protocol.Judgement | None) -> list[str]:
    if judgement is None:
        return []
    lines: list[str] = []
    if judgement.summary:
        lines += [judgement.summary, ""]
    if judgement.decision == protocol.DO_NOT_SHIP:
        lines += ["No defects are listed for this decision. Rework the change and push a new head.", ""]
    if judgement.merged:
        lines += [f"{judgement.merged} identical finding(s) were reported more than once and are listed once.", ""]
    if judgement.fixed:
        lines += [f"Verified fixed: {', '.join(judgement.fixed)}. Resolve their review threads.", ""]
    if judgement.withdrawn:
        lines += [f"Withdrawn as never defects: {', '.join(judgement.withdrawn)}. Resolve their review threads.", ""]
    return lines + _section("Still open from earlier reviews", [_entry_line(entry) for entry in judgement.carried])


def _disposition_label(status: str) -> str:
    return {"not_fixed": "maintained", "withdrawn": "withdrawn", "fixed": "fixed"}.get(status, status)


def _effective_status(plan: Mapping[str, Any], entry: PriorDefect) -> str:
    forced = bool((plan.get("protocol") or {}).get("forced"))
    if forced and entry.status == protocol.FIXED:
        return protocol.NOT_FIXED
    if forced and entry.status == protocol.WITHDRAWN and not protocol.withdrawal_cited(entry.evidence):
        return protocol.NOT_FIXED
    return entry.status


def _dispositions(plan: Mapping[str, Any], verdict: Verdict | None, evidence_cap: int | None) -> list[str]:
    disputes = [item for item in plan.get("disputes", []) if isinstance(item, Mapping) and item.get("id")]
    if not disputes or verdict is None:
        return []
    entries = {item.id: item for item in verdict.prior_defects}
    lines = []
    for dispute in disputes:
        entry = entries.get(dispute["id"])
        if entry is None:
            lines.append(f"- {dispute['id']}: no disposition recorded")
        else:
            shown = entry.evidence if evidence_cap is None else _cut(entry.evidence, evidence_cap)
            suffix = f": {shown}" if shown else ""
            lines.append(f"- {dispute['id']} ({_disposition_label(_effective_status(plan, entry))}){suffix}")
    return _section("Dispute dispositions", lines)


def finalize(
    plan: Mapping[str, Any],
    step: Step,
    errors: list[str],
    attempts: list[Attempt],
    verdicts: Mapping[int, Verdict],
    commentable: Mapping[str, frozenset[int]],
) -> Final:
    if errors:
        body = "\n".join(
            [*_header(plan, PENDING), *_section("Result withheld: the run's artifacts failed validation", errors)]
        )
        review = (
            _review_payload(plan, body, Placement((), ()), commentable)
            if plan.get("findings_delivery") == "review"
            else None
        )
        return Final(PENDING, "result withheld: artifacts failed validation", body, review, None, INTEGRITY)
    step, pending_cause = _settled(plan, step)
    state = STATES[step.kind]
    reviewer = step.reviewer.name if step.reviewer else None
    terminal = next(
        (attempt for attempt in attempts if attempt.reviewer == reviewer and attempt.outcome == step.kind),
        None,
    )
    verdict = verdicts.get(terminal.slot) if terminal is not None else None
    judgement = protocol.judge_planned(verdict, plan) if verdict is not None else None
    rules = plan.get("protocol") or {}
    defects = judgement.defects if judgement is not None else ()
    next_id = int(rules.get("next_id", 1))
    ids = {finding: f"D{next_id + index}" for index, finding in enumerate(defects)}
    cycle = rules.get("cycle", 1)
    markers = {finding: f"<!-- oracle-defect: c{cycle}-{defect_id} -->" for finding, defect_id in ids.items()}
    fresh, repeated = dedup.split(defects, plan.get("open_findings", ()))
    placement = place(fresh, commentable)
    strikes = int(rules.get("strikes", 0))
    if (
        judgement is not None
        and judgement.decision == protocol.DO_NOT_SHIP
        and rules.get("patch_digest") not in rules.get("struck_digests", ())
    ):
        strikes += 1
    marker = _marked(plan, judgement, reviewer or "", ids, next_id, strikes) if judgement and rules else None
    unrecorded = bool(judgement and rules and marker is None)
    if unrecorded:
        ids, markers = {}, {}
        state, pending_cause = PENDING, MARKER_LIMIT
    description = MARKER_LIMIT_DESCRIPTION if unrecorded else _description(step, judgement, reviewer, pending_cause)
    delivery = plan.get("findings_delivery", "comment")
    listed = [*placement.inline, *placement.summary, *(judgement.not_counted if judgement is not None else ())]

    def render(cap: int | None, evidence_cap: int | None = None) -> str:
        active = MAX_DETAIL if cap is None else cap
        findings_lines = [_finding_line(finding, ids.get(finding), active) for finding in placement.inline]
        other_lines = [_finding_line(finding, ids.get(finding), active) for finding in placement.summary]
        lines = [*_header(plan, state)]
        if unrecorded:
            lines += [MARKER_LIMIT_NOTE, ""]
        if pending_cause == OVERSIZE:
            lines += [OVERSIZE_NOTE, ""]
        if judgement is not None:
            lines += [f"Decision: **{judgement.label}**.", ""]
        lines += _reviewer_line(plan, reviewer)
        if cap is not None:
            lines += [_size_notice(listed, cap), ""]
        lines += _judgement_notes(judgement)
        lines += _dispositions(plan, verdict, evidence_cap)
        if delivery == "comment":
            lines += _section("Defects on diff lines", findings_lines)
        lines += _section(
            "Defects without a review thread" if delivery == "review" else "Defects outside the diff", other_lines
        )
        if placement.summary:
            lines += ["Defects outside the diff count like the others: fix them before merge.", ""]
        lines += _section(
            "Defects already open as review threads, not posted again",
            [
                f"- **{finding.severity}** `{display_path(finding.file)}`: {ids.get(finding, '')}{': ' if finding in ids else ''}{finding.title}"
                for finding in repeated
            ],
        )
        if judgement is not None and judgement.not_counted:
            hidden = "\n".join(_finding_line(finding, None, active) for finding in judgement.not_counted)
            lines += [
                "<details><summary>Not counted: defects in files this follow-up did not review</summary>",
                "",
                hidden,
                "",
                "</details>",
                "",
            ]
        if plan.get("scope") == "changed":
            lines += [f"Scope: files changed since head `{plan['reviewed_head']}` was reviewed.", ""]
        lines += _strike_line(
            plan, strikes if judgement is not None and not unrecorded else int(rules.get("strikes", 0))
        )
        lines += _section("Reviewers not available", _absences(attempts, step))
        body = "\n".join(lines).rstrip() + "\n"
        if marker is not None:
            body += "\n" + marker
        return body

    body = _fit(render)
    has_content = bool(defects or judgement is None or judgement.decision != protocol.SHIP or state != SUCCESS)
    review = _review_payload(plan, body, placement, commentable, markers) if delivery == "review" else None
    cause = pending_cause if state == PENDING else None
    final_strikes = strikes if judgement is not None and not unrecorded else None
    return Final(state, _clip(description), body if has_content else "", review, reviewer, cause, final_strikes)


def carried(plan: Mapping[str, Any]) -> Final:
    rules = plan["protocol"]
    previous = rules["previous"]
    decision = previous["decision"]
    outcome = SUCCESS if decision == protocol.SHIP else FAILURE
    reviewed_head = plan["reviewed_head"]
    label = protocol.LABELS[decision]
    description = f"carried from {reviewed_head[:12]}: {label}"
    lines = [
        *_header(plan, outcome),
        f"Decision: **{label}**, carried from head `{reviewed_head}`.",
        "",
        f"The patch is unchanged since head `{reviewed_head}` was reviewed, so its decision is carried to this head"
        " without running a reviewer. Its open review threads still apply.",
        "",
        *_section("Still open", [_entry_line(entry) for entry in protocol.unique_defects(previous["open_defects"])]),
        *_strike_line(plan, int(previous["strikes_after"])),
    ]
    body = (
        "\n".join(lines).rstrip()
        + "\n\n"
        + _record(
            plan,
            kind=protocol.CARRY,
            decision=decision,
            reviewer=previous["reviewer"],
            open_defects=protocol.unique_defects(previous["open_defects"]),
            next_id=int(previous["next_id"]),
            strikes_after=int(previous["strikes_after"]),
            summary=previous["summary"],
            carried=True,
        )
    )
    review = {"commit_id": plan["head_sha"], "event": "COMMENT", "body": body, "comments": []}
    delivered = review if plan.get("findings_delivery") == "review" else None
    return Final(outcome, _clip(description), body, delivered, None, None, int(previous["strikes_after"]))


def refused(plan: Mapping[str, Any]) -> Final:
    rules = plan["protocol"]
    strikes = int(rules["strikes"])
    lines = [
        *_header(plan, FAILURE),
        "Decision: **REFUSED**.",
        "",
        f"This pull request has received {strikes} DO NOT SHIP decisions, so the oracle will not review it again."
        " Close this pull request and open a new one with the reworked change. The limit stops review loops; it is"
        " not a security control, and a new pull request starts a new count.",
        "",
        *_strike_line(plan, strikes),
    ]
    body = (
        "\n".join(lines).rstrip()
        + "\n\n"
        + _record(
            plan,
            kind=protocol.REFUSAL,
            decision=protocol.REFUSED,
            reviewer="",
            open_defects=[],
            next_id=int(rules.get("next_id", 1)),
            strikes_after=strikes,
        )
    )
    review = {"commit_id": plan["head_sha"], "event": "COMMENT", "body": body, "comments": []}
    delivered = review if plan.get("findings_delivery") == "review" else None
    description = f"refused after {strikes} DO NOT SHIP decisions: open a new pull request"
    return Final(FAILURE, _clip(description), body, delivered, None, None, strikes)


def fixed(state: str, description: str) -> Final:
    return Final(state, _clip(description), "", None, None)


def status_payload(plan: Mapping[str, Any], final: Final, target_url: str) -> dict[str, Any]:
    return {
        "state": final.state,
        "context": plan["status_context"],
        "description": final.description,
        "target_url": target_url,
    }
