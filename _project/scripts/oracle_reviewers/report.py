from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from . import dedup, protocol, selection
from .retry import ALL_ABSENT, FAILURE, INTEGRITY, OVERSIZE, PENDING, SUCCESS, UNREPORTED
from .selection import Attempt, Step
from .verdict import Finding, Placement, Verdict, comment_span, place

DESCRIPTION_LIMIT = 140
OVERSIZE_DESCRIPTION = "pending: too large for the oracle; split the pull request or ask the owner for a stand-in"
OVERSIZE_NOTE = (
    "This pull request is too large for the oracle: its brief exceeds the size cap even without the diff, "
    "so no reviewer can run and this head is not retried. Split it into smaller pull requests, or ask the "
    "owner for a stand-in review of this head."
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


def _finding_line(finding: Finding, defect_id: str | None = None) -> str:
    detail = f"\n  {finding.detail}" if finding.detail else ""
    span = f"{finding.line}-{finding.end_line}" if finding.end_line else str(finding.line)
    named = f"{defect_id}: " if defect_id else ""
    return f"- **{finding.severity}** `{finding.file}:{span}`: {named}{finding.title}{detail}"


def _entry_line(entry: Mapping[str, Any]) -> str:
    span = f"{entry['line']}-{entry['end_line']}" if entry.get("end_line") else str(entry["line"])
    return f"- **{entry['severity']}** `{entry['file']}:{span}`: {entry['id']}: {entry['title']}"


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
        lines += ["Shadow mode: this result is advisory and does not replace the required `oracle-review` check.", ""]
    lines += ["This result supersedes those for earlier heads; their open review threads still apply.", ""]
    return lines


def _reviewer_line(plan: Mapping[str, Any], name: str | None) -> list[str]:
    spec = next((item for item in plan["chain"] if item["name"] == name), None)
    if spec is None:
        return []
    effort = f", effort {spec['effort']}" if spec.get("effort") else ""
    return [f"Reviewer: {name} (role {spec['role']}, {spec['harness']} {spec['model']}{effort}).", ""]


def _absences(attempts: list[Attempt], step: Step) -> list[str]:
    lines = [f"- {attempt.reviewer}: absent ({attempt.absence})" for attempt in attempts if attempt.outcome == "absent"]
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
    if judgement is not None and judgement.decision == protocol.DO_NOT_SHIP:
        strikes += 1
    if pending_cause == OVERSIZE:
        description = OVERSIZE_DESCRIPTION
    elif judgement is None:
        description = "pending: " + "; ".join(step.reasons or ("no reviewer available",))
    elif judgement.decision == protocol.SHIP_WITH_FIXES:
        description = f"{reviewer}: {judgement.label}, {judgement.open_count} defect(s) to fix"
    else:
        description = f"{reviewer}: {judgement.label}"
    delivery = plan.get("findings_delivery", "comment")
    findings_lines = [_finding_line(finding, ids[finding]) for finding in placement.inline]
    other_lines = [_finding_line(finding, ids[finding]) for finding in placement.summary]
    lines = [*_header(plan, state)]
    if pending_cause == OVERSIZE:
        lines += [OVERSIZE_NOTE, ""]
    if judgement is not None:
        lines += [f"Decision: **{judgement.label}**.", ""]
    lines += _reviewer_line(plan, reviewer)
    if judgement is not None and judgement.summary:
        lines += [judgement.summary, ""]
    if judgement is not None and judgement.decision == protocol.DO_NOT_SHIP:
        lines += ["No defects are listed for this decision. Rework the change and push a new head.", ""]
    if judgement is not None and judgement.fixed:
        lines += [f"Verified fixed: {', '.join(judgement.fixed)}. Resolve their review threads.", ""]
    if judgement is not None:
        lines += _section("Still open from earlier reviews", [_entry_line(entry) for entry in judgement.carried])
    if delivery == "comment":
        lines += _section("Defects on diff lines", findings_lines)
    lines += _section(
        "Defects without a review thread" if delivery == "review" else "Defects outside the diff", other_lines
    )
    if placement.summary:
        lines += ["Defects outside the diff count like the others: fix them before merge.", ""]
    lines += _section(
        "Defects already open as review threads, not posted again",
        [f"- **{finding.severity}** `{finding.file}`: {ids[finding]}: {finding.title}" for finding in repeated],
    )
    if judgement is not None and judgement.not_counted:
        hidden = "\n".join(_finding_line(finding) for finding in judgement.not_counted)
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
    lines += _strike_line(plan, strikes if judgement is not None else int(rules.get("strikes", 0)))
    lines += _section("Reviewers not available", _absences(attempts, step))
    body = "\n".join(lines).rstrip() + "\n"
    if judgement is not None and rules:
        open_defects = [*judgement.carried, *(protocol.defect_entry(ids[item], item) for item in defects)]
        body += "\n" + _record(
            plan,
            kind=rules["kind"],
            decision=judgement.decision,
            reviewer=reviewer or "",
            open_defects=open_defects,
            next_id=next_id + len(defects),
            strikes_after=strikes,
            summary=judgement.summary,
        )
    has_content = bool(defects or judgement is None or judgement.decision != protocol.SHIP or state != SUCCESS)
    review = _review_payload(plan, body, placement, commentable, markers) if delivery == "review" else None
    cause = pending_cause if state == PENDING else None
    final_strikes = strikes if judgement is not None else None
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
        *_section("Still open", [_entry_line(entry) for entry in previous["open_defects"]]),
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
            open_defects=list(previous["open_defects"]),
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
