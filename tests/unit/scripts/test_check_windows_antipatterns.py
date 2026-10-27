from __future__ import annotations

import json
from pathlib import Path

import pytest
from check_windows_antipatterns import (
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


def _codes(tmp_path: Path, src: str) -> list[str]:
    return [v.code for v in check_file(_write(tmp_path, src))]


def test_wa004_dev_null_literal(tmp_path: Path) -> None:
    assert _codes(tmp_path, "import subprocess\nsubprocess.run(['git', '-c', '/dev/null'])\n") == ["WA004"]


def test_wa004_dev_null_as_path(tmp_path: Path) -> None:
    assert _codes(tmp_path, "from pathlib import Path\np = Path('/dev/null')\n") == ["WA004"]


def test_wa004_os_devnull_is_clean(tmp_path: Path) -> None:
    assert _codes(tmp_path, "import os\np = os.devnull\n") == []


def test_wa004_longer_path_is_clean(tmp_path: Path) -> None:
    assert _codes(tmp_path, "p = '/dev/nullable'\n") == []


@pytest.mark.parametrize("module", ["fcntl", "termios", "pwd", "grp", "resource"])
def test_wa005_module_level_import(tmp_path: Path, module: str) -> None:
    assert _codes(tmp_path, f"import {module}\n") == ["WA005"]


def test_wa005_from_import(tmp_path: Path) -> None:
    assert _codes(tmp_path, "from fcntl import flock\n") == ["WA005"]


def test_wa005_function_level_import_is_clean(tmp_path: Path) -> None:
    assert _codes(tmp_path, "def f():\n    import fcntl\n    return fcntl\n") == []


def test_wa005_sys_platform_guard_is_clean(tmp_path: Path) -> None:
    src = "import sys\nif sys.platform != 'win32':\n    import fcntl\n"
    assert _codes(tmp_path, src) == []


def test_wa005_os_name_guard_is_clean(tmp_path: Path) -> None:
    src = "import os\nif os.name == 'posix':\n    import pwd\n"
    assert _codes(tmp_path, src) == []


def test_wa005_try_except_import_error_is_clean(tmp_path: Path) -> None:
    src = "try:\n    import termios\nexcept ImportError:\n    termios = None\n"
    assert _codes(tmp_path, src) == []


def test_wa005_try_without_import_error_handler_flags(tmp_path: Path) -> None:
    src = "try:\n    import grp\nexcept ValueError:\n    pass\n"
    assert _codes(tmp_path, src) == ["WA005"]


def test_wa005_unrelated_if_still_flags(tmp_path: Path) -> None:
    assert _codes(tmp_path, "import os\nif os.environ.get('X'):\n    import resource\n") == ["WA005"]


def test_wa006_env_literal_without_systemroot(tmp_path: Path) -> None:
    src = "import subprocess\nsubprocess.run(['x'], env={'PATH': '/usr/bin:/bin'})\n"
    assert _codes(tmp_path, src) == ["WA006"]


@pytest.mark.parametrize("func", ["run", "Popen", "check_output", "check_call"])
def test_wa006_covers_subprocess_entry_points(tmp_path: Path, func: str) -> None:
    assert _codes(tmp_path, f"import subprocess\nsubprocess.{func}(['x'], env={{'A': '1'}})\n") == ["WA006"]


def test_wa006_alias_and_from_import(tmp_path: Path) -> None:
    aliased = "import subprocess as sp\nsp.run(['x'], env={'A': '1'})\n"
    imported = "from subprocess import run as r\nr(['x'], env={'A': '1'})\n"
    assert _codes(tmp_path, aliased) == ["WA006"]
    assert _codes(tmp_path, imported) == ["WA006"]


def test_wa006_systemroot_key_is_clean(tmp_path: Path) -> None:
    src = "import subprocess\nsubprocess.run(['x'], env={'PATH': 'p', 'SYSTEMROOT': 'c'})\n"
    assert _codes(tmp_path, src) == []


def test_wa006_os_environ_spread_is_clean(tmp_path: Path) -> None:
    src = "import os, subprocess\nsubprocess.run(['x'], env={**os.environ, 'A': '1'})\n"
    assert _codes(tmp_path, src) == []


def test_wa006_dict_of_os_environ_is_clean(tmp_path: Path) -> None:
    src = "import os, subprocess\nsubprocess.run(['x'], env=dict(os.environ, A='1'))\n"
    assert _codes(tmp_path, src) == []


@pytest.mark.parametrize("keyword", ["text=True", "universal_newlines=True"])
def test_git_encoding_not_ratcheted(tmp_path: Path, keyword: str) -> None:
    src = f"import subprocess\nsubprocess.run(['git', 'ls-files', '-z'], {keyword})\n"
    assert _codes(tmp_path, src) == []


def test_wa006_non_subprocess_call_is_clean(tmp_path: Path) -> None:
    assert _codes(tmp_path, "import foo\nfoo.run(['x'], env={'A': '1'})\n") == []


@pytest.mark.parametrize(
    "source",
    [
        "import fcntl\n",
        "import sys\nif sys.platform == 'win32':\n    import fcntl\n",
        "import sys\nif sys.platform != 'win32':\n    pass\nelse:\n    import fcntl\n",
        "import sys\nif sys.platform:\n    import fcntl\n",
    ],
)
def test_posix_imports_inside_project_are_checked(tmp_path: Path, source: str) -> None:
    root = tmp_path / "_project"
    root.mkdir()
    (root / "probe.py").write_text(source, encoding="utf-8")
    violations, _ = check_tree([tmp_path], tmp_path)
    assert [v.code for v in violations] == ["WA005"]


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("path == '/dev/null'", []),
        ("path in ('--- /dev/null', '+++ /dev/null')", []),
        ("open('/dev/null') == None", ["WA004"]),
        ("sys.platform != 'win32' and Path('/dev/null')", []),
        ("sys.platform != 'win32' or Path('/dev/null')", ["WA004"]),
    ],
)
def test_dev_null_guards_do_not_hide_windows_paths(tmp_path: Path, expression: str, expected: list[str]) -> None:
    assert _codes(tmp_path, f"import sys\nfrom pathlib import Path\n{expression}\n") == expected


@pytest.mark.parametrize(
    ("definition", "expected"),
    [
        ("environment = {'PATH': ''}", ["WA006"]),
        ("environment = {'SYSTEMROOT': 'C:/Windows'}", []),
        ("environment = {**os.environ, 'PATH': ''}", []),
        ("environment = os.environ.copy()", []),
        ("environment = {}\nenvironment['SYSTEMROOT'] = os.environ['SYSTEMROOT']", []),
        ("environment = {}\nfor key in ('SYSTEMROOT',):\n    environment[key] = os.environ[key]", []),
    ],
)
def test_local_environment_binding(tmp_path: Path, definition: str, expected: list[str]) -> None:
    body = definition.replace("\n", "\n    ")
    source = f"import os, subprocess\n\ndef launch():\n    {body}\n    subprocess.run(['x'], env=environment)\n"
    assert _codes(tmp_path, source) == expected


def test_function_environment_bindings_do_not_leak(tmp_path: Path) -> None:
    source = (
        "import subprocess\n"
        "def first():\n    environment = {}\n"
        "def second(environment):\n    subprocess.run(['x'], env=environment)\n"
    )
    assert _codes(tmp_path, source) == []


def test_default_roots_cover_project_tooling() -> None:
    import check_windows_antipatterns as checker

    assert {"benchbox", "tests", "scripts", "_project"} <= set(checker._DEFAULT_ROOTS)


def test_explicit_file_scan_ignores_baseline_entries_for_other_files(tmp_path: Path) -> None:
    import check_windows_antipatterns as checker

    clean = _write(tmp_path, "value = 1\n")
    other = tmp_path / "unrelated.py"
    other.write_text("value = 2\n", encoding="utf-8")
    baseline = {"unrelated.py": {"WA001": 1}}

    whole_tree = checker.apply_baseline([], baseline, tmp_path, [tmp_path])
    explicit = checker.apply_baseline([], baseline, tmp_path, [clean], explicit_files=[clean])

    assert whole_tree.stale
    assert explicit.stale == []
