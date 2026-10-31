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


def _pull(owner: str, ref: str, number: int = 42, *, title: str = "", body: str = "") -> dict:
    return {
        "number": number,
        "html_url": URL,
        "head": {"ref": ref, "repo": {"owner": {"login": owner}}},
        "title": title,
        "body": body,
    }


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
    assert lookup[4] == f"repos/{REPO}/pulls"
    assert _fields(lookup) == {
        "head": "BenchBox-dev:feat/widget",
        "base": "develop",
        "state": "open",
        "per_page": "100",
    }
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


@pytest.mark.parametrize(
    ("title", "body", "expected"),
    [
        ("fix: existing", "Existing body.\n", {}),
        ("  fix: existing ", "Existing body.\r\n\r\n", {}),
        ("fix: changed", "Existing body.\n", {"title": "fix: changed"}),
        (None, "Changed body.\n", {"body": "Changed body.\n"}),
        ("fix: changed", "Changed body.\n", {"title": "fix: changed", "body": "Changed body.\n"}),
        (None, "", {"body": ""}),
    ],
)
def test_reused_pr_updates_only_changed_fields_in_one_request(
    tmp_path: Path, title: str | None, body: str, expected: dict[str, str]
) -> None:
    body_file = tmp_path / "body.md"
    body_file.write_text(body)
    gh = FakeGh([_pull("BenchBox-dev", "feat/widget", title="fix: existing", body="Existing body.\n")])

    pr = pr_open_rest.publish(REPO, "BenchBox-dev", "feat/widget", "feat/widget", title, body_file, gh)

    assert pr == pr_open_rest.PublishedPr("reused", 42, URL)
    patches = gh.api("PATCH")
    if not expected:
        assert patches == []
    else:
        assert len(patches) == 1
        fields = _fields(patches[0])
        actual = {
            key: body_file.read_text(encoding="utf-8") if value == f"@{body_file}" else value
            for key, value in fields.items()
        }
        assert actual == expected
    assert gh.api("POST") == []


def test_explicit_title_only_does_not_overwrite_body() -> None:
    gh = FakeGh([_pull("BenchBox-dev", "feat/widget", title="fix: existing", body="Keep this body.")])

    pr_open_rest.publish(REPO, "BenchBox-dev", "feat/widget", "feat/widget", "fix: changed", None, gh)

    assert _fields(gh.api("PATCH")[0]) == {"title": "fix: changed"}


def test_reused_pr_body_read_failure_does_not_edit(tmp_path: Path) -> None:
    gh = FakeGh([_pull("BenchBox-dev", "feat/widget", title="fix: existing")])

    with pytest.raises(OSError):
        pr_open_rest.publish(
            REPO, "BenchBox-dev", "feat/widget", "feat/widget", "fix: changed", tmp_path / "missing", gh
        )

    assert gh.api("PATCH") == []


def test_cross_repository_head_filters_by_fork_owner_and_creates_with_owner_prefix() -> None:
    gh = FakeGh([_pull("someone-else", "feat/widget")], _log(("feat: add widget", "")))

    pr = pr_open_rest.publish(REPO, "fork-owner", "feat/widget", "fork-owner:feat/widget", None, None, gh)

    assert pr.status == "created"
    assert _fields(gh.api("GET")[0])["head"] == "fork-owner:feat/widget"
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


def test_branch_with_query_metacharacters_is_passed_as_one_field() -> None:
    branch = "feat/a+b#c&d%e"
    gh = FakeGh([_pull("BenchBox-dev", branch, 3)])

    pr = pr_open_rest.publish(REPO, "BenchBox-dev", branch, branch, None, None, gh)

    assert (pr.status, pr.number) == ("reused", 3)
    assert gh.api("GET")[0][4] == f"repos/{REPO}/pulls"
    assert _fields(gh.api("GET")[0])["head"] == f"BenchBox-dev:{branch}"


def test_malformed_matching_pr_fails_clearly() -> None:
    gh = FakeGh([{"head": {"ref": "feat/w", "repo": {"owner": {"login": "BenchBox-dev"}}}}])

    with pytest.raises(pr_open_rest.PrOpenError, match="missing number or html_url"):
        pr_open_rest.publish(REPO, "BenchBox-dev", "feat/w", "feat/w", None, None, gh)


def test_draft_create_sends_the_draft_field_and_default_create_does_not() -> None:
    drafted = FakeGh([], _log(("fix: widget", "")))
    pr_open_rest.publish(REPO, "BenchBox-dev", "fix/widget", "fix/widget", None, None, drafted, draft=True)
    assert _fields(drafted.api("POST")[0])["draft"] == "true"

    ready = FakeGh([], _log(("fix: widget", "")))
    pr_open_rest.publish(REPO, "BenchBox-dev", "fix/widget", "fix/widget", None, None, ready)
    assert "draft" not in _fields(ready.api("POST")[0])


def test_draft_reuse_leaves_an_existing_pr_untouched() -> None:
    gh = FakeGh([_pull("BenchBox-dev", "fix/widget", 42)])
    pr = pr_open_rest.publish(REPO, "BenchBox-dev", "fix/widget", "fix/widget", None, None, gh, draft=True)
    assert pr.status == "reused"
    assert gh.api("POST") == [] and gh.api("PATCH") == []


class ParentGh(FakeGh):
    def __init__(self, stack: dict[str, tuple[str, str]], log: str = "") -> None:
        super().__init__([], log)
        self.stack = stack

    def __call__(self, cmd: list[str]) -> tuple[int, str]:
        if cmd[0] == "gh" and "--method" in cmd and cmd[cmd.index("--method") + 1] == "GET":
            self.calls.append(cmd)
            head = _fields(cmd)["head"]
            owner, _, ref = head.partition(":")
            if ref in self.stack and self.stack[ref][0].lower() == owner.lower():
                pull = _pull(owner, ref)
                pull["base"] = {"ref": self.stack[ref][1]}
                return 0, json.dumps([pull])
            return 0, "[]"
        return super().__call__(cmd)


def test_a_parent_that_is_an_open_pr_branch_is_accepted() -> None:
    gh = ParentGh({"feat/parent": ("BenchBox-dev", "develop")})
    pr_open_rest.verify_parent(gh, REPO, "feat/upper", "feat/parent")


def test_a_parent_that_is_not_an_open_pr_is_refused() -> None:
    with pytest.raises(pr_open_rest.PrOpenError, match="not an open pull request"):
        pr_open_rest.verify_parent(ParentGh({}), REPO, "feat/upper", "feat/missing")


def test_a_fork_parent_is_refused() -> None:
    gh = ParentGh({"feat/parent": ("someone-else", "develop")})
    with pytest.raises(pr_open_rest.PrOpenError, match="not an open pull request"):
        pr_open_rest.verify_parent(gh, REPO, "feat/upper", "feat/parent")


def test_an_integration_branch_is_not_a_parent() -> None:
    with pytest.raises(pr_open_rest.PrOpenError, match="integration branch"):
        pr_open_rest.verify_parent(ParentGh({}), REPO, "feat/upper", "develop")


def test_a_fourth_level_is_refused_and_a_third_is_accepted() -> None:
    gh = ParentGh(
        {
            "feat/one": ("BenchBox-dev", "develop"),
            "feat/two": ("BenchBox-dev", "feat/one"),
            "feat/three": ("BenchBox-dev", "feat/two"),
        }
    )
    pr_open_rest.verify_parent(gh, REPO, "feat/upper", "feat/two")
    with pytest.raises(pr_open_rest.PrOpenError, match="at most 3"):
        pr_open_rest.verify_parent(gh, REPO, "feat/upper", "feat/three")


def test_a_cyclic_base_chain_is_refused() -> None:
    gh = ParentGh({"feat/a": ("BenchBox-dev", "feat/b"), "feat/b": ("BenchBox-dev", "feat/a")})
    with pytest.raises(pr_open_rest.PrOpenError, match="cyclic"):
        pr_open_rest.verify_parent(gh, REPO, "feat/upper", "feat/a")


def test_a_stacked_create_targets_the_parent_as_a_draft_after_listing_its_own_commits() -> None:
    gh = ParentGh({"feat/parent": ("BenchBox-dev", "develop")}, _log(("feat: upper", "Upper.")))

    pr = pr_open_rest.publish(
        REPO, "BenchBox-dev", "feat/upper", "feat/upper", None, None, gh, draft=True, parent="feat/parent"
    )

    assert pr.status == "created"
    (log_call,) = [call for call in gh.calls if call[0] == "git"]
    assert log_call[-1] == "origin/feat/parent..HEAD"
    (create,) = gh.api("POST")
    assert _fields(create)["base"] == "feat/parent"
    assert _fields(create)["draft"] == "true"
    *_, lookup = gh.api("GET")
    assert _fields(lookup)["base"] == "feat/parent"


def test_main_makes_a_stacked_pr_a_draft_even_without_the_flag(capsys: pytest.CaptureFixture[str]) -> None:
    gh = ParentGh({"feat/parent": ("BenchBox-dev", "develop")}, _log(("feat: upper", "Upper.")))
    argv = ["--repo", REPO, "--head-owner", "BenchBox-dev", "--branch", "feat/upper", "--head-spec", "feat/upper"]

    assert pr_open_rest.main([*argv, "--parent", "feat/parent"], gh) == 0

    (create,) = gh.api("POST")
    assert _fields(create)["draft"] == "true"
    assert pr_open_rest.main([*argv, "--parent", "feat/missing", "--verify-parent"], gh) == 1
    assert "not an open pull request" in capsys.readouterr().err


def test_publish_enforces_the_stack_policy_without_a_separate_verify_call() -> None:
    gh = ParentGh({}, _log(("feat: upper", "Upper.")))

    with pytest.raises(pr_open_rest.PrOpenError, match="not an open pull request"):
        pr_open_rest.publish(REPO, "BenchBox-dev", "feat/upper", "feat/upper", None, None, gh, parent="feat/missing")

    assert not gh.api("POST")


def test_a_branch_cannot_be_its_own_parent() -> None:
    gh = ParentGh({"feat/upper": ("BenchBox-dev", "develop")})
    with pytest.raises(pr_open_rest.PrOpenError, match="branch being opened"):
        pr_open_rest.verify_parent(gh, REPO, "feat/upper", "feat/upper")


def test_a_fork_branch_opening_a_stack_cannot_use_its_own_fork_pr_as_the_parent() -> None:
    gh = ParentGh({"feat/parent": ("someone-else", "develop")})
    with pytest.raises(pr_open_rest.PrOpenError, match="not an open pull request"):
        pr_open_rest.verify_parent(gh, REPO, "feat/upper", "feat/parent")
