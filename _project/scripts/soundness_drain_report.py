#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from required_lane import (
    REQUIRED_CHECK_NAMES,
    _parse_iso,
    is_check_run_success as is_check_run_success,
    is_required_lane_green,
    latest_check_run,
)
from soundness_paths import any_soundness_path

FIXTURE_PATH = SCRIPT_DIR / "fixtures" / "soundness_drain_fixture.json"

OWNER_LOGIN = "joeharris76"

DEFAULT_REPO = "BenchBox-dev/BenchBox"
DRAIN_LABEL = "awaiting-owner"
DRAIN_LABEL_COLOR = "b60205"
DRAIN_LABEL_DESCRIPTION = "Green, gated on owner review, parked >24h (see docs/internal/soundness-drain.md)"
PINNED_ISSUE_TITLE = "Soundness-PR drain queue"
DIGEST_BODY_MARKER = "<!-- soundness-drain-digest -->"
IDLE_THRESHOLD_HOURS = 24.0
API_ROOT = "https://api.github.com"


@dataclass(frozen=True)
class ClassifiedPR:
    number: int
    title: str
    html_url: str
    draft: bool
    required_green: bool
    soundness_gated: bool
    auto_merge_enabled: bool
    review_requested_owner: bool
    awaiting_owner: bool
    idle_hours: float
    park_time_hours: float | None
    qualifies: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def required_lane_green_at(check_runs: list[dict[str, Any]]) -> str | None:
    stamps: list[dt.datetime] = []
    raw: list[str] = []
    for name in REQUIRED_CHECK_NAMES:
        stamp = (latest_check_run(check_runs, name) or {}).get("completed_at")
        if not stamp:
            return None
        raw.append(str(stamp))
        try:
            stamps.append(_parse_iso(str(stamp)))
        except (ValueError, TypeError):
            return None
    latest = max(zip(stamps, raw), key=lambda x: x[0])[1]
    return latest


def is_soundness_gated(changed_files: list[str]) -> bool:
    return any_soundness_path(changed_files)


def is_awaiting_owner(
    *,
    auto_merge_enabled: bool,
    soundness_gated: bool,
    review_requested_owner: bool,
) -> bool:
    return (not auto_merge_enabled) and (soundness_gated or review_requested_owner)


def idle_hours(updated_at: str, now: dt.datetime) -> float:
    return (now - _parse_iso(updated_at)).total_seconds() / 3600.0


def park_time_hours(pr: dict[str, Any], now: dt.datetime, *, required_green: bool) -> float | None:
    if not required_green:
        return None
    anchor = required_lane_green_at(pr.get("check_runs") or []) or pr.get("updated_at")
    if not anchor:
        return None
    return (now - _parse_iso(anchor)).total_seconds() / 3600.0


def classify_pr(
    pr: dict[str, Any],
    now: dt.datetime,
    *,
    owner_login: str = OWNER_LOGIN,
    idle_threshold_hours: float = IDLE_THRESHOLD_HOURS,
) -> ClassifiedPR:
    check_runs = pr.get("check_runs") or []
    changed_files = pr.get("changed_files") or []
    requested_reviewers = pr.get("requested_reviewers") or []

    required_green = is_required_lane_green(check_runs)
    soundness_gated = is_soundness_gated(changed_files)
    auto_merge_enabled = bool(pr.get("auto_merge"))
    review_requested_owner = owner_login in requested_reviewers
    awaiting_owner = is_awaiting_owner(
        auto_merge_enabled=auto_merge_enabled,
        soundness_gated=soundness_gated,
        review_requested_owner=review_requested_owner,
    )
    idle_h = idle_hours(pr["updated_at"], now)
    draft = bool(pr.get("draft"))
    park_h = park_time_hours(pr, now, required_green=required_green)
    qualifies = (not draft) and required_green and awaiting_owner and (park_h or 0.0) > idle_threshold_hours

    return ClassifiedPR(
        number=pr["number"],
        title=pr.get("title", ""),
        html_url=pr.get("html_url", ""),
        draft=draft,
        required_green=required_green,
        soundness_gated=soundness_gated,
        auto_merge_enabled=auto_merge_enabled,
        review_requested_owner=review_requested_owner,
        awaiting_owner=awaiting_owner,
        idle_hours=idle_h,
        park_time_hours=park_h,
        qualifies=qualifies,
    )


def classify_all(
    prs: list[dict[str, Any]],
    now: dt.datetime,
    *,
    owner_login: str = OWNER_LOGIN,
    idle_threshold_hours: float = IDLE_THRESHOLD_HOURS,
) -> list[ClassifiedPR]:
    return [classify_pr(pr, now, owner_login=owner_login, idle_threshold_hours=idle_threshold_hours) for pr in prs]


def qualifying(classified: list[ClassifiedPR]) -> list[ClassifiedPR]:
    ready = [c for c in classified if c.qualifies]
    return sorted(ready, key=lambda c: c.park_time_hours or 0.0, reverse=True)


def _fmt_hours(hours: float) -> str:
    days, rem = divmod(hours, 24.0)
    if days >= 1:
        return f"{days:.0f}d{rem:.0f}h"
    return f"{hours:.1f}h"


def build_digest(classified: list[ClassifiedPR], *, now: dt.datetime, repo: str) -> str:
    ready = qualifying(classified)
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        DIGEST_BODY_MARKER,
        f"Soundness-PR drain queue -- daily digest (last updated {stamp})",
        "",
    ]
    if not ready:
        lines.append("Queue is empty: no PRs currently parked awaiting owner review.")
        return "\n".join(lines)

    lines.append(f"{len(ready)} PR(s) green on required checks, awaiting owner review, parked > 24h since green:")
    lines.append("")
    for c in ready:
        reason = "soundness-gated" if c.soundness_gated else "review-requested"
        park = _fmt_hours(c.park_time_hours) if c.park_time_hours is not None else "?"
        idle = _fmt_hours(c.idle_hours)
        lines.append(f"- #{c.number} {c.title} -- {reason}, idle {idle}, parked {park} -- {c.html_url}")
    lines.append("")
    lines.append(
        f"The `{DRAIN_LABEL}` label above reflects this list exactly (added/removed to match). "
        "The soundness auto-merge gate itself is untouched by this report -- see "
        "docs/internal/soundness-drain.md."
    )
    lines.append(f"(repo: {repo})")
    return "\n".join(lines)


def resolve_token() -> str | None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    if shutil.which("gh"):
        try:
            result = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except OSError:
            return None
        if result.returncode == 0:
            candidate = result.stdout.strip()
            if candidate:
                return candidate
    return None


class GitHubClient:
    def __init__(self, token: str) -> None:
        self.token = token

    def _request(self, method: str, url: str, body: dict[str, Any] | None = None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "benchbox-soundness-drain-report",
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    payload = resp.read()
                    return json.loads(payload) if payload else None
            except urllib.error.HTTPError as err:
                if err.code == 404:
                    return None
                if attempt == 2:
                    raise
            except urllib.error.URLError:
                if attempt == 2:
                    raise
        return None

    def get_all(self, path: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        query = dict(params or {})
        query.setdefault("per_page", "100")
        out: list[dict[str, Any]] = []
        page = 1
        while page <= 10:
            query["page"] = str(page)
            url = f"{API_ROOT}/{path}?{urllib.parse.urlencode(query)}"
            result = self._request("GET", url) or []
            if isinstance(result, dict) and "check_runs" in result:
                result = result["check_runs"]
            if not result:
                break
            out.extend(result)
            if len(result) < int(query["per_page"]):
                break
            page += 1
        return out

    def get_one(self, path: str) -> Any:
        return self._request("GET", f"{API_ROOT}/{path}")

    def post(self, path: str, body: dict[str, Any]) -> Any:
        return self._request("POST", f"{API_ROOT}/{path}", body)

    def patch(self, path: str, body: dict[str, Any]) -> Any:
        return self._request("PATCH", f"{API_ROOT}/{path}", body)

    def delete(self, path: str) -> Any:
        return self._request("DELETE", f"{API_ROOT}/{path}")


def fetch_open_prs(client: GitHubClient, owner: str, repo: str) -> list[dict[str, Any]]:
    raw_prs = client.get_all(f"repos/{owner}/{repo}/pulls", {"state": "open", "base": "develop"})
    normalized: list[dict[str, Any]] = []
    for raw in raw_prs:
        number = raw["number"]
        head_sha = raw["head"]["sha"]
        files = client.get_all(f"repos/{owner}/{repo}/pulls/{number}/files")
        check_runs = client.get_all(f"repos/{owner}/{repo}/commits/{head_sha}/check-runs", {"filter": "latest"})
        normalized.append(
            {
                "number": number,
                "title": raw.get("title", ""),
                "html_url": raw.get("html_url", ""),
                "draft": bool(raw.get("draft")),
                "created_at": raw.get("created_at"),
                "updated_at": raw.get("updated_at"),
                "auto_merge": raw.get("auto_merge"),
                "requested_reviewers": [r.get("login") for r in (raw.get("requested_reviewers") or [])],
                "changed_files": [
                    name for f in files for name in (f.get("filename", ""), f.get("previous_filename", "")) if name
                ],
                "check_runs": [
                    {
                        "name": run.get("name"),
                        "status": run.get("status"),
                        "conclusion": run.get("conclusion"),
                        "completed_at": run.get("completed_at"),
                        "started_at": run.get("started_at"),
                        "id": run.get("id"),
                        "check_suite": {"id": (run.get("check_suite") or {}).get("id")},
                        "details_url": run.get("details_url"),
                    }
                    for run in check_runs
                ],
            }
        )
    return normalized


def ensure_label_exists(client: GitHubClient, owner: str, repo: str) -> None:
    existing = client.get_one(f"repos/{owner}/{repo}/labels/{DRAIN_LABEL}")
    if existing is not None:
        return
    client.post(
        f"repos/{owner}/{repo}/labels",
        {"name": DRAIN_LABEL, "color": DRAIN_LABEL_COLOR, "description": DRAIN_LABEL_DESCRIPTION},
    )


def sync_labels(client: GitHubClient, owner: str, repo: str, ready: list[ClassifiedPR]) -> tuple[list[int], list[int]]:
    ensure_label_exists(client, owner, repo)
    qualifying_numbers = {c.number for c in ready}

    labeled_prs = client.get_all(
        f"repos/{owner}/{repo}/issues",
        {"labels": DRAIN_LABEL, "state": "open"},
    )
    labeled_numbers = {item["number"] for item in labeled_prs if "pull_request" in item}

    added: list[int] = []
    for number in sorted(qualifying_numbers - labeled_numbers):
        client.post(f"repos/{owner}/{repo}/issues/{number}/labels", {"labels": [DRAIN_LABEL]})
        added.append(number)

    removed: list[int] = []
    for number in sorted(labeled_numbers - qualifying_numbers):
        client.delete(f"repos/{owner}/{repo}/issues/{number}/labels/{DRAIN_LABEL}")
        removed.append(number)

    return added, removed


def find_pinned_issue(client: GitHubClient, owner: str, repo: str) -> dict[str, Any] | None:
    issues = client.get_all(f"repos/{owner}/{repo}/issues", {"state": "all"})
    for item in issues:
        if "pull_request" in item:
            continue
        if item.get("title") == PINNED_ISSUE_TITLE and DIGEST_BODY_MARKER in (item.get("body") or ""):
            return item
    return None


def upsert_pinned_issue(client: GitHubClient, owner: str, repo: str, body: str) -> str:
    existing = find_pinned_issue(client, owner, repo)
    if existing is None:
        created = client.post(
            f"repos/{owner}/{repo}/issues",
            {"title": PINNED_ISSUE_TITLE, "body": body},
        )
        return f"created issue #{created['number']}"
    payload: dict[str, Any] = {"body": body}
    if existing.get("state") == "closed":
        payload["state"] = "open"
    client.patch(f"repos/{owner}/{repo}/issues/{existing['number']}", payload)
    return f"updated issue #{existing['number']}"


def run_self_test() -> int:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    now = _parse_iso(fixture["as_of"])
    owner_login = fixture.get("owner_login", OWNER_LOGIN)
    classified = classify_all(fixture["prs"], now, owner_login=owner_login)
    by_number = {c.number: c for c in classified}

    failures: list[str] = []

    def expect(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    expected_qualifying = set(fixture["expected_qualifying"])
    actual_qualifying = {c.number for c in qualifying(classified)}
    expect(
        actual_qualifying == expected_qualifying,
        f"qualifying set mismatch: expected {sorted(expected_qualifying)}, got {sorted(actual_qualifying)}",
    )

    for number in expected_qualifying:
        expect(number in by_number, f"expected PR #{number} in fixture")

    for number, reason in fixture.get("expected_excluded_reasons", {}).items():
        c = by_number.get(int(number))
        expect(c is not None, f"missing fixture PR #{number}")
        if c is None:
            continue
        expect(not c.qualifies, f"PR #{number} unexpectedly qualifies (expected excluded: {reason})")
        if reason == "not_green":
            expect(not c.required_green, f"PR #{number} expected required_green=False")
        elif reason == "auto_merge_on":
            expect(c.auto_merge_enabled, f"PR #{number} expected auto_merge_enabled=True")
        elif reason == "too_young":
            expect(
                (c.park_time_hours or 0.0) <= IDLE_THRESHOLD_HOURS,
                f"PR #{number} expected park_time_hours <= 24",
            )
        elif reason == "draft":
            expect(c.draft, f"PR #{number} expected draft=True")

    for c in classified:
        if c.required_green:
            expect(c.park_time_hours is not None, f"PR #{c.number}: required_green but park_time_hours is None")
        else:
            expect(c.park_time_hours is None, f"PR #{c.number}: not required_green but park_time_hours is set")

    if failures:
        print("SELF-TEST FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print(f"SELF-TEST PASSED ({len(classified)} fixture PRs, {len(actual_qualifying)} qualifying).")
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report soundness-path PRs that have waited since their required checks went green.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO),
        help=f"owner/name (default: $GITHUB_REPOSITORY or {DEFAULT_REPO})",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of the digest text.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Mutate: sync the awaiting-owner label and (if non-empty) upsert the pinned digest issue.",
    )
    parser.add_argument(
        "--idle-hours",
        type=float,
        default=IDLE_THRESHOLD_HOURS,
        help=f"Idle threshold in hours (default: {IDLE_THRESHOLD_HOURS}).",
    )
    parser.add_argument("--self-test", action="store_true", help="Run the bundled fixture-based classifier test.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])

    if args.self_test:
        return run_self_test()

    try:
        owner, repo = args.repo.split("/", 1)
    except ValueError:
        print(f"::error::--repo must be owner/name, got {args.repo!r}", file=sys.stderr)
        return 2

    token = resolve_token()
    if not token:
        print(
            "::error::No GITHUB_TOKEN/GH_TOKEN in the environment and no authenticated `gh` on PATH.",
            file=sys.stderr,
        )
        return 2

    client = GitHubClient(token)
    prs = fetch_open_prs(client, owner, repo)
    now = dt.datetime.now(dt.timezone.utc)
    classified = classify_all(prs, now, idle_threshold_hours=args.idle_hours)
    ready = qualifying(classified)

    if args.json:
        print(
            json.dumps(
                {
                    "generated_at": now.isoformat(),
                    "repo": args.repo,
                    "evaluated_count": len(classified),
                    "qualifying": [c.to_dict() for c in ready],
                },
                indent=2,
            )
        )
    else:
        print(build_digest(classified, now=now, repo=args.repo))

    if args.apply:
        added, removed = sync_labels(client, owner, repo, ready)
        if added:
            print(f"label: added {DRAIN_LABEL} to {added}", file=sys.stderr)
        if removed:
            print(f"label: removed {DRAIN_LABEL} from {removed}", file=sys.stderr)
        if ready:
            outcome = upsert_pinned_issue(client, owner, repo, build_digest(classified, now=now, repo=args.repo))
            print(f"digest issue: {outcome}", file=sys.stderr)
        else:
            existing = find_pinned_issue(client, owner, repo)
            if existing is not None and "Queue is empty" not in (existing.get("body") or ""):
                client.patch(
                    f"repos/{owner}/{repo}/issues/{existing['number']}",
                    {"body": build_digest(classified, now=now, repo=args.repo)},
                )
                print(f"digest issue: #{existing['number']} marked empty (one-time)", file=sys.stderr)
            else:
                print("digest issue: queue empty, no update (silent by design)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
