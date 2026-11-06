from __future__ import annotations

import os
from pathlib import Path

import pytest
import theseus_cli
import theseus_release_check

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _tree(root: Path, *, lock: bool, policy: bool) -> Path:
    (root / ".github").mkdir(parents=True)
    if lock:
        (root / ".github" / "theseus-lock.json").write_text("{}", encoding="utf-8")
    if policy:
        (root / ".github" / "oracle-reviewers.yml").write_text("reviewers: []\n", encoding="utf-8")
    return root


def _run_cli(root: Path) -> int:
    previous = Path.cwd()
    os.chdir(root)
    try:
        return theseus_cli.main(["--trusted-ref", "HEAD", "path"])
    finally:
        os.chdir(previous)


def _never(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("the release tree must not reach the verification")


@pytest.mark.parametrize(("lock", "policy"), [(True, False), (False, True), (True, True)])
def test_either_file_present_is_not_a_release_tree(tmp_path: Path, lock: bool, policy: bool) -> None:
    root = _tree(tmp_path, lock=lock, policy=policy)

    assert not theseus_release_check._release_tree(root)
    assert not theseus_cli._release_tree(root)


def test_release_tree_skips_both_steps(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _tree(tmp_path, lock=False, policy=False)

    assert theseus_release_check.main(["x", str(root)], _never) == 0
    assert _run_cli(root) == 0
    assert capsys.readouterr().err.count("release tree: no Theseus lock") == 2


def test_policy_without_lock_still_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _tree(tmp_path, lock=False, policy=True)

    def down(_path: str) -> object:
        raise theseus_release_check.ReleaseCheckError("offline")

    assert theseus_release_check.main(["x", str(root)], down) == 1
    assert _run_cli(root) != 0
    assert "release tree" not in capsys.readouterr().err
