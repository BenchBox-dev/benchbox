from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts/pr_text.py"
SPEC = importlib.util.spec_from_file_location("pr_text", SCRIPT)
assert SPEC and SPEC.loader
pr_text = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pr_text
SPEC.loader.exec_module(pr_text)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO = "BenchBox-dev/BenchBox"
URL = "https://github.com/BenchBox-dev/BenchBox/pull/7"
HEAD = "feature/text"


class FakeGh:
    def __init__(self, current: dict[str, str] | None = None, *, fail: str | None = None) -> None:
        self.current = current or {"title": "fix: old title", "body": "Old body.\n"}
        self.fail = fail
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        if self.fail and command[1:3] == ["pr", self.fail]:
            return subprocess.CompletedProcess(command, 1, "", "boom")
        if command[1:3] == ["pr", "view"]:
            return subprocess.CompletedProcess(command, 0, json.dumps(self.current), "")
        if command[1:3] == ["pr", "create"]:
            return subprocess.CompletedProcess(command, 0, URL + "\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def edits(self) -> list[list[str]]:
        return [call for call in self.calls if call[1:3] == ["pr", "edit"]]


def _body(tmp_path: Path, text: str) -> str:
    path = tmp_path / "body.md"
    path.write_text(text, encoding="utf-8")
    return str(path)


@pytest.mark.parametrize(
    ("title", "body", "extra"),
    [
        ("", "", []),
        ("fix: new title", "", ["--title", "fix: new title"]),
        ("", "BODY", ["--body-file", "BODY"]),
        ("fix: new title", "BODY", ["--title", "fix: new title", "--body-file", "BODY"]),
    ],
)
def test_create_keeps_fill_and_adds_explicit_values(tmp_path: Path, title: str, body: str, extra: list[str]) -> None:
    body_file = _body(tmp_path, "Body.\n") if body else ""
    extra = [body_file if item == "BODY" else item for item in extra]
    gh = FakeGh()
    assert pr_text.create(gh, REPO, HEAD, title, body_file) == URL
    assert gh.calls == [["gh", "pr", "create", "--repo", REPO, "--base", "develop", "--fill", "--head", HEAD, *extra]]


def test_sync_sends_nothing_when_text_is_unchanged(tmp_path: Path) -> None:
    gh = FakeGh()
    assert not pr_text.sync(gh, REPO, URL, "fix: old title", _body(tmp_path, "Old body.\n"))
    assert gh.edits() == []


def test_sync_ignores_line_endings_and_trailing_whitespace(tmp_path: Path) -> None:
    gh = FakeGh({"title": "fix: old title", "body": "Line one.\r\nLine two.\r\n\r\n"})
    assert not pr_text.sync(gh, REPO, URL, "  fix: old title ", _body(tmp_path, "Line one.\nLine two.\n"))
    assert gh.edits() == []


def test_sync_sends_only_the_changed_body(tmp_path: Path) -> None:
    gh = FakeGh()
    body_file = _body(tmp_path, "New body.\n")
    assert pr_text.sync(gh, REPO, URL, "fix: old title", body_file)
    assert gh.edits() == [["gh", "pr", "edit", "--repo", REPO, URL, "--body-file", body_file]]


def test_sync_sends_title_and_body_in_one_edit(tmp_path: Path) -> None:
    gh = FakeGh()
    body_file = _body(tmp_path, "New body.\n")
    assert pr_text.sync(gh, REPO, URL, "fix: new title", body_file)
    assert gh.edits() == [
        ["gh", "pr", "edit", "--repo", REPO, URL, "--title", "fix: new title", "--body-file", body_file]
    ]


def test_sync_without_title_or_body_reads_nothing() -> None:
    gh = FakeGh()
    assert not pr_text.sync(gh, REPO, URL, "", "")
    assert gh.calls == []


def test_sync_stops_when_the_pr_cannot_be_read(tmp_path: Path) -> None:
    gh = FakeGh(fail="view")
    with pytest.raises(pr_text.PrTextError, match="gh pr view failed: boom"):
        pr_text.sync(gh, REPO, URL, "fix: new title", _body(tmp_path, "New body.\n"))
    assert gh.edits() == []


def test_main_reads_inputs_from_the_environment(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGh()
    environ = {"PR_TEXT_REPO": REPO, "PR_TEXT_URL": URL, "PR_TEXT_TITLE": "fix: old title"}
    assert pr_text.main(["sync"], environ, gh) == 0
    assert "unchanged; not editing" in capsys.readouterr().out
    assert pr_text.main(["sync"], {**environ, "PR_TEXT_REPO": "not a repo"}, gh) == 2
    assert pr_text.main(["publish"], environ, gh) == 2


def _stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.mark.skipif(shutil.which("make") is None, reason="needs GNU Make")
def test_pr_open_passes_the_title_through_the_environment_intact(tmp_path: Path) -> None:
    work = tmp_path / "work"
    stubs = tmp_path / "bin"
    work.mkdir()
    stubs.mkdir()
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    start = makefile.index(".PHONY: pr-open\n")
    end = makefile.index("\n\n", start)
    recipe = (
        makefile[start:end]
        .replace("$(MAKE) -s agent-write-preflight", "true")
        .replace('$(MAKE) -s pr-conflict-scan BRANCH="$$CURRENT"', "true")
    )
    (work / "Makefile").write_text(recipe + "\n", encoding="utf-8")
    record = tmp_path / "uv-calls"
    _stub(
        stubs,
        "git",
        'case "$1 $2" in\n'
        '  "branch --show-current") echo feature/text ;;\n'
        '  "remote get-url") echo git@github.com:BenchBox-dev/BenchBox.git ;;\n'
        "esac\nexit 0\n",
    )
    _stub(stubs, "gh", "exit 0\n")
    _stub(
        stubs,
        "uv",
        f'printf "%s|%s\\n" "$*" "$PR_TEXT_TITLE" >> "{record}"\n'
        'case "$*" in *"pr_text.py create"*) echo https://github.com/BenchBox-dev/BenchBox/pull/7 ;; esac\n',
    )
    title = """fix: it's "quoted" $HOME $(touch sentinel) `touch sentinel2` and %s"""
    env = {**os.environ, "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}"}
    result = subprocess.run(
        ["make", "-s", "pr-open", f"PR_TITLE={title}"],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    calls = record.read_text(encoding="utf-8").splitlines()
    create = [line for line in calls if "pr_text.py create" in line]
    assert create == [f"run -- python scripts/pr_text.py create|{title}"]
    assert not (work / "sentinel").exists() and not (work / "sentinel2").exists()
