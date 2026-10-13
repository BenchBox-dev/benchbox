#!/usr/bin/env python3

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

CLI_DESCRIPTION = (
    "Release readiness gate: canary freshness with advisory UAT gate evidence.\n"
    "\n"
    "Used by the release-PR validation workflow. It fails closed unless the latest\n"
    "completed release canary workflow run is green, fresh, and, by default, has a\n"
    "summary artifact whose checked commit is an ancestor of the release PR head.\n"
    "\n"
    "Committed UAT gate evidence (`_project/release-evidence/uat-gate-summary.json`,\n"
    "written by `make uat-gate-check`) is evaluated and reported when available,\n"
    "but is a non-blocking campaign report. Missing, red, dirty, stale, or\n"
    "non-ancestor UAT evidence does not fail release readiness. Release trees\n"
    "curate `_project/` away, so in CI the optional evidence is read from the\n"
    "fetched `origin/develop` ref via `git show`.\n"
    "\n"
    "The `RELEASE_READINESS_OVERRIDE_SHA`/`_REASON` admin escape hatch bypasses the\n"
    "blocking canary check for one exact release head SHA.\n"
)

BOOTSTRAP_REQUIRED_EXIT_CODE = 20

UAT_GATE_EVIDENCE_RELPATH = "_project/release-evidence/uat-gate-summary.json"


@dataclass(frozen=True)
class ReadinessResult:
    ok: bool
    message: str
    summary: list[str]
    bootstrap_required: bool = False


def _parse_github_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _api_json(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


class _SafeArtifactRedirectHandler(urllib.request.HTTPRedirectHandler):
    @staticmethod
    def _origin(url: str) -> tuple[str, str, int | None]:
        parsed = urllib.parse.urlsplit(url)
        return (parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port)

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and self._origin(req.full_url) != self._origin(redirected.full_url):
            for header in ("Authorization", "Cookie", "Proxy-Authorization"):
                redirected.remove_header(header)
        return redirected


def _api_bytes(url: str, token: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    opener = urllib.request.build_opener(_SafeArtifactRedirectHandler())
    with opener.open(request, timeout=30) as response:
        return response.read()


def _release_canary_commit_sha(run: dict[str, Any], token: str, *, expected_checked_ref: str = "develop") -> str:
    artifacts_url = run.get("artifacts_url")
    if not artifacts_url:
        if expected_checked_ref:
            raise RuntimeError(f"release canary run {run.get('id', '(unknown)')} has no artifacts URL")
        return str(run.get("head_sha", "")).strip()

    artifacts = _api_json(artifacts_url, token).get("artifacts", [])
    summary_artifacts = [
        artifact
        for artifact in artifacts
        if artifact.get("name") == "release-canary-summary" and not artifact.get("expired")
    ]
    if not summary_artifacts:
        raise RuntimeError(f"release canary run {run.get('id', '(unknown)')} has no release-canary-summary artifact")

    latest_summary = max(summary_artifacts, key=lambda artifact: artifact.get("created_at", ""))
    archive_url = latest_summary.get("archive_download_url")
    if not archive_url:
        raise RuntimeError(f"release canary run {run.get('id', '(unknown)')} summary artifact has no download URL")

    archive_bytes = _api_bytes(archive_url, token)
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        summary_names = [name for name in archive.namelist() if name.endswith("release-canary-summary.json")]
        if not summary_names:
            raise RuntimeError(f"release canary run {run.get('id', '(unknown)')} summary artifact has no summary JSON")
        with archive.open(summary_names[0]) as handle:
            payload = json.loads(handle.read().decode("utf-8"))

    checked_ref = str(payload.get("checked_ref", "")).strip()
    if expected_checked_ref and checked_ref != expected_checked_ref:
        raise RuntimeError(
            f"release canary run {run.get('id', '(unknown)')} tested {checked_ref!r}, expected {expected_checked_ref!r}"
        )
    commit_sha = str(payload.get("commit_sha", "")).strip()
    if not commit_sha:
        raise RuntimeError(f"release canary run {run.get('id', '(unknown)')} summary did not record commit_sha")
    return commit_sha


def _is_ancestor_with_git(ancestor_sha: str, head_sha: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor_sha, head_sha],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise RuntimeError(result.stderr.strip() or f"git merge-base failed with exit {result.returncode}")


def _override_active(env: dict[str, str], head_sha: str) -> tuple[bool, str]:
    override_sha = env.get("RELEASE_READINESS_OVERRIDE_SHA", "").strip()
    reason = env.get("RELEASE_READINESS_OVERRIDE_REASON", "").strip()
    if override_sha and reason and override_sha == head_sha:
        return True, reason
    return False, ""


def evaluate_canary_runs(
    runs: list[dict[str, Any]],
    *,
    now: datetime,
    max_age_hours: float,
    head_sha: str,
    require_ancestor: bool = True,
    is_ancestor: Callable[[str, str], bool] = _is_ancestor_with_git,
    canary_commit_sha: Callable[[dict[str, Any]], str] | None = None,
) -> ReadinessResult:
    completed = [run for run in runs if run.get("status") == "completed"]
    if not completed:
        return ReadinessResult(False, "No completed release canary runs found.", ["- canary: missing"])

    latest = max(completed, key=lambda run: _parse_github_time(run["updated_at"]))
    name = latest.get("display_title") or latest.get("name") or "release canary"
    conclusion = latest.get("conclusion")
    url = latest.get("html_url", "(no run URL)")
    run_sha = latest.get("head_sha", "")
    checked_sha = run_sha
    completed_at = _parse_github_time(latest["updated_at"])
    age_hours = (now - completed_at).total_seconds() / 3600
    age_display = f"{age_hours:.1f}h"

    summary = [
        f"- canary: {name}",
        f"- conclusion: {conclusion}",
        f"- completed_at: {completed_at.isoformat()}",
        f"- age: {age_display}",
        f"- workflow_sha: {run_sha}",
        f"- url: {url}",
    ]

    if conclusion != "success":
        return ReadinessResult(False, f"Latest release canary is {conclusion}; release is blocked.", summary)
    if age_hours > max_age_hours:
        return ReadinessResult(
            False,
            f"Latest release canary is stale ({age_display}; max {max_age_hours:g}h).",
            summary,
        )
    if require_ancestor and canary_commit_sha is not None:
        checked_sha = canary_commit_sha(latest)
    if checked_sha != run_sha:
        summary.append(f"- checked_sha: {checked_sha}")
    if require_ancestor and not is_ancestor(checked_sha, head_sha):
        return ReadinessResult(
            False,
            f"Latest release canary SHA {checked_sha} is not an ancestor of release head {head_sha}.",
            summary,
        )
    return ReadinessResult(True, "Release canary is green, fresh, and applicable.", summary)


def _load_uat_gate_evidence(path: str, ref: str) -> dict[str, Any] | None:
    file_path = Path(path)
    if file_path.is_file():
        return json.loads(file_path.read_text(encoding="utf-8"))
    if ref:
        result = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    return None


def evaluate_uat_gate_evidence(
    payload: dict[str, Any] | None,
    *,
    now: datetime,
    max_age_days: float,
    head_sha: str,
    require_ancestor: bool = True,
    is_ancestor: Callable[[str, str], bool] = _is_ancestor_with_git,
) -> ReadinessResult:
    if payload is None:
        return ReadinessResult(
            False,
            (
                "No committed UAT gate evidence found; campaign report is incomplete. "
                "To refresh the optional campaign, run the 3-stage release-gate "
                "sweep, `make uat-gate-check`, and commit "
                f"{UAT_GATE_EVIDENCE_RELPATH} to develop."
            ),
            ["- uat_gate: missing"],
        )

    verdict = str(payload.get("verdict", "")).strip()
    source_sha = str(payload.get("source_commit_sha", "")).strip()
    source_dirty = bool(payload.get("source_dirty", True))
    completed_raw = str(payload.get("completed_at") or "").strip()

    summary = [
        f"- uat_gate: {UAT_GATE_EVIDENCE_RELPATH}",
        f"- uat_verdict: {verdict or '(missing)'}",
        f"- uat_source_commit_sha: {source_sha or '(missing)'}",
        f"- uat_source_dirty: {str(source_dirty).lower()}",
        f"- uat_completed_at: {completed_raw or '(missing)'}",
    ]
    if payload.get("stage_artifact_digests") is None:
        summary.append("- uat_stage_artifact_digests: (missing)")
        if verdict == "green" and not source_dirty and completed_raw:
            return ReadinessResult(
                False,
                (
                    "UAT campaign evidence has no artifact digests: advisory evidence quality failed; evidence was "
                    "produced before artifact-digest provenance was added — "
                    "regenerate evidence with the current orchestrator and "
                    "`make uat-gate-check`"
                ),
                summary,
            )

    if verdict != "green":
        return ReadinessResult(
            False, f"UAT campaign evidence verdict is {verdict!r}; campaign report is incomplete.", summary
        )
    if source_dirty:
        return ReadinessResult(
            False,
            "UAT campaign evidence was produced from a dirty source tree; advisory evidence quality failed.",
            summary,
        )
    if not completed_raw:
        return ReadinessResult(
            False, "UAT campaign evidence records no completed_at; campaign report is incomplete.", summary
        )
    try:
        completed_at = datetime.fromisoformat(completed_raw)
    except ValueError:
        return ReadinessResult(
            False,
            f"UAT campaign evidence completed_at {completed_raw!r} is unparseable; advisory evidence quality failed.",
            summary,
        )
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=timezone.utc)
    age_days = (now - completed_at).total_seconds() / 86400
    summary.append(f"- uat_age: {age_days:.1f}d")
    if age_days > max_age_days:
        return ReadinessResult(
            False,
            f"UAT gate evidence is stale ({age_days:.1f}d; max {max_age_days:g}d).",
            summary,
        )
    if not source_sha:
        return ReadinessResult(
            False, "UAT campaign evidence records no source_commit_sha; campaign report is incomplete.", summary
        )
    if require_ancestor and not is_ancestor(source_sha, head_sha):
        return ReadinessResult(
            False,
            f"UAT gate evidence SHA {source_sha} is not an ancestor of release head {head_sha}.",
            summary,
        )
    return ReadinessResult(True, "UAT gate evidence is green, fresh, and applicable.", summary)


def _combined_result(canary: ReadinessResult, uat: ReadinessResult) -> ReadinessResult:
    summary = [*canary.summary, *uat.summary]
    if not uat.ok:
        return ReadinessResult(canary.ok, f"{canary.message} UAT evidence advisory: {uat.message}", summary)
    return ReadinessResult(canary.ok, f"{canary.message} UAT campaign report: {uat.message}", summary)


def _workflow_runs_url(repo: str, workflow: str, branch: str) -> str:
    branch = branch.strip()
    if not branch:
        raise ValueError("release canary workflow run lookup requires a trusted branch filter")
    workflow_quoted = urllib.parse.quote(workflow, safe="")
    query_args = {"branch": branch, "status": "completed", "per_page": "20"}
    query = urllib.parse.urlencode(query_args)
    return f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_quoted}/runs?{query}"


def _write_summary(result: ReadinessResult, *, summary_path: str | None, override_reason: str = "") -> None:
    lines = ["## Release Readiness", "", result.message, "", *result.summary]
    if override_reason:
        lines.extend(["", f"Emergency override: {override_reason}"])
    text = "\n".join(lines) + "\n"
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(text)
    else:
        print(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument("--workflow", default=os.environ.get("RELEASE_CANARY_WORKFLOW", "release-canary.yml"))
    parser.add_argument(
        "--branch",
        default=os.environ.get("RELEASE_CANARY_BRANCH", "develop"),
        help="Trusted branch/ref that owns the release-canary workflow runs (the default branch, develop as of the 2026-07-08 switch).",
    )
    parser.add_argument(
        "--max-age-hours", type=float, default=float(os.environ.get("RELEASE_CANARY_MAX_AGE_HOURS", "48"))
    )
    parser.add_argument("--head-sha", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "BenchBox-dev/BenchBox"))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument("--checked-ref", default=os.environ.get("RELEASE_CANARY_CHECKED_REF", "develop"))
    parser.add_argument("--no-ancestor-check", action="store_true")
    parser.add_argument(
        "--uat-max-age-days",
        type=float,
        default=float(os.environ.get("RELEASE_UAT_MAX_AGE_DAYS", "21")),
        help="Maximum age of the committed UAT gate evidence (default 21 days; see CLI_DESCRIPTION).",
    )
    parser.add_argument(
        "--uat-evidence-path",
        default=os.environ.get("RELEASE_UAT_EVIDENCE_PATH", UAT_GATE_EVIDENCE_RELPATH),
        help="Working-tree path of the committed UAT gate evidence.",
    )
    parser.add_argument(
        "--uat-evidence-ref",
        default=os.environ.get("RELEASE_UAT_EVIDENCE_REF", "origin/develop"),
        help="Git ref to read the evidence from when the working tree is curated (release/base checkouts).",
    )
    parser.add_argument(
        "--bootstrap-on-missing-workflow",
        action="store_true",
        help=f"Return exit {BOOTSTRAP_REQUIRED_EXIT_CODE} when the canary workflow is not yet available on the default branch.",
    )
    args = parser.parse_args(argv)

    if not args.head_sha:
        print("ERROR: release readiness requires GITHUB_SHA or --head-sha.", file=sys.stderr)
        return 1

    override, reason = _override_active(os.environ, args.head_sha)
    if override:
        result = ReadinessResult(
            True,
            "Release readiness override is active for this exact release head SHA.",
            [f"- sha: {args.head_sha}", "- canary: bypassed by explicit admin override"],
        )
        _write_summary(result, summary_path=os.environ.get("GITHUB_STEP_SUMMARY"), override_reason=reason)
        return 0

    if not args.token:
        print("ERROR: release readiness requires GITHUB_TOKEN or --token.", file=sys.stderr)
        return 1

    try:
        data = _api_json(_workflow_runs_url(args.repo, args.workflow, args.branch), args.token)
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and args.bootstrap_on_missing_workflow:
            result = ReadinessResult(
                False,
                (
                    f"Release canary workflow {args.workflow!r} is not available from the repository "
                    "default branch yet; inline bootstrap evidence is required."
                ),
                [
                    "- canary: bootstrap-required",
                    f"- workflow: {args.workflow}",
                    f"- branch_filter: {args.branch or '(none)'}",
                ],
                bootstrap_required=True,
            )
        else:
            result = ReadinessResult(
                False,
                f"Release canary readiness check failed while querying GitHub: HTTP {exc.code} {exc.reason}",
                ["- canary: api-or-ancestor-check-error"],
            )
    except Exception as exc:
        result = ReadinessResult(
            False,
            f"Release canary readiness check failed while querying GitHub: {exc}",
            ["- canary: api-or-ancestor-check-error"],
        )
    else:
        try:
            result = evaluate_canary_runs(
                data.get("workflow_runs", []),
                now=datetime.now(timezone.utc),
                max_age_hours=args.max_age_hours,
                head_sha=args.head_sha,
                require_ancestor=not args.no_ancestor_check,
                canary_commit_sha=lambda run: _release_canary_commit_sha(
                    run,
                    args.token,
                    expected_checked_ref=args.checked_ref,
                ),
            )
        except Exception as exc:
            result = ReadinessResult(
                False,
                f"Release canary readiness check failed while validating evidence: {exc}",
                ["- canary: api-or-ancestor-check-error"],
            )
    if result.ok:
        try:
            evidence = _load_uat_gate_evidence(args.uat_evidence_path, args.uat_evidence_ref)
            uat_result = evaluate_uat_gate_evidence(
                evidence,
                now=datetime.now(timezone.utc),
                max_age_days=args.uat_max_age_days,
                head_sha=args.head_sha,
                require_ancestor=not args.no_ancestor_check,
            )
        except Exception as exc:
            uat_result = ReadinessResult(
                False,
                f"UAT gate readiness check failed while validating evidence: {exc}",
                ["- uat_gate: evidence-read-error"],
            )
        result = _combined_result(result, uat_result)
    _write_summary(result, summary_path=os.environ.get("GITHUB_STEP_SUMMARY"))
    if not result.ok:
        print(f"ERROR: {result.message}", file=sys.stderr)
        return BOOTSTRAP_REQUIRED_EXIT_CODE if result.bootstrap_required else 1
    print(result.message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
