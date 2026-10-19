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
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from required_lane import (
    REQUIRED_CHECK_NAMES,  # noqa: F401
    is_check_run_success,  # noqa: F401
    is_required_lane_green,
    latest_check_run,  # noqa: F401
)
from soundness_paths import any_soundness_path

FIXTURE_PATH = SCRIPT_DIR / "fixtures" / "green_unmerged_fixture.json"

DEFAULT_REPO = "BenchBox-dev/BenchBox"
GRACE_PERIOD_HOURS = 2.0
API_ROOT = "https://api.github.com"
AUTO_MERGE_HOLD_LABEL = "no-auto-merge"

PINNED_ISSUE_TITLE = "Green-but-unmerged PR sweep"
DIGEST_BODY_MARKER = "<!-- green-unmerged-sweep -->"
ARM_INTENT_TIMELINE_EVENTS = frozenset(
    {
        "ready_for_review",
        "auto_squash_enabled",
        "auto_merge_enabled",
        "auto_merge_disabled",
    }
)
TIMELINE_ACCEPT = "application/vnd.github.mockingbird-preview+json, application/vnd.github+json"


CLI_DESCRIPTION = (
    "Nightly sweep for open develop PRs that look stranded after arm intent.\n"
    "\n"
    "Prior art: `harden-auto-merge-on-open-stranding` diagnosed the original\n"
    "failure class -- auto-merge-on-open.yml's `enable` job completed with\n"
    "`conclusion: success` while the PR's own `auto_merge` field later read\n"
    "back `null`. After the intentional auto-merge hold (#1592 and follow-ons),\n"
    "a green non-draft non-soundness PR with auto-merge OFF is **normal** when\n"
    'nobody asked to arm it. This script therefore does NOT treat "auto-merge\n'
    'off" alone as stranded.\n'
    "\n"
    "It is a read-only external observer that classifies every OPEN develop PR\n"
    "and alerts only when all of the following hold:\n"
    "\n"
    "    (a) required-lane green -- EVERY develop-ruleset required status\n"
    "        context in `REQUIRED_CHECK_NAMES` has its LATEST check run on the\n"
    "        PR's head SHA completed with conclusion `success`. Today that is\n"
    "        `ci-required-result`, `tooling`, `Results Explorer browser gate`,\n"
    "        `ruleset-drift`, and `Public-site visual acceptance`\n"
    "        (docs/internal/repo-admin-settings.md; live ruleset\n"
    "        develop-squash-only). Partial green (one context success, another\n"
    "        missing or red) is NOT required-green. Every required context\n"
    "        reports (path-aware skip still concludes success); a missing run\n"
    "        is fail-closed not-green.\n"
    "    (b) auto-merge is OFF    -- the PR's own `auto_merge` field is falsy\n"
    "        (null or an explicit off), re-read from REST (never inferred from\n"
    '        a workflow run\'s conclusion; see "Known timing behavior" below).\n'
    "    (c) not soundness-gated  -- `any_soundness_path` over the PR's changed\n"
    "        files is False. Soundness-gated PRs correctly never auto-merge\n"
    "        (see `auto_merge_soundness_paths.py` /\n"
    "        `.github/workflows/auto-merge-on-open.yml`); that withheld state\n"
    "        is by design and is covered by the separate daily\n"
    "        soundness-drain digest, not this sweep.\n"
    "    (d) past the grace period -- more than `GRACE_PERIOD_HOURS` (default\n"
    "        2h) since the head commit was pushed, so an arm that just fired\n"
    "        has had time to populate `auto_merge` before this sweep alerts.\n"
    "    (e) not explicitly held -- the PR does not carry the durable hold\n"
    "        label ``no-auto-merge`` (``AUTO_MERGE_HOLD_LABEL``). That label is\n"
    "        the same durable hold ``auto-merge-on-open.yml`` honours: drafts\n"
    "        are already excluded by (job skip / draft check); the label holds\n"
    "        a non-draft without converting it to draft. An explicit hold is\n"
    "        intentional, not stranded — this sweep must never re-arm it.\n"
    "    (f) prior arm intent     -- the issue/PR timeline shows evidence that\n"
    "        auto-merge was requested or previously enabled, then lost. Signals\n"
    "        (any one is enough):\n"
    "          * `ready_for_review` (draft → ready; historical workflow arm path,\n"
    "            deleted 2026-08-06 — still valid as arm-intent evidence in old\n"
    "            timelines)\n"
    "          * `auto_squash_enabled` / `auto_merge_enabled` (arm succeeded once)\n"
    "          * `auto_merge_disabled` (implies a prior enable that was dropped)\n"
    "        Never-armed intentional holds have none of these events and are\n"
    "        excluded even without the hold label. Missing timeline data\n"
    '        fail-closes to "no arm intent" (prefer missing a true strand over\n'
    "        false-positiveing holds).\n"
    "\n"
    "The only mutation this script ever performs (and only under `--apply`) is\n"
    'creating/updating ONE marker-tagged tracking issue (title "Green-but-unmerged\n'
    'PR sweep") with the current digest -- created/refreshed while the stranded\n'
    "set is non-empty, and patched to the empty state exactly once when it\n"
    "drains, then left alone. It never enables auto-merge, never merges, never labels, and\n"
    "never comments on a PR -- enabling auto-merge outside the sanctioned\n"
    "predicate path in `auto-merge-on-open.yml` / `make pr-ready` would bypass\n"
    "the soundness gate and would re-arm intentional holds (including a\n"
    "``no-auto-merge`` hold this sweep did not set).\n"
    "\n"
    "Known timing behavior (observed live 2026-07-23 against PRs #1282-#1286,\n"
    "all opened and auto-merge-enabled the same day): the `auto-merge-on-open.yml`\n"
    "`enable` job (`gh pr merge --auto --squash`) completed `conclusion: success`\n"
    "on every one of those PRs' head SHAs, per the Actions API. However, reading\n"
    "those same PRs back through the GitHub MCP server's `pull_request_read` tool\n"
    "omitted the `auto_merge` field entirely rather than surfacing it as populated\n"
    "or explicit `null` -- i.e. a caller relying on that read path cannot tell\n"
    '"enabled but not yet visible" from "never enabled" from "the read path just\n'
    "doesn't carry this field\". The raw REST `GET /pulls` endpoint (what this\n"
    "script calls directly) does carry `auto_merge` on every PR, so this script\n"
    "never infers state from a workflow run's conclusion -- it always re-fetches\n"
    "each PR's own current `auto_merge` object. Arm intent is read from the\n"
    "timeline REST endpoint (events such as `auto_squash_enabled`), not from\n"
    "workflow conclusions either.\n"
    "\n"
    "Auth: GITHUB_TOKEN or GH_TOKEN from the environment (used directly over the\n"
    "REST API). If neither is set but the `gh` CLI is on PATH, its token\n"
    "(`gh auth token`) is used instead -- this lets the script run locally\n"
    "against an interactively-authenticated `gh` without exporting a token by\n"
    "hand. No long-lived PAT is required or read from anywhere else.\n"
    "\n"
    "Usage:\n"
    "    uv run -- python _project/scripts/green_unmerged_sweep.py\n"
    "    uv run -- python _project/scripts/green_unmerged_sweep.py --json\n"
    "    uv run -- python _project/scripts/green_unmerged_sweep.py --apply\n"
    "    uv run -- python _project/scripts/green_unmerged_sweep.py --self-test\n"
)


@dataclass(frozen=True)
class ClassifiedPR:
    number: int
    title: str
    html_url: str
    draft: bool
    required_green: bool
    soundness_gated: bool
    auto_merge_enabled: bool
    had_arm_intent: bool
    head_age_hours: float
    stranded: bool
    explicit_hold: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_iso(value: str) -> dt.datetime:
    from required_lane import _parse_iso as _rl_parse_iso

    return _rl_parse_iso(value)


def is_soundness_gated(changed_files: list[str]) -> bool:
    return any_soundness_path(changed_files)


def has_auto_merge_hold_label(labels: list[str] | tuple[str, ...] | None) -> bool:
    if not labels:
        return False
    return AUTO_MERGE_HOLD_LABEL in {str(label).strip() for label in labels if label is not None}


def head_age_hours(pr: dict[str, Any], now: dt.datetime) -> float:
    anchor = pr.get("head_pushed_at") or pr.get("updated_at")
    if not anchor:
        return 0.0
    return (now - _parse_iso(anchor)).total_seconds() / 3600.0


def has_arm_intent(timeline_events: list[dict[str, Any]] | None) -> bool:
    if not timeline_events:
        return False
    for event in timeline_events:
        if event.get("event") in ARM_INTENT_TIMELINE_EVENTS:
            return True
    return False


def resolve_had_arm_intent(pr: dict[str, Any]) -> bool:
    if "had_arm_intent" in pr:
        return bool(pr["had_arm_intent"])
    return has_arm_intent(pr.get("timeline_events"))


def is_stranded(
    *,
    draft: bool,
    required_green: bool,
    auto_merge_enabled: bool,
    soundness_gated: bool,
    age_hours: float,
    grace_hours: float,
    explicit_hold: bool = False,
    had_arm_intent: bool = False,
) -> bool:
    return (
        (not draft)
        and required_green
        and (not auto_merge_enabled)
        and (not soundness_gated)
        and (not explicit_hold)
        and age_hours > grace_hours
        and had_arm_intent
    )


def classify_pr(
    pr: dict[str, Any],
    now: dt.datetime,
    *,
    grace_hours: float = GRACE_PERIOD_HOURS,
) -> ClassifiedPR:
    check_runs = pr.get("check_runs") or []
    changed_files = pr.get("changed_files") or []
    labels = pr.get("labels") or []

    required_green = is_required_lane_green(check_runs)
    soundness_gated = is_soundness_gated(changed_files)
    auto_merge_enabled = bool(pr.get("auto_merge"))
    draft = bool(pr.get("draft"))
    explicit_hold = has_auto_merge_hold_label(labels)
    age_h = head_age_hours(pr, now)
    arm_intent = resolve_had_arm_intent(pr)
    stranded = is_stranded(
        draft=draft,
        required_green=required_green,
        auto_merge_enabled=auto_merge_enabled,
        soundness_gated=soundness_gated,
        age_hours=age_h,
        grace_hours=grace_hours,
        explicit_hold=explicit_hold,
        had_arm_intent=arm_intent,
    )

    return ClassifiedPR(
        number=pr["number"],
        title=pr.get("title", ""),
        html_url=pr.get("html_url", ""),
        draft=draft,
        required_green=required_green,
        soundness_gated=soundness_gated,
        auto_merge_enabled=auto_merge_enabled,
        had_arm_intent=arm_intent,
        head_age_hours=age_h,
        stranded=stranded,
        explicit_hold=explicit_hold,
    )


def classify_all(
    prs: list[dict[str, Any]],
    now: dt.datetime,
    *,
    grace_hours: float = GRACE_PERIOD_HOURS,
) -> list[ClassifiedPR]:
    return [classify_pr(pr, now, grace_hours=grace_hours) for pr in prs]


def stranded_prs(classified: list[ClassifiedPR]) -> list[ClassifiedPR]:
    hits = [c for c in classified if c.stranded]
    return sorted(hits, key=lambda c: c.head_age_hours, reverse=True)


def _fmt_hours(hours: float) -> str:
    days, rem = divmod(hours, 24.0)
    if days >= 1:
        return f"{days:.0f}d{rem:.0f}h"
    return f"{hours:.1f}h"


def build_digest(
    classified: list[ClassifiedPR],
    *,
    now: dt.datetime,
    repo: str,
) -> str:
    hits = stranded_prs(classified)
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        DIGEST_BODY_MARKER,
        f"Green-but-unmerged PR sweep -- nightly digest (last updated {stamp})",
        "",
    ]

    if not hits:
        lines.append(
            "No stranded PRs: no open develop PR is green, non-soundness, past grace, "
            "auto-merge OFF, *and* showing prior arm intent "
            "(ready_for_review / auto_merge enable-then-drop). "
            "Intentional holds (never armed) are excluded by design."
        )
        return "\n".join(lines)

    lines.append(
        f"{len(hits)} PR(s) green on required checks, non-draft, non-soundness-gated, "
        f"auto-merge OFF after prior arm intent, head pushed > {GRACE_PERIOD_HOURS:.0f}h ago:"
    )
    lines.append("")
    for c in hits:
        lines.append(f"- #{c.number} {c.title} -- head pushed {_fmt_hours(c.head_age_hours)} ago -- {c.html_url}")
    lines.append("")
    lines.append(
        "This sweep never enables auto-merge itself (and must not re-arm a hold it did not set). "
        "When the branch is final, arm via `make pr-arm` (or `make pr-open READY=1`, or draft → "
        "ready). Do **not** re-push expecting `synchronize` to re-arm -- that path no longer "
        "enables auto-merge. To hold a non-draft intentionally, apply "
        f"the `{AUTO_MERGE_HOLD_LABEL}` label (or convert to draft). See docs/internal/pr-triage.md."
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

    def _request(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None = None,
        *,
        accept: str = "application/vnd.github+json",
    ) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": accept,
                "User-Agent": "benchbox-green-unmerged-sweep",
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

    def get_all(
        self,
        path: str,
        params: dict[str, str] | None = None,
        max_items: int | None = None,
        *,
        accept: str = "application/vnd.github+json",
    ) -> list[dict[str, Any]]:
        query = dict(params or {})
        query.setdefault("per_page", "100")
        out: list[dict[str, Any]] = []
        page = 1
        while page <= 10:
            query["page"] = str(page)
            url = f"{API_ROOT}/{path}?{urllib.parse.urlencode(query)}"
            result = self._request("GET", url, accept=accept) or []
            if isinstance(result, dict) and "check_runs" in result:
                result = result["check_runs"]
            if isinstance(result, dict) and "workflow_runs" in result:
                result = result["workflow_runs"]
            if not result:
                break
            out.extend(result)
            if max_items is not None and len(out) >= max_items:
                return out[:max_items]
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


def fetch_pr_timeline_events(client: GitHubClient, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
    try:
        raw_events = client.get_all(
            f"repos/{owner}/{repo}/issues/{number}/timeline",
            accept=TIMELINE_ACCEPT,
        )
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError, TypeError):
        return []
    compact: list[dict[str, Any]] = []
    for event in raw_events or []:
        name = event.get("event")
        if name:
            compact.append({"event": name})
    return compact


def fetch_open_prs(client: GitHubClient, owner: str, repo: str) -> list[dict[str, Any]]:
    raw_prs = client.get_all(f"repos/{owner}/{repo}/pulls", {"state": "open", "base": "develop"})
    normalized: list[dict[str, Any]] = []
    for raw in raw_prs:
        number = raw["number"]
        head_sha = raw["head"]["sha"]
        files = client.get_all(f"repos/{owner}/{repo}/pulls/{number}/files")
        check_runs = client.get_all(f"repos/{owner}/{repo}/commits/{head_sha}/check-runs")
        commit = client.get_one(f"repos/{owner}/{repo}/commits/{head_sha}") or {}
        commit_info = commit.get("commit") or {}
        head_pushed_at = (commit_info.get("committer") or {}).get("date") or (commit_info.get("author") or {}).get(
            "date"
        )
        raw_labels = raw.get("labels") or []
        label_names = [
            (item.get("name") if isinstance(item, dict) else str(item)) for item in raw_labels if item is not None
        ]
        timeline_events = fetch_pr_timeline_events(client, owner, repo, number)
        normalized.append(
            {
                "number": number,
                "title": raw.get("title", ""),
                "html_url": raw.get("html_url", ""),
                "draft": bool(raw.get("draft")),
                "updated_at": raw.get("updated_at"),
                "head_pushed_at": head_pushed_at,
                "auto_merge": raw.get("auto_merge"),
                "labels": [name for name in label_names if name],
                "timeline_events": timeline_events,
                "changed_files": [f.get("filename", "") for f in files],
                "check_runs": [
                    {
                        "name": run.get("name"),
                        "status": run.get("status"),
                        "conclusion": run.get("conclusion"),
                        "started_at": run.get("started_at"),
                    }
                    for run in check_runs
                ],
            }
        )
    return normalized


def find_pinned_issue(
    client: GitHubClient,
    owner: str,
    repo: str,
    *,
    record_mechanism: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    try:
        query = f'repo:{owner}/{repo} is:issue in:title "{PINNED_ISSUE_TITLE}"'
        encoded = urllib.parse.urlencode({"q": query})
        search_res = client.get_one(f"search/issues?{encoded}")
        if isinstance(search_res, dict) and "items" in search_res:
            candidates: list[dict[str, Any]] = []
            for item in search_res["items"]:
                if "pull_request" in item:
                    continue
                if item.get("title") == PINNED_ISSUE_TITLE and DIGEST_BODY_MARKER in (item.get("body") or ""):
                    candidates.append(item)
            if candidates:
                candidates.sort(key=lambda x: int(x["number"]))
                if record_mechanism:
                    record_mechanism("search")
                return candidates[0]
    except Exception:
        pass

    try:
        page = 1
        while True:
            params = {
                "state": "all",
                "sort": "created",
                "direction": "asc",
                "per_page": "100",
                "page": str(page),
            }
            page_items = client.get_one(f"repos/{owner}/{repo}/issues?{urllib.parse.urlencode(params)}")
            if not page_items or not isinstance(page_items, list):
                break
            for item in page_items:
                if "pull_request" in item:
                    continue
                if item.get("title") == PINNED_ISSUE_TITLE and DIGEST_BODY_MARKER in (item.get("body") or ""):
                    if record_mechanism:
                        record_mechanism("oldest_first_scan")
                    return item
            if len(page_items) < 100:
                break
            page += 1
    except Exception as err:
        raise RuntimeError(f"Pinned issue lookup failed closed: {err}") from err

    return None


def upsert_pinned_issue(
    client: GitHubClient,
    owner: str,
    repo: str,
    body: str,
    *,
    record_mechanism: Callable[[str], None] | None = None,
) -> str:
    existing = find_pinned_issue(client, owner, repo, record_mechanism=record_mechanism)
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
    grace_hours = fixture.get("grace_hours", GRACE_PERIOD_HOURS)
    classified = classify_all(fixture["prs"], now, grace_hours=grace_hours)
    by_number = {c.number: c for c in classified}

    failures: list[str] = []

    def expect(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    expected_stranded = set(fixture["expected_stranded"])
    actual_stranded = {c.number for c in stranded_prs(classified)}
    expect(
        actual_stranded == expected_stranded,
        f"stranded set mismatch: expected {sorted(expected_stranded)}, got {sorted(actual_stranded)}",
    )

    for number, reason in fixture.get("expected_excluded_reasons", {}).items():
        c = by_number.get(int(number))
        expect(c is not None, f"missing fixture PR #{number}")
        if c is None:
            continue
        expect(not c.stranded, f"PR #{number} unexpectedly stranded (expected excluded: {reason})")
        if reason == "not_green":
            expect(not c.required_green, f"PR #{number} expected required_green=False")
        elif reason == "auto_merge_on":
            expect(c.auto_merge_enabled, f"PR #{number} expected auto_merge_enabled=True")
        elif reason == "draft":
            expect(c.draft, f"PR #{number} expected draft=True")
        elif reason == "soundness_gated":
            expect(c.soundness_gated, f"PR #{number} expected soundness_gated=True")
        elif reason == "within_grace":
            expect(c.head_age_hours <= grace_hours, f"PR #{number} expected head_age_hours <= {grace_hours}")
        elif reason == "explicit_hold":
            expect(c.explicit_hold, f"PR #{number} expected explicit_hold=True")
        elif reason in ("intentional_hold", "no_arm_intent"):
            expect(not c.had_arm_intent, f"PR #{number} expected had_arm_intent=False ({reason})")

    for number in fixture.get("expected_stranded", []):
        c = by_number.get(int(number))
        expect(c is not None, f"missing stranded fixture PR #{number}")
        if c is None:
            continue
        expect(c.had_arm_intent, f"PR #{number} stranded but had_arm_intent=False")
        expect(not c.auto_merge_enabled, f"PR #{number} stranded but auto_merge still on")
        expect(not c.explicit_hold, f"PR #{number} stranded but explicit_hold=True")

    expect(
        DIGEST_BODY_MARKER in build_digest(classified, now=now, repo=fixture.get("repo", DEFAULT_REPO)),
        "build_digest output missing DIGEST_BODY_MARKER",
    )

    if failures:
        print("SELF-TEST FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print(f"SELF-TEST PASSED ({len(classified)} fixture PRs, {len(actual_stranded)} stranded).")
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO),
        help=f"owner/name (default: $GITHUB_REPOSITORY or {DEFAULT_REPO})",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of the digest text.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Mutate: upsert the pinned digest issue (no label, no auto-merge call, no PR comments).",
    )
    parser.add_argument(
        "--grace-hours",
        type=float,
        default=GRACE_PERIOD_HOURS,
        help=f"Grace period in hours since head push (default: {GRACE_PERIOD_HOURS}).",
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
    classified = classify_all(prs, now, grace_hours=args.grace_hours)
    hits = stranded_prs(classified)

    if args.json:
        print(
            json.dumps(
                {
                    "generated_at": now.isoformat(),
                    "repo": args.repo,
                    "evaluated_count": len(classified),
                    "stranded": [c.to_dict() for c in hits],
                },
                indent=2,
            )
        )
    else:
        print(build_digest(classified, now=now, repo=args.repo))

    if args.apply:
        should_have_issue = bool(hits)
        if should_have_issue:
            body = build_digest(classified, now=now, repo=args.repo)
            outcome = upsert_pinned_issue(client, owner, repo, body)
            print(f"digest issue: {outcome}", file=sys.stderr)
        else:
            existing = find_pinned_issue(client, owner, repo)
            existing_body = (existing or {}).get("body") or ""
            stale = "No stranded PRs" not in existing_body or "develop post-merge is RED" in existing_body
            if existing is not None and stale:
                client.patch(
                    f"repos/{owner}/{repo}/issues/{existing['number']}",
                    {"body": build_digest(classified, now=now, repo=args.repo)},
                )
                print(f"digest issue: #{existing['number']} marked clear (one-time)", file=sys.stderr)
            else:
                print("digest issue: nothing stranded, no update (silent by design)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
