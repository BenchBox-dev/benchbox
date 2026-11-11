from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_script():
    name = "check_rerun_shard_retention_under_test"
    path = REPO_ROOT / "scripts" / "check_rerun_shard_retention.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_script()


class TestStemDates:
    def test_parses_sweep_date(self):
        assert checker.shard_sweep_date(Path("uat-tuned-followup-resume-cedardb-20260505.yaml")) == date(2026, 5, 5)

    def test_rejects_undated_stem(self):
        assert checker.shard_sweep_date(Path("uat-smoke.yaml")) is None

    def test_rejects_impossible_date(self):
        assert checker.shard_sweep_date(Path("shard-20261345.yaml")) is None


class TestExpiry:
    def test_boundary_is_inclusive(self, tmp_path, monkeypatch):
        shard = tmp_path / "sweep-20260101.yaml"
        shard.write_text("name: x\n", encoding="utf-8")
        monkeypatch.setattr(checker, "SHARD_DIR", tmp_path)
        expired, _ = checker.find_expired(date(2026, 6, 30), 180)
        assert [p.name for p, _, _ in expired] == ["sweep-20260101.yaml"]

    def test_undated_shards_fail(self, tmp_path, monkeypatch):
        (tmp_path / "sweep-undated.yaml").write_text("name: x\n", encoding="utf-8")
        monkeypatch.setattr(checker, "SHARD_DIR", tmp_path)
        expired, undated = checker.find_expired(date(2026, 9, 25), 180)
        assert expired == []
        assert [p.name for p in undated] == ["sweep-undated.yaml"]

    def test_current_shards_pass(self, tmp_path, monkeypatch):
        (tmp_path / "sweep-20260901.yaml").write_text("name: x\n", encoding="utf-8")
        monkeypatch.setattr(checker, "SHARD_DIR", tmp_path)
        assert checker.find_expired(date(2026, 9, 25), 180) == ([], [])

    def test_main_reports_ok_when_current(self, tmp_path, monkeypatch, capsys):

        stamp = date.today().strftime("%Y%m%d")
        (tmp_path / f"sweep-{stamp}.yaml").write_text("name: x\n", encoding="utf-8")
        monkeypatch.setattr(checker, "SHARD_DIR", tmp_path)
        assert checker.main([]) == 0
        assert "OK" in capsys.readouterr().out

    def test_main_fails_with_archive_command_when_expired(self, tmp_path, monkeypatch, capsys):
        (tmp_path / "sweep-20260101.yaml").write_text("name: x\n", encoding="utf-8")
        monkeypatch.setattr(checker, "SHARD_DIR", tmp_path)
        assert checker.main([]) == 1
        out = capsys.readouterr().out
        assert "expired" in out
        assert "mkdir -p" in out and "git mv" in out

    def test_main_fails_closed_when_shard_directory_missing(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(checker, "SHARD_DIR", tmp_path / "renamed-away")
        assert checker.main([]) == 2
        assert "not found" in capsys.readouterr().err


