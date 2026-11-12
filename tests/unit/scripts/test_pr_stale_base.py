from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import pr_stale_base

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO = "BenchBox-dev/BenchBox"
HEAD = "a" * 40
SINCE = "2026-11-11T10:00:00Z"
SHA_NEW = "1" * 40
SHA_OLD = "2" * 40


class FakeGh:
    def __init__(
        self,
        *,
        since: str | None = SINCE,
        commits: list[str] | None = None,
        pr_files: list[str] | None = None,
        commit_files: dict[str, list[str]] | None = None,
        contents: dict[str, str] | None = None,
        fail: str | None = None,
        reopen_codes: list[int] | None = None,
    ) -> None:
        self.since = since
        self.commits = commits if commits is not None else [SHA_NEW]
        self.pr_files = pr_files or []
        self.commit_files = commit_files or {}
        self.contents = contents or {}
        self.fail = fail
        self.reopen_codes = reopen_codes or [0]
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> tuple[int, str]:
        self.calls.append(cmd)
        last = cmd[-1]
        if cmd[:3] == ["gh", "pr", "close"]:
            return 0, ""
        if cmd[:3] == ["gh", "pr", "reopen"]:
            return (self.reopen_codes.pop(0), "") if len(self.reopen_codes) > 1 else (self.reopen_codes[0], "boom")
        if self.fail and self.fail in last:
            return 1, "gh: HTTP 502"
        if "/actions/workflows/ci.yml/runs" in last:
            return 0, "" if self.since is None else self.since + "\n"
        if "/commits?sha=develop" in last:
            assert f"since={self.since}" in last
            return 0, "\n".join(self.commits)
        if last.endswith("/pulls/7/files"):
            return 0, "\n".join(self.pr_files)
        if "/contents/" in last:
            path = last.split("/contents/")[1].split("?")[0]
            return 0, self.contents[path]
        if "/commits/" in last:
            return 0, "\n".join(self.commit_files[last.rsplit("/", 1)[1]])
        raise AssertionError(f"unexpected command {cmd}")

    def refreshed(self) -> list[list[str]]:
        return [call for call in self.calls if call[:3] in (["gh", "pr", "close"], ["gh", "pr", "reopen"])]


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    (tmp_path / "benchbox").mkdir()
    (tmp_path / "benchbox" / "core.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "benchbox" / "helper.py").write_text("Y = 1\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "tool.py").write_text("Z = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text(
        "from benchbox import helper\nimport benchbox.core\nSCRIPT = 'scripts/tool.py'\n", encoding="utf-8"
    )
    return tmp_path


def _refusals(gh: FakeGh, checkout: Path) -> list[str]:
    return pr_stale_base.stale_base_refusals(gh, REPO, 7, HEAD, "develop", checkout)


def test_imported_paths_resolve_modules_from_imports_and_path_strings(checkout: Path) -> None:
    source = (checkout / "tests" / "test_a.py").read_text(encoding="utf-8")
    found = pr_stale_base.imported_paths(source, "tests/test_a.py", lambda p: (checkout / p).is_file())
    assert found == {"benchbox/helper.py", "benchbox/core.py", "scripts/tool.py"}


def test_relative_imports_resolve_against_the_importing_file(checkout: Path) -> None:
    found = pr_stale_base.imported_paths("from . import core\n", "benchbox/x.py", lambda p: (checkout / p).is_file())
    assert found == {"benchbox/core.py"}


def test_unparseable_source_imports_nothing() -> None:
    assert pr_stale_base.imported_paths("def (:", "tests/x.py", lambda _p: True) == set()


def test_a_pr_without_a_ci_run_is_refused_without_refreshing_ci(checkout: Path) -> None:
    gh = FakeGh(since=None)
    reasons = _refusals(gh, checkout)
    assert "cannot be established" in reasons[0]
    assert gh.refreshed() == []


def test_no_develop_commits_after_the_tested_base_allows_arming(checkout: Path) -> None:
    gh = FakeGh(commits=[])
    assert _refusals(gh, checkout) == []
    assert gh.refreshed() == []


def test_unrelated_develop_changes_allow_arming(checkout: Path) -> None:
    gh = FakeGh(pr_files=["benchbox/core.py"], commit_files={SHA_NEW: ["examples/readme.md"]})
    assert _refusals(gh, checkout) == []
    assert gh.refreshed() == []


def test_a_develop_change_to_a_file_the_pr_changes_refuses_and_starts_fresh_ci(checkout: Path) -> None:
    gh = FakeGh(pr_files=["benchbox/core.py"], commit_files={SHA_NEW: ["benchbox/core.py", "examples/x.md"]})
    reasons = _refusals(gh, checkout)
    assert len(reasons) == 1
    assert "benchbox/core.py" in reasons[0]
    assert "waiting for full CI run" in reasons[0]
    assert gh.refreshed() == [
        ["gh", "pr", "close", "7", "--repo", REPO],
        ["gh", "pr", "reopen", "7", "--repo", REPO],
    ]


def test_a_develop_change_to_a_file_the_prs_tests_import_refuses(checkout: Path) -> None:
    gh = FakeGh(pr_files=["tests/test_a.py"], commit_files={SHA_NEW: ["benchbox/helper.py"]})
    reasons = _refusals(gh, checkout)
    assert "benchbox/helper.py" in reasons[0]
    assert len(gh.refreshed()) == 2


def test_a_develop_test_that_imports_a_file_the_pr_changes_refuses(checkout: Path) -> None:
    gh = FakeGh(
        pr_files=["benchbox/core.py"],
        commit_files={SHA_NEW: ["tests/test_new.py"]},
        contents={"tests/test_new.py": "import benchbox.core\n"},
    )
    reasons = _refusals(gh, checkout)
    assert "tests/test_new.py" in reasons[0]


def test_a_develop_test_that_imports_nothing_the_pr_changes_is_ignored(checkout: Path) -> None:
    gh = FakeGh(
        pr_files=["benchbox/core.py"],
        commit_files={SHA_NEW: ["tests/test_new.py"]},
        contents={"tests/test_new.py": "import benchbox.helper\n"},
    )
    assert _refusals(gh, checkout) == []


def test_too_many_develop_commits_refuse_as_unbounded(checkout: Path) -> None:
    gh = FakeGh(commits=[f"{n:040d}" for n in range(pr_stale_base.DEVELOP_COMMIT_LIMIT)])
    reasons = _refusals(gh, checkout)
    assert "too many to compare" in reasons[0]
    assert len(gh.refreshed()) == 2


def test_a_reopen_that_keeps_failing_is_reported_with_the_manual_step(checkout: Path) -> None:
    gh = FakeGh(pr_files=["benchbox/core.py"], commit_files={SHA_NEW: ["benchbox/core.py"]}, reopen_codes=[1])
    reasons = _refusals(gh, checkout)
    assert "gh pr reopen 7" in reasons[0]


@pytest.mark.parametrize("fail", ["/actions/workflows/", "/commits?sha=", "/pulls/7/files", f"/commits/{SHA_NEW}"])
def test_an_unreadable_listing_fails_closed(checkout: Path, fail: str) -> None:
    gh = FakeGh(pr_files=["benchbox/core.py"], commit_files={SHA_NEW: ["benchbox/core.py"]}, fail=fail)
    with pytest.raises(Exception, match="cannot .*HTTP 502") as raised:
        _refusals(gh, checkout)
    assert type(raised.value).__name__ == "LandingError"
    assert gh.refreshed() == []


def test_script_tests_that_import_by_bare_module_name_are_resolved(checkout: Path) -> None:
    found = pr_stale_base.imported_paths(
        "import tool\nfrom tool import Z\n", "tests/test_b.py", lambda p: (checkout / p).is_file()
    )
    assert found == {"scripts/tool.py"}


def test_a_develop_change_to_a_module_a_changed_source_file_imports_refuses(checkout: Path) -> None:
    (checkout / "benchbox" / "core.py").write_text("from benchbox import helper\n", encoding="utf-8")
    gh = FakeGh(pr_files=["benchbox/core.py"], commit_files={SHA_NEW: ["benchbox/helper.py"]})
    assert "benchbox/helper.py" in _refusals(gh, checkout)[0]


def test_a_develop_source_file_that_imports_a_file_the_pr_changes_refuses(checkout: Path) -> None:
    gh = FakeGh(
        pr_files=["benchbox/core.py"],
        commit_files={SHA_NEW: ["benchbox/new_caller.py"]},
        contents={"benchbox/new_caller.py": "from benchbox import core\n"},
    )
    assert "benchbox/new_caller.py" in _refusals(gh, checkout)[0]


def test_a_develop_file_importing_a_module_the_pr_removed_is_flagged(checkout: Path) -> None:
    gh = FakeGh(
        pr_files=["benchbox/gone.py"],
        commit_files={SHA_NEW: ["benchbox/new_caller.py"]},
        contents={"benchbox/new_caller.py": "from benchbox import gone\n"},
    )
    assert "benchbox/new_caller.py" in _refusals(gh, checkout)[0]


def test_a_non_python_data_file_named_by_a_changed_file_is_related(checkout: Path) -> None:
    (checkout / "benchbox" / "data.json").write_text("{}\n", encoding="utf-8")
    (checkout / "benchbox" / "core.py").write_text('PATH = "benchbox/data.json"\n', encoding="utf-8")
    gh = FakeGh(pr_files=["benchbox/core.py"], commit_files={SHA_NEW: ["benchbox/data.json"]})
    assert "benchbox/data.json" in _refusals(gh, checkout)[0]


@pytest.mark.parametrize("added", ["tests/conftest.py", "conftest.py", "pytest.ini", "pytest-ci.ini"])
def test_a_changed_test_depends_on_conftest_and_pytest_config_even_when_develop_adds_them(
    checkout: Path, added: str
) -> None:
    gh = FakeGh(pr_files=["tests/test_a.py"], commit_files={SHA_NEW: [added]})
    assert added in _refusals(gh, checkout)[0]


def test_importing_a_submodule_depends_on_its_parent_package_init_even_when_develop_adds_it(checkout: Path) -> None:
    found = pr_stale_base.imported_paths("import benchbox.core\n", "tests/t.py", lambda p: (checkout / p).is_file())
    assert "benchbox/__init__.py" in found
