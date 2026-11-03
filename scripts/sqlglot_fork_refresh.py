from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

FORK = "BenchBox-dev/benchbox-sqlglot"
BRANCH_PREFIX = "refresh/upstream-"
ISSUE_PREFIX = "SQLGlot fork refresh to "
ISSUE_SUFFIX = " needs follow-up"
SIGNATURE_MARKER = "<!-- sqlglot-fork-refresh-signature: "
BOT_NAME = "benchbox-bot"
BOT_EMAIL = "bot@benchbox.dev"
REFRESH_COMMAND = ["uv", "run", "--no-project", "--python", "3.12", "--", "python", "-m", "downstream.refresh"]
FOLLOW_UP_EXIT = 3
FAILING_CONCLUSIONS = {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}
ISSUE_STATES = {"conflict", "failed", "ci-failing", "declined", "unpushed"}
UNKNOWN_TAG = "the newest upstream tag"
STATE_FILE = "state.json"
OPEN_FILE = "open.json"
BUNDLE_FILE = "refresh.bundle"

Runner = Callable[..., subprocess.CompletedProcess]


def run(
    args: Sequence[str], cwd: Path | None = None, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess:
    environment = {**os.environ, **env} if env else None
    return subprocess.run(list(args), cwd=cwd, env=environment, capture_output=True, text=True, check=False)


def checked(result: subprocess.CompletedProcess) -> subprocess.CompletedProcess:
    if result.returncode:
        raise RuntimeError(
            f"{' '.join(map(str, result.args[:3]))} exited {result.returncode}: {result.stderr.strip()[-500:]}"
        )
    return result


def api_items(runner: Runner, path: str, expression: str) -> list[dict[str, Any]]:
    result = checked(runner(["gh", "api", "--paginate", path, "--jq", expression], None))
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def branch_for(tag: str) -> str:
    return BRANCH_PREFIX + tag.removeprefix("v")


def issue_title(tag: str) -> str:
    return f"{ISSUE_PREFIX}{tag}{ISSUE_SUFFIX}"


def refresh_pull_requests(runner: Runner, branch: str, fork: str) -> list[dict[str, Any]]:
    owner = fork.split("/", 1)[0]
    return api_items(
        runner,
        f"repos/{fork}/pulls?state=all&head={owner}:{branch}&per_page=100",
        ".[] | {number, state, merged: (.merged_at != null), url: .html_url, sha: .head.sha}",
    )


def pull_request_checks(runner: Runner, sha: str, fork: str) -> tuple[list[str], bool]:
    runs = api_items(
        runner,
        f"repos/{fork}/commits/{sha}/check-runs?per_page=100",
        ".check_runs[] | {name, status, conclusion}",
    )
    failing = sorted(r["name"] for r in runs if r["conclusion"] in FAILING_CONCLUSIONS)
    pending = any(r["status"] != "completed" for r in runs) or not runs
    return failing, pending


def prepare(clone: Path, out_dir: Path, runner: Runner = run, fork: str = FORK) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    checked(runner(["git", "config", "user.name", BOT_NAME], clone))
    checked(runner(["git", "config", "user.email", BOT_EMAIL], clone))
    latest = json.loads(checked(runner([*REFRESH_COMMAND, "latest"], clone)).stdout)
    tag = latest["latest_tag"]
    state: dict[str, Any] = {"tag": tag, "current": latest["current"]["tag"]}
    if not latest["refresh_available"]:
        return {**state, "state": "current"}
    branch = branch_for(tag)
    state["branch"] = branch
    existing = refresh_pull_requests(runner, branch, fork)
    if existing:
        pull = existing[0]
        state["pull_request"] = pull["url"]
        if pull["state"] != "open":
            return {**state, "state": "declined"}
        failing, pending = pull_request_checks(runner, pull["sha"], fork)
        if failing:
            return {**state, "state": "ci-failing", "failing": failing}
        return {**state, "state": "pending" if pending else "awaiting-review"}
    report_path = out_dir / "report.json"
    result = runner(
        [*REFRESH_COMMAND, "auto", "--tag", tag, "--work-dir", str(out_dir / "work"), "--report", str(report_path)],
        clone,
    )
    if result.returncode not in (0, FOLLOW_UP_EXIT) or not report_path.is_file():
        return {
            **state,
            "state": "failed",
            "step": "auto",
            "detail": (result.stderr or result.stdout).strip()[-1500:] or f"exit {result.returncode}",
        }
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report["status"] != "refreshed":
        return {
            **state,
            "state": report["status"],
            "step": report.get("step", ""),
            "entry": report.get("entry", ""),
            "paths": report.get("paths", []),
            "detail": report.get("detail", ""),
        }
    checked(runner(["git", "bundle", "create", str(out_dir / BUNDLE_FILE), branch], clone))
    return {
        **state,
        "state": "ready",
        "version": report["version"],
        "upstream_sha": report["upstream_sha"],
        "entries": report["entries"],
    }


def prepare_or_report(clone: Path, out_dir: Path, runner: Runner = run, fork: str = FORK) -> dict[str, Any]:
    try:
        return prepare(clone, out_dir, runner, fork)
    except (RuntimeError, ValueError, KeyError, OSError) as error:
        return {"state": "failed", "tag": UNKNOWN_TAG, "step": "prepare", "detail": str(error)[-1500:]}


def pull_request_body(state: Mapping[str, Any], run_url: str) -> str:
    return (
        f"Refreshes the fork onto upstream SQLGlot {state['tag']} ({state['upstream_sha']}), the newest final tag, "
        f"with `python -m downstream.refresh auto`. All {state['entries']} retained carried and original entries "
        f"apply without conflicts and none is retired; `downstream/patches.json` records version {state['version']}.\n\n"
        "The fork's checks run on this pull request. Review it as any refresh (`downstream/MAINTAINING.md`, "
        "Upstream refresh). After it merges, build the wheel from the merged `main`, verify it, and adopt it in "
        "BenchBox with `make sqlglot-adopt-wheel`.\n\n"
        f"Opened by the scheduled refresh workflow: {run_url}\n"
    )


def authorization(token: str) -> dict[str, str]:
    encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {encoded}",
    }


def close_superseded(runner: Runner, branch: str, fork: str, tag: str) -> list[int]:
    superseded = api_items(
        runner,
        f"repos/{fork}/pulls?state=open&per_page=100",
        f'.[] | select(.head.ref | startswith("{BRANCH_PREFIX}")) | select(.head.ref != "{branch}") | {{number}}',
    )
    for pull in superseded:
        number = str(pull["number"])
        checked(
            runner(
                ["gh", "pr", "comment", number, "-R", fork, "--body", f"Superseded by the refresh to {tag}."],
                None,
            )
        )
        checked(runner(["gh", "pr", "close", number, "-R", fork], None))
    return [pull["number"] for pull in superseded]


def open_pull_request(
    clone: Path,
    out_dir: Path,
    token: str,
    run_url: str,
    runner: Runner = run,
    fork: str = FORK,
) -> dict[str, Any]:
    state = json.loads((out_dir / STATE_FILE).read_text(encoding="utf-8"))
    if not token:
        return {"status": "unpushed", "reason": "the SQLGLOT_FORK_REFRESH_TOKEN secret is not configured"}
    branch = state["branch"]
    checked(
        runner(
            ["git", "fetch", str(out_dir / BUNDLE_FILE), f"refs/heads/{branch}:refs/heads/{branch}"],
            clone,
        )
    )
    checked(runner(["git", "push", "origin", branch], clone, authorization(token)))
    body = out_dir / "pull-request.md"
    body.write_text(pull_request_body(state, run_url), encoding="utf-8")
    created = checked(
        runner(
            [
                "gh",
                "api",
                "--method",
                "POST",
                f"repos/{fork}/pulls",
                "-f",
                f"title=chore: refresh SQLGlot from upstream {state['tag']}",
                "-F",
                f"body=@{body}",
                "-f",
                "base=main",
                "-f",
                f"head={branch}",
                "--jq",
                ".html_url",
            ],
            None,
        )
    )
    closed = close_superseded(runner, branch, fork, state["tag"])
    return {"status": "opened", "url": created.stdout.strip(), "superseded": closed}


def issue_body(state: Mapping[str, Any], opened: Mapping[str, Any], run_url: str) -> str:
    tag = state["tag"]
    kind = state["state"]
    if kind == "conflict":
        what = (
            f"Carried patch `{state['entry']}` does not apply to upstream {tag}"
            f" (conflicting files: {', '.join(f'`{p}`' for p in state['paths']) or 'none reported'})."
        )
    elif kind == "failed":
        what = f"The automatic refresh to {tag} stopped at `{state['step']}`:\n\n```\n{state['detail']}\n```"
    elif kind == "ci-failing":
        what = (
            f"The refresh pull request {state['pull_request']} has failing checks: "
            + ", ".join(f"`{name}`" for name in state["failing"])
            + "."
        )
    elif kind == "declined":
        what = (
            f"The refresh pull request {state['pull_request']} was closed without merging, and the fork is still on "
            f"upstream {state['current']}."
        )
    else:
        reason = opened.get("reason", "the pull request job did not complete")
        what = (
            f"Every carried patch applies to upstream {tag}, but the refresh was not pushed: {reason}. "
            f"`python -m downstream.refresh auto --tag {tag}` in a clean fork checkout reproduces the branch."
        )
    steps = (
        "Resolve it by hand following `downstream/MAINTAINING.md` (Upstream refresh) in "
        f"https://github.com/{FORK}, merge the fork pull request, then adopt the wheel with "
        "`make sqlglot-adopt-wheel`. "
        "`scripts/check_sqlglot_upstream_freshness.py` fails every pull request until the bundled runtime is on "
        f"{tag} or a reviewed waiver is recorded."
    )
    return f"{what}\n\n{steps}\n\nLatest scheduled run: {run_url}\n"


def signature(state: Mapping[str, Any], opened: Mapping[str, Any]) -> str:
    material = json.dumps(
        {key: state.get(key) for key in ("state", "tag", "entry", "paths", "step", "failing")}
        | {"reason": opened.get("reason")},
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def open_issues(runner: Runner) -> list[dict[str, Any]]:
    result = checked(
        runner(
            [
                "gh",
                "issue",
                "list",
                "--state",
                "open",
                "--limit",
                "100",
                "--search",
                f'"{ISSUE_PREFIX}" in:title',
                "--json",
                "number,title,body",
            ],
            None,
        )
    )
    return [issue for issue in json.loads(result.stdout) if issue["title"].startswith(ISSUE_PREFIX)]


def sync_issue(out_dir: Path, run_url: str, runner: Runner = run) -> dict[str, Any]:
    state = json.loads((out_dir / STATE_FILE).read_text(encoding="utf-8"))
    open_file = out_dir / OPEN_FILE
    opened = json.loads(open_file.read_text(encoding="utf-8")) if open_file.is_file() else {}
    kind = state["state"]
    if kind == "ready":
        kind = "awaiting-review" if opened.get("status") == "opened" else "unpushed"
        state = {**state, "state": kind}
    wanted = issue_title(state["tag"]) if kind in ISSUE_STATES else None
    issues = open_issues(runner)
    outcome: dict[str, Any] = {"state": kind, "closed": [], "action": "none"}
    for issue in issues:
        if issue["title"] != wanted:
            reason = f"Superseded or resolved: the scheduled refresh now reports `{kind}` for {state['tag']}."
            checked(runner(["gh", "issue", "close", str(issue["number"]), "--comment", reason], None))
            outcome["closed"].append(issue["number"])
    if wanted is None:
        return outcome
    body = issue_body(state, opened, run_url)
    marker = f"{SIGNATURE_MARKER}{signature(state, opened)} -->"
    current = next((issue for issue in issues if issue["title"] == wanted), None)
    if current is None:
        checked(runner(["gh", "issue", "create", "--title", wanted, "--body", f"{body}\n{marker}\n"], None))
        outcome["action"] = "created"
    elif marker not in current["body"]:
        number = str(current["number"])
        checked(runner(["gh", "issue", "edit", number, "--body", f"{body}\n{marker}\n"], None))
        checked(runner(["gh", "issue", "comment", number, "--body", f"The reason changed.\n\n{body}"], None))
        outcome["action"] = "updated"
    return outcome


def write_output(name: str, value: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("prepare", "open-pr", "sync-issue"):
        sub = commands.add_parser(action)
        sub.add_argument("--out-dir", type=Path, required=True)
        if action != "sync-issue":
            sub.add_argument("--clone", type=Path, required=True)
        if action != "prepare":
            sub.add_argument("--run-url", required=True)
    args = parser.parse_args(argv)
    if args.action == "prepare":
        state = prepare_or_report(args.clone, args.out_dir)
        (args.out_dir / STATE_FILE).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        write_output("state", state["state"])
        print(json.dumps(state, indent=2))
    elif args.action == "open-pr":
        opened = open_pull_request(args.clone, args.out_dir, os.environ.get("FORK_TOKEN", ""), args.run_url)
        (args.out_dir / OPEN_FILE).write_text(json.dumps(opened, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(opened, indent=2))
    else:
        print(json.dumps(sync_issue(args.out_dir, args.run_url), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
