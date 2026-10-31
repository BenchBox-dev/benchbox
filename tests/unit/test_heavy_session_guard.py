from __future__ import annotations

import os
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest

from benchbox.utils.host_lock import EXIT_ADMISSION, EXIT_REFUSED
from tests.utilities.lock_holder import held_lock
from tests.utilities.paths import REPO_ROOT

pytestmark = [pytest.mark.unit, pytest.mark.medium]

PERMISSIVE = {
    "BENCHBOX_HEAVY_ADMIT": "force",
    "BENCHBOX_HEAVY_WATCHDOG": "off",
}
IMPOSSIBLE = {**PERMISSIVE, "BENCHBOX_HEAVY_ADMIT": "", "BENCHBOX_HEAVY_ADMIT_MIN_GIB": "1000000"}

MIXED = "import pytest\n@pytest.mark.slow\ndef test_slow():\n    pass\ndef test_plain():\n    pass\n"
LIVE = "import pytest\n@pytest.mark.live_integration\ndef test_live():\n    pass\n"
PLAIN = "def test_plain():\n    pass\n"

INI = (
    "[pytest]\n"
    'addopts = -m "not slow and not stress and not live_integration and not resource_heavy"\n'
    "markers =\n    slow: s\n    stress: s\n    live_integration: l\n    resource_heavy: r\n"
)


def _env(tmp_path: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    home = tmp_path / "caller-home"
    home.mkdir(exist_ok=True)
    env = dict(
        os.environ,
        HOME=str(home),
        USERPROFILE=str(home),
        PYTHONPATH=str(REPO_ROOT),
        BENCHBOX_TEST_LOCK_DIR=str(tmp_path / "lock"),
        BENCHBOX_TEST_LOCK_WAIT_SECONDS="0",
    )
    for key in (
        "BENCHBOX_TEST_SESSION_OWNER",
        "BENCHBOX_SKIP_TEST_LOCK",
        *[name for name in env if name.startswith("PYTEST_XDIST_")],
    ):
        env.pop(key, None)
    env.update(extra or {})
    return env


def _pytest(
    tmp_path: Path, source: str, *args: str, held: bool, extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    (tmp_path / "pytest.ini").write_text(INI, encoding="utf-8")
    (tmp_path / "test_probe.py").write_text(source, encoding="utf-8")
    with held_lock(tmp_path / "lock") if held else nullcontext():
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-c",
                str(tmp_path / "pytest.ini"),
                "--confcutdir",
                str(tmp_path),
                "-p",
                "_benchbox_pytest_xdist_safety",
                "-p",
                "no:cacheprovider",
                "-q",
                *args,
                "test_probe.py",
            ],
            cwd=tmp_path,
            env=_env(tmp_path, {**PERMISSIVE, **(extra or {})}),
            capture_output=True,
            text=True,
            timeout=120,
        )


@pytest.mark.parametrize(
    ("source", "args"),
    [(MIXED, ("-n", "0", "-m", "slow")), (LIVE, ("-n", "0", "-m", "live_integration")), (PLAIN, ("-n", "2"))],
    ids=["serial-slow", "serial-live", "parallel"],
)
def test_a_session_that_needs_the_lock_is_refused_while_it_is_held(
    tmp_path: Path, source: str, args: tuple[str, ...]
) -> None:
    result = _pytest(tmp_path, source, *args, held=True)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "passed" not in result.stdout
    if args[:2] == ("-n", "0"):
        assert result.returncode == EXIT_REFUSED, result.stdout + result.stderr


def test_a_serial_session_without_heavy_items_does_not_wait(tmp_path: Path) -> None:
    result = _pytest(tmp_path, MIXED, "-n", "0", held=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_a_heavy_serial_session_runs_nested_heavy_work_without_waiting(tmp_path: Path) -> None:
    nested = (
        "import os, subprocess, sys\n"
        "import pytest\n"
        "@pytest.mark.slow\n"
        "def test_nested():\n"
        "    inner = os.path.join(os.path.dirname(__file__), 'inner')\n"
        "    os.makedirs(inner, exist_ok=True)\n"
        "    open(os.path.join(inner, 'test_inner.py'), 'w').write("
        "'import pytest\\n@pytest.mark.slow\\ndef test_inner():\\n    pass\\n')\n"
        "    open(os.path.join(inner, 'pytest.ini'), 'w').write(open(os.path.join(os.path.dirname(__file__), 'pytest.ini')).read())\n"
        "    child = subprocess.run([sys.executable, '-m', 'pytest', '-n', '0', '-m', 'slow', '-p', "
        "'_benchbox_pytest_xdist_safety', '-p', 'no:cacheprovider', '-q', 'test_inner.py'], cwd=inner, env=os.environ.copy(), capture_output=True, "
        "text=True, timeout=60)\n"
        "    assert child.returncode == 0, child.stdout + child.stderr\n"
        "    wrapped = subprocess.run([sys.executable, '-m', 'benchbox.utils.host_lock', 'run', '--', 'true'], "
        "env=os.environ.copy(), capture_output=True, text=True, timeout=60)\n"
        "    assert wrapped.returncode == 0, wrapped.stdout + wrapped.stderr\n"
    )
    result = _pytest(tmp_path, nested, "-n", "0", "-m", "slow", held=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "heavy-work admission:" in result.stderr


def test_failing_admission_refuses_a_heavy_session_only(tmp_path: Path) -> None:
    refused = _pytest(tmp_path, MIXED, "-n", "0", "-m", "slow", held=False, extra=IMPOSSIBLE)
    assert refused.returncode == EXIT_ADMISSION, refused.stdout + refused.stderr
    assert "REFUSED" in refused.stdout + refused.stderr
    second = tmp_path / "second"
    second.mkdir()
    default = _pytest(second, PLAIN, "-n", "2", held=False, extra=IMPOSSIBLE)
    assert default.returncode == 0, default.stdout + default.stderr


@pytest.mark.parametrize(
    ("expression", "heavy"),
    [
        ("not slow and not stress and not live_integration and not resource_heavy", False),
        ("slow", True),
        ("(slow or resource_heavy) and not (stress or live_integration)", True),
        ("live_gizmosql", True),
        ("fast and not (slow or stress or resource_heavy or live_integration)", False),
        ("unit", True),
        ("not stress", True),
        ("not (stress or resource_heavy or live_integration)", True),
        ("integration and not live_integration and not stress", True),
        ("integration and slow", True),
        ("medium and not (slow or stress or resource_heavy or live_integration)", False),
        ("", True),
        (None, False),
    ],
)
def test_a_marker_expression_is_heavy_only_when_it_selects_a_heavy_marker(expression: str | None, heavy: bool) -> None:
    import _benchbox_pytest_xdist_safety as plugin

    assert plugin._expression_selects_heavy(expression) is heavy
