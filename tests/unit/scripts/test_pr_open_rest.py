from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

ROOT = Path(__file__).resolve().parents[3]
REPO = "BenchBox-dev/BenchBox"
URL = "https://github.com/BenchBox-dev/BenchBox/pull/42"


def _load():
    spec = importlib.util.spec_from_file_location("pr_open_rest", ROOT / "scripts/pr_open_rest.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pr_open_rest = _load()


def _log(*commits: tuple[str, str]) -> str:
    return "".join(f"{subject}\x1f{body}\n\x1e\n" for subject, body in commits)


def _pull(owner: str, ref: str, number: int = 42) -> dict:
    return {"number": number, "html_url": URL, "head": {"ref": ref, "repo": {"owner": {"login": owner}}}}


class FakeGh:
    def __init__(self, open_pulls: list[dict], log: str = "", fail: str | None = None) -> None:
        self.open_pulls = open_pulls
        self.log = log
        self.fail = fail
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> tuple[int, str]:
        self.calls.append(cmd)
        if cmd[:3] == ["gh", "api", "graphql"] or cmd[:2] == ["gh", "pr"]:
            return 1, "HTTP 403: GitHub GraphQL is not available"
        if cmd[0] == "git":
            return 0, self.log
        method = cmd[cmd.index("--method") + 1]
        if self.fail == method:
            return 1, "HTTP 422: Validation Failed"
        if method == "GET":
            return 0, json.dumps(self.open_pulls)
        return 0, json.dumps({"number": 77, "html_url": "https://github.com/BenchBox-dev/BenchBox/pull/77"})

    def api(self, method: str) -> list[list[str]]:
        return [call for call in self.calls if call[:2] == ["gh", "api"] and call[call.index("--method") + 1] == method]


def _fields(call: list[str]) -> dict[str, str]:
    return dict(call[i + 1].split("=", 1) for i, arg in enumerate(call) if arg in ("-f", "-F"))


def test_creates_pr_over_rest_with_oldest_commit_subject_as_title() -> None:
    gh = FakeGh([], _log(("feat: add widget", "Why widget."), ("fix: widget edge", "")))

    pr = pr_open_rest.publish(REPO, "BenchBox-dev", "feat/widget", "feat/widget", None, None, gh)

    assert pr == pr_open_rest.PublishedPr("created", 77, "https://github.com/BenchBox-dev/BenchBox/pull/77")
    (lookup,) = gh.api("GET")
    assert lookup[-1] == f"repos/{REPO}/pulls?head=BenchBox-dev:feat/widget&base=develop&state=open&per_page=100"
    (create,) = gh.api("POST")
    assert create[4] == f"repos/{REPO}/pulls"
    assert _fields(create) == {
        "title": "feat: add widget",
        "head": "feat/widget",
        "base": "develop",
        "body": "- feat: add widget\n- fix: widget edge",
    }


def test_multi_commit_title_never_uses_branch_name() -> None:
    gh = FakeGh([], _log(("chore: remove site from core", ""), ("chore: follow-up", "")))

    pr_open_rest.publish(
        REPO, "BenchBox-dev", "chore/remove-site-from-core", "chore/remove-site-from-core", None, None, gh
    )

    assert _fields(gh.api("POST")[0])["title"] == "chore: remove site from core"


def test_single_commit_body_is_commit_body_and_explicit_title_wins() -> None:
    gh = FakeGh([], _log(("feat: add widget", "Line one.\n\nLine two.")))

    pr_open_rest.publish(REPO, "BenchBox-dev", "feat/widget", "feat/widget", "feat: chosen title", None, gh)

    fields = _fields(gh.api("POST")[0])
    assert fields["title"] == "feat: chosen title"
    assert fields["body"] == "Line one.\n\nLine two."


def test_body_file_is_sent_as_file_on_create(tmp_path: Path) -> None:
    body = tmp_path / "body.md"
    body.write_text("Summary\n")
    gh = FakeGh([], _log(("feat: add widget", "")))

    pr_open_rest.publish(REPO, "BenchBox-dev", "feat/widget", "feat/widget", None, body, gh)

    assert _fields(gh.api("POST")[0])["body"] == f"@{body}"


def test_reuses_existing_pr_and_updates_body(tmp_path: Path) -> None:
    body = tmp_path / "body.md"
    body.write_text("Updated\n")
    gh = FakeGh([_pull("benchbox-DEV", "feat/widget")])

    pr = pr_open_rest.publish(REPO, "BenchBox-dev", "feat/widget", "feat/widget", None, body, gh)

    assert pr == pr_open_rest.PublishedPr("reused", 42, URL)
    assert gh.api("POST") == []
    (patch,) = gh.api("PATCH")
    assert patch[4] == f"repos/{REPO}/pulls/42"
    assert _fields(patch) == {"body": f"@{body}"}


def test_reuse_without_body_file_leaves_pr_untouched() -> None:
    gh = FakeGh([_pull("BenchBox-dev", "feat/widget")])

    pr = pr_open_rest.publish(REPO, "BenchBox-dev", "feat/widget", "feat/widget", None, None, gh)

    assert pr.status == "reused"
    assert gh.api("PATCH") == [] and gh.api("POST") == []


def test_cross_repository_head_filters_by_fork_owner_and_creates_with_owner_prefix() -> None:
    gh = FakeGh([_pull("someone-else", "feat/widget")], _log(("feat: add widget", "")))

    pr = pr_open_rest.publish(REPO, "fork-owner", "feat/widget", "fork-owner:feat/widget", None, None, gh)

    assert pr.status == "created"
    assert "head=fork-owner:feat/widget" in gh.api("GET")[0][-1]
    assert _fields(gh.api("POST")[0])["head"] == "fork-owner:feat/widget"


def test_reuses_matching_cross_repository_pr() -> None:
    gh = FakeGh([_pull("fork-owner", "feat/widget", 9)])

    pr = pr_open_rest.publish(REPO, "fork-owner", "feat/widget", "fork-owner:feat/widget", None, None, gh)

    assert (pr.status, pr.number) == ("reused", 9)


def test_uses_only_rest_routes(tmp_path: Path) -> None:
    body = tmp_path / "body.md"
    body.write_text("x")
    for pulls in ([], [_pull("BenchBox-dev", "feat/widget")]):
        gh = FakeGh(pulls, _log(("feat: add widget", "")))
        pr_open_rest.publish(REPO, "BenchBox-dev", "feat/widget", "feat/widget", None, body, gh)
        assert all(call[:2] in (["gh", "api"], ["git", "log"]) for call in gh.calls)
        assert not any(arg == "graphql" for call in gh.calls for arg in call)


def test_refuses_branch_without_commits() -> None:
    gh = FakeGh([], "")

    with pytest.raises(pr_open_rest.PrOpenError, match="no commits ahead"):
        pr_open_rest.publish(REPO, "BenchBox-dev", "feat/widget", "feat/widget", None, None, gh)
    assert gh.api("POST") == []


def test_main_prints_status_number_url_and_reports_create_failure(capsys: pytest.CaptureFixture[str]) -> None:
    args = ["--repo", REPO, "--head-owner", "BenchBox-dev", "--branch", "feat/w", "--head-spec", "feat/w"]

    assert pr_open_rest.main(args, FakeGh([_pull("BenchBox-dev", "feat/w")])) == 0
    assert capsys.readouterr().out == f"reused 42 {URL}\n"

    assert pr_open_rest.main(args, FakeGh([], _log(("feat: w", "")), fail="POST")) == 1
    assert "PR create failed: HTTP 422" in capsys.readouterr().err


def test_main_rejects_bad_repo_and_missing_body_file(tmp_path: Path) -> None:
    gh = FakeGh([])
    base = ["--head-owner", "o", "--branch", "b", "--head-spec", "b"]

    assert pr_open_rest.main(["--repo", "not-a-repo", *base], gh) == 2
    assert pr_open_rest.main(["--repo", REPO, *base, "--body-file", str(tmp_path / "missing.md")], gh) == 1
    assert gh.calls == []
