from __future__ import annotations

from pathlib import Path

import pytest

from _project.scripts import oracle_mutation

pytestmark = [pytest.mark.unit, pytest.mark.fast]

SETTINGS = {"paths_to_mutate": ["src/pkg"], "also_copy": ["extra/data"]}


def _tree(root: Path) -> None:
    for name in ("src", "extra", "tests"):
        (root / name).mkdir(parents=True)
    for name in ("pytest.ini", "pyproject.toml", "_benchbox_pytest_xdist_safety.py"):
        (root / name).write_text("", encoding="utf-8")


def test_staging_links_every_top_level_path_to_the_source_tree(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _tree(root)
    target = tmp_path / "stage"
    oracle_mutation.stage(root, target, SETTINGS)
    assert (target / "src").resolve() == (root / "src").resolve()
    assert (target / "tests").resolve() == (root / "tests").resolve()


def test_staging_repoints_a_link_left_by_another_checkout(tmp_path: Path) -> None:
    old, root = tmp_path / "old", tmp_path / "root"
    _tree(old)
    _tree(root)
    target = tmp_path / "stage"
    oracle_mutation.stage(old, target, SETTINGS)
    oracle_mutation.stage(root, target, SETTINGS)
    assert (target / "src").readlink() == root / "src"
    assert (target / "tests").readlink() == root / "tests"
