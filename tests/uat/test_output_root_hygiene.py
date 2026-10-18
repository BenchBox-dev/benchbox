from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.coffeeshop.generator import CoffeeShopDataGenerator
from tests.uat.artifact_hygiene import (
    LocalArtifactGrowthError,
    assert_no_local_growth,
    audit_local_datagen,
    configured_external_root,
    local_runs_root,
    snapshot_local_runs,
)

pytestmark = pytest.mark.medium


def _isolated_roots(tmp_path: Path) -> tuple[Path, Path]:
    worktree = tmp_path / "worktree"
    external = tmp_path / "external_runs"
    worktree.mkdir()
    external.mkdir()
    return worktree, external


def test_external_root_generation_leaves_worktree_clean(monkeypatch, tmp_path):
    worktree, external = _isolated_roots(tmp_path)
    monkeypatch.chdir(worktree)
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(external))

    assert configured_external_root(cwd=worktree) == external.resolve()
    before = snapshot_local_runs(worktree)

    tables = CoffeeShopDataGenerator(scale_factor=0.0001, compress_data=False).generate_data()

    assert Path(tables["order_lines"]).parent.is_relative_to(external)
    assert_no_local_growth(before, external, cwd=worktree)
    assert not local_runs_root(worktree).exists()


def test_unexpected_local_write_fails_the_hygiene_check(monkeypatch, tmp_path):
    worktree, external = _isolated_roots(tmp_path)
    monkeypatch.chdir(worktree)
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(external))
    before = snapshot_local_runs(worktree)

    leaked = local_runs_root(worktree) / "datagen" / "tpch_sf1" / "lineitem.tbl"
    leaked.parent.mkdir(parents=True)
    leaked.write_bytes(b"x" * 2048)

    with pytest.raises(LocalArtifactGrowthError) as excinfo:
        assert_no_local_growth(before, external, cwd=worktree)
    message = str(excinfo.value)
    assert str(local_runs_root(worktree)) in message
    assert str(external) in message
    assert leaked.exists()


def test_default_local_run_is_not_blocked(monkeypatch, tmp_path):
    worktree, _external = _isolated_roots(tmp_path)
    monkeypatch.chdir(worktree)
    monkeypatch.delenv("BENCHBOX_OUTPUT_DIR", raising=False)

    local = local_runs_root(worktree) / "datagen" / "tpch_sf1"
    local.mkdir(parents=True)
    (local / "lineitem.tbl").write_bytes(b"x" * 2048)

    assert configured_external_root(cwd=worktree) is None
    assert audit_local_datagen(cwd=worktree) is None
