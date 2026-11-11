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
    name = "check_uv_lock_revision_under_test"
    path = REPO_ROOT / "_project" / "scripts" / "check_uv_lock_revision.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


guard = _load_script()


class TestRevisionComparison:
    def test_downgrade_rejected(self, capsys):
        assert guard.main(["--old", "3", "--new", "2"]) == 1
        assert "DOWNGRADE" in capsys.readouterr().err

    def test_unchanged_accepted(self):
        assert guard.main(["--old", "3", "--new", "3"]) == 0

    def test_upgrade_accepted(self):
        assert guard.main(["--old", "3", "--new", "4"]) == 0

    def test_old_and_new_must_travel_together(self):
        with pytest.raises(SystemExit) as excinfo:
            guard.main(["--old", "3"])
        assert excinfo.value.code == 2


class TestRevisionParsing:
    def test_parses_committed_style_lock(self):
        text = 'version = 1\nrevision = 3\nrequires-python = ">=3.10"\n'
        assert guard.parse_revision(text, "uv.lock") == 3

    def test_malformed_lock_reported(self):
        with pytest.raises(ValueError, match="uv.lock"):
            guard.parse_revision("no revision line here\n", "uv.lock")

    def test_repo_lock_has_parseable_revision(self):
        text = (REPO_ROOT / "uv.lock").read_text(encoding="utf-8")
        assert guard.parse_revision(text, "uv.lock") >= 3

    def test_baseline_ref_is_used_for_git_lookup(self, monkeypatch, tmp_path):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            return type("Result", (), {"returncode": 0, "stdout": "revision = 3\n"})()

        monkeypatch.setattr(guard.subprocess, "run", fake_run)
        assert guard._committed_lock_text(tmp_path, "origin/develop") == "revision = 3\n"
        assert calls == [["git", "--no-replace-objects", "show", "origin/develop:uv.lock"]]

    def test_explicit_missing_baseline_fails_closed(self, monkeypatch, capsys):
        monkeypatch.setattr(guard, "_committed_lock_text", lambda _root, _ref: None)
        assert guard.main(["--baseline-ref", "missing-base"]) == 1
        assert "explicitly requested baseline" in capsys.readouterr().err

    def test_default_missing_head_remains_valid_for_fresh_repo(self, monkeypatch, capsys):
        monkeypatch.setattr(guard, "_committed_lock_text", lambda _root, _ref: None)
        assert guard.main([]) == 0
        assert "nothing to compare" in capsys.readouterr().out


