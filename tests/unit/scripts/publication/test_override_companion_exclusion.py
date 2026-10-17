from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[4]
PUBLICATION_DIR = REPO_ROOT / "scripts" / "publication"


def _load(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(module_name, PUBLICATION_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ledger_seed = _load("ledger_seed_under_test", "create_ledger_seed.py")
bijection = _load("bijection_override_under_test", "check_corpus_bijection.py")
reconciler = _load("reconciler_override_under_test", "reconciler.py")
parity = _load("parity_override_under_test", "validator_parity.py")

BUNDLE = "results-data/bundles/tpch/duckdb/sf1.json"
OVERRIDE = "results-data/bundles/tpch/duckdb/sf1.override.json"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def _init_repo(path: Path) -> Path:
    repo = path / "repo"
    (repo / "results-data" / "bundles" / "tpch" / "duckdb").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    return repo


def test_ledger_seed_ignores_override_suffix() -> None:
    assert ledger_seed._is_primary_bundle(BUNDLE) is True
    assert ledger_seed._is_primary_bundle(OVERRIDE) is False
    assert ledger_seed._is_primary_bundle("results-data/bundles/sf1.override.json") is False


def test_bijection_ignores_override_suffix() -> None:
    assert bijection._is_primary_bundle(BUNDLE) is True
    assert bijection._is_primary_bundle(OVERRIDE) is False


def test_reconciler_corpus_paths_ignore_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path)
    (repo / BUNDLE).write_text("{}", encoding="utf-8")
    (repo / OVERRIDE).write_text("{}", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    monkeypatch.setattr(reconciler, "REPO_ROOT", repo)
    assert reconciler.get_corpus_paths("HEAD") == {BUNDLE}


def test_parity_override_change_back_maps_bundle_not_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path)
    (repo / BUNDLE).write_text("{}", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / BUNDLE).write_text('{"v": 2}', encoding="utf-8")
    (repo / OVERRIDE).write_text("{}", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "merge")
    merge = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(parity, "CHECKOUT_ROOT", repo)
    assert parity._discover_changed_bundles(base, merge) == [BUNDLE]


def test_parity_override_only_change_still_yields_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path)
    (repo / BUNDLE).write_text("{}", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / OVERRIDE).write_text("{}", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "merge")
    merge = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(parity, "CHECKOUT_ROOT", repo)
    assert parity._discover_changed_bundles(base, merge) == [BUNDLE]
