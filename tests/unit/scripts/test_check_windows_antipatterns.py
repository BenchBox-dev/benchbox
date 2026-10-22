from __future__ import annotations

import json
from pathlib import Path

import pytest
from check_windows_antipatterns import (
    _DEFAULT_ROOTS,
    Violation,
    apply_baseline,
    check_file,
    check_tree,
    load_baseline,
    write_baseline,
)

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def _write(tmp_path: Path, source: str) -> Path:

    p = tmp_path / "test_source.py"
    p.write_text(source)
    return p


def test_wa001_bare_os_access(tmp_path: Path) -> None:
    src = "import os\nos.access('/tmp/x', os.X_OK)\n"
    violations = check_file(_write(tmp_path, src))
    assert len(violations) == 1
    assert violations[0].code == "WA001"


def test_wa001_guarded_os_name(tmp_path: Path) -> None:
    src = "import os\nos.name != 'nt' and os.access('/tmp/x', os.X_OK)\n"
    violations = check_file(_write(tmp_path, src))
    assert violations == []


def test_wa001_guarded_sys_platform(tmp_path: Path) -> None:
    src = "import os, sys\nsys.platform != 'win32' and os.access('/tmp/x', os.X_OK)\n"
    violations = check_file(_write(tmp_path, src))
    assert violations == []


def test_wa001_guarded_if_block(tmp_path: Path) -> None:
    src = "import os\nif os.name != 'nt':\n    os.access('/tmp/x', os.X_OK)\n"
    violations = check_file(_write(tmp_path, src))
    assert violations == []


def test_wa001_unguarded_else_branch(tmp_path: Path) -> None:
    src = "import os\nif os.name != 'nt':\n    pass\nelse:\n    os.access('/tmp/x', os.X_OK)\n"
    violations = check_file(_write(tmp_path, src))
    assert len(violations) == 1
    assert violations[0].code == "WA001"


def test_wa002_bare_signal(tmp_path: Path) -> None:
    src = "import signal\nsignal.SIGTERM\n"
    violations = check_file(_write(tmp_path, src))
    assert len(violations) == 1
    assert violations[0].code == "WA002"


def test_wa002_guarded_hasattr_if(tmp_path: Path) -> None:
    src = "import signal\nif hasattr(signal, 'SIGTERM'):\n    signal.SIGTERM\n"
    violations = check_file(_write(tmp_path, src))
    assert violations == []


def test_wa002_guarded_hasattr_boolop(tmp_path: Path) -> None:
    src = "import signal\ncond = True\nif hasattr(signal, 'SIGTERM') and cond:\n    signal.SIGTERM\n"
    violations = check_file(_write(tmp_path, src))
    assert violations == []


def test_wa002_guarded_getattr_3arg(tmp_path: Path) -> None:
    src = "import signal\ngetattr(signal, 'SIGTERM', None)\n"
    violations = check_file(_write(tmp_path, src))
    assert violations == []


def test_wa002_unguarded_getattr_2arg(tmp_path: Path) -> None:
    src = "import signal\ngetattr(signal, 'SIGTERM')\n"
    violations = check_file(_write(tmp_path, src))
    assert len(violations) == 1
    assert violations[0].code == "WA002"


def test_wa002_guarded_try_except(tmp_path: Path) -> None:
    src = "import signal\ntry:\n    signal.SIGTERM\nexcept AttributeError:\n    pass\n"
    violations = check_file(_write(tmp_path, src))
    assert violations == []


def test_wa002_guarded_try_bare_except(tmp_path: Path) -> None:
    src = "import signal\ntry:\n    signal.SIGTERM\nexcept:\n    pass\n"
    violations = check_file(_write(tmp_path, src))
    assert violations == []


def test_wa002_all_signal_names(tmp_path: Path) -> None:
    signal_names = ["SIGTERM", "SIGKILL", "SIGHUP", "SIGUSR1", "SIGUSR2", "SIGCHLD", "SIGPIPE"]
    for sig_name in signal_names:
        f = tmp_path / f"sig_{sig_name}.py"
        f.write_text(f"import signal\nsignal.{sig_name}\n")
        violations = check_file(f)
        assert len(violations) == 1, f"{sig_name} not flagged"
        assert violations[0].code == "WA002"


@pytest.mark.parametrize(
    "call",
    [
        "path.read_text()",
        "path.read_text(errors='replace')",
        "path.read_text(encoding=None)",
        "path.read_text(None)",
        "path.read_text(*options)",
        "path.read_text(**options)",
    ],
)
def test_wa003_read_text_without_explicit_encoding(tmp_path: Path, call: str) -> None:
    violations = check_file(_write(tmp_path, f"def read(path, options=()):\n    return {call}\n"))
    assert len(violations) == 1
    assert violations[0].code == "WA003"


@pytest.mark.parametrize(
    "call",
    [
        "path.read_text(encoding='utf-8')",
        "path.read_text('utf-8')",
        "path.read_text(encoding='cp1252', errors='strict')",
        "path.read_text(resolve_encoding())",
    ],
)
def test_wa003_explicit_encoding_is_allowed(tmp_path: Path, call: str) -> None:
    violations = check_file(_write(tmp_path, f"def read(path):\n    return {call}\n"))
    assert violations == []


def test_wa003_binary_reads_are_unchanged(tmp_path: Path) -> None:
    src = "def read(path):\n    return path.read_bytes(), path.open('rb').read()\n"
    assert check_file(_write(tmp_path, src)) == []


def test_noqa_suppresses_wa001(tmp_path: Path) -> None:
    src = "import os\nos.access('/tmp/x', os.X_OK)  # noqa: WA001\n"
    violations = check_file(_write(tmp_path, src))
    assert violations == []


def test_noqa_suppresses_wa002(tmp_path: Path) -> None:
    src = "import signal\nsignal.SIGTERM  # noqa: WA002\n"
    violations = check_file(_write(tmp_path, src))
    assert violations == []


def test_noqa_wrong_code_no_effect(tmp_path: Path) -> None:
    src = "import os\nos.access('/tmp/x', os.X_OK)  # noqa: WA002\n"
    violations = check_file(_write(tmp_path, src))
    assert len(violations) == 1
    assert violations[0].code == "WA001"


def test_noqa_suppresses_wa003(tmp_path: Path) -> None:
    src = "def read(path):\n    return path.read_text()  # noqa: WA003\n"
    assert check_file(_write(tmp_path, src)) == []


def test_check_tree_walks_directory(tmp_path: Path) -> None:
    (tmp_path / "clean1.py").write_text("import os\n")
    (tmp_path / "clean2.py").write_text("x = 1\n")
    (tmp_path / "bad.py").write_text("import os\nos.access('/tmp/x', os.X_OK)\n")
    violations, file_count = check_tree([tmp_path])
    assert file_count == 3
    assert len(violations) == 1
    assert violations[0].code == "WA001"


def test_check_tree_empty_directory(tmp_path: Path) -> None:
    violations, file_count = check_tree([tmp_path])
    assert violations == []
    assert file_count == 0


def _violation(root: Path, relative: str, code: str = "WA003") -> Violation:
    return Violation(file=root / relative, line=1, col=0, code=code, message="m")


def test_default_roots_cover_tests_and_scripts() -> None:
    assert {"benchbox", "tests", "scripts"} <= set(_DEFAULT_ROOTS)


def test_baseline_allows_the_recorded_count(tmp_path: Path) -> None:
    violations = [_violation(tmp_path, "tests/a.py"), _violation(tmp_path, "tests/a.py")]

    result = apply_baseline(violations, {"tests/a.py": {"WA003": 2}}, tmp_path, [tmp_path / "tests"])

    assert result.clean


def test_baseline_rejects_a_new_violation_in_a_baselined_file(tmp_path: Path) -> None:
    violations = [_violation(tmp_path, "tests/a.py"), _violation(tmp_path, "tests/a.py")]

    result = apply_baseline(violations, {"tests/a.py": {"WA003": 1}}, tmp_path, [tmp_path / "tests"])

    assert len(result.regressions) == 2
    assert not result.clean


def test_baseline_rejects_a_violation_in_an_unlisted_file(tmp_path: Path) -> None:
    result = apply_baseline([_violation(tmp_path, "scripts/new.py")], {}, tmp_path, [tmp_path / "scripts"])

    assert [v.file.name for v in result.regressions] == ["new.py"]


def test_baseline_counts_each_code_separately(tmp_path: Path) -> None:
    violations = [_violation(tmp_path, "tests/a.py", "WA003"), _violation(tmp_path, "tests/a.py", "WA002")]

    result = apply_baseline(violations, {"tests/a.py": {"WA003": 1}}, tmp_path, [tmp_path / "tests"])

    assert [v.code for v in result.regressions] == ["WA002"]


def test_baseline_entry_above_the_current_count_is_stale(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "a.py").write_text("x = 1\n", encoding="utf-8")

    result = apply_baseline([], {"tests/a.py": {"WA003": 2}}, tmp_path, [tmp_path / "tests"])

    assert result.stale == ["tests/a.py: WA003 baseline allows 2, found 0"]
    assert not result.clean


def test_baseline_entry_for_a_file_absent_from_the_tree_is_ignored(tmp_path: Path) -> None:
    result = apply_baseline([], {"tests/gone.py": {"WA003": 2}}, tmp_path, [tmp_path / "tests"])

    assert result.clean


def test_stale_check_ignores_paths_outside_the_scanned_roots(tmp_path: Path) -> None:
    result = apply_baseline([], {"tests/a.py": {"WA003": 2}}, tmp_path, [tmp_path / "scripts"])

    assert result.clean


def test_write_baseline_refuses_to_raise_a_count(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"files": {"tests/a.py": {"WA003": 1}}}), encoding="utf-8")
    violations = [_violation(tmp_path, "tests/a.py"), _violation(tmp_path, "tests/a.py")]

    assert write_baseline(path, violations, tmp_path, [tmp_path / "tests"]) == 1
    assert load_baseline(path) == {"tests/a.py": {"WA003": 1}}


def test_write_baseline_lowers_counts_and_keeps_unscanned_entries(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps(
            {"files": {"tests/a.py": {"WA003": 3}, "tests/gone.py": {"WA003": 1}, "scripts/b.py": {"WA003": 2}}}
        ),
        encoding="utf-8",
    )

    assert write_baseline(path, [_violation(tmp_path, "tests/a.py")], tmp_path, [tmp_path / "tests"]) == 0

    assert load_baseline(path) == {"tests/a.py": {"WA003": 1}, "scripts/b.py": {"WA003": 2}}


def test_write_baseline_creates_a_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "quality" / "baseline.json"

    assert write_baseline(path, [_violation(tmp_path, "tests/a.py")], tmp_path, [tmp_path / "tests"]) == 0
    assert load_baseline(path) == {"tests/a.py": {"WA003": 1}}
