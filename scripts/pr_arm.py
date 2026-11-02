#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_text_edit_verdict
import pr_arm_omp_bump
import pr_landing

CLI_DESCRIPTION = "Arm a pull request for its exact head after a live check that nothing holds it."

HOLD_LABEL = pr_landing.HOLD_LABEL
REPOSITORY = "BenchBox-dev/BenchBox"
BASE_BRANCH = "develop"
VIEW_FIELDS = "number,state,isDraft,labels,reviewDecision,headRefOid,baseRefName"

BASELINE_DIRECTORY = "tests/fixtures/fast_tier_baseline"
VERDICT_ARTIFACT_PREFIX = "fast-duration-verdict"
VERDICT_FILE = "fast-duration-verdict.json"
PROCEEDING_VERDICTS = frozenset({"pass", "neutral", "not-applicable"})
PRODUCER_CHECK = "core"
WAITING_MARKERS = ("waiting for full CI run", "has not finished for")

Runner = Callable[[list[str]], tuple[int, str]]

CI_RUNS_JQ = r".workflow_runs[] | [.id, .display_title, .status] | @tsv"


def _ci_runs(run: Runner, repo: str, head: str) -> list[tuple[int, str, str]]:
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
    runs: list[tuple[int, str, str]] = []
    for line in out.splitlines():
        fields = line.split("\t")
        if len(fields) == 3 and fields[0].isdigit():
            runs.append((int(fields[0]), fields[1], fields[2]))
    return runs


def _waiting_full_run(runs: Sequence[tuple[int, str, str]], url: str) -> int | None:
    match = pr_landing.check_suites.ACTIONS_RUN_RE.search(url)
    if match is None:
        return None
    placeholder = {run_id for run_id, title, _ in runs if title == ci_text_edit_verdict.TEXT_EDIT_RUN_NAME}
    if int(match.group(1)) not in placeholder:
        return None
    return next(
        (
            run_id
            for run_id, title, status in runs
            if title != ci_text_edit_verdict.TEXT_EDIT_RUN_NAME and status != "completed"
        ),
        None,
    )


def _waiting_reason(name: str, full_run: int) -> str:
    return (
        f"waiting for full CI run {full_run}: the required {name!r} check is a placeholder failure that re-runs "
        f"on its own when that run completes; arm again after it completes"
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
        full_run = _waiting_full_run(_ci_runs(run, repo, head), str(core.get("url") or ""))
        if full_run is not None:
            return _waiting_reason(PRODUCER_CHECK, full_run)
    remedy = f"rerun it with make pr-rerun PR={number}"
    if conclusion not in pr_landing.check_suites.RERUN_FAILED | pr_landing.check_suites.RERUN_ALL:
        remedy += " (or push a new head if it refuses)"
    return (
        f"the {PRODUCER_CHECK} job ended {conclusion or 'without a conclusion'} for {head[:9]} and no verdict "
        f"will arrive; {remedy}, then arm again"
    )


def _placeholder_waits(run: Runner, repo: str, branch: str, head: str) -> tuple[dict, dict[str, dict]]:
    required = pr_landing.ruleset_required_contexts(run, repo, branch)
    evaluations = pr_landing.check_suites.evaluate(
        pr_landing.live_check_runs(run, repo, head), required, pr_landing.check_suites.NON_BLOCKING
    )
    runs = _ci_runs(run, repo, head) if any(item.failed for item in evaluations.values()) else []
    placeholders: dict[str, dict] = {}
    for name, item in evaluations.items():
        waits = {}
        for suite in item.failed:
            full_run = _waiting_full_run(runs, suite.url)
            if full_run is not None:
                waits[suite] = full_run
        placeholders[name] = waits
    return evaluations, placeholders


def suite_refusals(run: Runner, repo: str, branch: str, head: str) -> list[str]:
    evaluations, placeholders = _placeholder_waits(run, repo, branch, head)
    waiting: list[str] = []
    remaining = {}
    for name, item in evaluations.items():
        waits = placeholders[name]
        waiting.extend(_waiting_reason(name, full_run) for full_run in dict.fromkeys(waits.values()))
        remaining[name] = pr_landing.check_suites.NameEvaluation(
            name, tuple(suite for suite in item.suites if suite not in waits), item.passing
        )
    return [*waiting, *pr_landing.check_suites.hidden_failure_reasons(remaining)]


def visible_refusals(run: Runner, repo: str, branch: str, head: str) -> list[str]:
    _, placeholders = _placeholder_waits(run, repo, branch, head)
    waiting = frozenset(name for name, waits in placeholders.items() if waits)
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


GATE_SCRIPT = "_project/scripts/oracle_gate.py"
GATE_WAITING_EXIT = 3
GATE_TREE = "_project/scripts .github/oracle-reviewers.yml .github/soundness-paths.txt .github/inert-paths.txt"


def gate_refusals(run: Runner, repo: str, number: int, head: str, label: str = "pr-arm") -> list[str]:
    code, out = run(
        [
            "sh",
            "-c",
            'set -eu; DIR="$(mktemp -d)"; trap \'rm -rf "$DIR"\' EXIT; '
            f"git fetch --quiet origin {BASE_BRANCH}; "
            f'git archive origin/{BASE_BRANCH} {GATE_TREE} | tar -x -C "$DIR"; '
            'cd "$DIR"; GITHUB_TOKEN="$(gh auth token)" uv run --isolated --no-project --python 3.12 '
            f'--with pyyaml==6.0.3 -- python {GATE_SCRIPT} --repo "$0" --pr "$1" --head-sha "$2" --no-post',
            repo,
            str(number),
            head,
        ]
    )
    if code == 0:
        return []
    detail = out.strip().splitlines()[-1][:300] if out.strip() else "no output"
    if code == GATE_WAITING_EXIT:
        return [f"a fresh oracle-gate evaluation of {head[:9]} does not pass: {detail}"]
    return [f"a fresh oracle-gate evaluation of {head[:9]} failed (exit {code}): {detail}"]


def refusals(
    view: dict,
    local_head: str,
    *,
    unpublished: Sequence[str],
    unresolved_threads: bool,
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
        reasons.append("an unresolved, non-outdated review thread is open")
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
    code, out = run(["git", "config", "--worktree", key])
    if code == 1:
        return None
    if code != 0:
        raise pr_landing.LandingError(f"cannot read the worktree setting {key}: {out.strip()[:200]}")
    return out.strip() or None


def batch_refusals(run: Runner, checkout: Path, head: str, number: int) -> list[str]:
    if _worktree_config(run, "benchbox.batch.role") != "integration":
        return []
    batch_id = _worktree_config(run, "benchbox.batch.id")
    if batch_id is None:
        return ["the integration worktree names no batch (benchbox.batch.id is unset)"]
    evidence = pr_landing._load_batch_evidence(
        checkout, str(checkout / ".todo-batch" / batch_id / "final-evidence.json")
    )
    tracker = pr_landing.read_batch_tracker(batch_id)
    reasons: list[str] = []
    if tracker.get("lifecycle") != "active":
        reasons.append(f"batch {batch_id} is not active in the tracker")
    final_pr = tracker.get("final_pr")
    if not isinstance(final_pr, dict) or final_pr.get("number") != number:
        reasons.append(f"batch {batch_id} is not bound to PR #{number}")
    reasons.extend(pr_landing.check_batch_evidence(checkout, evidence or {}, head, tracker))
    return reasons


def _outcome(line: str, code: int) -> int:
    sys.stderr.flush()
    print(f"pr-arm: outcome: {line}", flush=True)
    return code


def _refused(reason: str, code: int) -> int:
    print(f"pr-arm: refusing: {reason}", file=sys.stderr)
    return _outcome(f"refused: {reason}", code)


def arm(
    pr: str | None,
    head: str | None,
    repo: str = REPOSITORY,
    run: Runner = pr_landing.live_run,
    checkout: Path = Path("."),
    unpublished: Callable[[Path], list[str]] = pr_landing.unpublished_work,
    threads: Callable[[Runner, str, int], bool] = pr_landing.unresolved_review_threads,
    suites: Callable[[Runner, str, str, str], list[str]] = suite_refusals,
    duration: Callable[[Runner, str, int, str], list[str]] = duration_verdict_refusals,
    visible: Callable[[Runner, str, str, str], list[str]] = visible_refusals,
    bump: Callable[[Runner, str, int, str], list[str]] = pr_arm_omp_bump.bump_refusals,
    gate: Callable[[Runner, str, int, str], list[str]] = gate_refusals,
    batch: Callable[[Runner, Path, str, int], list[str]] = batch_refusals,
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
                *(bump(run, repo, int(view["number"]), local_head) if view.get("headRefOid") == local_head else []),
                *visible(run, repo, BASE_BRANCH, local_head),
                *(gate(run, repo, int(view["number"]), local_head) if view.get("headRefOid") == local_head else []),
                *(
                    batch(run, checkout, local_head, int(view["number"]))
                    if view.get("headRefOid") == local_head
                    else []
                ),
            ],
        )
        if reasons:
            for reason in reasons:
                print(f"pr-arm: refusing to arm PR #{view['number']}: {reason}", file=sys.stderr)
            if all(any(marker in reason for marker in WAITING_MARKERS) for reason in reasons):
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


def _environment_selection(env: Mapping[str, str]) -> dict[str, str | None]:
    return {
        name: env.get(f"PR_ARM_{name.upper()}") if env.get(f"PR_ARM_{name.upper()}_SET") == "1" else None
        for name in ("pr", "head", "repo")
    }


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    selected = _environment_selection(os.environ if env is None else env)
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--pr", default=selected["pr"], help="pull request number; default: the current branch's PR")
    parser.add_argument("--head", default=selected["head"], help="expected head SHA; refused unless it is local HEAD")
    parser.add_argument("--repo", default=selected["repo"] if selected["repo"] is not None else REPOSITORY)
    args = parser.parse_args(argv)
    return arm(args.pr, args.head, args.repo)


if __name__ == "__main__":
    raise SystemExit(main())
