from __future__ import annotations

import dataclasses
import os
import subprocess
from pathlib import Path

import pytest

from _project.scripts.oracle_reviewers import cli, github, local, protocol

pytestmark = [pytest.mark.unit, pytest.mark.fast]

BLOCKS = "".join("a\nb\nc\nrequires_auth\nd\n" for _ in range(3))
TWIN_FUNCTIONS = "def guarded(user):\n    a\n    b\n    c\n\n\n" * 2
BINARY = b"\x00\x01\x02payload\xff"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _commit(repo: Path, message: str, **files: bytes | str | None) -> None:
    for name, content in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            _git(repo, "rm", "--quiet", name)
            continue
        target.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        _git(repo, "add", name)
    _git(repo, "commit", "--quiet", "--allow-empty", "-m", message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "--quiet", "--initial-branch=develop")
    _commit(
        path,
        "seed",
        **{"blocks.py": BLOCKS, "twins.py": TWIN_FUNCTIONS, "data.bin": BINARY, "run.sh": "echo hi\n"},
    )
    _git(path, "switch", "--quiet", "-c", "feat/x")
    return path


def _states(repo: Path, head: str = "HEAD") -> list[protocol.FileState]:
    return local.file_states(repo, _git(repo, "merge-base", "develop", head), head)


def _digest(repo: Path, head: str = "HEAD") -> str:
    return protocol.patch_digest(protocol.fingerprints(_states(repo, head)))


def _variant(repo: Path, name: str, **files: bytes | str | None) -> str:
    _git(repo, "switch", "--quiet", "-c", name, "develop")
    _commit(repo, name, **files)
    return name


def _removing(text: str, block: int) -> str:
    lines = text.splitlines(keepends=True)
    del lines[block * 5 + 2]
    return "".join(lines)


def test_the_same_removal_in_a_different_identical_block_is_a_different_digest(repo: Path) -> None:
    first = _variant(repo, "first", **{"blocks.py": _removing(BLOCKS, 0)})
    second = _variant(repo, "second", **{"blocks.py": _removing(BLOCKS, 2)})
    assert _digest(repo, first) != _digest(repo, second)


def test_the_same_removal_in_a_repeated_heading_function_is_a_different_digest(repo: Path) -> None:
    def drop(function: int) -> str:
        lines = TWIN_FUNCTIONS.splitlines(keepends=True)
        del lines[function * 6 + 2]
        return "".join(lines)

    first = _variant(repo, "first", **{"twins.py": drop(0)})
    second = _variant(repo, "second", **{"twins.py": drop(1)})
    assert _digest(repo, first) != _digest(repo, second)


def test_a_mode_only_change_is_a_different_digest(repo: Path) -> None:
    plain = _variant(repo, "plain", **{"run.sh": "echo hi\n", "extra.txt": "x\n"})
    executable = _variant(repo, "executable", **{"extra.txt": "x\n"})
    _git(repo, "update-index", "--chmod=+x", "extra.txt")
    _git(repo, "commit", "--quiet", "--amend", "--no-edit")
    assert _digest(repo, plain) != _digest(repo, executable)
    assert {state.new_mode for state in _states(repo, executable)} == {"100755"}


def test_binary_files_with_different_contents_have_different_full_blob_ids(repo: Path) -> None:
    first = _variant(repo, "first", **{"data.bin": BINARY + b"1"})
    second = _variant(repo, "second", **{"data.bin": BINARY + b"2"})
    (state,) = _states(repo, first)
    assert len(state.new_id) == 40 and len(state.old_id) == 40
    assert _digest(repo, first) != _digest(repo, second)


def test_bytes_that_differ_only_in_invalid_utf8_are_different_digests(repo: Path) -> None:
    first = _variant(repo, "first", **{"blocks.py": b"x = '\xe9'\n"})
    second = _variant(repo, "second", **{"blocks.py": b"x = '\xf1'\n"})
    assert _digest(repo, first) != _digest(repo, second)


def test_a_byte_identical_rebase_keeps_the_digest(repo: Path) -> None:
    _commit(repo, "feature", **{"blocks.py": _removing(BLOCKS, 1)})
    before = _digest(repo)
    _git(repo, "switch", "--quiet", "develop")
    _commit(repo, "unrelated", **{"elsewhere.txt": "y\n"})
    _git(repo, "switch", "--quiet", "feat/x")
    _git(repo, "rebase", "--quiet", "develop")
    assert _digest(repo) == before


def test_a_rebase_onto_a_develop_that_changed_the_same_file_changes_the_digest(repo: Path) -> None:
    _commit(repo, "feature", **{"blocks.py": _removing(BLOCKS, 1)})
    before = _digest(repo)
    _git(repo, "switch", "--quiet", "develop")
    _commit(repo, "same file", **{"blocks.py": "# header\n" + BLOCKS})
    _git(repo, "switch", "--quiet", "feat/x")
    _git(repo, "rebase", "--quiet", "develop")
    assert _digest(repo) != before


def test_the_local_digest_equals_the_ci_digest_for_a_rename_a_mode_change_and_a_binary_file(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _git(repo, "mv", "blocks.py", "renamed.py")
    (repo / "run.sh").chmod(0o755)
    _git(repo, "add", "run.sh")
    _commit(repo, "feature", **{"data.bin": BINARY + b"changed", "added.txt": "new\n", "twins.py": None})
    head = _git(repo, "rev-parse", "HEAD")
    base = _git(repo, "merge-base", "develop", head)
    local_states = local.file_states(repo, base, head)
    assert {"renamed.py", "run.sh", "data.bin", "added.txt", "twins.py"} == {state.new_path for state in local_states}
    assert any(state.old_path == "blocks.py" and state.new_path == "renamed.py" for state in local_states)

    def tree(_: str, sha: str) -> dict[str, tuple[str, str]]:
        listing = subprocess.run(
            ["git", "-C", str(repo), "ls-tree", "-r", "-z", "--full-tree", sha], capture_output=True, check=True
        ).stdout.decode()
        entries = {}
        for row in filter(None, listing.split("\0")):
            meta, path = row.split("\t", 1)
            mode, _, blob = meta.split(" ")
            entries[path] = (mode, blob)
        return entries

    monkeypatch.setattr(github, "merge_base", lambda repo_name, left, right: base)
    monkeypatch.setattr(github, "tree_blobs", tree)
    files = [
        dataclasses.replace(
            item,
            sha="" if item.removed else _git(repo, "rev-parse", f"{head}:{item.path}"),
        )
        for item in local.changed_files(repo, base, head)
    ]
    plan = {"repo": "owner/name", "base_sha": base, "head_sha": head}
    ci_states = cli._file_states(plan, files)
    assert ci_states is not None
    assert sorted(ci_states, key=lambda state: state.new_path) == sorted(local_states, key=lambda state: state.new_path)
    assert protocol.patch_digest(protocol.fingerprints(ci_states)) == protocol.patch_digest(
        protocol.fingerprints(local_states)
    )
