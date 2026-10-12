from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from time import sleep
from typing import Any

from . import attempts as attempt_files, dedup, github, protocol, report, retry, runner, selection
from .absence import ERROR, Absence
from .brief import build_brief, with_full_diff, with_read_rule, write_private
from .classifier import ChangedFile, classify
from .diff import commentable_lines, select_files
from .policy import Policy, Reviewer, Tier, load_policy
from .selection import SelectionInput
from .verdict import DO_NOT_SHIP

PLAN_FILE = "plan.json"
BRIEF_FILE = "brief.md"
DIFF_FILE = "diff.patch"
WORKFLOW_FILE = "oracle-verdict.yml"
WORKFLOW_SOURCE = Path(".github/workflows") / WORKFLOW_FILE
DEVELOP_REF = "refs/heads/develop"
NEW_DIFF_ACTIONS = ("opened", "synchronize")
REVIEW = "review"
SUCCESS = "success"
FORK = "fork"
SKIP = "skip"
CARRY = "carry"
REFUSED = "refused"
HOLD = "hold"
PROSE_SUFFIXES = (".md", ".mdx", ".rst")
GUARD_ATTEMPTS = 3
GUARD_PAUSE_SECONDS = 2


def _now() -> datetime:
    return datetime.now(UTC)


def _output(values: Mapping[str, str]) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    lines = "".join(f"{key}={value}\n" for key, value in values.items())
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(lines)
    sys.stdout.write(lines)


def _summary(text: str) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _pr_number(value: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]{0,8}", value or ""):
        raise SystemExit(f"oracle-verdict: invalid pull request number {value!r}")
    return int(value)


def _event() -> dict[str, Any]:
    path = os.environ.get("GITHUB_EVENT_PATH")
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else {}


def _soundness_predicate() -> Any:
    from _project.scripts.soundness_paths import is_soundness_path

    return is_soundness_path


def _base_plan(policy: Policy, repo: str, pr: int, run_id: str) -> dict[str, Any]:
    return {
        "schema": 1,
        "mode": policy.mode,
        "status_context": policy.status_context,
        "bot_login": policy.bot_login,
        "findings_delivery": policy.findings_delivery,
        "repo": repo,
        "pr": pr,
        "run_id": run_id,
        "base_sha": "",
        "base_ref": "",
        "head_sha": "",
        "decision": SKIP,
        "decision_reason": "",
        "tier": None,
        "tier_reasons": [],
        "max_defects": policy.protocol.max_defects,
        "evidence_files": [],
        "chain": [],
        "diversity_exempt": [],
        "excluded_families": [],
        "brief_mode": "oversize",
        "scope": "full",
        "reviewed_head": None,
        "open_findings": {},
        "protocol": None,
        "max_attempts": policy.max_attempts,
        "pool_blocked_until": {},
        "manual": False,
        "created_at": _now().isoformat(),
    }


def _finish_plan(out_dir: Path, plan: dict[str, Any]) -> int:
    _write_json(out_dir / PLAN_FILE, plan)
    post = plan["decision"] in (REVIEW, SUCCESS, FORK, CARRY, REFUSED, HOLD)
    _output(
        {
            "decision": plan["decision"],
            "post": "true" if post else "false",
            "pr": str(plan["pr"]),
            "head_sha": plan["head_sha"],
            "tier": plan["tier"] or "",
        }
    )
    _summary(f"oracle-verdict plan for #{plan['pr']}: {plan['decision']} ({plan['decision_reason']})")
    return 0


def command_plan(args: argparse.Namespace) -> int:
    policy = load_policy(Path(args.policy))
    repo = os.environ["GITHUB_REPOSITORY"]
    run_id = os.environ["GITHUB_RUN_ID"]
    event_name = os.environ["GITHUB_EVENT_NAME"]
    event = _event()
    if event_name == "workflow_dispatch" and os.environ.get("GITHUB_REF") != DEVELOP_REF:
        print("oracle-verdict: error: dispatch runs only from develop", file=sys.stderr)
        return 2
    pr = _pr_number(args.pr)
    out_dir = Path(args.out)
    plan = _base_plan(policy, repo, pr, run_id)
    pull = github.get_json(f"repos/{repo}/pulls/{pr}")
    plan["base_sha"] = pull["base"]["sha"]
    plan["base_ref"] = pull["base"]["ref"]
    plan["head_sha"] = pull["head"]["sha"]
    if pull["state"] != "open":
        plan["decision_reason"] = "the pull request is not open"
        return _finish_plan(out_dir, plan)
    if pull["base"]["ref"] != "develop":
        plan["decision_reason"] = "the pull request does not target develop"
        return _finish_plan(out_dir, plan)
    if pull.get("draft"):
        plan["decision_reason"] = "the pull request is a draft"
        return _finish_plan(out_dir, plan)
    event_head = (event.get("pull_request") or {}).get("head", {}).get("sha")
    if event_head and event_head != plan["head_sha"]:
        plan["decision_reason"] = "a newer head exists; its own run reviews it"
        return _finish_plan(out_dir, plan)
    files = [ChangedFile.from_api(item) for item in github.get_paginated(f"repos/{repo}/pulls/{pr}/files?per_page=100")]
    if github.get_json(f"repos/{repo}/pulls/{pr}")["head"]["sha"] != plan["head_sha"]:
        plan["decision_reason"] = "the head moved while the plan read it; its own run reviews it"
        return _finish_plan(out_dir, plan)
    labels = [label["name"] for label in pull.get("labels", [])]
    classification = classify(files, labels, policy, _soundness_predicate())
    plan["tier"] = classification.tier
    plan["tier_reasons"] = list(classification.reasons)
    if not classification.soundness:
        plan["decision"] = SUCCESS
        plan["decision_reason"] = "no soundness path changed"
        return _finish_plan(out_dir, plan)
    if (pull["head"].get("repo") or {}).get("full_name") != repo:
        plan["decision"] = FORK
        plan["decision_reason"] = "fork: owner review"
        return _finish_plan(out_dir, plan)
    manual = event_name in ("issue_comment", "workflow_dispatch")
    action = event.get("action", "")
    new_diff = event_name == "pull_request_target" and (
        action in NEW_DIFF_ACTIONS or (action == "edited" and "base" in (event.get("changes") or {}))
    )
    try:
        previous = github.latest_state(repo, pr)
    except (github.GitHubError, KeyError, ValueError) as exc:
        if manual:
            plan["decision_reason"] = f"the retry state could not be read: {exc}"
            return _finish_plan(out_dir, plan)
        previous = None
    now = _now()
    rerun = retry.decide_rerun(
        manual=manual, new_diff=new_diff, head_sha=plan["head_sha"], previous=previous, now=now, rules=policy.retry
    )
    if not rerun.allowed:
        plan["decision_reason"] = rerun.reason
        return _finish_plan(out_dir, plan)
    tier = policy.tiers[classification.tier or "very-high"]
    in_scope = set(classification.soundness_paths)
    scoped = [
        item for item in files if any(path in in_scope or not path.endswith(PROSE_SUFFIXES) for path in item.paths)
    ]
    plan["manual"] = manual
    plan["previous_state"] = previous.to_json() if previous else None
    try:
        found = protocol.history(github.oracle_reviews(repo, pr), policy.bot_login)
    except (github.GitHubError, KeyError, TypeError, ValueError) as exc:
        found = protocol.History([], f"the oracle's review history could not be read: {exc}")
    if found.error is not None:
        plan.update({"decision": HOLD, "decision_reason": found.error})
        return _finish_plan(out_dir, plan)
    full_diff = github.get_diff(repo, pr)
    complete = full_diff is not None and len(files) >= int(pull.get("changed_files") or 0)
    strike_count = max(protocol.strikes(found.records), previous.strikes if previous else 0)
    step = _protocol_round(plan, policy, tier, found, scoped, full_diff if complete else None, strike_count)
    if step.kind == protocol.SKIP:
        plan["decision_reason"] = step.reason
        return _finish_plan(out_dir, plan)
    if step.kind == protocol.REFUSAL:
        plan.update({"decision": REFUSED, "decision_reason": step.reason})
        return _finish_plan(out_dir, plan)
    if step.kind == protocol.CARRY:
        plan.update({"decision": CARRY, "decision_reason": step.reason, "reviewed_head": step.previous["head_sha"]})
        return _finish_plan(out_dir, plan)
    try:
        plan["open_findings"] = dedup.open_fingerprints(github.review_threads(repo, pr), policy.bot_login)
    except (github.GitHubError, KeyError, ValueError) as exc:
        _summary(f"oracle-verdict: open review threads could not be read, so none are suppressed: {exc}")
    _plan_review(plan, out_dir, policy, tier, step, files, scoped, in_scope, full_diff, labels, previous, now)
    plan["decision_reason"] = f"{step.reason}; {rerun.reason}"
    return _finish_plan(out_dir, plan)


def _protocol_round(
    plan: dict[str, Any],
    policy: Policy,
    tier: Tier,
    found: protocol.History,
    scoped: list[ChangedFile],
    full_diff: str | None,
    strike_count: int,
) -> protocol.Round:
    paths = sorted(item.path for item in scoped)
    patches = protocol.file_patches(full_diff or "")
    current = protocol.patch_map(patches, paths)
    digest = (protocol.patch_digest(patches, paths) if full_diff is not None else None) or f"unread-{plan['head_sha']}"
    base_ref = plan["base_ref"]
    step = protocol.plan_round(
        found,
        head_sha=plan["head_sha"],
        base_ref=base_ref,
        tier=tier.name,
        digest=digest,
        patches=current,
        paths=paths,
        strike_count=strike_count,
        max_do_not_ship=policy.protocol.max_do_not_ship,
    )
    latest = step.previous or {}
    follow_up = step.kind == protocol.FOLLOW_UP
    plan["protocol"] = {
        "kind": step.kind,
        "reason": step.reason,
        "cycle": step.cycle,
        "round": step.number,
        "base_ref": base_ref,
        "patch_map": current,
        "patch_digest": digest,
        "strikes": strike_count,
        "max_do_not_ship": policy.protocol.max_do_not_ship,
        "prior": list(latest.get("open_defects", [])) if follow_up else [],
        "next_id": int(latest.get("next_id", 1)) if follow_up else 1,
        "changed": sorted(step.changed) if follow_up and step.changed is not None else None,
        "previous": dict(latest) if step.kind == protocol.CARRY else None,
    }
    return step


def _plan_review(
    plan: dict[str, Any],
    out_dir: Path,
    policy: Policy,
    tier: Tier,
    step: protocol.Round,
    files: list[ChangedFile],
    scoped: list[ChangedFile],
    in_scope: set[str],
    full_diff: str | None,
    labels: list[str],
    previous: retry.State | None,
    now: datetime,
) -> None:
    latest = step.previous or {}
    follow_up = step.kind == protocol.FOLLOW_UP
    changed = [item for item in scoped if follow_up and step.changed is not None and item.path in step.changed]
    partial = follow_up and 0 < len(changed) < len(scoped)
    diff_text = full_diff
    if partial and full_diff is not None:
        diff_text = select_files(full_diff, frozenset(path for item in changed for path in item.paths))
        if set(commentable_lines(diff_text)) != {item.path for item in changed}:
            partial, diff_text = False, full_diff
    if not partial:
        changed = scoped if follow_up else files
    if follow_up:
        plan["protocol"]["changed"] = sorted(item.path for item in changed)
    restart = latest.get("summary") if step.kind == protocol.FIRST and latest.get("decision") == DO_NOT_SHIP else None
    brief = build_brief(
        repo=plan["repo"],
        pr=plan["pr"],
        base_sha=plan["base_sha"],
        head_sha=plan["head_sha"],
        tier=tier.name,
        max_defects=policy.protocol.max_defects,
        files=changed if partial else files,
        diff_text=diff_text,
        max_bytes=policy.brief_max_bytes,
        reviewed_head=latest["head_sha"] if partial else None,
        unchanged=[item for item in scoped if item not in changed] if partial else [],
        untracked=[item for item in files if item not in scoped] if partial else [],
        full_diff_available=bool(full_diff),
        prior=plan["protocol"]["prior"],
        restart_summary=restart or None,
    )
    write_private(out_dir / BRIEF_FILE, brief.text)
    write_private(out_dir / DIFF_FILE, full_diff or "")
    reviewed_now = changed if follow_up else files
    plan.update(
        {
            "decision": REVIEW,
            "evidence_files": sorted(item.path for item in reviewed_now if item.path in in_scope and not item.removed),
            "chain": _ordered_chain(policy.chain(tier.name), latest.get("reviewer") if follow_up else None),
            "diversity_exempt": list(tier.diversity_exempt),
            "excluded_families": sorted(selection.excluded_families(labels, policy)),
            "brief_mode": brief.mode,
            "scope": "changed" if partial else "full",
            "reviewed_head": latest["head_sha"] if partial else None,
            "pool_blocked_until": {
                pool: moment.isoformat()
                for pool, moment in (previous.pool_blocked_until.items() if previous else ())
                if moment > now
            },
        }
    )


def _ordered_chain(chain: list[Reviewer], preferred: str | None) -> list[dict[str, Any]]:
    first = [reviewer for reviewer in chain if reviewer.name == preferred]
    return [reviewer.to_json() for reviewer in first + [reviewer for reviewer in chain if reviewer not in first]]


def _selection(plan: Mapping[str, Any]) -> SelectionInput:
    return SelectionInput.from_plan(plan, _now())


def command_select(args: argparse.Namespace) -> int:
    plan = _read_json(Path(args.plan))
    loaded = attempt_files.load(Path(args.attempts_dir), plan, os.environ["GITHUB_RUN_ID"])
    step, errors = attempt_files.decide(_selection(plan), loaded)
    chosen = (
        step.reviewer
        if not errors and step.kind == selection.REVIEW and len(loaded.attempts) == args.slot - 1
        else None
    )
    _output({"reviewer": chosen.name if chosen else "", "harness": chosen.harness if chosen else ""})
    _summary(f"slot {args.slot}: {chosen.name if chosen else step.kind} {'; '.join(errors)}".rstrip())
    return 0


def _chain_reviewer(plan: Mapping[str, Any], name: str) -> Reviewer:
    for item in plan["chain"]:
        if item["name"] == name:
            return Reviewer.from_json(item)
    raise SystemExit(f"oracle-verdict: {name!r} is not in this run's reviewer chain")


def command_review(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan)
    plan = _read_json(plan_path)
    reviewer = _chain_reviewer(plan, args.reviewer)
    if reviewer.harness != args.harness:
        raise SystemExit(f"oracle-verdict: {reviewer.name} does not run on the {args.harness} job")
    brief_path = plan_path.parent / BRIEF_FILE
    brief_path.chmod(0o600)
    prompt = brief_path.read_text(encoding="utf-8") if plan["brief_mode"] != "oversize" else ""
    workspace = Path(args.workspace)
    if prompt:
        prompt = with_read_rule(prompt, reviewer.harness)
    if prompt and (plan.get("scope") == "changed" or plan["brief_mode"] == "file-list"):
        prompt = with_full_diff(prompt, runner.stage_pull_request_diff(plan_path.parent / DIFF_FILE, workspace))
    outcome = runner.review(
        reviewer,
        workspace=workspace,
        head_sha=plan["head_sha"],
        prompt=prompt,
        scratch=Path(args.scratch),
        now=_now(),
        plan=plan,
    )
    artifact = attempt_files.build_artifact(
        run_id=os.environ["GITHUB_RUN_ID"],
        pr=plan["pr"],
        head_sha=plan["head_sha"],
        slot=args.slot,
        reviewer=reviewer.name,
        verdict=outcome.verdict,
        missing=outcome.missing,
        diagnostic=outcome.diagnostic,
    )
    _write_json(Path(args.out_dir) / attempt_files.artifact_name(args.slot), artifact)
    state = "verdict" if outcome.missing is None else f"absent ({outcome.missing.kind}: {outcome.missing.detail})"
    _summary(f"slot {args.slot}: {reviewer.name} {state}")
    return 0


def command_ensure_attempt(args: argparse.Namespace) -> int:
    plan = _read_json(Path(args.plan))
    path = Path(args.out_dir) / attempt_files.artifact_name(args.slot)
    if path.is_file():
        return 0
    artifact = attempt_files.build_artifact(
        run_id=os.environ["GITHUB_RUN_ID"],
        pr=plan["pr"],
        head_sha=plan["head_sha"],
        slot=args.slot,
        reviewer=args.reviewer,
        verdict=None,
        missing=Absence(ERROR, "the reviewer job did not finish"),
    )
    _write_json(path, artifact)
    return 0


def command_finalize(args: argparse.Namespace) -> int:
    plan = _read_json(Path(args.plan))
    out_dir = Path(args.out_dir)
    run_url = (
        f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{plan['repo']}/actions/runs/{plan['run_id']}"
    )
    if plan["run_id"] != os.environ["GITHUB_RUN_ID"]:
        raise SystemExit("oracle-verdict: the plan belongs to another run")
    now = _now()
    if plan["decision"] == SUCCESS:
        final = report.fixed(retry.SUCCESS, "no soundness path changed")
    elif plan["decision"] == FORK:
        final = report.fixed(retry.PENDING, "fork: owner review")
    elif plan["decision"] == HOLD:
        final = report.fixed(retry.PENDING, plan["decision_reason"])
    elif plan["decision"] in (CARRY, REFUSED):
        previous = retry.State.from_json(plan["previous_state"]) if plan.get("previous_state") else None
        final = report.carried(plan) if plan["decision"] == CARRY else report.refused(plan)
        state = retry.next_state(
            previous=previous,
            pr=plan["pr"],
            head_sha=plan["head_sha"],
            outcome=final.state,
            manual=bool(plan["manual"]),
            now=now,
            pool_blocked_until=previous.pool_blocked_until if previous else {},
            strikes=final.strikes,
        )
        _write_json(out_dir / "state" / github.STATE_FILE, state.to_json())
    else:
        selection_input = _selection(plan)
        loaded = attempt_files.load(Path(args.attempts_dir), plan, os.environ["GITHUB_RUN_ID"])
        step, errors = attempt_files.decide(selection_input, loaded)
        diff_path = Path(args.plan).parent / DIFF_FILE
        commentable = commentable_lines(diff_path.read_text(encoding="utf-8")) if diff_path.is_file() else {}
        final = report.finalize(plan, step, errors, loaded.attempts, loaded.verdicts, commentable)
        previous = plan.get("previous_state")
        state = retry.next_state(
            previous=retry.State.from_json(previous) if previous else None,
            pr=plan["pr"],
            head_sha=plan["head_sha"],
            outcome=final.state,
            manual=bool(plan["manual"]),
            now=now,
            pool_blocked_until=selection.pool_resets(selection_input, loaded.attempts),
            pending_cause=final.pending_cause,
            strikes=final.strikes,
        )
        _write_json(out_dir / "state" / github.STATE_FILE, state.to_json())
    _write_json(out_dir / "status.json", report.status_payload(plan, final, run_url))
    if final.body and (plan["findings_delivery"] == "comment" or final.review is None):
        _write_json(out_dir / "comment.json", {"body": final.body})
    if final.review is not None:
        _write_json(out_dir / "review.json", final.review)
    _output(
        {
            "state": final.state,
            "comment": "true" if (out_dir / "comment.json").is_file() else "false",
            "review": "true" if final.review is not None else "false",
            "has_state": "true" if (out_dir / "state" / github.STATE_FILE).is_file() else "false",
        }
    )
    _summary(f"oracle-verdict #{plan['pr']} {plan['head_sha']}: {final.state}: {final.description}")
    if final.body:
        _summary(final.body)
    return 0


def _reread_history(plan: Mapping[str, Any]) -> protocol.History:
    error: Exception | None = None
    for attempt in range(GUARD_ATTEMPTS):
        if attempt:
            sleep(GUARD_PAUSE_SECONDS)
        try:
            return protocol.history(github.oracle_reviews(plan["repo"], plan["pr"]), plan["bot_login"])
        except (github.GitHubError, KeyError, TypeError, ValueError) as exc:
            error = exc
    _summary(
        f"oracle-verdict: posting without confirming that head {plan['head_sha']} has no decision yet; "
        f"the review list could not be re-read after {GUARD_ATTEMPTS} attempts: {error}"
    )
    return protocol.History()


def command_guard(args: argparse.Namespace) -> int:
    plan = _read_json(Path(args.plan))
    found = _reread_history(plan)
    done = found.latest is not None and found.latest["head_sha"] == plan["head_sha"]
    _output({"skip": "true" if done else "false"})
    if done:
        _summary(f"oracle-verdict: head {plan['head_sha']} already has a decision; this run posts nothing")
    return 0


def command_sweep(args: argparse.Namespace) -> int:
    policy = load_policy(Path(args.policy))
    repo = os.environ["GITHUB_REPOSITORY"]
    now = _now()
    dispatched = 0
    for pull in github.get_paginated(f"repos/{repo}/pulls?state=open&base=develop&per_page=100"):
        if dispatched >= policy.retry.sweep_max_dispatches:
            break
        if pull.get("draft") or (pull["head"].get("repo") or {}).get("full_name") != repo:
            continue
        state = github.latest_state(repo, pull["number"])
        if state is None or not retry.due_for_retry(state, pull["head"]["sha"], now, policy.retry):
            continue
        github.dispatch(repo, WORKFLOW_FILE, pull["number"])
        dispatched += 1
        _summary(f"retry dispatched for #{pull['number']}")
    _summary(f"oracle-verdict sweep dispatched {dispatched} retr{'y' if dispatched == 1 else 'ies'}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oracle-verdict")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--policy", required=True)
    plan.add_argument("--pr", required=True)
    plan.add_argument("--out", required=True)
    plan.set_defaults(handler=command_plan)
    select = commands.add_parser("select")
    select.add_argument("--plan", required=True)
    select.add_argument("--attempts-dir", required=True)
    select.add_argument("--slot", type=int, required=True)
    select.set_defaults(handler=command_select)
    review = commands.add_parser("review")
    review.add_argument("--plan", required=True)
    review.add_argument("--slot", type=int, required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--harness", required=True)
    review.add_argument("--workspace", required=True)
    review.add_argument("--scratch", required=True)
    review.add_argument("--out-dir", required=True)
    review.set_defaults(handler=command_review)
    ensure = commands.add_parser("ensure-attempt")
    ensure.add_argument("--plan", required=True)
    ensure.add_argument("--slot", type=int, required=True)
    ensure.add_argument("--reviewer", required=True)
    ensure.add_argument("--out-dir", required=True)
    ensure.set_defaults(handler=command_ensure_attempt)
    finalize = commands.add_parser("finalize")
    finalize.add_argument("--plan", required=True)
    finalize.add_argument("--attempts-dir", required=True)
    finalize.add_argument("--out-dir", required=True)
    finalize.set_defaults(handler=command_finalize)
    guard = commands.add_parser("guard")
    guard.add_argument("--plan", required=True)
    guard.set_defaults(handler=command_guard)
    sweep = commands.add_parser("sweep")
    sweep.add_argument("--policy", required=True)
    sweep.set_defaults(handler=command_sweep)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
