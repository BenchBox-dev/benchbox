#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_text_edit_verdict
import pr_landing
import theseus_cli
import trunk_revert

CLI_DESCRIPTION = "Arm a pull request for its exact head after a live check that nothing holds it."

HOLD_LABEL = pr_landing.HOLD_LABEL
REPOSITORY = "BenchBox-dev/benchbox"
BASE_BRANCH = "develop"
VIEW_FIELDS = "number,state,isDraft,labels,reviewDecision,headRefOid,baseRefName,headRefName,id"

BASELINE_DIRECTORY = "tests/fixtures/fast_tier_baseline"
VERDICT_ARTIFACT_PREFIX = "fast-duration-verdict"
VERDICT_FILE = "fast-duration-verdict.json"
PROCEEDING_VERDICTS = frozenset({"pass", "neutral", "not-applicable"})
PRODUCER_CHECK = "core"
WAITING_MARKERS = ("waiting for full CI run", "waiting for the placeholder re-run", "has not finished for")
WAIT_INTERVAL_SECONDS = 180.0
WAIT_MAX_MINUTES = 120

_last_outcome = ""

Runner = Callable[[list[str]], tuple[int, str]]

CI_RUNS_JQ = r".workflow_runs[] | [.id, .display_title, .status, .conclusion, .updated_at] | @tsv"
PLACEHOLDER_REFRESH_SECONDS = 600
REFRESH_RUNS_JQ = (
    '.workflow_runs[] | select(.display_title == "ci-text-edit-refresh ({head})") | [.id, .status, .conclusion] | @tsv'
)


class CiRun(NamedTuple):
    id: int
    title: str
    status: str
    conclusion: str = ""
    updated_at: datetime | None = None


class Wait(NamedTuple):
    run_id: int
    kind: str


FULL_RUN = "full"
PLACEHOLDER_BEHIND_FULL_RUN = "placeholder-behind-full"
PLACEHOLDER_REFRESH = "placeholder-refresh"
PLACEHOLDER_RERUNNING = "placeholder-rerunning"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _ci_runs(run: Runner, repo: str, head: str) -> list[CiRun]:
    code, out = run(
        [
            "gh",
            "api",
            "--paginate",
            "--jq",
            CI_RUNS_JQ,
            f"repos/{repo}/actions/workflows/ci.yml/runs?event=pull_request&head_sha={head}&per_page=100",
        ]
    )
    if code != 0:
        raise pr_landing.LandingError(f"could not list the CI runs of {head[:9]}: {out.strip()[:200]}")
    runs: list[CiRun] = []
    for line in out.splitlines():
        fields = (line.split("\t") + ["", "", "", "", ""])[:5]
        if fields[0].isdigit():
            runs.append(CiRun(int(fields[0]), fields[1], fields[2], fields[3], _timestamp(fields[4])))
    return runs


def _refresh_runs(run: Runner, repo: str, head: str) -> list[CiRun]:
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        return []
    code, out = run(
        [
            "gh",
            "api",
            "--paginate",
            "--jq",
            REFRESH_RUNS_JQ.format(head=head),
            f"repos/{repo}/actions/workflows/ci-text-edit-refresh.yml/runs?event=workflow_run&per_page=100",
        ]
    )
    if code != 0:
        return []
    runs: list[CiRun] = []
    for line in out.splitlines():
        fields = (line.split("\t") + ["", "", ""])[:3]
        if fields[0].isdigit():
            runs.append(CiRun(int(fields[0]), "", fields[1], fields[2]))
    return runs


def _placeholder_refresh_pending(
    failed: CiRun, full_runs: Sequence[CiRun], refresh: Sequence[CiRun], now: datetime
) -> Wait | None:
    if not full_runs:
        return None
    newest = max(full_runs, key=lambda item: item.id)
    if newest.conclusion != "success" or newest.updated_at is None or failed.updated_at is None:
        return None
    if failed.updated_at >= newest.updated_at:
        return None
    live = [item for item in refresh if item.conclusion != "skipped"]
    if any(item.status != "completed" for item in live):
        return Wait(newest.id, PLACEHOLDER_REFRESH)
    if live:
        return None
    if 0 <= (now - newest.updated_at).total_seconds() < PLACEHOLDER_REFRESH_SECONDS:
        return Wait(newest.id, PLACEHOLDER_REFRESH)
    return None


def _waiting_full_run(runs: Sequence[CiRun], url: str, now: datetime, refresh: Sequence[CiRun] = ()) -> Wait | None:
    match = pr_landing.check_suites.ACTIONS_RUN_RE.search(url)
    if match is None:
        return None
    failed_run = int(match.group(1))
    placeholders = [item for item in runs if item.title == ci_text_edit_verdict.TEXT_EDIT_RUN_NAME]
    failed = next((item for item in placeholders if item.id == failed_run), None)
    if failed is None:
        in_progress = any(item.id == failed_run and item.status != "completed" for item in runs)
        return Wait(failed_run, FULL_RUN) if in_progress else None
    full_runs = [item for item in runs if item.title != ci_text_edit_verdict.TEXT_EDIT_RUN_NAME]
    full_open = next((item for item in full_runs if item.status != "completed"), None)
    if full_open is not None:
        return Wait(full_open.id, PLACEHOLDER_BEHIND_FULL_RUN)
    rerunning = next((item for item in placeholders if item.status != "completed"), None)
    if rerunning is not None:
        return Wait(rerunning.id, PLACEHOLDER_RERUNNING)
    return _placeholder_refresh_pending(failed, full_runs, refresh, now)


def _waiting_reason(name: str, run_id: int, kind: str) -> str:
    if kind == PLACEHOLDER_BEHIND_FULL_RUN:
        return (
            f"waiting for full CI run {run_id}: the required {name!r} check is a placeholder failure that re-runs "
            f"on its own when that run completes; arm again after it completes"
        )
    if kind == PLACEHOLDER_RERUNNING:
        return (
            f"waiting for the placeholder re-run of CI run {run_id}: the required {name!r} check is a placeholder "
            f"failure that is being re-run on its own; arm again in a few minutes"
        )
    if kind == PLACEHOLDER_REFRESH:
        return (
            f"waiting for the placeholder re-run after full CI run {run_id} completed: the required {name!r} check "
            f"is a placeholder failure that the ci-text-edit-refresh workflow re-runs on its own; "
            f"arm again in a few minutes"
        )
    return (
        f"waiting for full CI run {run_id} to complete: the required {name!r} check already failed inside it, "
        f"and GitHub re-runs failed jobs only after the whole run completes; once it completes, run make pr-rerun "
        f"if the check is still failed, then arm again"
    )


def _core_state(run: Runner, repo: str, number: int, head: str) -> str:
    core = pr_landing.live_check_verdicts(run, repo, head).get(PRODUCER_CHECK)
    if core is None or core.get("status") != "completed":
        return (
            f"the {PRODUCER_CHECK} job that records it has not finished for {head[:9]}; "
            f"arm again once {PRODUCER_CHECK} completes"
        )
    conclusion = core.get("conclusion")
    if conclusion not in pr_landing.check_suites.NON_BLOCKING:
        waiting = _waiting_full_run(
            _ci_runs(run, repo, head), str(core.get("url") or ""), _now(), _refresh_runs(run, repo, head)
        )
        if waiting is not None:
            return _waiting_reason(PRODUCER_CHECK, *waiting)
    remedy = f"rerun it with make pr-rerun PR={number}"
    if conclusion not in pr_landing.check_suites.RERUN_FAILED | pr_landing.check_suites.RERUN_ALL:
        remedy += " (or push a new head if it refuses)"
    return (
        f"the {PRODUCER_CHECK} job ended {conclusion or 'without a conclusion'} for {head[:9]} and no verdict "
        f"will arrive; {remedy}, then arm again"
    )


def _in_flight_waits(run: Runner, repo: str, branch: str, head: str) -> tuple[dict, dict[str, dict]]:
    required = pr_landing.ruleset_required_contexts(run, repo, branch)
    evaluations = pr_landing.check_suites.evaluate(
        pr_landing.live_check_runs(run, repo, head), required, pr_landing.check_suites.NON_BLOCKING
    )
    runs = _ci_runs(run, repo, head) if any(item.failed for item in evaluations.values()) else []
    refresh = _refresh_runs(run, repo, head) if runs else []
    now = _now()
    waits_by_name: dict[str, dict] = {}
    for name, item in evaluations.items():
        waits = {}
        for suite in item.failed:
            waiting = _waiting_full_run(runs, suite.url, now, refresh)
            if waiting is not None:
                waits[suite] = waiting
        waits_by_name[name] = waits
    return evaluations, waits_by_name


def suite_refusals(run: Runner, repo: str, branch: str, head: str) -> list[str]:
    evaluations, waits_by_name = _in_flight_waits(run, repo, branch, head)
    waiting: list[str] = []
    remaining = {}
    for name, item in evaluations.items():
        waits = waits_by_name[name]
        waiting.extend(_waiting_reason(name, *wait) for wait in dict.fromkeys(waits.values()))
        remaining[name] = pr_landing.check_suites.NameEvaluation(
            name, tuple(suite for suite in item.suites if suite not in waits), item.passing
        )
    return [*waiting, *pr_landing.check_suites.hidden_failure_reasons(remaining)]


def visible_refusals(run: Runner, repo: str, branch: str, head: str) -> list[str]:
    _, waits_by_name = _in_flight_waits(run, repo, branch, head)
    waiting = frozenset(name for name, waits in waits_by_name.items() if waits)
    return pr_landing.visible_required_failure_reasons(run, repo, branch, head, waiting)


def _read_duration_verdict(run: Runner, repo: str, number: int, head: str) -> tuple[dict | None, str | None]:
    name = f"{VERDICT_ARTIFACT_PREFIX}-{number}-{head}"
    code, out = run(
        [
            "gh",
            "api",
            "--jq",
            r'[.artifacts[] | select(.expired | not)][0] | if . == null then "" '
            r'else "\(.workflow_run.id) \(.workflow_run.head_sha)" end',
            f"repos/{repo}/actions/artifacts?name={name}&per_page=5",
        ]
    )
    if code != 0:
        return None, f"could not look up the verify-fast-duration verdict of {head[:9]}: {out.strip()[:200]}"
    fields = out.split()
    if not fields:
        return (
            None,
            f"no verify-fast-duration verdict is recorded for {head[:9]}: {_core_state(run, repo, number, head)}",
        )
    if len(fields) != 2 or not fields[0].isdigit() or fields[1] != head:
        return None, f"the verify-fast-duration verdict found is not bound to {head[:9]}"
    with tempfile.TemporaryDirectory(prefix="pr-arm-verdict-") as scratch:
        code, out = run(["gh", "run", "download", fields[0], "--repo", repo, "-n", name, "-D", scratch])
        path = Path(scratch) / VERDICT_FILE
        if code != 0 or not path.is_file():
            return None, f"could not download the verify-fast-duration verdict of {head[:9]}: {out.strip()[:200]}"
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, f"the verify-fast-duration verdict of {head[:9]} is unreadable"
    if not isinstance(report, dict):
        return None, f"the verify-fast-duration verdict of {head[:9]} is unreadable"
    return report, None


def _changes_baseline(run: Runner, repo: str, number: int) -> bool:
    code, out = run(["gh", "api", "--paginate", "--jq", ".[].filename", f"repos/{repo}/pulls/{number}/files"])
    if code != 0:
        raise pr_landing.LandingError(f"cannot list the files changed by PR #{number}: {out.strip()[:200]}")
    return any(line.startswith(f"{BASELINE_DIRECTORY}/") for line in out.splitlines())


def _ratio(value: object) -> str:
    return "n/a" if not isinstance(value, (int, float)) else f"{value:.3f}"


def duration_verdict_refusals(run: Runner, repo: str, number: int, head: str, label: str = "pr-arm") -> list[str]:
    report, refusal = _read_duration_verdict(run, repo, number, head)
    if report is None:
        return [refusal] if refusal else []
    verdict = report.get("verdict")
    if verdict in PROCEEDING_VERDICTS:
        print(f"{label}: duration check: verify-fast-duration verdict for {head[:9]} is {verdict}")
        return []
    if verdict != "would-block":
        return [
            f"verify-fast-duration verdict for {head[:9]} is {verdict if isinstance(verdict, str) else 'missing'}, "
            f"not one of {', '.join(sorted(PROCEEDING_VERDICTS))}: {str(report.get('reason') or 'no reason recorded')[:200]}. "
            "Fix the cause and re-run the core job with make pr-rerun, or push a new head"
        ]
    if _changes_baseline(run, repo, number):
        print(
            f"{label}: duration check: would-block verdict for {head[:9]} is excused because the PR updates {BASELINE_DIRECTORY}"
        )
        return []
    return [
        f"verify-fast-duration reported would-block for {head[:9]}: normalized summed-time ratio "
        f"{_ratio(report.get('sum_ratio'))}, median per-test ratio {_ratio(report.get('median_test_ratio'))}. "
        f"The duration gate blocks `core` on a would-block verdict, and arming refuses it unless the PR changes "
        f"{BASELINE_DIRECTORY}: remove the slowdown, or regenerate the "
        f"baseline with scripts/build_fast_duration_baseline.py (python -m scripts.build_fast_duration_baseline "
        f"build --manifest <manifest> --output {BASELINE_DIRECTORY}) and justify the change in the PR"
    ]


def gate_refusals(run: Runner, repo: str, number: int, head: str, label: str = "pr-arm") -> list[str]:
    return theseus_cli.gate_refusals(run, repo, number, head, label)


def refusals(
    view: dict,
    local_head: str,
    *,
    unpublished: Sequence[str],
    unresolved_threads: Sequence[dict] = (),
    hidden_failures: Sequence[str] = (),
) -> list[str]:
    reasons: list[str] = []
    if view.get("state") != "OPEN":
        reasons.append(f"PR is {view.get('state')!r}, not OPEN")
    if view.get("isDraft"):
        reasons.append("PR is a draft")
    if view.get("baseRefName") != BASE_BRANCH:
        reasons.append(f"PR targets {view.get('baseRefName')!r}; this path arms only PRs into {BASE_BRANCH!r}")
    labels = {label.get("name") for label in view.get("labels") or []}
    if HOLD_LABEL in labels:
        reasons.append(f"durable hold label {HOLD_LABEL!r} is present; remove it deliberately to release the hold")
    if view.get("reviewDecision") == "CHANGES_REQUESTED":
        reasons.append("a reviewer requested changes")
    if unresolved_threads:
        reasons.append(pr_landing.describe_unresolved_threads(unresolved_threads))
    reasons.extend(hidden_failures)
    reasons.extend(f"unpublished work: {problem}" for problem in unpublished)
    if view.get("headRefOid") != local_head:
        reasons.append(f"pushed head {str(view.get('headRefOid'))[:9]} is not local HEAD {local_head[:9]}; push first")
    return reasons


def _current_branch_pr(run: Runner, repo: str) -> str:
    code, branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if code != 0 or not branch.strip() or branch.strip() == "HEAD":
        raise pr_landing.LandingError("cannot resolve the current branch; pass PR=<number>")
    code, out = run(
        ["gh", "pr", "list", "--repo", repo, "--head", branch.strip(), "--base", BASE_BRANCH, "--state", "open"]
        + ["--json", "number"]
    )
    if code != 0:
        raise pr_landing.LandingError(f"cannot list pull requests for {branch.strip()!r}: {out.strip()}")
    numbers = [item["number"] for item in json.loads(out)]
    if len(numbers) != 1:
        raise pr_landing.LandingError(
            f"expected exactly one open PR into {BASE_BRANCH!r} from {branch.strip()!r}, found {len(numbers)}; pass PR=<number>"
        )
    return str(numbers[0])


def _worktree_config(run: Runner, key: str) -> str | None:
    code, out = run(["git", "config", "--get", "extensions.worktreeConfig"])
    if code != 0 or out.strip().lower() != "true":
        return None
    code, out = run(["git", "config", "--worktree", key])
    if code == 1:
        return None
    if code != 0:
        raise pr_landing.LandingError(f"cannot read the worktree setting {key}: {out.strip()[:200]}")
    return out.strip() or None


def batch_refusals(
    run: Runner,
    checkout: Path,
    head: str,
    number: int,
    view: dict | None = None,
    require_batch: bool = False,
    discover: Callable[..., tuple[str | None, str]] | None = None,
    repo: str = REPOSITORY,
) -> list[str]:
    batch_role = _worktree_config(run, "benchbox.batch.role")
    batch_id = _worktree_config(run, "benchbox.batch.id")
    tracker: dict | None = None
    if batch_role == "integration":
        if batch_id is None:
            return ["the integration worktree names no batch (benchbox.batch.id is unset)"]
        tracker = pr_landing.read_batch_tracker(batch_id)
        discovered_id: str | None = None if tracker.get("lifecycle") == "aborted" else batch_id
    else:
        v = view or {}
        discovery_fn = discover or pr_landing.discover_batch
        discovered_id, _rev = discovery_fn(run, repo, v.get("headRefName"), number, v.get("id"))

    if discovered_id is None:
        if require_batch:
            return ["require-batch asserted but PR is not a registered batch PR"]
        return []

    evidence = pr_landing._load_batch_evidence(
        checkout, str(checkout / ".todo-batch" / discovered_id / "final-evidence.json")
    )
    if evidence is None:
        return [f"batch {discovered_id} has no final evidence file at .todo-batch/{discovered_id}/final-evidence.json"]
    if tracker is None:
        tracker = pr_landing.read_batch_tracker(discovered_id)

    reasons: list[str] = []
    if tracker.get("lifecycle") != "active":
        reasons.append(f"batch {discovered_id} is not active in the tracker")
    final_pr = tracker.get("final_pr")
    if not isinstance(final_pr, dict) or final_pr.get("number") != number:
        reasons.append(f"batch {discovered_id} is not bound to PR #{number}")
    reasons.extend(pr_landing.check_batch_evidence(checkout, evidence or {}, head, tracker))
    return reasons


def _outcome(line: str, code: int) -> int:
    global _last_outcome
    _last_outcome = line
    sys.stderr.flush()
    print(f"pr-arm: outcome: {line}", flush=True)
    return code


def _refused(reason: str, code: int) -> int:
    print(f"pr-arm: refusing: {reason}", file=sys.stderr)
    return _outcome(f"refused: {reason}", code)


GATE_NOT_PASSING = " does not pass: "


def _review_verdict_pending(reason: str, head: str) -> bool:
    if not reason.startswith("a fresh review-gate evaluation of ") or GATE_NOT_PASSING not in reason:
        return False
    detail = reason.split(GATE_NOT_PASSING, 1)[1]
    return pr_landing.review_gate_waiting(detail, head) and not detail.startswith(pr_landing.REVIEW_GATE_WAITING_PREFIX)


def _is_waiting(reason: str, head: str, wait_on_pending_review: bool) -> bool:
    if any(marker in reason for marker in WAITING_MARKERS):
        return True
    return wait_on_pending_review and _review_verdict_pending(reason, head)


def arm(
    pr: str | None,
    head: str | None,
    repo: str = REPOSITORY,
    run: Runner = pr_landing.live_run,
    checkout: Path = Path("."),
    unpublished: Callable[[Path], list[str]] = pr_landing.unpublished_work,
    threads: Callable[[Runner, str, int], Sequence[dict]] = pr_landing.unresolved_review_threads,
    suites: Callable[[Runner, str, str, str], list[str]] = suite_refusals,
    duration: Callable[[Runner, str, int, str], list[str]] = duration_verdict_refusals,
    visible: Callable[[Runner, str, str, str], list[str]] = visible_refusals,
    gate: Callable[[Runner, str, int, str], list[str]] = gate_refusals,
    batch: Callable[..., list[str]] = batch_refusals,
    require_batch: bool = False,
    discover: Callable[..., tuple[str | None, str]] | None = None,
    wait_on_pending_review: bool = False,
) -> int:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        return _refused(f"--repo must be owner/name, got {repo!r}", 2)
    if head is not None and not re.fullmatch(r"[0-9a-f]{40}", head):
        return _refused(f"--head must be a full lowercase commit SHA, got {head!r}", 2)
    try:
        code, out = run(["git", "rev-parse", "HEAD"])
        if code != 0:
            return _refused(f"cannot read local HEAD: {out.strip()}", 1)
        local_head = out.strip()
        if head is not None and head != local_head:
            return _refused(f"requested head {head[:9]} is not local HEAD {local_head[:9]}", 2)
        if pr is not None and not re.fullmatch(r"[1-9][0-9]*", pr):
            return _refused(f"--pr must be a plain PR number, got {pr!r}", 2)
        number = pr or _current_branch_pr(run, repo)
        code, out = run(["gh", "pr", "view", number, "--repo", repo, "--json", VIEW_FIELDS])
        if code != 0:
            return _refused(f"cannot read the pull request: {out.strip()}", 1)
        view = json.loads(out)
        reasons = refusals(
            view,
            local_head,
            unpublished=unpublished(checkout),
            unresolved_threads=threads(run, repo, int(view["number"])),
            hidden_failures=[
                *suites(run, repo, BASE_BRANCH, local_head),
                *(duration(run, repo, int(view["number"]), local_head) if view.get("headRefOid") == local_head else []),
                *visible(run, repo, BASE_BRANCH, local_head),
                *(gate(run, repo, int(view["number"]), local_head) if view.get("headRefOid") == local_head else []),
                *(
                    batch(run, checkout, local_head, int(view["number"]), view, require_batch, discover, repo)
                    if view.get("headRefOid") == local_head
                    else []
                ),
            ],
        )
        if reasons:
            for reason in reasons:
                print(f"pr-arm: refusing to arm PR #{view['number']}: {reason}", file=sys.stderr)
            if all(_is_waiting(reason, local_head, wait_on_pending_review) for reason in reasons):
                return _outcome(f"waiting: PR #{view['number']} is not armed yet: {' | '.join(reasons)}", 2)
            return _outcome(f"refused: PR #{view['number']} was not armed: {' | '.join(reasons)}", 2)
        code, out = run(
            [
                "gh",
                "pr",
                "merge",
                str(view["number"]),
                "--repo",
                repo,
                "--squash",
                "--auto",
                "--match-head-commit",
                local_head,
            ]
        )
        print(out.strip())
        if code != 0:
            return _outcome(f"refused: gh pr merge exited {code} for PR #{view['number']}: {out.strip()[:200]}", code)
        return _outcome(f"armed: PR #{view['number']} at head {local_head}", 0)
    except (pr_landing.LandingError, subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"pr-arm: refusing, live state could not be verified: {exc}", file=sys.stderr)
        return _outcome(f"refused: live state could not be verified: {exc}", 1)


def _mono_time() -> float:
    try:
        from benchbox.utils.clock import mono_time
    except ImportError:
        from time import monotonic as mono_time
    return mono_time()


def wait_minutes(raw: str | None) -> int | None:
    if raw is None or raw == "":
        return None
    if not re.fullmatch(r"[1-9][0-9]*", raw) or int(raw) > WAIT_MAX_MINUTES:
        raise ValueError(f"WAIT must be a whole number of minutes from 1 to {WAIT_MAX_MINUTES}, got {raw!r}")
    return int(raw)


def trunk_gate_refusal(
    pr: str | None, repo: str, run: Runner = pr_landing.live_run, trunk_run: Runner = trunk_revert.live_run
) -> str | None:
    try:
        ref = pr
        if not ref:
            code, out = run(["git", "branch", "--show-current"])
            ref = out.strip()
            if code != 0 or not ref:
                return "the current branch could not be read for the trunk check"
        return trunk_revert.arm_gate(ref, trunk_run, repo)
    except (trunk_revert.TrunkError, subprocess.SubprocessError, OSError, ValueError) as exc:
        return f"the trunk state could not be read: {exc}"


def arm_waiting(
    attempt: Callable[[], int],
    trunk_gate: Callable[[], str | None],
    minutes: int,
    clock: Callable[[], float] = _mono_time,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    deadline = clock() + minutes * 60.0
    while True:
        code = attempt()
        if not _last_outcome.startswith("waiting:"):
            return code
        remaining = deadline - clock()
        if remaining <= 0:
            return _outcome(f"{_last_outcome} (WAIT={minutes} expired)", code)
        pause = min(WAIT_INTERVAL_SECONDS, remaining)
        print(
            f"pr-arm: waiting {pause:.0f}s before checking again ({remaining / 60:.0f} min of WAIT={minutes} left)",
            file=sys.stderr,
            flush=True,
        )
        sleep(pause)
        refusal = trunk_gate()
        if refusal:
            return _refused(f"stopped waiting, the trunk check refuses: {refusal}", 2)


def _environment_selection(env: Mapping[str, str]) -> dict[str, Any]:
    return {
        "pr": env.get("PR_ARM_PR") if env.get("PR_ARM_PR_SET") == "1" else None,
        "head": env.get("PR_ARM_HEAD") if env.get("PR_ARM_HEAD_SET") == "1" else None,
        "repo": env.get("PR_ARM_REPO") if env.get("PR_ARM_REPO_SET") == "1" else None,
        "wait": env.get("PR_ARM_WAIT") if env.get("PR_ARM_WAIT_SET") == "1" else None,
        "require_batch": env.get("PR_ARM_REQUIRE_BATCH") == "1",
    }


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    selected = _environment_selection(os.environ if env is None else env)
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--pr", default=selected["pr"], help="pull request number; default: the current branch's PR")
    parser.add_argument("--head", default=selected["head"], help="expected head SHA; refused unless it is local HEAD")
    parser.add_argument("--repo", default=selected["repo"] if selected["repo"] is not None else REPOSITORY)
    parser.add_argument(
        "--wait-minutes",
        default=selected["wait"],
        help=f"keep checking while the outcome is waiting, for up to this many minutes (1-{WAIT_MAX_MINUTES})",
    )
    parser.add_argument(
        "--require-batch",
        action="store_true",
        default=selected["require_batch"],
        help="assert that the PR is a registered batch PR",
    )
    args = parser.parse_args(argv)
    try:
        minutes = wait_minutes(args.wait_minutes)
    except ValueError as exc:
        return _refused(str(exc), 2)

    def attempt() -> int:
        options: dict[str, bool] = {}
        if args.require_batch:
            options["require_batch"] = True
        if minutes is not None:
            options["wait_on_pending_review"] = True
        return arm(args.pr, args.head, args.repo, **options)

    if minutes is None:
        return attempt()
    return arm_waiting(attempt, lambda: trunk_gate_refusal(args.pr, args.repo), minutes)


if __name__ == "__main__":
    raise SystemExit(main())
