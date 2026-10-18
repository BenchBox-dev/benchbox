from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_script():
    name = "check_project_references_under_test"
    path = REPO_ROOT / "_project" / "scripts" / "check_project_references.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_script()


class TestNormalize:
    def test_strips_trailing_punctuation(self):
        assert checker._normalize("_project/foo/bar.md`") == "_project/foo/bar.md"
        assert checker._normalize("_project/foo/bar.md).") == "_project/foo/bar.md"

    def test_placeholders_ignored(self):
        assert checker._is_placeholder("_project/foo.md")
        assert checker._is_placeholder("_project/blind-spots/foo.md")
        assert checker._is_placeholder("_project/specs/example.md")
        assert checker._is_placeholder("_project/specs/" + "foo" + ".yaml")
        assert checker._is_placeholder("_project/specs/" + "bar" + ".json")
        assert not checker._is_placeholder("_project/decisions/" + "arch-pilot" + "-eval.md")

    def test_normalize_strips_autolink_bracket(self):
        assert checker._normalize("_project/decisions/" + "arch-pilot" + "-eval.md>") == (
            "_project/decisions/arch-pilot" + "-eval.md"
        )


class TestBaselineRoundTrip:
    def test_baseline_exists_and_gate_passes(self, capsys):
        assert checker.BASELINE_PATH.exists()
        assert checker.main([]) == 0
        assert "no new breakage" in capsys.readouterr().out

    def test_new_reference_detected_against_baseline(self, tmp_path, monkeypatch, capsys):
        scanned = tmp_path / "scanned.md"
        scanned.write_text("see _project/decisions/synthetic-new-1" + "234.md\n", encoding="utf-8")
        monkeypatch.setattr(checker, "_tracked_files", lambda: ["scanned.md"])
        monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(checker, "BASELINE_PATH", tmp_path / "baseline.txt")
        (tmp_path / "baseline.txt").write_text("", encoding="utf-8")
        assert checker.main([]) == 1
        assert "NEW stale" in capsys.readouterr().out

    def test_fixed_baseline_entry_forces_regen(self, tmp_path, monkeypatch, capsys):
        (tmp_path / "baseline.txt").write_text(
            "gone.md::_project/decisions/synthetic-gone-99" + "9.md\n", encoding="utf-8"
        )
        monkeypatch.setattr(checker, "_tracked_files", list)
        monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(checker, "BASELINE_PATH", tmp_path / "baseline.txt")
        assert checker.main([]) == 1
        assert "regenerate" in capsys.readouterr().out
