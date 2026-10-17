from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

CLI_DESCRIPTION = "Create a pull request, or update its title and body only when they changed."

BASE_BRANCH = "develop"
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


class PrTextError(RuntimeError):
    pass


def _subprocess_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)


def _gh(runner: Runner, arguments: list[str]) -> str:
    try:
        result = runner(["gh", *arguments])
    except OSError as exc:
        raise PrTextError("could not execute the GitHub CLI") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise PrTextError(f"gh pr {arguments[1]} failed: {detail}")
    return result.stdout


def normalise_body(text: str) -> str:
    return text.replace("\r\n", "\n").rstrip()


def normalise_title(text: str) -> str:
    return text.strip()


def _read_body(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise PrTextError(f"cannot read PR_BODY_FILE {path}: {exc.strerror}") from exc


def create_arguments(repo: str, head: str, title: str, body_file: str) -> list[str]:
    arguments = ["pr", "create", "--repo", repo, "--base", BASE_BRANCH, "--fill", "--head", head]
    if title:
        arguments += ["--title", title]
    if body_file:
        arguments += ["--body-file", body_file]
    return arguments


def create(runner: Runner, repo: str, head: str, title: str, body_file: str) -> str:
    if body_file:
        _read_body(body_file)
    return _gh(runner, create_arguments(repo, head, title, body_file)).strip()


def edit_arguments(
    url: str, repo: str, current: Mapping[str, str], title: str, body_file: str, body: str | None
) -> list[str]:
    arguments: list[str] = []
    if title and normalise_title(title) != normalise_title(current.get("title") or ""):
        arguments += ["--title", title]
    if body is not None and normalise_body(body) != normalise_body(current.get("body") or ""):
        arguments += ["--body-file", body_file]
    if not arguments:
        return []
    return ["pr", "edit", "--repo", repo, url, *arguments]


def sync(runner: Runner, repo: str, url: str, title: str, body_file: str) -> bool:
    body = _read_body(body_file) if body_file else None
    if not title and body is None:
        return False
    try:
        current = json.loads(_gh(runner, ["pr", "view", "--repo", repo, url, "--json", "title,body"]))
    except ValueError as exc:
        raise PrTextError("gh pr view returned invalid JSON") from exc
    if not isinstance(current, dict):
        raise PrTextError("gh pr view returned an invalid record")
    arguments = edit_arguments(url, repo, current, title, body_file, body)
    if not arguments:
        return False
    _gh(runner, arguments)
    return True


def main(
    argv: Sequence[str] | None = None, environ: Mapping[str, str] = os.environ, runner: Runner = _subprocess_runner
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args not in (["create"], ["sync"]):
        print("usage: pr_text.py create|sync (inputs come from PR_TEXT_* environment variables)", file=sys.stderr)
        return 2
    repo = environ.get("PR_TEXT_REPO", "")
    title = environ.get("PR_TEXT_TITLE", "")
    body_file = environ.get("PR_TEXT_BODY_FILE", "")
    if not REPOSITORY_RE.fullmatch(repo):
        print("PR_TEXT_REPO must be a GitHub owner/name", file=sys.stderr)
        return 2
    try:
        if args == ["create"]:
            head = environ.get("PR_TEXT_HEAD", "")
            if not head:
                print("PR_TEXT_HEAD is required to create a PR", file=sys.stderr)
                return 2
            print(create(runner, repo, head, title, body_file))
            return 0
        url = environ.get("PR_TEXT_URL", "")
        if not url:
            print("PR_TEXT_URL is required to update a PR", file=sys.stderr)
            return 2
        if sync(runner, repo, url, title, body_file):
            print("Updated the PR title or body.")
        else:
            print("PR title and body unchanged; not editing.")
        return 0
    except PrTextError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
