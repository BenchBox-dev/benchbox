from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.medium]

_ROOT = Path(__file__).resolve().parents[3]
_PROJECT_SCRIPTS_DIR = str(_ROOT / "_project" / "scripts")
if _PROJECT_SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_SCRIPTS_DIR)

_SCRIPT = _ROOT / "_project" / "scripts" / "timing_policy_check.py"


def _load():
    spec = importlib.util.spec_from_file_location("_timing_policy_check_wall_clock", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load()
audit = sys.modules["timing_audit"]


def _write_allowlist(path: Path, entries: list[dict]) -> Path:
    path.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    return path


def _tree(tmp_path: Path, source: str) -> Path:
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "mod.py").write_text(source, encoding="utf-8")
    return root


def _run_main(monkeypatch: pytest.MonkeyPatch, repo: Path, allowlist: Path, *extra: str) -> int:
    real_collect = audit.collect_findings
    monkeypatch.setattr(mod, "collect_findings", lambda _root, roots: real_collect(repo, roots))
    monkeypatch.setattr(
        sys, "argv", ["timing_policy_check.py", "--roots", "pkg", "--allowlist", str(allowlist), *extra]
    )
    return mod.main()


def test_is_allowed_matches_on_path_glob_only() -> None:
    entry = {"path_glob": "pkg/*.py"}
    assert mod._is_allowed("pkg/mod.py", "t = time.time() - s", "time.time", entry)
    assert not mod._is_allowed("other/mod.py", "t = time.time() - s", "time.time", entry)


def test_is_allowed_defaults_to_any_path() -> None:
    assert mod._is_allowed("anything/at/all.py", "x", "time.time", {})


def test_is_allowed_requires_matching_symbol() -> None:
    entry = {"path_glob": "**", "symbol": "datetime.now"}
    assert mod._is_allowed("a.py", "x", "datetime.now", entry)
    assert not mod._is_allowed("a.py", "x", "time.time", entry)


def test_is_allowed_requires_matching_line_regex() -> None:
    entry = {"path_glob": "**", "line_regex": r"deadline"}
    assert mod._is_allowed("a.py", "deadline = time.time() + 5", "time.time", entry)
    assert not mod._is_allowed("a.py", "elapsed = time.time() - t0", "time.time", entry)


def test_load_allowlist_rejects_non_list_entries(tmp_path: Path) -> None:
    bad = tmp_path / "allow.json"
    bad.write_text(json.dumps({"entries": {"path_glob": "**"}}), encoding="utf-8")
    with pytest.raises(ValueError, match="'entries' list"):
        mod._load_allowlist(bad)


def test_load_allowlist_treats_missing_entries_as_empty(tmp_path: Path) -> None:
    empty = tmp_path / "allow.json"
    empty.write_text("{}", encoding="utf-8")
    assert mod._load_allowlist(empty) == []


def test_strict_fails_on_a_non_allowlisted_wall_clock_duration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    repo = _tree(tmp_path, "import time\nstart = 0\nelapsed = time.time() - start\n")
    allowlist = _write_allowlist(tmp_path / "allow.json", [])

    assert _run_main(monkeypatch, repo, allowlist, "--strict") == 1
    out = capsys.readouterr().out
    assert "Violations: 1" in out
    assert "pkg/mod.py:3 [time.time]" in out


def test_non_strict_reports_but_does_not_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = _tree(tmp_path, "import time\nstart = 0\nelapsed = time.time() - start\n")
    allowlist = _write_allowlist(tmp_path / "allow.json", [])

    assert _run_main(monkeypatch, repo, allowlist) == 0


def test_allowlisted_wall_clock_duration_passes_strict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    repo = _tree(tmp_path, "import time\nstart = 0\nelapsed = time.time() - start\n")
    allowlist = _write_allowlist(tmp_path / "allow.json", [{"path_glob": "pkg/mod.py", "symbol": "time.time"}])

    assert _run_main(monkeypatch, repo, allowlist, "--strict") == 0
    out = capsys.readouterr().out
    assert "Allowlisted: 1" in out
    assert "Violations: 0" in out


def test_monotonic_clock_use_is_not_a_violation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    repo = _tree(
        tmp_path,
        "from benchbox.utils.clock import mono_time\nstart = mono_time()\nelapsed = mono_time() - start\n",
    )
    allowlist = _write_allowlist(tmp_path / "allow.json", [])

    assert _run_main(monkeypatch, repo, allowlist, "--strict") == 0
    assert "Timing policy candidates: 0" in capsys.readouterr().out


def test_unclassified_wall_clock_is_informational_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    repo = _tree(tmp_path, "import time\nnow = time.time()\n")
    allowlist = _write_allowlist(tmp_path / "allow.json", [])

    assert _run_main(monkeypatch, repo, allowlist, "--strict") == 0
    out = capsys.readouterr().out
    assert "Unknown (informational): 1" in out
    assert "Violations: 0" in out


@pytest.mark.parametrize("flag", ["--skip-fast-lane", "--only-fast-lane", "--emit-fast-count", "--delta-check"])
def test_wall_clock_check_does_not_expose_fast_lane_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, flag: str
) -> None:
    repo = _tree(tmp_path, "x = 1\n")
    allowlist = _write_allowlist(tmp_path / "allow.json", [])
    with pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, repo, allowlist, flag)
    assert excinfo.value.code == 2


def test_wall_clock_check_never_starts_pytest_collection() -> None:
    text = _SCRIPT.read_text(encoding="utf-8")
    assert "subprocess" not in text
    assert "fast_test_lane_policy" not in text


