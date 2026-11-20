from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from scripts import release_artifact_consumer as consumer, release_line_create
from tests.unit.scripts.test_release_artifact_execution import git
from tests.unit.scripts.test_release_maintenance_admission import commit_version, no_fetch, tag

pytestmark = [pytest.mark.unit, pytest.mark.medium]

ROOT = Path(__file__).resolve().parents[3]
LINE = "release/0.4.x"
OLD_MAKEFILE = "include make/pr-landing.mk\n"
OLD_LANDING = ".PHONY: pr-open pr-arm\npr-open:\n\t@echo the tag opens pull requests against develop only\n"

requires_make = pytest.mark.skipif(
    os.name == "nt" or shutil.which("make") is None,
    reason="needs GNU Make and a POSIX shell; the stubs are shell scripts",
)

RECORDER = """#!{python}
import json, os, subprocess, sys
name = os.path.basename(sys.argv[0])
with open(os.environ["CALLS"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps([name, sys.argv[1:], os.environ.get("PR_ARM_BASE", "")]) + "\\n")
if "scripts/pr_base.py" in sys.argv:
    sys.exit(subprocess.call([sys.executable, "scripts/pr_base.py", sys.argv[-1]]))
if "scripts/local_review.py" in sys.argv:
    sys.exit(0)
if "scripts/pr_open_rest.py" in sys.argv:
    print("created", 7, "https://github.com/BenchBox-dev/BenchBox/pull/7")
"""
GIT = """#!/bin/sh
echo "$*" >> "$GIT_LOG"
case "$1 $2" in
  "branch --show-current") echo fix/x ;;
  "remote get-url") echo git@github.com:BenchBox-dev/BenchBox.git ;;
  "rev-parse HEAD") echo 1111111111111111111111111111111111111111 ;;
esac
exit 0
"""


def _source(tmp_path: Path, *, declares_landing: bool = True) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-b", "develop")
    git(root, "config", "user.name", "Test Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    (root / "Makefile").write_text(OLD_MAKEFILE, encoding="utf-8")
    (root / "make").mkdir()
    (root / "make/pr-landing.mk").write_text(OLD_LANDING, encoding="utf-8")
    git(root, "add", "Makefile", "make/pr-landing.mk")
    commit_version(root, "0.4.1")
    tag(root, "v0.4.1")
    shipped = [*consumer.CREATION_LANDING_PATHS, "scripts/release_artifact_consumer.py"] if declares_landing else []
    for path in shipped:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / path, target)
        git(root, "add", path)
    commit_version(root, "0.5.0")
    git(root, "update-ref", "refs/remotes/origin/develop", "HEAD")
    return root


@pytest.fixture
def old_tag_source(tmp_path: Path) -> Path:
    return _source(tmp_path)


def _creation_without_landing(root: Path) -> str:
    source = git(root, "rev-parse", "refs/remotes/origin/develop")
    git(root, "checkout", "-q", "--detach", "v0.4.1")
    git(root, "commit", "-q", "--allow-empty", "-m", f"Carry develop's reviewer files\n\nDevelop-Source: {source}")
    return git(root, "rev-parse", "HEAD")


def test_a_creation_commit_from_before_the_landing_carry_stays_admissible(tmp_path: Path) -> None:
    root = _source(tmp_path, declares_landing=False)
    legacy = _creation_without_landing(root)

    consumer.verify_line(root, LINE, legacy, lambda _path: [])


def test_a_creation_commit_from_a_declaring_source_must_carry_the_landing_tooling(tmp_path: Path) -> None:
    root = _source(tmp_path)
    bare = _creation_without_landing(root)

    with pytest.raises(ValueError, match=r"creation does not carry"):
        consumer.verify_line(root, LINE, bare, lambda _path: [])


def _source_declaring(tmp_path: Path, consumer_text: str, shipped: tuple[str, ...]) -> tuple[Path, str]:
    root = _source(tmp_path, declares_landing=False)
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts/release_artifact_consumer.py").write_text(consumer_text, encoding="utf-8")
    git(root, "add", "scripts/release_artifact_consumer.py")
    for path in shipped:
        shutil.copy2(ROOT / path, root / path)
        git(root, "add", path)
    git(root, "commit", "-q", "-m", "declare the landing carry")
    git(root, "update-ref", "refs/remotes/origin/develop", "HEAD")
    return root, git(root, "rev-parse", "HEAD")


def _creation_carrying(root: Path, source: str, carried: tuple[str, ...]) -> str:
    git(root, "checkout", "-q", "--detach", "v0.4.1")
    for path in carried:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / path, root / path)
        git(root, "add", path)
    git(root, "commit", "-q", "--allow-empty", "-m", f"Carry develop's files\n\nDevelop-Source: {source}")
    return git(root, "rev-parse", "HEAD")


def test_a_creation_commit_is_checked_against_the_tuples_its_source_declared(tmp_path: Path) -> None:
    declared = ("scripts/pr_base.py",)
    text = f"CREATION_LANDING_PATHS = {declared!r}\nCREATION_LANDING_TESTS = ()\n"
    root, source = _source_declaring(tmp_path, text, declared)

    consumer.verify_line(root, LINE, _creation_carrying(root, source, declared), lambda _path: [])
    with pytest.raises(ValueError, match=r"creation does not carry scripts/pr_base.py"):
        consumer.verify_line(root, LINE, _creation_carrying(root, source, ()), lambda _path: [])


def test_a_source_that_only_names_the_tuple_declares_no_landing_carry(tmp_path: Path) -> None:
    root, source = _source_declaring(tmp_path, "# CREATION_LANDING_PATHS is not declared yet\n", ())

    consumer.verify_line(root, LINE, _creation_carrying(root, source, ()), lambda _path: [])


def test_a_source_whose_declared_tuple_is_not_a_literal_is_refused(tmp_path: Path) -> None:
    root, source = _source_declaring(tmp_path, "CREATION_LANDING_PATHS = tuple(sorted(()))\n", ())

    with pytest.raises(ValueError, match=r"CREATION_LANDING_PATHS as a non-literal"):
        consumer.verify_line(root, LINE, _creation_carrying(root, source, ()), lambda _path: [])


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("CREATION_LANDING_PATHS = ()\nCREATION_LANDING_PATHS += ('a',)\nCREATION_LANDING_TESTS = ()\n", "form"),
        ("CREATION_LANDING_PATHS, CREATION_LANDING_TESTS = (), ()\n", "form"),
        ("CREATION_LANDING_PATHS = ()\n", "declares only one of"),
    ],
)
def test_a_source_declaring_the_carry_in_a_form_the_check_cannot_read_is_refused(
    tmp_path: Path, text: str, message: str
) -> None:
    root, source = _source_declaring(tmp_path, text, ())

    with pytest.raises(ValueError, match=message):
        consumer.verify_line(root, LINE, _creation_carrying(root, source, ()), lambda _path: [])


def test_an_annotated_declaration_of_the_carry_is_enforced(tmp_path: Path) -> None:
    declared = ("scripts/pr_base.py",)
    text = f"CREATION_LANDING_PATHS: tuple[str, ...] = {declared!r}\nCREATION_LANDING_TESTS: tuple[str, ...] = ()\n"
    root, source = _source_declaring(tmp_path, text, declared)

    consumer.verify_line(root, LINE, _creation_carrying(root, source, declared), lambda _path: [])
    with pytest.raises(ValueError, match=r"creation does not carry scripts/pr_base.py"):
        consumer.verify_line(root, LINE, _creation_carrying(root, source, ()), lambda _path: [])


def _create(root: Path) -> str:
    branch, commit = release_line_create.create_line(root, "v0.4.1", fetch=no_fetch)
    assert branch == LINE
    return commit


def _checkout(root: Path, commit: str, destination: Path) -> Path:
    destination.mkdir()
    archive = destination.parent / f"{destination.name}.tar"
    git(root, "archive", "--format=tar", "-o", str(archive), commit)
    with tarfile.open(archive) as bundle:
        bundle.extractall(destination, filter="data")
    return destination


def test_the_old_tag_has_no_landing_tooling_for_a_line(old_tag_source: Path) -> None:
    for path in ("scripts/pr_base.py", "scripts/gh_transient.py", "scripts/pr_open_rest.py"):
        assert (
            subprocess.run(
                ["git", "-C", str(old_tag_source), "cat-file", "-e", f"v0.4.1:{path}"], capture_output=True, check=False
            ).returncode
            != 0
        )
    assert "PR_OPEN_BASE" not in git(old_tag_source, "show", "v0.4.1:make/pr-landing.mk")


def test_the_creation_commit_carries_develops_landing_tooling_and_nothing_else(old_tag_source: Path) -> None:
    commit = _create(old_tag_source)

    changed = set(git(old_tag_source, "diff", "--name-only", "v0.4.1", commit).splitlines())
    assert changed <= set(consumer.creation_carried_paths(old_tag_source, commit, "origin/develop"))
    assert set(consumer.CREATION_LANDING_PATHS) <= changed
    for path in consumer.CREATION_LANDING_PATHS:
        assert git(old_tag_source, "ls-tree", commit, "--", path) == git(
            old_tag_source, "ls-tree", "origin/develop", "--", path
        )
    assert git(old_tag_source, "show", f"{commit}:Makefile") == OLD_MAKEFILE.strip()
    assert not [path for path in changed if path.startswith("benchbox/")]


def test_verify_line_refuses_a_creation_commit_with_the_tags_older_landing_tooling(old_tag_source: Path) -> None:
    commit = _create(old_tag_source)
    git(old_tag_source, "checkout", "-q", "--detach", commit)
    (old_tag_source / "make/pr-landing.mk").write_text(OLD_LANDING, encoding="utf-8")
    git(old_tag_source, "add", "make/pr-landing.mk")
    git(old_tag_source, "commit", "-q", "--amend", "--no-edit")
    stale = git(old_tag_source, "rev-parse", "HEAD")

    with pytest.raises(ValueError, match=r"does not carry make/pr-landing\.mk|sets make/pr-landing\.mk"):
        consumer.verify_line(old_tag_source, LINE, stale, lambda _path: [])


def _stubs(tmp_path: Path) -> tuple[Path, Path, Path]:
    stubs = tmp_path / "stubs"
    stubs.mkdir(parents=True)
    for name in ("uv", "python3", "fake-make"):
        (stubs / name).write_text(RECORDER.format(python=sys.executable), encoding="utf-8")
        (stubs / name).chmod(0o755)
    (stubs / "git").write_text(GIT, encoding="utf-8")
    (stubs / "git").chmod(0o755)
    return stubs, tmp_path / "calls.jsonl", tmp_path / "git.log"


def _run(
    line: Path, tmp_path: Path, *arguments: str
) -> tuple[subprocess.CompletedProcess[str], list[list[object]], str]:
    stubs, calls, git_log = _stubs(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "CALLS": str(calls),
        "GIT_LOG": str(git_log),
        "BENCHBOX_MAKE_TIMINGS": "0",
    }
    result = subprocess.run(
        ["make", "-s", f"MAKE={stubs / 'fake-make'}", *arguments],
        cwd=line,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    recorded = [json.loads(row) for row in calls.read_text(encoding="utf-8").splitlines()] if calls.exists() else []
    return result, recorded, git_log.read_text(encoding="utf-8") if git_log.exists() else ""


@requires_make
def test_a_line_cut_from_an_old_tag_opens_and_arms_pull_requests_into_the_line(
    old_tag_source: Path, tmp_path: Path
) -> None:
    line = _checkout(old_tag_source, _create(old_tag_source), tmp_path / "line")

    opened, calls, git_log = _run(
        line, tmp_path / "open", "pr-open", f"BASE={LINE}", "READY=1", "TITLE=fix: repair a thing"
    )

    assert opened.returncode == 0, opened.stderr
    (publisher,) = [args for name, args, _ in calls if name == "uv" and "scripts/pr_open_rest.py" in args]
    assert publisher[publisher.index("--base") + 1] == LINE
    assert f"fetch origin {LINE} --quiet" in git_log
    (review,) = [args for name, args, _ in calls if name == "python3" and "scripts/local_review.py" in args]
    assert review[review.index("--trusted-ref") + 1] == f"origin/{LINE}"
    (arm,) = [args for name, args, _ in calls if name == "fake-make" and "pr-arm" in args]
    assert f"BASE={LINE}" in arm

    armed, calls, _ = _run(line, tmp_path / "arm", "pr-arm", "PR=7", f"BASE={LINE}", "HEAD=" + "1" * 40)

    assert armed.returncode == 0, armed.stderr
    assert [base for name, args, base in calls if name == "uv" and "scripts/pr_arm.py" in args] == [LINE]
    assert any("scripts/trunk_revert.py" in args and "arm-gate" in args for _, args, _ in calls)


@requires_make
def test_a_line_cut_from_an_old_tag_refuses_a_base_that_is_not_develop_or_a_line(
    old_tag_source: Path, tmp_path: Path
) -> None:
    line = _checkout(old_tag_source, _create(old_tag_source), tmp_path / "line")

    result, calls, git_log = _run(line, tmp_path / "open", "pr-open", "BASE=feature/x", "TITLE=fix: repair a thing")

    assert result.returncode == 2
    assert "BASE must be develop or a release/X.Y.x maintenance line" in result.stderr
    assert not [args for _, args, _ in calls if "scripts/pr_open_rest.py" in args]
    assert "push" not in git_log
