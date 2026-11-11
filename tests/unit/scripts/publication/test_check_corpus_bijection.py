from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.fast]

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = REPO_ROOT / "scripts" / "publication" / "check_corpus_bijection.py"
SEED_PATH = REPO_ROOT / "publication" / "ledger-seed.json"

spec = importlib.util.spec_from_file_location("check_corpus_bijection", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
bijection = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bijection
spec.loader.exec_module(bijection)

RECORDED_SOURCE = str(json.loads(SEED_PATH.read_text(encoding="utf-8"))["source"])


def test_check_bijection_exact_pass() -> None:
    ok, errors = bijection.check_bijection(["a", "b"], ["a", "b", "extra"])
    assert ok
    assert errors == []


def test_check_bijection_reports_unaccounted_skip() -> None:
    ok, errors = bijection.check_bijection(["a", "b"], ["a"])
    assert not ok
    assert any("b" in e for e in errors)


def test_check_bijection_disposition_exempts_planned_omission() -> None:
    ok, errors = bijection.check_bijection(["a", "b"], ["a"], dispositions={"b": "published_only"})
    assert ok
    assert errors == []


def test_bundle_files_in_dir_rejects_duplicate_basename(tmp_path: Path) -> None:
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    (tmp_path / "one" / "dup.json").write_text("{}")
    (tmp_path / "two" / "dup.json").write_text("{}")
    with pytest.raises(bijection.BijectionError, match="duplicate bundle basename"):
        bijection.bundle_files_in_dir(tmp_path)


def test_accepted_paths_from_recorded_source(tmp_path: Path) -> None:
    del tmp_path
    paths = bijection.accepted_paths_from_ref(RECORDED_SOURCE)
    assert paths, "recorded snapshot must contain primary bundles"
    assert all(p.startswith("results-data/bundles/") and p.endswith(".json") for p in paths)


def test_accepted_paths_bad_ref_fails_closed() -> None:
    with pytest.raises(bijection.BijectionError, match="ls-tree failed"):
        bijection.accepted_paths_from_ref("totally/not/a/ref")


def test_load_dispositions_empty_for_missing_file(tmp_path: Path) -> None:
    assert bijection.load_dispositions(tmp_path / "no-seed.json") == {}


def test_main_expect_source_rejects_moved_ref(capsys: pytest.CaptureFixture[str]) -> None:
    rc = bijection.main(["--accepted-ref", "HEAD", "--expect-source", RECORDED_SOURCE])
    assert rc == 1
    assert "moved" in capsys.readouterr().out


def test_main_recorded_source_passes_path_stage(capsys: pytest.CaptureFixture[str]) -> None:
    rc = bijection.main(
        [
            "--accepted-ref",
            RECORDED_SOURCE,
            "--expect-source",
            RECORDED_SOURCE,
            "--bundles-dir",
            str(REPO_ROOT / "results-data" / "bundles"),
            "--ledger-seed",
            str(SEED_PATH),
        ]
    )
    assert rc == 0, capsys.readouterr().out


