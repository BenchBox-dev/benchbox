#!/usr/bin/env python3

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

CLI_DESCRIPTION = (
    "Fail-closed exact-refresh certification classifier.\n"
    "\n"
    "Evidence generation only. This module never skips a CI job and never publishes\n"
    "a required status context. Callers inject event, Git, ruleset, Check Run, and\n"
    "Actions-run data so unit tests do not touch the network.\n"
    "\n"
    "Version 1 permits at most one shadow-eligible refresh after a trusted full\n"
    "certification. Missing, stale, ambiguous, or untrusted input returns\n"
    "``full_required``.\n"
)

DECISION_SHADOW = "shadow_eligible"
DECISION_FULL = "full_required"

REASON_NOT_SYNCHRONIZE = "not_synchronize"
REASON_MISSING_EVENT_SHA = "missing_event_sha"
REASON_AFTER_HEAD_MISMATCH = "after_head_mismatch"
REASON_NOT_SAME_REPOSITORY = "not_same_repository"
REASON_FORK_HEAD = "fork_head"
REASON_HEAD_DRIFT = "current_head_drift"
REASON_BASE_DRIFT = "current_base_drift"
REASON_SYNTHETIC_MERGE_AS_HEAD = "synthetic_merge_as_head"
REASON_HEAD_OBJECT_MISSING = "head_object_missing"
REASON_NOT_TWO_PARENTS = "not_exactly_two_parents"
REASON_PARENT1_NOT_BEFORE = "parent1_not_before"
REASON_PARENT2_NOT_BASE = "parent2_not_base"
REASON_CHAINED_REFRESH = "chained_refresh"
REASON_MERGE_TREE_MISMATCH = "merge_tree_mismatch"
REASON_MERGE_TREE_FAILED = "merge_tree_failed"
REASON_NO_REQUIRED_CONTEXTS = "no_required_contexts"
REASON_MISSING_PRIOR_CHECK = "missing_prior_check"
REASON_PRIOR_CHECK_NOT_SUCCESS = "prior_check_not_success"
REASON_PRIOR_CHECK_UNBOUND = "prior_check_unbound"
REASON_PRIOR_NOT_FULL = "prior_certification_not_full"
REASON_WORKFLOW_FINGERPRINT = "workflow_fingerprint_mismatch"
REASON_SELF_CHANGE = "self_change"
REASON_MERGE_DRIVER_CHANGE = "merge_driver_change"
REASON_DEPENDENCY_CHANGE = "dependency_lock_change"
REASON_WORKFLOW_CHANGE = "workflow_change"
REASON_GENERATED_DATA_CHANGE = "generated_data_change"
REASON_SHARED_FIXTURE_CHANGE = "shared_fixture_change"
REASON_SCHEMA_CHANGE = "schema_or_registry_change"
REASON_UNKNOWN_PATH = "unknown_path"
REASON_MISSING_PATH_EVIDENCE = "missing_path_evidence"
REASON_MALFORMED_PAYLOAD = "malformed_payload"

REASON_CODES: tuple[str, ...] = (
    REASON_NOT_SYNCHRONIZE,
    REASON_MISSING_EVENT_SHA,
    REASON_AFTER_HEAD_MISMATCH,
    REASON_NOT_SAME_REPOSITORY,
    REASON_FORK_HEAD,
    REASON_HEAD_DRIFT,
    REASON_BASE_DRIFT,
    REASON_SYNTHETIC_MERGE_AS_HEAD,
    REASON_HEAD_OBJECT_MISSING,
    REASON_NOT_TWO_PARENTS,
    REASON_PARENT1_NOT_BEFORE,
    REASON_PARENT2_NOT_BASE,
    REASON_CHAINED_REFRESH,
    REASON_MERGE_TREE_MISMATCH,
    REASON_MERGE_TREE_FAILED,
    REASON_NO_REQUIRED_CONTEXTS,
    REASON_MISSING_PRIOR_CHECK,
    REASON_PRIOR_CHECK_NOT_SUCCESS,
    REASON_PRIOR_CHECK_UNBOUND,
    REASON_PRIOR_NOT_FULL,
    REASON_WORKFLOW_FINGERPRINT,
    REASON_SELF_CHANGE,
    REASON_MERGE_DRIVER_CHANGE,
    REASON_DEPENDENCY_CHANGE,
    REASON_WORKFLOW_CHANGE,
    REASON_GENERATED_DATA_CHANGE,
    REASON_SHARED_FIXTURE_CHANGE,
    REASON_SCHEMA_CHANGE,
    REASON_UNKNOWN_PATH,
    REASON_MISSING_PATH_EVIDENCE,
    REASON_MALFORMED_PAYLOAD,
)

CERTIFICATION_FULL = "full"
CERTIFICATION_FAST = "fast"

SELF_CHANGE_PATHS = (
    "scripts/pr_refresh_certification.py",
    "scripts/path_filter_decision.py",
    "scripts/ruleset_drift_check.py",
    ".github/path-filters.yml",
)
WORKFLOW_PREFIX = ".github/workflows/"
MERGE_DRIVER_PATHS = (".gitattributes",)
DEPENDENCY_PATHS = ("uv.lock", "pyproject.toml")
GENERATED_PREFIXES = ("results-data/",)
SHARED_FIXTURE_PATHS = ("tests/conftest.py", "pytest.ini", ".pre-commit-config.yaml")
SHARED_FIXTURE_PREFIXES = ("tests/fixtures/",)
SCHEMA_PREFIXES = ("migrations/",)
SCHEMA_TOKENS = ("schema", "registry")
KNOWN_ROOTS = (
    ".github/",
    ".claude/",
    "_project/",
    "benchbox/",
    "docs/",
    "examples/",
    "landing/",
    "results-data/",
    "scripts/",
    "tests/",
    "vendor/",
    "make/",
)


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    parents: tuple[str, ...]
    tree: str


@dataclass(frozen=True)
class CheckRunInfo:
    id: int
    name: str
    status: str
    conclusion: str
    started_at: str
    head_sha: str
    run_id: int | None


@dataclass(frozen=True)
class ActionsRunInfo:
    id: int
    name: str
    path: str
    head_sha: str
    event: str
    certification_kind: str
    workflow_fingerprint: str
    base_sha: str


@dataclass(frozen=True)
class ClassificationRequest:
    action: str
    before: str
    after: str
    head_sha: str
    base_sha: str
    current_head_sha: str
    current_base_sha: str
    head_repo_id: int | None
    base_repo_id: int | None
    is_fork: bool
    pr_number: int
    synthetic_merge_sha: str
    required_contexts: tuple[str, ...]
    commits: Mapping[str, CommitInfo]
    merge_tree: str | None
    merge_tree_error: str | None
    check_runs: tuple[CheckRunInfo, ...]
    actions_runs: tuple[ActionsRunInfo, ...]
    current_workflow_fingerprint: str
    authored_paths: tuple[str, ...] | None
    intervening_paths: tuple[str, ...] | None


@dataclass
class Classification:
    decision: str
    reasons: list[str] = field(default_factory=list)
    pr_number: int | None = None
    feature_head: str | None = None
    tested_base_sha: str | None = None
    parent1: str | None = None
    parent2: str | None = None
    required_contexts: list[str] = field(default_factory=list)
    check_run_ids: dict[str, int] = field(default_factory=dict)
    actions_run_ids: dict[str, int] = field(default_factory=dict)
    workflow_fingerprint: str | None = None
    certification_kind: str | None = None
    version: int = 1

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


def _is_sha(value: object) -> bool:
    if not isinstance(value, str):
        return False
    if len(value) not in {40, 64}:
        return False
    allowed = set("0123456789abcdefABCDEF")
    return all(ch in allowed for ch in value) and set(value) != {"0"}


def _parse_started(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def latest_check_run(check_runs: tuple[CheckRunInfo, ...], name: str) -> CheckRunInfo | None:
    matches = [run for run in check_runs if run.name == name]
    if not matches:
        return None
    return max(matches, key=lambda run: _parse_started(run.started_at))


def required_contexts_from_ruleset(ruleset: Mapping[str, Any]) -> tuple[str, ...]:
    contexts: list[str] = []
    for rule in ruleset.get("rules") or []:
        if not isinstance(rule, dict) or rule.get("type") != "required_status_checks":
            continue
        parameters = rule.get("parameters") or {}
        for check in parameters.get("required_status_checks") or []:
            if isinstance(check, dict) and check.get("context"):
                contexts.append(str(check["context"]))
    return tuple(contexts)


def workflow_fingerprint(entries: Mapping[str, str]) -> str:
    lines = [f"{path}={digest}" for path, digest in sorted(entries.items())]
    joined = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()


def _run_by_id(runs: tuple[ActionsRunInfo, ...], run_id: int | None) -> ActionsRunInfo | None:
    if run_id is None:
        return None
    for run in runs:
        if run.id == run_id:
            return run
    return None


def _path_matches(path: str, exact: tuple[str, ...], prefixes: tuple[str, ...] = ()) -> bool:
    if path in exact:
        return True
    return any(path.startswith(prefix) for prefix in prefixes)


def _unknown_path(path: str) -> bool:
    if path in {
        "AGENTS.md",
        "CLAUDE.md",
        "GEMINI.md",
        "ANTIGRAVITY.md",
        "Makefile",
        "README.md",
        "pyproject.toml",
        "uv.lock",
        "skill-sync.conf",
        "tools/skill-sync",
        ".gitignore",
        ".gitattributes",
        ".pre-commit-config.yaml",
        "pytest.ini",
    }:
        return False
    return not any(path.startswith(root) for root in KNOWN_ROOTS)


def _schema_or_registry(path: str) -> bool:
    if any(path.startswith(prefix) or f"/{prefix}" in f"/{path}" for prefix in SCHEMA_PREFIXES):
        return True
    name = path.rsplit("/", 1)[-1].lower()
    return any(token in name for token in SCHEMA_TOKENS)


Predicate = Callable[[ClassificationRequest, Classification], str | None]


def pred_event_shas(req: ClassificationRequest, _out: Classification) -> str | None:
    if not all(
        _is_sha(value)
        for value in (req.before, req.after, req.head_sha, req.base_sha, req.current_head_sha, req.current_base_sha)
    ):
        return REASON_MISSING_EVENT_SHA
    return None


def pred_synchronize(req: ClassificationRequest, _out: Classification) -> str | None:
    if req.action != "synchronize":
        return REASON_NOT_SYNCHRONIZE
    return None


def pred_after_is_head(req: ClassificationRequest, _out: Classification) -> str | None:
    if req.after != req.head_sha:
        return REASON_AFTER_HEAD_MISMATCH
    return None


def pred_same_repository(req: ClassificationRequest, _out: Classification) -> str | None:
    if req.head_repo_id is None or req.base_repo_id is None or req.head_repo_id != req.base_repo_id:
        return REASON_NOT_SAME_REPOSITORY
    return None


def pred_not_fork(req: ClassificationRequest, _out: Classification) -> str | None:
    if req.is_fork:
        return REASON_FORK_HEAD
    return None


def pred_head_not_drifted(req: ClassificationRequest, _out: Classification) -> str | None:
    if req.current_head_sha != req.after:
        return REASON_HEAD_DRIFT
    return None


def pred_base_not_drifted(req: ClassificationRequest, _out: Classification) -> str | None:
    if req.current_base_sha != req.base_sha:
        return REASON_BASE_DRIFT
    return None


def pred_not_synthetic_head(req: ClassificationRequest, _out: Classification) -> str | None:
    if req.synthetic_merge_sha and req.head_sha == req.synthetic_merge_sha:
        return REASON_SYNTHETIC_MERGE_AS_HEAD
    return None


def pred_head_object(req: ClassificationRequest, out: Classification) -> str | None:
    commit = req.commits.get(req.head_sha)
    if commit is None or commit.sha != req.head_sha:
        return REASON_HEAD_OBJECT_MISSING
    out.feature_head = commit.sha
    return None


def pred_two_parents(req: ClassificationRequest, out: Classification) -> str | None:
    commit = req.commits.get(req.head_sha)
    if commit is None or len(commit.parents) != 2:
        return REASON_NOT_TWO_PARENTS
    out.parent1, out.parent2 = commit.parents
    return None


def pred_parent1_before(req: ClassificationRequest, _out: Classification) -> str | None:
    commit = req.commits.get(req.head_sha)
    if commit is None or commit.parents[0] != req.before:
        return REASON_PARENT1_NOT_BEFORE
    return None


def pred_parent2_base(req: ClassificationRequest, _out: Classification) -> str | None:
    commit = req.commits.get(req.head_sha)
    if commit is None or commit.parents[1] != req.base_sha:
        return REASON_PARENT2_NOT_BASE
    return None


def pred_not_chained(req: ClassificationRequest, _out: Classification) -> str | None:
    commit = req.commits.get(req.head_sha)
    if commit is None:
        return REASON_CHAINED_REFRESH
    parent1 = req.commits.get(commit.parents[0])
    if parent1 is None:
        return REASON_HEAD_OBJECT_MISSING
    if len(parent1.parents) != 1:
        return REASON_CHAINED_REFRESH
    return None


def pred_merge_tree(req: ClassificationRequest, _out: Classification) -> str | None:
    if req.merge_tree_error:
        return REASON_MERGE_TREE_FAILED
    commit = req.commits.get(req.head_sha)
    if commit is None or not req.merge_tree:
        return REASON_MERGE_TREE_FAILED
    if req.merge_tree != commit.tree:
        return REASON_MERGE_TREE_MISMATCH
    return None


def pred_required_contexts(req: ClassificationRequest, out: Classification) -> str | None:
    if not req.required_contexts:
        return REASON_NO_REQUIRED_CONTEXTS
    out.required_contexts = list(req.required_contexts)
    return None


def pred_prior_full_checks(req: ClassificationRequest, out: Classification) -> str | None:
    commit = req.commits.get(req.head_sha)
    if commit is None:
        return REASON_MISSING_PRIOR_CHECK
    parent1 = commit.parents[0]
    bound_runs: dict[str, int] = {}
    bound_checks: dict[str, int] = {}
    kinds: list[str] = []
    fingerprints: list[str] = []
    for name in req.required_contexts:
        check = latest_check_run(req.check_runs, name)
        if check is None:
            return REASON_MISSING_PRIOR_CHECK
        if check.status != "completed" or check.conclusion != "success":
            return REASON_PRIOR_CHECK_NOT_SUCCESS
        if check.head_sha != parent1:
            return REASON_PRIOR_CHECK_UNBOUND
        run = _run_by_id(req.actions_runs, check.run_id)
        if run is None:
            return REASON_PRIOR_CHECK_UNBOUND
        if run.head_sha != parent1 or not _is_sha(run.base_sha):
            return REASON_PRIOR_CHECK_UNBOUND
        if run.certification_kind != CERTIFICATION_FULL:
            return REASON_PRIOR_NOT_FULL
        bound_checks[name] = check.id
        bound_runs[name] = run.id
        kinds.append(run.certification_kind)
        fingerprints.append(run.workflow_fingerprint)
    out.check_run_ids = bound_checks
    out.actions_run_ids = bound_runs
    out.certification_kind = CERTIFICATION_FULL if kinds and all(kind == CERTIFICATION_FULL for kind in kinds) else None
    if fingerprints and any(fp != fingerprints[0] for fp in fingerprints):
        return REASON_WORKFLOW_FINGERPRINT
    if fingerprints:
        out.workflow_fingerprint = fingerprints[0]
    return None


def pred_fingerprint(req: ClassificationRequest, out: Classification) -> str | None:
    if not req.current_workflow_fingerprint or not out.workflow_fingerprint:
        return REASON_WORKFLOW_FINGERPRINT
    if out.workflow_fingerprint != req.current_workflow_fingerprint:
        return REASON_WORKFLOW_FINGERPRINT
    return None


def pred_paths(req: ClassificationRequest, _out: Classification) -> str | None:
    if req.authored_paths is None or req.intervening_paths is None:
        return REASON_MISSING_PATH_EVIDENCE
    paths = tuple(dict.fromkeys((*req.authored_paths, *req.intervening_paths)))
    for path in paths:
        if not path:
            return REASON_MISSING_PATH_EVIDENCE
        if _unknown_path(path):
            return REASON_UNKNOWN_PATH
        if _path_matches(path, SELF_CHANGE_PATHS):
            return REASON_SELF_CHANGE
        if path.startswith(WORKFLOW_PREFIX):
            return REASON_WORKFLOW_CHANGE
        if _path_matches(path, MERGE_DRIVER_PATHS):
            return REASON_MERGE_DRIVER_CHANGE
        if _path_matches(path, DEPENDENCY_PATHS):
            return REASON_DEPENDENCY_CHANGE
        if _path_matches(path, (), GENERATED_PREFIXES):
            return REASON_GENERATED_DATA_CHANGE
        if _path_matches(path, SHARED_FIXTURE_PATHS, SHARED_FIXTURE_PREFIXES):
            return REASON_SHARED_FIXTURE_CHANGE
        if _schema_or_registry(path):
            return REASON_SCHEMA_CHANGE
    return None


ELIGIBILITY_PREDICATES: tuple[tuple[str, Predicate], ...] = (
    ("event_shas", pred_event_shas),
    ("synchronize", pred_synchronize),
    ("after_is_head", pred_after_is_head),
    ("same_repository", pred_same_repository),
    ("not_fork", pred_not_fork),
    ("head_not_drifted", pred_head_not_drifted),
    ("base_not_drifted", pred_base_not_drifted),
    ("not_synthetic_head", pred_not_synthetic_head),
    ("head_object", pred_head_object),
    ("two_parents", pred_two_parents),
    ("parent1_before", pred_parent1_before),
    ("parent2_base", pred_parent2_base),
    ("not_chained", pred_not_chained),
    ("merge_tree", pred_merge_tree),
    ("required_contexts", pred_required_contexts),
    ("prior_full_checks", pred_prior_full_checks),
    ("fingerprint", pred_fingerprint),
    ("paths", pred_paths),
)


def classify(request: ClassificationRequest) -> Classification:

    out = Classification(decision=DECISION_FULL, pr_number=request.pr_number)
    try:
        if request.pr_number <= 0:
            out.reasons.append(REASON_MALFORMED_PAYLOAD)
            return out
        for _name, predicate in ELIGIBILITY_PREDICATES:
            reason = predicate(request, out)
            if reason:
                out.reasons.append(reason)
                out.decision = DECISION_FULL
                return out
        out.decision = DECISION_SHADOW
        out.tested_base_sha = request.base_sha
        return out
    except (AttributeError, TypeError, ValueError, KeyError, IndexError):
        out.reasons.append(REASON_MALFORMED_PAYLOAD)
        out.decision = DECISION_FULL
        return out


def _commit_from_mapping(raw: Mapping[str, Any]) -> CommitInfo:
    parents = tuple(str(item) for item in raw.get("parents") or ())
    return CommitInfo(sha=str(raw["sha"]), parents=parents, tree=str(raw["tree"]))


def request_from_mapping(raw: Mapping[str, Any]) -> ClassificationRequest:
    raw_commits = raw.get("commits") or {}
    if not isinstance(raw_commits, Mapping):
        raise TypeError("commits must be a mapping")
    commits: dict[str, CommitInfo] = {}
    for sha, body in raw_commits.items():
        if not isinstance(sha, str) or not isinstance(body, Mapping):
            raise TypeError("commit entries must be mappings keyed by SHA")
        commits[sha] = _commit_from_mapping(body)
    checks = tuple(
        CheckRunInfo(
            id=int(item["id"]),
            name=str(item["name"]),
            status=str(item["status"]),
            conclusion=str(item["conclusion"]),
            started_at=str(item.get("started_at") or ""),
            head_sha=str(item["head_sha"]),
            run_id=int(item["run_id"]) if item.get("run_id") is not None else None,
        )
        for item in raw.get("check_runs") or ()
    )
    runs = tuple(
        ActionsRunInfo(
            id=int(item["id"]),
            name=str(item["name"]),
            path=str(item.get("path") or ""),
            head_sha=str(item["head_sha"]),
            event=str(item.get("event") or ""),
            certification_kind=str(item.get("certification_kind") or ""),
            workflow_fingerprint=str(item.get("workflow_fingerprint") or ""),
            base_sha=str(item.get("base_sha") or ""),
        )
        for item in raw.get("actions_runs") or ()
    )
    authored = raw.get("authored_paths")
    intervening = raw.get("intervening_paths")
    for name, paths in (("authored_paths", authored), ("intervening_paths", intervening)):
        if paths is not None and (
            not isinstance(paths, (list, tuple)) or any(not isinstance(path, str) for path in paths)
        ):
            raise TypeError(f"{name} must be a sequence of strings")
    return ClassificationRequest(
        action=str(raw.get("action") or ""),
        before=str(raw.get("before") or ""),
        after=str(raw.get("after") or ""),
        head_sha=str(raw.get("head_sha") or ""),
        base_sha=str(raw.get("base_sha") or ""),
        current_head_sha=str(raw.get("current_head_sha") or raw.get("after") or ""),
        current_base_sha=str(raw.get("current_base_sha") or raw.get("base_sha") or ""),
        head_repo_id=raw.get("head_repo_id"),
        base_repo_id=raw.get("base_repo_id"),
        is_fork=bool(raw.get("is_fork")),
        pr_number=int(raw.get("pr_number") or 0),
        synthetic_merge_sha=str(raw.get("synthetic_merge_sha") or ""),
        required_contexts=tuple(raw.get("required_contexts") or ()),
        commits=commits,
        merge_tree=raw.get("merge_tree"),
        merge_tree_error=raw.get("merge_tree_error"),
        check_runs=checks,
        actions_runs=runs,
        current_workflow_fingerprint=str(raw.get("current_workflow_fingerprint") or ""),
        authored_paths=None if authored is None else tuple(authored),
        intervening_paths=None if intervening is None else tuple(intervening),
    )


def merge_tree_write_tree(parent1: str, parent2: str) -> tuple[str | None, str | None]:

    try:
        completed = subprocess.run(
            ["git", "merge-tree", "--write-tree", parent1, parent2],
            check=False,
            text=True,
            capture_output=True,
        )
    except OSError as exc:
        return None, str(exc)
    if completed.returncode != 0:
        return None, completed.stderr.strip() or f"merge-tree exit {completed.returncode}"
    tree = completed.stdout.strip()
    return (tree or None), None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--input", type=Path, help="Classification request JSON")
    parser.add_argument("--json-out", type=Path, help="Write the decision JSON")
    args = parser.parse_args(argv)
    if args.input is None:
        raw = json.load(sys.stdin)
    else:
        raw = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        decision = Classification(decision=DECISION_FULL, reasons=[REASON_MALFORMED_PAYLOAD])
    else:
        try:
            decision = classify(request_from_mapping(raw))
        except (AttributeError, TypeError, ValueError, KeyError):
            decision = Classification(decision=DECISION_FULL, reasons=[REASON_MALFORMED_PAYLOAD])
    text = json.dumps(decision.to_json(), indent=2, sort_keys=True) + "\n"
    if args.json_out:
        args.json_out.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0 if decision.decision in {DECISION_SHADOW, DECISION_FULL} else 1


if __name__ == "__main__":
    raise SystemExit(main())
