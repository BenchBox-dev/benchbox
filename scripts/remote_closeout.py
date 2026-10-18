#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import batch_integration
import pr_landing

CLI_DESCRIPTION = "Bind, record, arm, withdraw, and re-arm a cloud-built pull request from fetched objects alone."

REPOSITORY = "BenchBox-dev/BenchBox"
BASE_BRANCH = "develop"
STORE_SCHEMA = "remote_closeout_v1"
STORE_ENV = "BENCHBOX_REVIEWS_DIR"
ENV_PREFIX = "REMOTE_CLOSEOUT_"
TRACKER_CONFIG = pr_landing.REPO_ROOT / ".todo-db" / "config.json"
FETCH = ["git", "-c", "gc.auto=0", "-c", "maintenance.auto=false", "fetch", "--no-tags", "origin"]
VIEW_FIELDS = "number,state,isDraft,labels,reviewDecision,headRefOid,headRefName,baseRefName,id,isCrossRepository"
OBSERVED_FIELDS = ("labels", "draft", "threads")
DISPOSITIONS = ("approve", "changes-requested")
FINDING_FIELDS = ("location", "reasoning", "severity")
REVIEWER_FIELDS = ("model", "run_id")
PASSING_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})
PR_RE = re.compile(r"[1-9][0-9]*")
DIGEST_RE = re.compile(r"[0-9a-f]{64}")
BRANCH_RE = re.compile(r"[A-Za-z0-9._/-]+")

Runner = pr_landing.Runner
LandingError = pr_landing.LandingError


class Refused(LandingError):
    pass


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _sha(value: object) -> bool:
    return isinstance(value, str) and pr_landing.FULL_REVISION_RE.fullmatch(value) is not None


def _text_object(value: object, fields: Sequence[str]) -> bool:
    return isinstance(value, dict) and set(value) == set(fields) and all(_text(value[field]) for field in fields)


RECORD_CHECKS: dict[str, tuple[Callable[[object], bool], str]] = {
    "pr_number": (lambda v: isinstance(v, int) and not isinstance(v, bool) and v > 0, "a positive integer"),
    "head_branch": (_text, "a non-empty string"),
    "pr_node_id": (_text, "a non-empty string"),
    "base_sha": (_sha, "a full lowercase commit SHA"),
    "head_sha": (_sha, "a full lowercase commit SHA"),
    "manifest_digest": (
        lambda v: isinstance(v, str) and DIGEST_RE.fullmatch(v) is not None,
        "a lowercase SHA-256 hex digest",
    ),
    "findings": (
        lambda v: isinstance(v, list) and all(_text_object(finding, FINDING_FIELDS) for finding in v),
        "a list of objects with exactly non-empty 'location', 'reasoning', and 'severity'",
    ),
    "disposition": (lambda v: v in DISPOSITIONS, "'approve' or 'changes-requested'"),
    "reviewer": (lambda v: _text_object(v, REVIEWER_FIELDS), "an object with exactly non-empty 'model' and 'run_id'"),
}
BINDING_FIELDS = (("pr_number", "pr_number"), ("head_branch", "head_branch"), ("pr_node_id", "node_id"))


def live_run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, timeout=300, check=False)
    stdout = proc.stdout.decode("utf-8", "surrogateescape")
    if proc.returncode == 0:
        return 0, stdout
    return proc.returncode, stdout + proc.stderr.decode("utf-8", "surrogateescape")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


def _short(value: object) -> str:
    return str(value)[:12]


def _repository(repo: str | None) -> str:
    try:
        return pr_landing.normalize_github_repository(repo)
    except LandingError as exc:
        raise Refused(str(exc)) from exc


def _pr_number(value: str | None) -> int:
    if value is None or not PR_RE.fullmatch(value):
        raise Refused(f"PR must be a plain PR number, got {value!r}")
    return int(value)


def _revision(value: str | None, label: str) -> str:
    if not _sha(value):
        raise Refused(f"{label} must be a full lowercase commit SHA, got {value!r}")
    return str(value)


def store_path(repo: str, number: int) -> Path:
    override = os.environ.get(STORE_ENV)
    root = Path(override).expanduser() if override else Path.home() / ".benchbox" / "reviews"
    owner, name = pr_landing.normalize_github_repository(repo).split("/")
    return root / owner / name / f"pr-{number}.json"


def _load_store(path: Path, repo: str, number: int) -> dict:
    identity = {"schema": STORE_SCHEMA, "repository": repo, "pr_number": number}
    if not path.exists():
        return {**identity, "binding": None, "records": [], "withdrawal": None, "arms": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LandingError(f"unreadable review store {path}: {exc}") from exc
    if not isinstance(data, dict) or any(data.get(key) != value for key, value in identity.items()):
        raise LandingError(f"{path} is not the review store for {repo}#{number}")
    return data


@contextmanager
def _locked_store(repo: str, number: int, wait: float | None = None) -> Iterator[tuple[Path, dict]]:
    import fcntl

    path = store_path(repo, number)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(f"{path.name}.lock").open("a", encoding="utf-8") as lock:
        deadline = None if wait is None else time.monotonic() + wait
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX if deadline is None else fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if deadline is not None and time.monotonic() >= deadline:
                    raise Refused(
                        f"another close-out command held the review store for PR #{number} for over {wait} seconds"
                    ) from exc
                time.sleep(0.2)
        yield path, _load_store(path, repo, number)


def _view(run: Runner, repo: str, number: int, fields: str = VIEW_FIELDS) -> dict:
    code, out = run(["gh", "pr", "view", str(number), "--repo", repo, "--json", fields])
    if code != 0:
        raise LandingError(f"cannot read PR #{number}: {out.strip()[:200]}")
    view = json.loads(out)
    if not isinstance(view, dict):
        raise LandingError(f"gh pr view returned a non-object payload for PR #{number}")
    return view


def observe(run: Runner, repo: str, view: dict) -> dict:
    threads = [
        {
            "id": str(thread.get("id") or ""),
            "resolved": bool(thread.get("isResolved")),
            "outdated": bool(thread.get("isOutdated")),
        }
        for thread in pr_landing.review_threads(run, repo, int(view["number"]))
        if isinstance(thread, dict)
    ]
    labels = {str(label.get("name") or "") for label in view.get("labels") or [] if isinstance(label, dict)}
    return {
        "labels": sorted(labels),
        "draft": bool(view.get("isDraft")),
        "threads": sorted(threads, key=lambda thread: thread["id"]),
    }


def _identity_refusals(binding: dict | None, view: dict, number: int) -> list[str]:
    if binding is None:
        return [f"condition 2: no binding is recorded for PR #{number}; bind it when the builder's report names it"]
    live = {"pr_number": view.get("number"), "head_branch": view.get("headRefName"), "node_id": view.get("id")}
    return [
        f"condition 2: the live PR {field.replace('_', ' ')} {live[field]!r} is not the bound {binding.get(field)!r}"
        for _, field in BINDING_FIELDS
        if binding.get(field) != live[field]
    ]


def _require_origin(run: Runner, repo: str) -> None:
    code, out = run(["git", "remote", "get-url", "origin"])
    if code != 0:
        raise LandingError(f"cannot read the origin remote: {out.strip()[:200]}")
    origin = pr_landing.github_repository(out.strip())
    if origin != repo:
        raise Refused(f"origin is {origin}, not {repo}; fetch from the repository the PR belongs to")


def _rev_parse(run: Runner, name: str) -> str:
    code, out = run(["git", "rev-parse", name])
    value = out.strip()
    if code != 0 or not _sha(value):
        raise LandingError(f"git rev-parse {name} failed: {value[:200]}")
    return value


def _fetch(run: Runner, refspec: str) -> str:
    code, out = run([*FETCH, refspec])
    if code != 0:
        raise LandingError(f"git fetch {refspec} failed: {out.strip()[:200]}")
    return _rev_parse(run, "FETCH_HEAD")


def fetch_pr_head(run: Runner, repo: str, number: int) -> str:
    _require_origin(run, repo)
    code, out = run(["gh", "pr", "view", str(number), "--repo", repo, "--json", "headRefOid", "--jq", ".headRefOid"])
    reported = out.strip()
    if code != 0 or not _sha(reported):
        raise LandingError(f"cannot read the head GitHub reports for PR #{number}: {reported[:200]}")
    fetched = _fetch(run, f"refs/pull/{number}/head")
    if fetched != reported:
        raise Refused(
            f"condition 4: fetched {_short(fetched)} for PR #{number} but GitHub reports {_short(reported)}; stopping"
        )
    return reported


def fetch_develop(run: Runner) -> str:
    fetched = _fetch(run, f"refs/heads/{BASE_BRANCH}")
    tracking = _rev_parse(run, f"refs/remotes/origin/{BASE_BRANCH}")
    if fetched != tracking:
        raise Refused(
            f"condition 4: FETCH_HEAD {_short(fetched)} is not origin/{BASE_BRANCH} {_short(tracking)} after the "
            "fetch; another fetch overwrote it, so retry"
        )
    return fetched


def pin_manifest(run: Runner, head: str, develop: str) -> tuple[str, str]:
    code, out = run(["git", "merge-base", head, develop])
    base = out.strip()
    if code != 0 or not _sha(base):
        raise LandingError(f"git merge-base {_short(head)} {_short(develop)} failed: {base[:200]}")
    code, out = run(["git", "diff", "--raw", "--no-renames", "--no-abbrev", base, head])
    if code != 0:
        raise LandingError(f"git diff --raw {_short(base)} {_short(head)} failed: {out.strip()[:200]}")
    return base, out


def manifest_digest(listing: str) -> str:
    return hashlib.sha256(listing.encode("utf-8", "surrogateescape")).hexdigest()


def compute_manifest(run: Runner, head: str, develop: str) -> tuple[str, str]:
    base, listing = pin_manifest(run, head, develop)
    return base, manifest_digest(listing)


def _tracker_file(run: Runner, name: str, config_path: Path) -> object:
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        state_repo = pr_landing.github_repository(config["state_remote"])
        branch = config["state_branch"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise LandingError(f"cannot read the tracker configuration {config_path}: {exc}") from exc
    if not isinstance(branch, str) or not BRANCH_RE.fullmatch(branch):
        raise LandingError(f"tracker state branch {branch!r} is malformed")
    code, out = run(
        [
            "gh",
            "api",
            "-H",
            "Accept: application/vnd.github.raw+json",
            f"repos/{state_repo}/contents/{name}?ref={branch}",
        ]
    )
    if code != 0:
        raise LandingError(f"cannot read the tracker's {name} from {state_repo}@{branch}: {out.strip()[:200]}")
    try:
        return json.loads(out)
    except ValueError as exc:
        raise LandingError(f"the tracker's {name} on {state_repo}@{branch} is not JSON: {exc}") from exc


def tracker_batches(run: Runner, config_path: Path = TRACKER_CONFIG) -> dict:
    index = _tracker_file(run, "index.json", config_path)
    batches = index.get("batches", {}) if isinstance(index, dict) else None
    if not isinstance(batches, dict):
        raise LandingError("the tracker index has no readable batch registry")
    return batches


def tracker_item(run: Runner, item: str, config_path: Path = TRACKER_CONFIG) -> dict:
    if len(item) > pr_landing.MAX_TODO_ID_LEN or not pr_landing.TODO_ID_RE.fullmatch(item):
        raise LandingError(f"the bound item {item!r} is not a tracker item ID")
    data = _tracker_file(run, f"items/{item}.json", config_path)
    description = data.get("description") if isinstance(data, dict) else None
    acceptance = data.get("acceptance", []) if isinstance(data, dict) else None
    if not isinstance(description, str) or not isinstance(acceptance, list) or not all(map(_text, acceptance)):
        raise LandingError(f"tracker item {item!r} has no readable description and acceptance criteria")
    return {"id": item, "description": description, "acceptance": acceptance}


def _same_repository(value: object, repo: str) -> bool:
    for parse in (pr_landing.normalize_github_repository, pr_landing.github_repository):
        try:
            return parse(value) == repo
        except LandingError:
            continue
    return True


def batch_refusals(batches: Mapping[str, object], repo: str, view: dict, item: str | None) -> list[str]:
    reasons: list[str] = []
    for batch_id, batch in sorted(batches.items()):
        if not isinstance(batch, dict):
            reasons.append(f"condition 1: tracker batch {batch_id!r} is unreadable")
            continue
        if not _same_repository(batch.get("repository"), repo):
            continue
        final = batch.get("final_pr") if isinstance(batch.get("final_pr"), dict) else {}
        members = batch.get("members") if isinstance(batch.get("members"), list) else []
        if batch.get("integration_branch") == view.get("headRefName"):
            reasons.append(f"condition 1: batch PR: its head branch is the integration branch of batch {batch_id!r}")
        if final and (final.get("number") == view.get("number") or final.get("node_id") == view.get("id")):
            reasons.append(f"condition 1: batch PR: it is the final PR of batch {batch_id!r}")
        if item and item in members and batch.get("lifecycle", "active") != "aborted":
            reasons.append(f"condition 1: batch PR: its tracker item {item!r} is a member of batch {batch_id!r}")
    return reasons


def _worktree_batch_id(run: Runner, path: str) -> str | None:
    code, out = run(["git", "-C", path, "config", "--worktree", "--get", f"{batch_integration.CONFIG_PREFIX}.id"])
    if code == 1:
        return None
    if code != 0:
        raise LandingError(f"cannot read the batch configuration of worktree {path}: {out.strip()[:200]}")
    return out.strip() or None


def feature_worktree_refusals(run: Runner, branch: str) -> list[str]:
    code, out = run(["git", "worktree", "list", "--porcelain"])
    if code != 0:
        raise LandingError(f"git worktree list failed: {out.strip()[:200]}")
    reasons: list[str] = []
    path: str | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line.removeprefix("worktree ")
        elif line == f"branch refs/heads/{branch}" and path is not None:
            batch_id = _worktree_batch_id(run, path)
            if batch_id:
                reasons.append(
                    f"condition 1: feature-mode PR: worktree {path} records batch {batch_id!r} on its branch"
                )
    return reasons


def _stacked_children(run: Runner, repo: str, branch: str) -> list[int]:
    code, out = run(["gh", "pr", "list", "--repo", repo, "--base", branch, "--state", "open", "--json", "number"])
    if code != 0:
        raise LandingError(f"cannot list PRs based on {branch!r}: {out.strip()[:200]}")
    return [int(entry["number"]) for entry in json.loads(out)]


def _eligibility_refusals(
    run: Runner, repo: str, checkout: Path, view: dict, item: str | None, head: str, manifest: tuple[str, str] | None
) -> list[str]:
    branch = str(view.get("headRefName") or "")
    reasons: list[str] = []
    if view.get("isCrossRepository") is not False:
        reasons.append("condition 1: fork PR: its head is not a branch of this repository, where cloud builders push")
    if view.get("baseRefName") != BASE_BRANCH:
        reasons.append(f"condition 1: stacked PR: it targets {view.get('baseRefName')!r}, not {BASE_BRANCH!r}")
    reasons.extend(
        f"condition 1: stacked PR: open PR #{child} is based on its head branch {branch!r}"
        for child in _stacked_children(run, repo, branch)
    )
    reasons.extend(batch_refusals(tracker_batches(run), repo, view, item))
    reasons.extend(feature_worktree_refusals(run, branch))
    if manifest is not None and pr_landing.soundness_paths_changed(checkout, manifest[0], head):
        reasons.append(
            "condition 1: the fetched base..head diff touches a soundness path; it closes out under the soundness rules"
        )
    return reasons


def _record_refusals(record: dict | None, view: dict, head: str) -> list[str]:
    reasons: list[str] = []
    if view.get("headRefOid") != head:
        reasons.append(f"condition 3: the live PR head {_short(view.get('headRefOid'))} is not HEAD {_short(head)}")
    if record is None:
        return [*reasons, "condition 3: no review record is stored for this PR"]
    if record["head_sha"] != head:
        reasons.append(f"condition 3: the latest record reviewed {_short(record['head_sha'])}, not HEAD {_short(head)}")
    if record["disposition"] != "approve":
        reasons.append(f"condition 3: the latest record's disposition is {record['disposition']!r}, not 'approve'")
    if record.get("published") is False:
        reasons.append(
            "condition 3: the latest record's findings are not all published; "
            "run make pr-landing-remote-review again to finish"
        )
    return reasons


def _manifest_refusals(record: dict | None, manifest: tuple[str, str] | None, head: str) -> list[str]:
    if record is None:
        return []
    if manifest is None:
        return [f"condition 4: the fetched head is not HEAD {_short(head)}, so nothing can be recomputed"]
    base, digest = manifest
    reasons: list[str] = []
    if record["base_sha"] != base:
        reasons.append(
            f"condition 4: the recomputed merge-base {_short(base)} is not the record's {_short(record['base_sha'])}"
        )
    if record["manifest_digest"] != digest:
        reasons.append(
            f"condition 4: the recomputed manifest digest {_short(digest)} is not the record's "
            f"{_short(record['manifest_digest'])}"
        )
    return reasons


def _builder_refusals(record: dict | None, view: dict, head: str) -> list[str]:
    if record is None:
        return []
    reported = record["builder_head"]
    if reported != head:
        return [f"condition 5: the builder's latest report names {_short(reported)}, not HEAD {_short(head)}"]
    if view.get("headRefOid") != reported:
        return [f"condition 5: a push followed the builder's report; the live head is {_short(view.get('headRefOid'))}"]
    return []


def failing_required_checks(verdicts: Mapping[str, dict]) -> list[tuple[str, str]]:
    failing: list[tuple[str, str]] = []
    for name in pr_landing.REQUIRED_CONTEXTS:
        conclusion = (verdicts.get(name) or {}).get("conclusion")
        if conclusion and conclusion not in PASSING_CONCLUSIONS:
            failing.append((name, str(conclusion)))
    return failing


def _readiness_refusals(view: dict, observation: dict, verdicts: Mapping[str, dict]) -> list[str]:
    reasons: list[str] = []
    if view.get("state") != "OPEN":
        reasons.append(f"condition 6: the PR is {view.get('state')!r}, not OPEN")
    if pr_landing.HOLD_LABEL in observation["labels"]:
        reasons.append(f"condition 6: the {pr_landing.HOLD_LABEL!r} label is present")
    if observation["draft"]:
        reasons.append("condition 6: the PR is a draft")
    if view.get("baseRefName") != BASE_BRANCH:
        reasons.append(f"condition 6: the PR targets {view.get('baseRefName')!r}, not {BASE_BRANCH!r}")
    if view.get("reviewDecision") == "CHANGES_REQUESTED":
        reasons.append("condition 6: a reviewer requested changes")
    if any(not thread["resolved"] and not thread["outdated"] for thread in observation["threads"]):
        reasons.append("condition 6: an unresolved, non-outdated review thread is open")
    reasons.extend(
        f"condition 6: required check {name!r} failed with conclusion {conclusion!r}; "
        "pending checks do not block arming"
        for name, conclusion in failing_required_checks(verdicts)
    )
    return reasons


def _describe(field: str, value: object) -> str:
    if field != "threads" or not isinstance(value, list):
        return repr(value)
    open_threads = sum(1 for thread in value if not thread.get("resolved") and not thread.get("outdated"))
    return f"{len(value)} threads, {open_threads} open"


def observation_changes(record: dict | None, observation: dict) -> list[str]:
    if record is None:
        return []
    recorded = record["observation"]
    return [
        f"{field} changed from {_describe(field, recorded.get(field))} to {_describe(field, observation[field])}"
        for field in OBSERVED_FIELDS
        if recorded.get(field) != observation[field]
    ]


def evaluate(
    run: Runner, repo: str, checkout: Path, number: int, head: str, state: dict, explained: str | None
) -> tuple[list[str], list[str]]:
    binding = state.get("binding")
    record = state["records"][-1] if state["records"] else None
    view = _view(run, repo, number)
    observation = observe(run, repo, view)
    fetched = fetch_pr_head(run, repo, number)
    develop = fetch_develop(run)
    manifest = compute_manifest(run, head, develop) if fetched == head else None
    changes = observation_changes(record, observation)
    reasons = [
        *_eligibility_refusals(run, repo, checkout, view, binding.get("item") if binding else None, head, manifest),
        *_identity_refusals(binding, view, number),
        *_record_refusals(record, view, head),
        *_manifest_refusals(record, manifest, head),
        *_builder_refusals(record, view, head),
        *_readiness_refusals(view, observation, pr_landing.live_check_verdicts(run, repo, head)),
    ]
    if changes and not explained:
        reasons.extend(f"condition 7: {change}; pass OWNER_EXPLAINED if the owner explained it" for change in changes)
    return reasons, changes


def _transition_refusals(state: dict, *, ready: bool) -> list[str]:
    withdrawal = state.get("withdrawal")
    if not ready:
        if withdrawal:
            return ["the PR is withdrawn; re-arm it with make pr-landing-remote-ready"]
        if state["arms"]:
            return [
                "the PR was already armed; withdraw it with make pr-landing-remote-withdraw, "
                "then use make pr-landing-remote-ready"
            ]
        return []
    if not withdrawal:
        return ["no withdrawal is recorded; ready re-arms only a PR that make pr-landing-remote-withdraw withdrew"]
    if len(state["records"]) <= withdrawal["records_before"]:
        return ["no review record was stored after the withdrawal; the new head needs a new record"]
    return []


def _arm(
    pr: str | None,
    head: str | None,
    repo: str | None,
    owner_explained: str | None,
    run: Runner,
    checkout: Path,
    *,
    ready: bool,
) -> int:
    repo = _repository(repo)
    number = _pr_number(pr)
    head = _revision(head, "HEAD")
    explained = (owner_explained or "").strip() or None
    with _locked_store(repo, number) as (path, state):
        reasons = _transition_refusals(state, ready=ready)
        found, changes = evaluate(run, repo, checkout, number, head, state, explained)
        reasons.extend(found)
        if reasons:
            raise Refused("\n".join(reasons))
        code, out = run(
            ["gh", "pr", "merge", str(number), "--repo", repo, "--squash", "--auto", "--match-head-commit", head]
        )
        print(out.strip())
        if code != 0:
            return code
        state["arms"].append(
            {
                "head": head,
                "armed_at": _now(),
                "via": "ready" if ready else "arm",
                "record_index": len(state["records"]) - 1,
                "observation_changes": changes,
                "owner_explained": explained,
                "cleared_withdrawal": state.get("withdrawal"),
            }
        )
        state["withdrawal"] = None
        pr_landing._write_json_atomic(path, state)
    return 0


def _withdraw(pr: str | None, repo: str | None, run: Runner) -> int:
    repo = _repository(repo)
    number = _pr_number(pr)
    with _locked_store(repo, number) as (path, state):
        binding = state.get("binding")
        if binding is None:
            raise Refused(f"no binding is recorded for PR #{number}; there is nothing this closer armed")
        try:
            result = pr_landing.withdraw_readiness(
                run, repo, number, expected_branch=binding["head_branch"], expected_node_id=binding["node_id"]
            )
        except pr_landing.WrongPR as exc:
            raise Refused(str(exc)) from exc
        state["withdrawal"] = {
            "withdrawn_at": _now(),
            "records_before": len(state["records"]),
            "result": result["withdrew"],
        }
        pr_landing._write_json_atomic(path, state)
    print(json.dumps(result))
    return 0


def _bind(pr: str | None, branch: str | None, item: str | None, repo: str | None, run: Runner) -> int:
    repo = _repository(repo)
    number = _pr_number(pr)
    if not branch:
        raise Refused("BRANCH is required: the head branch the builder's report names")
    if item and (len(item) > pr_landing.MAX_TODO_ID_LEN or not pr_landing.TODO_ID_RE.fullmatch(item)):
        raise Refused(f"ITEM must be a tracker item ID, got {item!r}")
    view = _view(run, repo, number)
    if view.get("number") != number or view.get("headRefName") != branch:
        raise Refused(f"PR #{number} has head branch {view.get('headRefName')!r}, not the reported {branch!r}")
    if not _text(view.get("id")):
        raise LandingError(f"PR #{number} has no node ID")
    binding = {"pr_number": number, "head_branch": branch, "node_id": view["id"], "item": item or None}
    with _locked_store(repo, number) as (path, state):
        existing = state.get("binding")
        if existing is not None:
            if {key: existing.get(key) for key in binding} != binding:
                raise Refused(f"PR #{number} is already bound to {existing!r}; a binding never changes")
            print(f"PR #{number} is already bound in {path}")
            return 0
        state["binding"] = {**binding, "recorded_at": _now()}
        pr_landing._write_json_atomic(path, state)
    print(f"bound PR #{number} ({branch}, {view['id']}) in {path}")
    if not item:
        print("no ITEM was given, so the tracker batch-member check will be skipped for this PR")
    return 0


def record_problems(record: object) -> list[str]:
    if not isinstance(record, dict):
        return ["the record must be a JSON object"]
    problems = [f"missing field {field!r}" for field in RECORD_CHECKS if field not in record]
    problems.extend(f"unknown field {field!r}" for field in sorted(set(record) - set(RECORD_CHECKS)))
    problems.extend(
        f"{field} must be {expected}"
        for field, (valid, expected) in RECORD_CHECKS.items()
        if field in record and not valid(record[field])
    )
    return problems


def _read_record(record_file: str | None) -> dict:
    if not record_file:
        raise Refused("RECORD is required: the path of the reviewer's record JSON")
    try:
        record = json.loads(Path(record_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(f"cannot read the record {record_file}: {exc}") from exc
    problems = record_problems(record)
    if problems:
        raise Refused("\n".join(problems))
    return record


def _store_record(run: Runner, repo: str, number: int, state: dict, record: dict, builder_head: str) -> None:
    binding = state.get("binding")
    if binding is None:
        raise Refused(f"no binding is recorded for PR #{number}; bind it before storing a record")
    disagreements = [field for field, bound in BINDING_FIELDS if record[field] != binding.get(bound)]
    if record["pr_number"] != number:
        disagreements.append("pr_number")
    if disagreements:
        raise Refused(
            f"the record's {', '.join(sorted(set(disagreements)))} disagree with the binding for PR #{number}"
        )
    open_findings = [
        entry["id"]
        for entry in state.get("review_findings") or []
        if isinstance(entry, dict) and entry["status"] == "open"
    ]
    if record["disposition"] == "approve" and open_findings:
        raise Refused(
            f"an approve record cannot be stored while blocking findings {', '.join(open_findings)} are open; "
            "run make pr-landing-remote-review"
        )
    view = _view(run, repo, number)
    live_identity = _identity_refusals(binding, view, number)
    if live_identity:
        raise Refused("\n".join(live_identity))
    state["records"].append(
        {
            **record,
            "builder_head": builder_head,
            "recorded_at": _now(),
            "observed_head": view.get("headRefOid"),
            "observation": observe(run, repo, view),
        }
    )


def _record(pr: str | None, record_file: str | None, builder_head: str | None, repo: str | None, run: Runner) -> int:
    repo = _repository(repo)
    number = _pr_number(pr)
    builder_head = _revision(builder_head, "BUILDER_HEAD")
    record = _read_record(record_file)
    with _locked_store(repo, number) as (path, state):
        _store_record(run, repo, number, state, record, builder_head)
        pr_landing._write_json_atomic(path, state)
    print(f"stored a {record['disposition']} record for PR #{number} at {_short(record['head_sha'])} in {path}")
    return 0


def _manifest(pr: str | None, head: str | None, repo: str | None, run: Runner) -> int:
    repo = _repository(repo)
    number = _pr_number(pr)
    head = _revision(head, "HEAD")
    fetched = fetch_pr_head(run, repo, number)
    if fetched != head:
        raise Refused(f"GitHub reports head {_short(fetched)} for PR #{number}, not HEAD {_short(head)}")
    base, digest = compute_manifest(run, head, fetch_develop(run))
    print(json.dumps({"pr_number": number, "head_sha": head, "base_sha": base, "manifest_digest": digest}, indent=2))
    return 0


def _guarded(name: str, action: Callable[[], int]) -> int:
    try:
        return action()
    except Refused as exc:
        for line in str(exc).splitlines():
            print(f"{name}: refusing: {line}", file=sys.stderr)
        return 2
    except (LandingError, subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"{name}: refusing, live state could not be verified: {exc}", file=sys.stderr)
        return 1


def bind(
    pr: str | None, branch: str | None, item: str | None = None, repo: str = REPOSITORY, run: Runner = live_run
) -> int:
    return _guarded("pr-landing-remote-bind", lambda: _bind(pr, branch, item, repo, run))


def record(
    pr: str | None, record_file: str | None, builder_head: str | None, repo: str = REPOSITORY, run: Runner = live_run
) -> int:
    return _guarded("pr-landing-remote-record", lambda: _record(pr, record_file, builder_head, repo, run))


def manifest(pr: str | None, head: str | None, repo: str = REPOSITORY, run: Runner = live_run) -> int:
    return _guarded("pr-landing-remote-manifest", lambda: _manifest(pr, head, repo, run))


def arm(
    pr: str | None,
    head: str | None,
    repo: str = REPOSITORY,
    owner_explained: str | None = None,
    run: Runner = live_run,
    checkout: Path = Path("."),
) -> int:
    return _guarded("pr-arm-remote", lambda: _arm(pr, head, repo, owner_explained, run, checkout, ready=False))


def withdraw(pr: str | None, repo: str = REPOSITORY, run: Runner = live_run) -> int:
    return _guarded("pr-landing-remote-withdraw", lambda: _withdraw(pr, repo, run))


def ready(
    pr: str | None,
    head: str | None,
    repo: str = REPOSITORY,
    owner_explained: str | None = None,
    run: Runner = live_run,
    checkout: Path = Path("."),
) -> int:
    return _guarded("pr-landing-remote-ready", lambda: _arm(pr, head, repo, owner_explained, run, checkout, ready=True))


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    environment = os.environ if env is None else env

    def selected(name: str) -> str | None:
        return environment.get(f"{ENV_PREFIX}{name}") or None

    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--repo", default=selected("REPO") or REPOSITORY)
    commands = parser.add_subparsers(dest="command", required=True)
    options = {
        "bind": ("PR", "BRANCH", "ITEM"),
        "record": ("PR", "RECORD", "BUILDER_HEAD"),
        "manifest": ("PR", "HEAD"),
        "arm": ("PR", "HEAD", "OWNER_EXPLAINED"),
        "withdraw": ("PR",),
        "ready": ("PR", "HEAD", "OWNER_EXPLAINED"),
    }
    for command, names in options.items():
        sub = commands.add_parser(command)
        for name in names:
            sub.add_argument(f"--{name.lower().replace('_', '-')}", default=selected(name))
    args = parser.parse_args(argv)
    if args.command == "bind":
        return bind(args.pr, args.branch, args.item, args.repo)
    if args.command == "record":
        return record(args.pr, args.record, args.builder_head, args.repo)
    if args.command == "manifest":
        return manifest(args.pr, args.head, args.repo)
    if args.command == "withdraw":
        return withdraw(args.pr, args.repo)
    action = arm if args.command == "arm" else ready
    return action(args.pr, args.head, args.repo, args.owner_explained)


if __name__ == "__main__":
    raise SystemExit(main())
