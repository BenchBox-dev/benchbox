from __future__ import annotations

import json
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest
from check_comment_policy import main

pytestmark = [pytest.mark.unit, pytest.mark.medium]

LAUNCHER = "TRUSTED_FILES = ('scripts/comment_syntax.py',)\n"
EMPTY_POLICY = {"version": 1, "external": [], "exceptions": []}
CONSUMER = "consumer.toml"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def _exception(path: str, text: str, **changes: str) -> dict:
    return {
        "path": path,
        "symbol": "",
        "text": text,
        "kind": "directive",
        "consumer": CONSUMER,
        "necessity": "The import is an intentionally exported compatibility name.",
        "alternative": "Replacing the public export would break callers.",
        "owner": "maintainers",
        "removal": "Remove when the export is retired.",
        "expires": (date.today() + timedelta(days=90)).isoformat(),
        **changes,
    }


def _repo(
    tmp_path: Path, policy: dict | None = None, launcher: str = LAUNCHER, extra: dict[str, str] | None = None
) -> str:
    files = {
        "quality/comment-policy.json": json.dumps(policy or EMPTY_POLICY),
        "scripts/run_comment_policy.py": launcher,
        "scripts/comment_syntax.py": "x = 1\n",
        "a.py": "x = 1\n",
        "b.py": "# inherited\ny = 1\n",
        CONSUMER: "consumer\n",
        "notes.txt": "notes\n",
        **(extra or {}),
    }
    _git(tmp_path, "init", "-q")
    for name, content in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(content, encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "base")
    return _git(tmp_path, "rev-parse", "HEAD")


def _write(root: Path, name: str, content: str, staged: bool) -> None:
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(content, encoding="utf-8")
    if staged:
        _git(root, "add", name)


def _args(root: Path, base: str, staged: bool, *extra: str) -> list[str]:
    return ["--root", str(root), "--mode", "strict", "--base", base, *(["--staged"] if staged else []), *extra]


@pytest.mark.parametrize("staged", [False, True])
def test_a_violation_in_a_changed_file_fails_and_inherited_ones_are_not_scanned(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], staged: bool
) -> None:
    base = _repo(tmp_path)
    _write(tmp_path, "a.py", "# added\nx = 2\n", staged)
    capsys.readouterr()
    assert main(_args(tmp_path, base, staged)) == 1
    output = capsys.readouterr().out
    assert "a.py:1: CP comment" in output
    assert "b.py" not in output
    assert "1 source files" in output


@pytest.mark.parametrize("staged", [False, True])
def test_a_violation_in_an_unchanged_file_passes_pr_mode_and_fails_the_full_scan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], staged: bool
) -> None:
    base = _repo(tmp_path)
    _write(tmp_path, "a.py", "x = 2\n", staged)
    assert main(_args(tmp_path, base, staged)) == 0
    capsys.readouterr()
    assert main(_args(tmp_path, base, staged, "--full")) == 1
    assert "b.py:1: CP comment" in capsys.readouterr().out


@pytest.mark.parametrize("staged", [False, True])
def test_a_pr_that_changes_no_file_scans_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], staged: bool
) -> None:
    base = _repo(tmp_path)
    capsys.readouterr()
    assert main(_args(tmp_path, base, staged)) == 0
    assert "0 source files" in capsys.readouterr().out


@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.parametrize(
    ("target", "content"),
    [
        ("quality/comment-policy.json", json.dumps({**EMPTY_POLICY, "enforcement": "blocking"})),
        ("quality/comment-cleanup-scope.json", "{}\n"),
        ("scripts/run_comment_policy.py", LAUNCHER + "\n"),
        ("scripts/comment_syntax.py", "x = 2\n"),
    ],
)
def test_each_global_change_scans_the_whole_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], staged: bool, target: str, content: str
) -> None:
    base = _repo(tmp_path)
    _write(tmp_path, target, content, staged)
    capsys.readouterr()
    assert main(_args(tmp_path, base, staged)) == 1
    assert "b.py:1: CP comment" in capsys.readouterr().out


@pytest.mark.parametrize("staged", [False, True])
def test_a_trusted_file_dropped_from_the_candidate_launcher_still_forces_a_full_scan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], staged: bool
) -> None:
    base = _repo(tmp_path)
    _write(tmp_path, "scripts/run_comment_policy.py", "TRUSTED_FILES = ('scripts/other.py',)\n", staged)
    _write(tmp_path, "scripts/comment_syntax.py", "x = 2\n", staged)
    capsys.readouterr()
    assert main(_args(tmp_path, base, staged)) == 1
    assert "b.py:1: CP comment" in capsys.readouterr().out


@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.parametrize("launcher", ["def (:\n", "OTHER = 1\n", "TRUSTED_FILES = compute()\n"])
def test_an_unreadable_base_launcher_forces_a_full_scan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], staged: bool, launcher: str
) -> None:
    base = _repo(tmp_path, launcher=launcher)
    _write(tmp_path, "a.py", "x = 2\n", staged)
    capsys.readouterr()
    assert main(_args(tmp_path, base, staged)) == 1
    assert "b.py:1: CP comment" in capsys.readouterr().out


@pytest.mark.parametrize("staged", [False, True])
def test_a_changed_exception_consumer_rescans_the_exception_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], staged: bool
) -> None:
    registered = {**EMPTY_POLICY, "exceptions": [_exception("c.py", "# noqa: F401")]}
    base = _repo(tmp_path, registered, extra={"c.py": "x = 1  # noqa: E501\n"})
    _write(tmp_path, "notes.txt", "changed\n", staged)
    assert main(_args(tmp_path, base, staged)) == 0
    _write(tmp_path, CONSUMER, "changed\n", staged)
    capsys.readouterr()
    assert main(_args(tmp_path, base, staged)) == 1
    assert "c.py:1: CP comment" in capsys.readouterr().out


@pytest.mark.parametrize("staged", [False, True])
def test_an_expired_exception_fails_a_pr_that_changes_no_source_or_policy(tmp_path: Path, staged: bool) -> None:
    expired = {**EMPTY_POLICY, "exceptions": [_exception("c.py", "# noqa: F401", expires="2000-01-01")]}
    base = _repo(tmp_path, expired, extra={"c.py": "x = 1\n"})
    _write(tmp_path, "notes.txt", "changed\n", staged)
    assert main(_args(tmp_path, base, staged)) == 1


@pytest.mark.parametrize("staged", [False, True])
def test_a_missing_exception_consumer_fails_a_pr_that_changes_no_source(tmp_path: Path, staged: bool) -> None:
    registered = {**EMPTY_POLICY, "exceptions": [_exception("c.py", "# noqa: F401")]}
    base = _repo(tmp_path, registered, extra={"c.py": "x = 1  # noqa: F401\n"})
    if staged:
        _git(tmp_path, "rm", "-q", CONSUMER)
    else:
        (tmp_path / CONSUMER).unlink()
    assert main(_args(tmp_path, base, staged)) == 2


@pytest.mark.parametrize("staged", [False, True])
def test_relaxing_enforcement_fails_a_pr_that_changes_no_source(tmp_path: Path, staged: bool) -> None:
    base = _repo(tmp_path, {**EMPTY_POLICY, "enforcement": "blocking"})
    _write(tmp_path, "quality/comment-policy.json", json.dumps({**EMPTY_POLICY, "enforcement": "advisory"}), staged)
    assert main(_args(tmp_path, base, staged)) == 2


@pytest.mark.parametrize("staged", [False, True])
def test_a_new_exception_without_review_does_not_cover_an_unchanged_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], staged: bool
) -> None:
    base = _repo(tmp_path, extra={"c.py": "x = 1  # noqa: F401\n"})
    registered = {**EMPTY_POLICY, "exceptions": [_exception("c.py", "# noqa: F401")]}
    _write(tmp_path, "quality/comment-policy.json", json.dumps(registered), staged)
    capsys.readouterr()
    assert main(_args(tmp_path, base, staged)) == 1
    assert "c.py:1: CP comment" in capsys.readouterr().out


@pytest.mark.parametrize("staged", [False, True])
def test_a_missing_external_provenance_fails_a_pr_that_changes_no_source(tmp_path: Path, staged: bool) -> None:
    external = [{"path": "vendor/", "owner": "upstream", "provenance": "provenance.txt"}]
    base = _repo(
        tmp_path,
        {**EMPTY_POLICY, "external": external},
        extra={"vendor/old.py": "# upstream\nx = 1\n", "provenance.txt": "upstream\n"},
    )
    if staged:
        _git(tmp_path, "rm", "-q", "provenance.txt")
    else:
        (tmp_path / "provenance.txt").unlink()
    assert main(_args(tmp_path, base, staged)) == 2
