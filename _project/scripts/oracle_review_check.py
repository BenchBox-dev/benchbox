#!/usr/bin/env python3
from __future__ import annotations

import fnmatch
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from inert_paths import is_inert_path
from soundness_paths import is_soundness_path

ORACLE_LOGIN = "benchbox-oracle"
ORACLE_CONTEXT = "oracle-verdict"
_ORACLE_VERDICT = re.compile(rf"### {ORACLE_CONTEXT}: (?P<state>[a-z]+) for `(?P<sha>[0-9a-f]{{40}})`")
_RUN_SUFFIX = re.compile(r" \[run (?P<run>[0-9a-z]+)\]$")
EXEMPT_SUCCESS_DESCRIPTION = "only exempt paths changed"
STANDIN_ATTESTERS = frozenset({"joeharris76"})
REFUSED_DESCRIPTION_PREFIX = "refused after"
_STANDIN_MARKER = re.compile(r"Stand-in oracle review: APPROVE ([0-9a-f]{40})")


def _attested_shas(comment: dict[str, Any]) -> list[str]:
    match = _STANDIN_MARKER.fullmatch((comment.get("body") or "").strip())
    return [match.group(1)] if match else []


def _is_standin(comment: dict[str, Any], head_sha: str, not_before: datetime | None) -> bool:
    return (
        comment.get("login") in STANDIN_ATTESTERS
        and comment.get("user_type") == "User"
        and comment.get("updated_at") in (None, comment.get("created_at"))
        and (not_before is None or _parse_time(comment["created_at"]) > not_before)
        and head_sha in _attested_shas(comment)
    )


def _api_root(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS and not parsed.path.strip("/"):
        return value.rstrip("/")
    return GITHUB_API_ROOT


LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
GITHUB_API_ROOT = "https://api.github.com"
API_ROOT = _api_root(os.environ.get("GITHUB_API_URL", ""))
PAGE_SIZE = 100
PASS = 0
WAITING = 1
ERROR = 2

_HEAD_OID_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) { headRefOid }
  }
}
"""


class CheckError(RuntimeError):
    pass


def _login(value: str | None) -> str:
    return (value or "").removesuffix("[bot]")


def _is_oracle(login: str | None, account_type: str | None) -> bool:
    return _login(login) == ORACLE_LOGIN and account_type == "Bot"


DECISIVE_VERDICTS = frozenset({"success", "failure"})
Moment = TypeVar("Moment", str, datetime)


def verdict_state(body: str | None, sha: str, pattern: re.Pattern[str]) -> str | None:
    match = pattern.match(body or "")
    return match.group("state") if match and match.group("sha") == sha else None


def standin_not_before(base_change: Moment | None, verdicts: Iterable[tuple[Moment, str | None]]) -> Moment | None:
    decisive = (at for at, state in verdicts if state in DECISIVE_VERDICTS)
    return max(filter(None, (base_change, *decisive)), default=None)


def _survives_base_change(moment: datetime, base_time: datetime | None) -> bool:
    from oracle_reviewers.base_change import survives

    return survives(moment, base_time)


def _receipt_candidates(
    reviews: Iterable[dict[str, Any]], head_sha: str, base_time: datetime | None
) -> list[dict[str, Any]]:
    return [
        review
        for review in reviews
        if _is_oracle(review.get("login"), review.get("user_type"))
        and review.get("commit_id") == head_sha
        and review.get("state") not in {"PENDING", "DISMISSED"}
        and review.get("submitted_at")
        and _survives_base_change(_parse_time(review["submitted_at"]), base_time)
    ]


def _status_run_uuid(status: dict[str, Any]) -> str | None:
    match = _RUN_SUFFIX.search(status.get("description") or "")
    return match.group("run") if match else None


def _strip_run_suffix(description: str | None) -> str:
    return _RUN_SUFFIX.sub("", description or "")


def _receipt_run_uuid(receipt: dict[str, Any]) -> str | None:
    from oracle_reviewers.protocol import decode_marker

    try:
        record = decode_marker(receipt.get("body") or "")
    except Exception:
        return None
    return record.get("run_uuid") if isinstance(record, dict) else None


def _receipt_agrees(receipt: dict[str, Any], head_sha: str, status_uuid: str | None) -> bool:
    if (
        status_uuid is None
        or "last_edited_at" not in receipt
        or "includes_created_edit" not in receipt
        or receipt.get("last_edited_at") is not None
        or receipt.get("includes_created_edit")
        or verdict_state(receipt.get("body"), head_sha, _ORACLE_VERDICT) != "success"
    ):
        return False
    receipt_uuid = _receipt_run_uuid(receipt)
    return receipt_uuid is not None and receipt_uuid == status_uuid


def _receipt_backed_success(
    verdicts: list[dict[str, Any]], reviews: Iterable[dict[str, Any]], head_sha: str, base_time: datetime | None
) -> bool:
    candidates = _receipt_candidates(reviews, head_sha, base_time)
    for status in reversed(verdicts):
        if status["state"] != "success":
            return False
        if _strip_run_suffix(status.get("description")) == EXEMPT_SUCCESS_DESCRIPTION:
            continue
        status_uuid = _status_run_uuid(status)
        own = [
            receipt for receipt in candidates if status_uuid is not None and _receipt_run_uuid(receipt) == status_uuid
        ]
        latest = max(own, key=lambda review: (_parse_time(review["submitted_at"]), review.get("id") or 0), default=None)
        if latest is not None:
            return _receipt_agrees(latest, head_sha, status_uuid)
    return False


def _verdict_statuses(statuses: Iterable[dict[str, Any]], base_time: datetime | None) -> list[dict[str, Any]]:
    return sorted(
        (
            status
            for status in statuses
            if status.get("context") == ORACLE_CONTEXT
            and _is_oracle(status.get("login"), status.get("user_type"))
            and status.get("created_at")
            and _survives_base_change(_parse_time(status["created_at"]), base_time)
        ),
        key=lambda status: (_parse_time(status["created_at"]), status.get("id") or 0),
    )


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def decide(
    head_sha: str,
    files: Iterable[str],
    statuses: Iterable[dict[str, Any]],
    paths: Callable[[Iterable[str]], bool],
    base_date: str | None = None,
    comments: Iterable[dict[str, Any]] = (),
    *,
    reviews: Iterable[dict[str, Any]] = (),
    bump_refusals: Iterable[str] = (),
) -> tuple[int, str]:
    if not paths(files):
        return PASS, "oracle-review: only exempt paths changed"
    refused = list(bump_refusals)
    if refused:
        return WAITING, f"oracle-review: omp pin bump refused: {'; '.join(refused)}"

    base_time = _parse_time(base_date) if base_date else None
    verdicts = _verdict_statuses(statuses, base_time)
    latest = verdicts[-1] if verdicts else None
    verdict = latest["state"] if latest else None
    standin_after = standin_not_before(
        base_time, ((_parse_time(status["created_at"]), status["state"]) for status in verdicts)
    )
    standin = next((comment for comment in comments if _is_standin(comment, head_sha, standin_after)), None)
    if latest and verdict != "success" and not standin:
        if (latest.get("description") or "").startswith(REFUSED_DESCRIPTION_PREFIX):
            return WAITING, (
                f"oracle-review: the oracle refused to review {head_sha} after repeated DO NOT SHIP decisions; "
                "close this pull request and open a new one, or post a stand-in attestation after that verdict"
            )
        return WAITING, (
            f"oracle-review: the oracle's latest verdict for {head_sha} is {verdict}, not success; fix the "
            "findings, or post a stand-in attestation after that verdict"
        )
    if not (latest or standin):
        return WAITING, (
            f"oracle-review: waiting for the oracle's verdict on {head_sha}, or a stand-in attestation "
            f"'Stand-in oracle review: APPROVE {head_sha}' from a listed attester"
        )

    if verdict == "success":
        if _strip_run_suffix(latest.get("description")) == EXEMPT_SUCCESS_DESCRIPTION:
            if standin is None:
                return WAITING, (
                    f"oracle-review: the oracle-verdict status for {head_sha} reports only exempt paths changed, "
                    "but the gate's current rules require a review of this head; push a new head (a byte-identical "
                    "rebase is enough), or have the owner stand-in post an attestation"
                )
        else:
            if _receipt_backed_success(verdicts, reviews, head_sha, base_time):
                return PASS, f"oracle-review: pass (oracle verdict for {head_sha})"
            if standin is None:
                return WAITING, (
                    f"oracle-review: the oracle-verdict status for {head_sha} is success, but this pull request "
                    f"has no unedited oracle review of {head_sha} from the same verdict run agreeing with it; "
                    "push a new head (a byte-identical rebase is enough) so the verdict runs again, or have the "
                    "owner stand-in post an attestation"
                )
    assert standin is not None
    return PASS, f"oracle-review: pass (stand-in review attested by {standin.get('login')} for {head_sha})"


def _request(token: str, url: str, body: dict[str, Any] | None = None) -> Any:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "benchbox-oracle-review-check",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise CheckError(f"GitHub returned HTTP {exc.code} for {url}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CheckError(f"GitHub request to {url} failed: {exc}") from exc


def _paginate(token: str, path: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        batch = _request(token, f"{API_ROOT}{path}?per_page={PAGE_SIZE}&page={page}")
        items.extend(batch)
        if len(batch) < PAGE_SIZE:
            return items
        page += 1


def fetch_pull(token: str, repo: str, pr: int) -> dict[str, Any]:
    return _request(token, f"{API_ROOT}/repos/{repo}/pulls/{pr}")


def changed_paths(items: Iterable[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for item in items:
        paths.append(item["filename"])
        if item.get("previous_filename"):
            paths.append(item["previous_filename"])
    return paths


def fetch_files(token: str, repo: str, pr: int) -> list[dict[str, Any]]:
    return _paginate(token, f"/repos/{repo}/pulls/{pr}/files")


def _review_rules() -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        from oracle_reviewers.policy import load_policy

        classifier = load_policy(Path(".github/oracle-reviewers.yml")).classifier
        return classifier.exempt_paths, classifier.gate_paths
    except Exception:
        return (), ()


def _path_needs_review(path: str, exempt: tuple[str, ...], gate: tuple[str, ...]) -> bool:
    if is_soundness_path(path):
        return True
    if any(fnmatch.fnmatchcase(path, pattern) for pattern in gate):
        return True
    if is_inert_path(path):
        return False
    return not any(fnmatch.fnmatchcase(path, pattern) for pattern in exempt)


def path_matcher(
    listed_files: int,
    changed_files: int,
    exempt_paths: Iterable[str] = (),
    gate_paths: Iterable[str] = (),
) -> Callable[[Iterable[str]], bool]:
    if listed_files < changed_files:
        return lambda _paths: True
    exempt = tuple(exempt_paths)
    gate = tuple(gate_paths)

    def needs_review(paths: Iterable[str]) -> bool:
        return any(_path_needs_review(path, exempt, gate) for path in paths)

    return needs_review


_REVIEW_EDIT_QUERY = """
query($id: ID!) {
  node(id: $id) {
    ... on PullRequestReview {
      lastEditedAt
      includesCreatedEdit
    }
  }
}
"""


def fetch_reviews(token: str, repo: str, pr: int) -> list[dict[str, Any]]:
    return [
        {
            "login": (item.get("user") or {}).get("login"),
            "user_type": (item.get("user") or {}).get("type"),
            "commit_id": item.get("commit_id"),
            "state": item.get("state"),
            "submitted_at": item.get("submitted_at"),
            "body": item.get("body"),
            "node_id": item.get("node_id"),
            "id": item.get("id"),
        }
        for item in _paginate(token, f"/repos/{repo}/pulls/{pr}/reviews")
    ]


def fetch_review_edit(token: str, node_id: str) -> tuple[str | None, bool]:
    payload = _request(token, f"{API_ROOT}/graphql", {"query": _REVIEW_EDIT_QUERY, "variables": {"id": node_id}})
    if payload.get("errors"):
        raise CheckError(f"GitHub GraphQL returned errors: {payload['errors']}")
    node = payload.get("data", {}).get("node")
    if not isinstance(node, dict) or "lastEditedAt" not in node or "includesCreatedEdit" not in node:
        raise CheckError(f"GitHub GraphQL returned no PullRequestReview node for {node_id}")
    return node.get("lastEditedAt"), bool(node.get("includesCreatedEdit"))


def annotate_receipt(token: str, reviews: list[dict[str, Any]], head_sha: str, base_date: str | None = None) -> None:
    base_time = _parse_time(base_date) if base_date else None
    for receipt in _receipt_candidates(reviews, head_sha, base_time):
        _annotate_one(token, receipt)


def _annotate_one(token: str, receipt: dict[str, Any]) -> None:
    if not receipt.get("node_id"):
        receipt["last_edited_at"] = ""
        receipt["includes_created_edit"] = True
        return
    try:
        last_edited_at, includes_created_edit = fetch_review_edit(token, receipt["node_id"])
    except (CheckError, KeyError, ValueError):
        last_edited_at, includes_created_edit = "", True
    receipt["last_edited_at"] = last_edited_at
    receipt["includes_created_edit"] = includes_created_edit


def fetch_statuses(token: str, repo: str, head_sha: str) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id"),
            "context": item.get("context"),
            "state": item.get("state"),
            "description": item.get("description"),
            "login": (item.get("creator") or {}).get("login"),
            "user_type": (item.get("creator") or {}).get("type"),
            "created_at": item.get("created_at"),
        }
        for item in _paginate(token, f"/repos/{repo}/commits/{head_sha}/statuses")
    ]


def fetch_text(token: str, repo: str, path: str, ref: str) -> str:
    url = f"{API_ROOT}/repos/{repo}/contents/{path}?ref={urllib.parse.quote(ref, safe='')}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.raw",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "benchbox-oracle-review-check",
    }
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise CheckError(f"GitHub returned HTTP {exc.code} for {url}") from exc
    except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as exc:
        raise CheckError(f"GitHub request to {url} failed: {exc}") from exc


def fetch_bump_refusals(
    token: str,
    repo: str,
    pull: dict[str, Any],
    files: list[str],
    listed_files: int,
    comments: list[dict[str, Any]],
) -> list[str]:
    from oracle_reviewers import omp_bump_gate

    complete = listed_files >= pull["changed_files"]
    if omp_bump_gate.POLICY_PATH not in files and complete:
        return []
    head_repo = ((pull["head"].get("repo") or {}).get("full_name")) or repo
    try:
        base_text = fetch_text(token, repo, omp_bump_gate.POLICY_PATH, pull["base"]["ref"])
        head_text = fetch_text(token, head_repo, omp_bump_gate.POLICY_PATH, pull["head"]["sha"])
        return omp_bump_gate.refusals(
            [*files, omp_bump_gate.POLICY_PATH],
            base_text,
            head_text,
            comments,
            pull["head"]["sha"],
            complete,
        )
    except omp_bump_gate.BumpEvidenceError as exc:
        return [f"omp pin bump evidence could not be verified: {exc}"]


def fetch_comments(token: str, repo: str, pr: int) -> list[dict[str, Any]]:
    return [
        {
            "login": (item.get("user") or {}).get("login"),
            "user_type": (item.get("user") or {}).get("type"),
            "body": item.get("body"),
            "created_at": item["created_at"],
            "updated_at": item.get("updated_at"),
        }
        for item in _paginate(token, f"/repos/{repo}/issues/{pr}/comments")
    ]


def latest_base_change(events: Iterable[dict[str, Any]]) -> str | None:
    dates = [event["created_at"] for event in events if event.get("event") == "base_ref_changed"]
    return max(dates, key=_parse_time) if dates else None


def fetch_base_change_date(token: str, repo: str, pr: int) -> str | None:
    return latest_base_change(_paginate(token, f"/repos/{repo}/issues/{pr}/timeline"))


def fetch_head_oid(token: str, repo: str, pr: int) -> str:
    owner, name = repo.split("/", 1)
    payload = _request(
        token,
        f"{API_ROOT}/graphql",
        {"query": _HEAD_OID_QUERY, "variables": {"owner": owner, "name": name, "number": pr}},
    )
    if payload.get("errors"):
        raise CheckError(f"GitHub GraphQL returned errors: {payload['errors']}")
    return payload["data"]["repository"]["pullRequest"]["headRefOid"]
