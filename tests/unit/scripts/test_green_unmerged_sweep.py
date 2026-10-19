from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = [
    pytest.mark.unit,
]

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "_project/scripts/green_unmerged_sweep.py"

spec = importlib.util.spec_from_file_location("green_unmerged_sweep", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
sweep = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sweep
spec.loader.exec_module(sweep)


class FakeGitHubClient:
    def __init__(
        self,
        *,
        search_items: list[dict[str, Any]] | None = None,
        search_error: Exception | None = None,
        paginated_issues: list[dict[str, Any]] | None = None,
        scan_error: Exception | None = None,
    ) -> None:
        self.search_items = search_items
        self.search_error = search_error
        self.paginated_issues = paginated_issues or []
        self.scan_error = scan_error
        self.created_issues: list[dict[str, Any]] = []
        self.patched_issues: list[tuple[str, dict[str, Any]]] = []

    def get_one(self, path: str) -> Any:
        if path.startswith("search/issues"):
            if self.search_error is not None:
                raise self.search_error
            return {"items": self.search_items if self.search_items is not None else []}

        if path.startswith("repos/") and "/issues?" in path:
            if self.scan_error is not None:
                raise self.scan_error
            from urllib.parse import parse_qs, urlparse

            parsed = urlparse(path)
            query = parse_qs(parsed.query)
            page = int(query.get("page", ["1"])[0])
            per_page = int(query.get("per_page", ["100"])[0])
            start = (page - 1) * per_page
            return self.paginated_issues[start : start + per_page]

        return None

    def post(self, path: str, body: dict[str, Any]) -> Any:
        self.created_issues.append({"path": path, "body": body})
        return {"number": 9999, "title": body.get("title")}

    def patch(self, path: str, body: dict[str, Any]) -> Any:
        self.patched_issues.append((path, body))
        return {"number": int(path.split("/")[-1]), "title": sweep.PINNED_ISSUE_TITLE}


def _make_pinned_issue(number: int = 1508, state: str = "closed") -> dict[str, Any]:
    return {
        "number": number,
        "title": sweep.PINNED_ISSUE_TITLE,
        "body": f"{sweep.DIGEST_BODY_MARKER}\nDigest content",
        "state": state,
    }


def test_search_mechanism_finds_pinned_issue() -> None:
    pinned = _make_pinned_issue(1508, "closed")
    client = FakeGitHubClient(search_items=[pinned])
    mechanisms: list[str] = []

    found = sweep.find_pinned_issue(client, "owner", "repo", record_mechanism=mechanisms.append)
    assert found is not None
    assert found["number"] == 1508
    assert mechanisms == ["search"]


def test_fallback_to_oldest_first_scan_when_search_empty() -> None:
    pinned = _make_pinned_issue(1508, "open")
    client = FakeGitHubClient(search_items=[], paginated_issues=[pinned])
    mechanisms: list[str] = []

    found = sweep.find_pinned_issue(client, "owner", "repo", record_mechanism=mechanisms.append)
    assert found is not None
    assert found["number"] == 1508
    assert mechanisms == ["oldest_first_scan"]


def test_pinned_issue_beyond_1000_records_is_updated_not_duplicated() -> None:
    pinned = _make_pinned_issue(1508, "open")
    issues = [{"number": i, "title": f"Issue {i}", "body": "regular issue"} for i in range(1, 1051)]
    issues.append(pinned)
    client = FakeGitHubClient(search_items=[], paginated_issues=issues)
    mechanisms: list[str] = []

    result = sweep.upsert_pinned_issue(client, "owner", "repo", "new digest", record_mechanism=mechanisms.append)
    assert "updated issue #1508" in result
    assert len(client.created_issues) == 0
    assert len(client.patched_issues) == 1
    assert client.patched_issues[0][0] == "repos/owner/repo/issues/1508"
    assert client.patched_issues[0][1] == {"body": "new digest"}
    assert mechanisms == ["oldest_first_scan"]


def test_reopen_if_closed_and_no_label() -> None:
    pinned = _make_pinned_issue(1508, "closed")
    client = FakeGitHubClient(search_items=[pinned])

    result = sweep.upsert_pinned_issue(client, "owner", "repo", "new digest")
    assert "updated issue #1508" in result
    assert len(client.created_issues) == 0
    assert len(client.patched_issues) == 1
    patch_payload = client.patched_issues[0][1]
    assert patch_payload["state"] == "open"
    assert patch_payload["body"] == "new digest"
    assert "labels" not in patch_payload


def test_lookup_error_aborts_without_creating_or_editing() -> None:
    client = FakeGitHubClient(
        search_error=RuntimeError("Search rate limited"),
        scan_error=RuntimeError("Connection reset"),
    )

    with pytest.raises(RuntimeError, match="Pinned issue lookup failed closed"):
        sweep.upsert_pinned_issue(client, "owner", "repo", "new digest")

    assert len(client.created_issues) == 0
    assert len(client.patched_issues) == 0
