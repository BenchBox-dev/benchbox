from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.coffeeshop.generator import CoffeeShopDataGenerator
from tests.uat.artifact_hygiene import (
    LocalArtifactGrowthError,
    assert_no_local_growth,
    audit_local_datagen,
    configured_external_root,
    dir_size_bytes,
    local_runs_root,
    snapshot_local_runs,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _isolated_roots(tmp_path: Path) -> tuple[Path, Path]:

    worktree = tmp_path / "worktree"
    external = tmp_path / "external_runs"
    worktree.mkdir()
    external.mkdir()
    return worktree, external


def test_external_root_run_does_not_grow_local_benchmark_runs(monkeypatch, tmp_path):

    worktree, external = _isolated_roots(tmp_path)
    monkeypatch.chdir(worktree)
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(external))

    resolved = configured_external_root(cwd=worktree)
    assert resolved == external.resolve()

    before = snapshot_local_runs(worktree)
    assert before.total_bytes == 0

    generator = CoffeeShopDataGenerator(scale_factor=0.0001, compress_data=False)
    tables = generator.generate_data()

    order_lines_parent = Path(tables["order_lines"]).parent
    assert order_lines_parent.is_relative_to(external)
    assert dir_size_bytes(external) > 0

    assert_no_local_growth(before, external, cwd=worktree)
    assert not local_runs_root(worktree).exists()


def test_assert_no_local_growth_flags_a_simulated_leak(monkeypatch, tmp_path):

    worktree, external = _isolated_roots(tmp_path)
    monkeypatch.chdir(worktree)
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(external))

    before = snapshot_local_runs(worktree)

    leaked = local_runs_root(worktree) / "datagen" / "coffeeshop_sf00001" / "order_lines.csv"
    leaked.parent.mkdir(parents=True)
    leaked.write_bytes(b"x" * 4096)

    with pytest.raises(LocalArtifactGrowthError) as excinfo:
        assert_no_local_growth(before, external, cwd=worktree)

    message = str(excinfo.value)
    assert str(local_runs_root(worktree)) in message
    assert str(external) in message
    assert "PR #780" in message

    assert leaked.exists()


def test_assert_no_local_growth_flags_same_size_rewrite(monkeypatch, tmp_path):

    import os

    worktree, external = _isolated_roots(tmp_path)
    monkeypatch.chdir(worktree)
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(external))

    leaked = local_runs_root(worktree) / "datagen" / "coffeeshop_sf00001" / "order_lines.csv"
    leaked.parent.mkdir(parents=True)
    leaked.write_bytes(b"a" * 4096)

    before = snapshot_local_runs(worktree)
    assert before.total_bytes == 4096

    leaked.unlink()
    leaked.write_bytes(b"b" * 4096)
    st = leaked.stat()
    os.utime(leaked, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    with pytest.raises(LocalArtifactGrowthError) as excinfo:
        assert_no_local_growth(before, external, cwd=worktree)
    assert str(local_runs_root(worktree)) in str(excinfo.value)
    assert leaked.exists()


def test_configured_external_root_skips_default_local_runs(monkeypatch, tmp_path):

    worktree, _external = _isolated_roots(tmp_path)
    monkeypatch.chdir(worktree)
    monkeypatch.delenv("BENCHBOX_OUTPUT_DIR", raising=False)

    assert configured_external_root(cwd=worktree) is None

    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", "")
    assert configured_external_root(cwd=worktree) is None

    inside = worktree / "benchmark_runs"
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(inside))
    assert configured_external_root(cwd=worktree) is None


def test_nested_worktree_cwd_uses_the_enclosing_worktree_boundary():

    repo_root = Path(__file__).resolve().parents[2]
    nested = repo_root / "docs"

    assert local_runs_root(nested) == repo_root / "benchmark_runs"
    assert (
        configured_external_root(
            {"BENCHBOX_OUTPUT_DIR": str(repo_root / "benchmark_runs")},
            cwd=nested,
        )
        is None
    )


def test_audit_local_datagen_is_noop_without_external_root(monkeypatch, tmp_path):

    worktree, _external = _isolated_roots(tmp_path)
    monkeypatch.delenv("BENCHBOX_OUTPUT_DIR", raising=False)

    leaked = local_runs_root(worktree) / "datagen" / "tpch_sf1" / "lineitem.tbl"
    leaked.parent.mkdir(parents=True)
    leaked.write_bytes(b"x" * 1024)

    assert audit_local_datagen(cwd=worktree) is None


def test_audit_local_datagen_reports_existing_local_artifacts(monkeypatch, tmp_path):

    worktree, external = _isolated_roots(tmp_path)
    monkeypatch.setenv("BENCHBOX_OUTPUT_DIR", str(external))

    leaked = local_runs_root(worktree) / "datagen" / "tpch_sf1" / "lineitem.tbl"
    leaked.parent.mkdir(parents=True)
    leaked.write_bytes(b"x" * 8192)

    message = audit_local_datagen(cwd=worktree, output=str(external))
    assert message is not None
    assert str(local_runs_root(worktree) / "datagen") in message
    assert str(external) in message

    assert audit_local_datagen(cwd=worktree, output=str(external), threshold_bytes=8192) is None


