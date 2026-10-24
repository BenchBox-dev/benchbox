from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
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
        self.paths: list[str] = []

    def get_all(self, path: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        assert path.endswith("/issues")
        assert params == {"state": "all"}
        return sorted(self.paginated_issues, key=lambda item: item["number"], reverse=True)[:1000]

    def get_one(self, path: str) -> Any:
        self.paths.append(path)
        if path.startswith("search/issues"):
            if self.search_error is not None:
                raise self.search_error
            return {"items": self.search_items if self.search_items is not None else [], "incomplete_results": False}

        if path.startswith("repos/") and "/issues?" in path:
            if self.scan_error is not None:
                raise self.scan_error
            from urllib.parse import parse_qs, urlparse

            parsed = urlparse(path)
            query = parse_qs(parsed.query)
            page = int(query.get("page", ["1"])[0])
            per_page = int(query.get("per_page", ["100"])[0])
            assert query["sort"] == ["created"]
            assert query["direction"] == ["asc"]
            start = (page - 1) * per_page
            issues = sorted(self.paginated_issues, key=lambda item: item["number"])
            return issues[start : start + per_page]

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
    issues = [{"number": i, "title": f"Issue {i}", "body": "regular issue"} for i in range(1, 2601)]
    issues[1507] = pinned
    client = FakeGitHubClient(search_items=[], paginated_issues=issues)

    result = sweep.upsert_pinned_issue(client, "owner", "repo", "new digest")
    assert "updated issue #1508" in result
    assert len(client.created_issues) == 0
    assert len(client.patched_issues) == 1
    assert client.patched_issues[0][0] == "repos/owner/repo/issues/1508"
    assert client.patched_issues[0][1] == {"body": "new digest"}


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


@pytest.mark.parametrize(
    "payload", [None, {}, {"message": "Not Found"}, "bad", [None], [{}], [{"number": 1, "title": "issue", "body": 42}]]
)
def test_malformed_scan_aborts_without_writes(payload: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeGitHubClient()
    monkeypatch.setattr(client, "get_one", lambda path: payload)
    with pytest.raises(RuntimeError, match="Pinned issue lookup failed closed"):
        sweep.upsert_pinned_issue(client, "owner", "repo", "new digest")
    assert client.created_issues == []
    assert client.patched_issues == []


def test_scan_error_after_full_page_aborts_without_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeGitHubClient()
    first_page = [{"number": i, "title": "regular", "body": None} for i in range(1, 101)]
    responses = iter([None, first_page, None])
    monkeypatch.setattr(client, "get_one", lambda path: next(responses))
    with pytest.raises(RuntimeError, match="Pinned issue lookup failed closed"):
        sweep.upsert_pinned_issue(client, "owner", "repo", "new digest")
    assert client.created_issues == []
    assert client.patched_issues == []


def test_valid_exhausted_scan_allows_label_free_creation() -> None:
    client = FakeGitHubClient()
    mechanisms: list[str] = []
    assert (
        sweep.upsert_pinned_issue(client, "owner", "repo", "digest", record_mechanism=mechanisms.append)
        == "created issue #9999"
    )
    assert client.created_issues[0]["body"] == {"title": sweep.PINNED_ISSUE_TITLE, "body": "digest"}
    assert mechanisms == ["oldest_first_scan"]


def test_search_requests_canonical_oldest_match() -> None:
    from urllib.parse import parse_qs, urlparse

    client = FakeGitHubClient(search_items=[_make_pinned_issue(1508), _make_pinned_issue(2531)])
    assert sweep.find_pinned_issue(client, "owner", "repo")["number"] == 1508
    query = parse_qs(urlparse(client.paths[0]).query)
    assert query["sort"] == ["created"]
    assert query["order"] == ["asc"]


@pytest.mark.parametrize(
    "search_payload",
    [
        None,
        {},
        {"items": None, "incomplete_results": False},
        {"items": [_make_pinned_issue(2531)], "incomplete_results": True},
    ],
)
def test_unreliable_search_uses_scan(search_payload: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeGitHubClient(paginated_issues=[_make_pinned_issue(1508)])
    get_one = client.get_one
    monkeypatch.setattr(client, "get_one", lambda path: search_payload if path.startswith("search/") else get_one(path))
    assert sweep.find_pinned_issue(client, "owner", "repo")["number"] == 1508


@pytest.mark.parametrize("apply", [False, True])
def test_cli_records_actual_lookup_mechanism(
    apply: bool, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = FakeGitHubClient(paginated_issues=[_make_pinned_issue(1508)])
    monkeypatch.setattr(sweep, "resolve_token", lambda: "test-token")
    monkeypatch.setattr(sweep, "GitHubClient", lambda token: client)
    monkeypatch.setattr(sweep, "fetch_open_prs", lambda *args: [])
    assert sweep.main(["--repo", "owner/repo", *(["--apply"] if apply else [])]) == 0
    stderr = capsys.readouterr().err
    assert "digest issue lookup: oldest_first_scan" in stderr
    if not apply:
        assert "would update issue #1508" in stderr
        assert client.created_issues == []
        assert client.patched_issues == []


def _pr_with_hidden_failure() -> dict[str, Any]:
    def run(suite: int, started: str, conclusion: str) -> dict[str, Any]:
        return {
            "name": "core",
            "status": "completed",
            "conclusion": conclusion,
            "started_at": started,
            "id": suite,
            "check_suite": {"id": suite},
            "details_url": f"https://github.com/owner/repo/actions/runs/{suite}/job/1",
        }

    return {
        "number": 9,
        "title": "t",
        "html_url": "https://github.com/owner/repo/pull/9",
        "draft": False,
        "head_pushed_at": "2026-10-20T00:00:00Z",
        "updated_at": "2026-10-20T00:00:00Z",
        "check_runs": [run(1, "2026-10-24T09:00:00Z", "failure"), run(2, "2026-10-24T10:00:00Z", "success")],
    }


def test_apply_publishes_the_digest_when_only_a_hidden_failure_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    cleared = {**_make_pinned_issue(1508, state="open"), "body": f"{sweep.DIGEST_BODY_MARKER}\nNo stranded PRs"}
    client = FakeGitHubClient(paginated_issues=[cleared])
    monkeypatch.setattr(sweep, "resolve_token", lambda: "test-token")
    monkeypatch.setattr(sweep, "GitHubClient", lambda token: client)
    monkeypatch.setattr(sweep, "fetch_open_prs", lambda *args: [_pr_with_hidden_failure()])
    assert sweep.main(["--repo", "owner/repo", "--apply"]) == 0
    bodies = [payload.get("body", "") for _path, payload in client.patched_issues]
    assert any("actions/runs/1" in body and "make pr-rerun" in body for body in bodies)


def test_http_404_scan_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    client = sweep.GitHubClient("test-token")
    writes: list[str] = []
    monkeypatch.setattr(
        sweep.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            urllib.error.HTTPError("https://api.github.com", 404, "Not Found", {}, None)
        ),
    )
    monkeypatch.setattr(client, "post", lambda *args: writes.append("post"))
    monkeypatch.setattr(client, "patch", lambda *args: writes.append("patch"))
    with pytest.raises(RuntimeError, match="Pinned issue lookup failed closed"):
        sweep.upsert_pinned_issue(client, "owner", "repo", "digest")
    assert writes == []
