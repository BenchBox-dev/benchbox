#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SITE_RECEIPT_RE = re.compile(r"^site-deploy receipt sha256:([0-9a-f]{64}) run:(\d+)(?: target:(\S+))?$")
RECOVERY_SUFFIX = "dispatch site-inputs.yml with parent_core_sha set to the last deployed core_sha"
SITE_REPO = "BenchBox-dev/benchbox-site"
SITE_ENVIRONMENT = "github-pages"
PRODUCTION_TARGET = "production"
TRUNK_BRANCH = "develop"
SITE_INPUTS_WORKFLOW_PATH = ".github/workflows/site-inputs.yml"


class ParentResolutionError(RuntimeError):
    pass


class ParentBundleMissingError(ParentResolutionError):
    pass


class NotTrunkCertifiedError(ParentResolutionError):
    pass


FAILURE_CLASS_OTHER = "other"
FAILURE_CLASS_PARENT_BUNDLE_MISSING = "parent-bundle-missing"
FAILURE_CLASS_NOT_TRUNK_CERTIFIED = "not-trunk-certified"
FAILURE_CLASSES = (
    FAILURE_CLASS_PARENT_BUNDLE_MISSING,
    FAILURE_CLASS_NOT_TRUNK_CERTIFIED,
    FAILURE_CLASS_OTHER,
)


def failure_class(error: ParentResolutionError) -> str:
    if isinstance(error, NotTrunkCertifiedError):
        return FAILURE_CLASS_NOT_TRUNK_CERTIFIED
    if isinstance(error, ParentBundleMissingError):
        return FAILURE_CLASS_PARENT_BUNDLE_MISSING
    return FAILURE_CLASS_OTHER


def _git(*cmd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *cmd], cwd=str(ROOT), check=False, text=True, capture_output=True)


def _gh(args: Sequence[str]) -> str:
    proc = subprocess.run(["gh", *args], check=False, text=True, capture_output=True)
    if proc.returncode != 0:
        raise ParentResolutionError(f"gh {' '.join(args)} failed ({proc.returncode}): {proc.stderr[-2000:]}")
    return proc.stdout


def resolve_commit(ref: str) -> str:
    proc = _git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
    sha = proc.stdout.strip()
    if proc.returncode != 0 or not SHA_RE.match(sha):
        raise ParentResolutionError("parent_core_sha does not name a commit")
    return sha


def is_ancestor(parent: str, core: str) -> bool:
    return _git("merge-base", "--is-ancestor", parent, core).returncode == 0


def decide_parent(
    core_sha: str,
    dispatch_parent: str | None,
    bundled_shas: Sequence[str],
    is_ancestor_fn: Callable[[str, str], bool],
    parent_state_fn: Callable[[str], str],
) -> tuple[str, str]:
    if dispatch_parent:
        if dispatch_parent == core_sha or not is_ancestor_fn(dispatch_parent, core_sha):
            raise ParentResolutionError(f"parent_core_sha {dispatch_parent} is not an ancestor of {core_sha}")
        state = parent_state_fn(dispatch_parent)
        if state == "bundle":
            return (dispatch_parent, "bundle")
        if state == "expired":
            raise ParentBundleMissingError(f"site-inputs bundle for parent {dispatch_parent} is expired")
        pooled = [sha for sha in bundled_shas if sha.strip()]
        if not pooled:
            return (dispatch_parent, "dispatch")
        raise ParentBundleMissingError(f"no site-inputs bundle attested on parent {dispatch_parent}")
    for candidate in bundled_shas:
        if candidate and candidate != core_sha and is_ancestor_fn(candidate, core_sha):
            return (candidate, "bundle")
    raise ParentBundleMissingError(f"no unexpired develop bundle is an ancestor of {core_sha}; {RECOVERY_SUFFIX}")


def decide_certified_by(
    core_sha: str,
    run_id: str,
    trunk_runs: Sequence[dict],
) -> str:
    if run_id.strip():
        return run_id.strip()
    for run in trunk_runs:
        if not isinstance(run, dict):
            continue
        if (
            str(run.get("event")) == "push"
            and run.get("headSha") == core_sha
            and str(run.get("headBranch")) == TRUNK_BRANCH
            and str(run.get("conclusion")) == "success"
        ):
            return str(run.get("databaseId"))
    raise NotTrunkCertifiedError(f"no successful trunk.yml push run on {core_sha}")


def recent_trunk_runs(repo: str, limit: int = 20) -> list[dict]:
    raw = _gh(
        [
            "run",
            "list",
            "--workflow",
            "trunk.yml",
            "--branch",
            "develop",
            "--status",
            "success",
            "--json",
            "databaseId,headSha,headBranch,event,conclusion",
            "--limit",
            str(limit),
        ]
    )
    try:
        rows = json.loads(raw)
    except ValueError as error:
        raise ParentResolutionError("cannot list trunk.yml runs") from error
    if not isinstance(rows, list):
        raise ParentResolutionError("cannot list trunk.yml runs")
    return [row for row in rows if isinstance(row, dict)]


def recent_bundled_shas(repo: str, limit: int = 100) -> list[str]:
    raw = _gh(
        [
            "run",
            "list",
            "--workflow",
            "site-inputs.yml",
            "--branch",
            "develop",
            "--status",
            "success",
            "--json",
            "databaseId",
            "--limit",
            str(limit),
            "-q",
            ".[].databaseId",
        ]
    )
    run_ids = [line.strip() for line in raw.split() if line.strip()]
    ordered: list[str] = []
    seen: set[str] = set()
    for run_id in run_ids:
        names = _gh(
            [
                "api",
                f"repos/{repo}/actions/runs/{run_id}/artifacts",
                "-q",
                '.artifacts[] | select(.expired == false and (.name | startswith("site-inputs-")))'
                ' | .name | ltrimstr("site-inputs-")',
            ]
        )
        for name in [item.strip() for item in names.split() if item.strip()]:
            if name not in seen:
                seen.add(name)
                ordered.append(name)
    return ordered


def _is_trunk_site_inputs_run(run_id: str, repo: str) -> bool:
    raw = _gh(
        [
            "api",
            f"repos/{repo}/actions/runs/{run_id}",
            "-q",
            '[.repository.full_name, (.path // ""), (.head_branch // ""), (.conclusion // "")] | @tsv',
        ]
    )
    fields = raw.rstrip("\n").split("\t")
    if len(fields) != 4:
        return False
    run_repo, run_path, head_branch, conclusion = fields
    return (
        run_repo == repo
        and run_path.split("@", 1)[0] == SITE_INPUTS_WORKFLOW_PATH
        and head_branch == TRUNK_BRANCH
        and conclusion == "success"
    )


def parent_bundle_state(parent_sha: str, repo: str) -> str:
    expected = f"site-inputs-{parent_sha}"
    rows = _gh(
        [
            "api",
            f"repos/{repo}/actions/artifacts?name={expected}&per_page=100",
            "-q",
            '.artifacts[] | "\\(.name) \\(.expired) \\(.workflow_run.id)"',
        ]
    )
    expired_seen = False
    trusted_runs: dict[str, bool] = {}
    for line in rows.splitlines():
        parts = line.strip().split()
        if len(parts) != 3 or parts[0] != expected:
            continue
        _, expired, run_id = parts
        if run_id not in trusted_runs:
            trusted_runs[run_id] = _is_trunk_site_inputs_run(run_id, repo)
        if not trusted_runs[run_id]:
            continue
        if expired == "false":
            return "bundle"
        expired_seen = True
    if expired_seen:
        return "expired"
    return "missing"


def receipt_canonical_sha256(receipt: dict) -> str:
    canonical = json.dumps(receipt, sort_keys=True, indent=2) + "\n"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def download_site_receipt(site_repo: str, run_id: str) -> dict:
    dest = tempfile.mkdtemp(prefix="site-receipt-")
    proc = subprocess.run(
        ["gh", "run", "download", run_id, "-R", site_repo, "-n", f"site-deploy-receipt-{run_id}", "-D", dest],
        check=False,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise ParentResolutionError(f"cannot download the production receipt of run {run_id}: {proc.stderr[-1000:]}")
    candidates = sorted(Path(dest).glob("*.json"))
    if not candidates:
        raise ParentResolutionError(f"the production receipt of run {run_id} holds no JSON")
    try:
        return json.loads(candidates[0].read_text(encoding="utf-8"))
    except ValueError as error:
        raise ParentResolutionError(f"the production receipt of run {run_id} is not JSON") from error


def site_deployed_core_sha(
    site_repo: str,
    api_fn: Callable[[Sequence[str]], str] = _gh,
    download_fn: Callable[[str, str], dict] | None = None,
) -> str:
    load = download_fn if download_fn is not None else download_site_receipt
    page = 1
    while True:
        try:
            raw = api_fn(
                [
                    "api",
                    f"repos/{site_repo}/deployments?environment={SITE_ENVIRONMENT}&per_page=100&page={page}",
                    "-q",
                    ".[].id",
                ]
            )
        except ParentResolutionError as error:
            raise ParentResolutionError(f"cannot read the site deployments; {RECOVERY_SUFFIX}") from error
        deployment_ids = [line.strip() for line in raw.split() if line.strip()]
        if not deployment_ids:
            break
        for deployment_id in deployment_ids:
            try:
                descriptions = api_fn(
                    [
                        "api",
                        f"repos/{site_repo}/deployments/{deployment_id}/statuses?per_page=100",
                        "-q",
                        '.[] | select(.state == "success") | .description // empty',
                    ]
                )
            except ParentResolutionError as error:
                raise ParentResolutionError(f"cannot read the site deployments; {RECOVERY_SUFFIX}") from error
            for line in descriptions.splitlines():
                match = SITE_RECEIPT_RE.match(line.strip())
                if not match:
                    continue
                sha, run_id, target = match.group(1), match.group(2), match.group(3)
                receipt: dict | None = None
                if target is None:
                    try:
                        receipt = load(site_repo, run_id)
                    except ParentResolutionError as error:
                        raise ParentResolutionError(
                            f"cannot verify the production receipt of run {run_id}; {RECOVERY_SUFFIX}"
                        ) from error
                    if not isinstance(receipt, dict):
                        raise ParentResolutionError(
                            f"the production receipt of run {run_id} is not JSON; {RECOVERY_SUFFIX}"
                        )
                    target = str(receipt.get("target", ""))
                if target != PRODUCTION_TARGET:
                    continue
                if receipt is None:
                    try:
                        receipt = load(site_repo, run_id)
                    except ParentResolutionError as error:
                        raise ParentResolutionError(
                            f"cannot verify the production receipt of run {run_id}; {RECOVERY_SUFFIX}"
                        ) from error
                    if not isinstance(receipt, dict):
                        raise ParentResolutionError(
                            f"the production receipt of run {run_id} is not JSON; {RECOVERY_SUFFIX}"
                        )
                if receipt_canonical_sha256(receipt) != sha or str(receipt.get("run_id", "")) != run_id:
                    raise ParentResolutionError(
                        f"the production receipt of run {run_id} does not match its recorded digest; {RECOVERY_SUFFIX}"
                    )
                core_sha = str(receipt.get("core_sha", ""))
                if not SHA_RE.match(core_sha):
                    raise ParentResolutionError(
                        f"the production receipt of run {run_id} names no commit; {RECOVERY_SUFFIX}"
                    )
                return core_sha
        page += 1
    raise ParentResolutionError(f"no production deployment is recorded on the site; {RECOVERY_SUFFIX}")


def decide_deployed_parent(
    core_sha: str,
    deployed_sha: str,
    is_ancestor_fn: Callable[[str, str], bool],
    parent_state_fn: Callable[[str], str],
) -> tuple[str, str]:
    if deployed_sha == core_sha or not is_ancestor_fn(deployed_sha, core_sha):
        raise ParentResolutionError(f"deployed parent {deployed_sha} is not an ancestor of {core_sha}")
    state = parent_state_fn(deployed_sha)
    if state != "bundle":
        raise ParentBundleMissingError(f"site-inputs bundle for deployed parent {deployed_sha} is {state}")
    return (deployed_sha, "bundle")


def resolve_and_decide(
    core_sha: str,
    dispatch_parent_raw: str,
    repo: str,
    limit: int,
    run_id: str,
) -> tuple[str, str, str]:
    dispatch_parent = resolve_commit(dispatch_parent_raw) if dispatch_parent_raw.strip() else None
    if dispatch_parent is None:
        deployed_sha = site_deployed_core_sha(SITE_REPO)
        parent_sha, parent_source = decide_deployed_parent(
            core_sha, deployed_sha, is_ancestor, lambda sha: parent_bundle_state(sha, repo)
        )
    else:
        bundled = recent_bundled_shas(repo, limit)
        parent_sha, parent_source = decide_parent(
            core_sha, dispatch_parent, bundled, is_ancestor, lambda sha: parent_bundle_state(sha, repo)
        )
    certified_by = decide_certified_by(core_sha, run_id, recent_trunk_runs(repo))
    return (parent_sha, parent_source, certified_by)


def main(argv: Sequence[str] | None = None, environ: dict[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description="Resolve the site-inputs bundle parent by SHA.")
    parser.add_argument("--core-sha", default="")
    parser.add_argument("--dispatch-parent", default=env.get("DISPATCH_PARENT", ""))
    parser.add_argument("--repo", default=env.get("REPO", env.get("GITHUB_REPOSITORY", "")))
    parser.add_argument("--run-id", default=env.get("RUN_ID", ""))
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    try:
        if not args.repo:
            raise ParentResolutionError("missing repository for parent lookup")
        if args.core_sha:
            core_sha = args.core_sha.strip()
            if not SHA_RE.match(core_sha):
                raise ParentResolutionError("core_sha is not a full commit SHA")
        else:
            proc = _git("rev-parse", "HEAD")
            core_sha = proc.stdout.strip()
            if proc.returncode != 0 or not SHA_RE.match(core_sha):
                raise ParentResolutionError("cannot resolve HEAD to a full commit SHA")
        parent_sha, parent_source, certified_by = resolve_and_decide(
            core_sha, args.dispatch_parent, args.repo, args.limit, args.run_id
        )
    except ParentResolutionError as error:
        print(str(error), file=sys.stderr)
        output = env.get("GITHUB_OUTPUT", "")
        if output:
            with open(output, "a", encoding="utf-8") as handle:
                handle.write(f"failure_class={failure_class(error)}\n")
        return 1
    output = env.get("GITHUB_OUTPUT", "")
    lines = [f"parent_sha={parent_sha}", f"parent_source={parent_source}", f"certified_by={certified_by}"]
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
