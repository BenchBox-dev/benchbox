from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from scripts.local_validation import _close_lock, wait_for_lock, write_holder
from tests.utilities.paths import REPO_ROOT

pytestmark = [pytest.mark.unit, pytest.mark.medium]


def test_unit_home_is_owned_and_empty(_unit_home: Path) -> None:
    assert Path.home() == _unit_home
    assert os.environ["USERPROFILE"] == str(_unit_home)
    assert not (_unit_home / ".benchbox").exists()


def test_cli_invocation_owns_quiet_and_provider_state() -> None:
    import benchbox.utils.config_interface as ci
    import benchbox.utils.printing as printing

    before = (printing._QUIET, ci._config_provider)
    installed = object()

    @click.command()
    def command():
        printing.set_quiet(True)
        ci._config_provider = installed
        assert printing.is_quiet()
        assert ci._config_provider is installed
        raise click.ClickException("intentional failure")

    result = CliRunner().invoke(command)
    assert result.exit_code == 1
    assert (printing._QUIET, ci._config_provider) == before


@pytest.mark.parametrize("collect_only", [False, True])
def test_home_is_isolated_before_conftest_and_collection(tmp_path: Path, collect_only: bool) -> None:
    real_home = tmp_path / "caller-home"
    real_config = real_home / ".benchbox" / "config.yaml"
    real_config.parent.mkdir(parents=True)
    poison = "poisoned-real-home-config\n"
    real_config.write_text(poison, encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "conftest.py").write_text(
        "import os\nfrom pathlib import Path\n"
        'assert str(Path.home()) != os.environ["PROBE_CALLER_HOME"]\n'
        'assert not (Path.home() / ".benchbox" / "config.yaml").exists()\n'
        'assert os.environ["USERPROFILE"] == str(Path.home())\n',
        encoding="utf-8",
    )
    (tmp_path / "test_probe.py").write_text(
        "from pathlib import Path\n"
        'assert not (Path.home() / ".benchbox").exists()\n'
        "def test_home():\n"
        '    (Path.home() / ".benchbox").mkdir()\n'
        '    (Path.home() / ".benchbox" / "config.yaml").write_text("owned")\n',
        encoding="utf-8",
    )
    env = dict(
        os.environ,
        HOME=str(real_home),
        USERPROFILE=str(real_home),
        PROBE_CALLER_HOME=str(real_home),
        PYTHONPATH=str(REPO_ROOT),
    )
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        str(tmp_path / "pytest.ini"),
        "--confcutdir",
        str(tmp_path),
        "-p",
        "_benchbox_pytest_xdist_safety",
        "-n",
        "0",
        "-q",
        "test_probe.py",
    ]
    if collect_only:
        command.append("--collect-only")
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert real_config.read_text(encoding="utf-8") == poison


def test_contended_lock_prevents_home_setup(tmp_path: Path) -> None:
    lock_path = tmp_path / "lock" / "test.lock"
    fd = wait_for_lock(lock_path, 0)
    write_holder(fd, lock_path, phase="test", gate="isolation-negative")
    env = dict(
        os.environ,
        PYTHONPATH=str(REPO_ROOT),
        BENCHBOX_TEST_LOCK_DIR=str(lock_path.parent),
        BENCHBOX_TEST_LOCK_WAIT_SECONDS="0",
    )
    env.pop("BENCHBOX_TEST_SESSION_OWNER", None)
    script = (
        "import os\nfrom tests.utilities import session_isolation as isolation\n"
        "before = dict(os.environ)\n"
        'def forbidden(*a, **k):\n    raise AssertionError("HOME created before lock")\n'
        "isolation.tempfile.TemporaryDirectory = forbidden\n"
        "try:\n    isolation.start()\n"
        "except TimeoutError:\n    assert dict(os.environ) == before\n"
        'else:\n    raise AssertionError("contended lock bypassed")\n'
    )
    try:
        result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert f"pid:{os.getpid()} " in lock_path.read_text(encoding="utf-8")
    finally:
        _close_lock(fd)


def test_session_finish_restores_caller_environment(tmp_path: Path) -> None:
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    script = (
        "import os\nfrom pathlib import Path\nfrom tests.utilities import session_isolation as isolation\n"
        "before = dict(os.environ)\n"
        "isolation.start()\nowned_home = Path.home()\n"
        'assert owned_home.is_dir()\nassert str(owned_home) != before["HOME"]\n'
        "isolation.finish()\nisolation.finish()\n"
        "assert dict(os.environ) == before\nassert not owned_home.exists()\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("caller_value", [None, "", "{tmp}/caller-runs"])
def test_session_owns_benchmark_runs_root(tmp_path: Path, caller_value: str | None) -> None:
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    env.pop("BENCHBOX_TEST_SESSION_OWNER", None)
    env.pop("BENCHBOX_OUTPUT_DIR", None)
    if caller_value is not None:
        env["BENCHBOX_OUTPUT_DIR"] = caller_value.format(tmp=tmp_path)
    script = (
        "import os\nfrom pathlib import Path\nfrom tests.utilities import session_isolation as isolation\n"
        "before = dict(os.environ)\n"
        "isolation.start(acquire_lock=False)\n"
        'caller = (before.get("BENCHBOX_OUTPUT_DIR") or "").strip()\n'
        'expected = caller or str(Path.home() / "benchmark_runs")\n'
        'assert os.environ["BENCHBOX_OUTPUT_DIR"] == expected, os.environ["BENCHBOX_OUTPUT_DIR"]\n'
        "isolation.finish()\n"
        "assert dict(os.environ) == before\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _run_session_probe(
    tmp_path: Path,
    test_source: str,
    *pytest_args: str,
    extra_env: dict[str, str] | None = None,
    lock_held: bool = False,
    caller_files: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    caller_home = tmp_path / "caller-home"
    caller_home.mkdir()
    for relative, content in (caller_files or {}).items():
        seeded = caller_home / relative
        seeded.parent.mkdir(parents=True, exist_ok=True)
        seeded.write_text(content, encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers =\n    live_integration: live\n", encoding="utf-8")
    (tmp_path / "test_probe.py").write_text(test_source, encoding="utf-8")
    lock_path = tmp_path / "lock" / "test.lock"
    fd = wait_for_lock(lock_path, 0) if lock_held else None
    if fd is not None:
        write_holder(fd, lock_path, phase="test", gate="isolation-negative")
    env = dict(
        os.environ,
        HOME=str(caller_home),
        USERPROFILE=str(caller_home),
        PROBE_CALLER_HOME=str(caller_home),
        PYTHONPATH=str(REPO_ROOT),
        BENCHBOX_TEST_LOCK_DIR=str(lock_path.parent),
        BENCHBOX_TEST_LOCK_WAIT_SECONDS="0",
        **(extra_env or {}),
    )
    for inherited in (
        "BENCHBOX_TEST_SESSION_OWNER",
        "BENCHBOX_SKIP_TEST_LOCK",
        *[k for k in env if k.startswith("PYTEST_XDIST_")],
    ):
        env.pop(inherited, None)
    env.update(extra_env or {})
    try:
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
                *pytest_args,
                "test_probe.py",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        if fd is not None:
            _close_lock(fd)


HOME_PROBE = (
    "import os\nfrom pathlib import Path\n"
    "def test_home():\n"
    '    assert str(Path.home()) != os.environ["PROBE_CALLER_HOME"]\n'
)


def test_a_serial_run_does_not_wait_for_the_shared_lock(tmp_path: Path) -> None:
    result = _run_session_probe(tmp_path, HOME_PROBE, "-n", "0", lock_held=True)
    assert result.returncode == 0, result.stdout + result.stderr


WORKER_OUTPUT_PROBE = (
    "import os\n"
    "def test_record():\n"
    '    with open(os.environ["PROBE_RECORD"], "a", encoding="utf-8") as f:\n'
    '        f.write(os.environ["PYTEST_XDIST_WORKER"] + "|" + os.environ["HOME"] + "|" + os.environ["BENCHBOX_OUTPUT_DIR"] + "\\n")\n'
)


def test_each_parallel_worker_owns_its_benchmark_runs_root(tmp_path: Path) -> None:
    record = tmp_path / "record.txt"
    result = _run_session_probe(
        tmp_path,
        WORKER_OUTPUT_PROBE,
        "-n",
        "2",
        "--dist",
        "each",
        extra_env={"BENCHBOX_SKIP_TEST_LOCK": "1", "PROBE_RECORD": str(record)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rows = [line.split("|") for line in record.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert len({row[0] for row in rows}) == 2
    assert len({row[2] for row in rows}) == 2
    for _worker, home, output in rows:
        assert output == str(Path(home) / "benchmark_runs")


def test_a_parallel_run_still_waits_for_the_shared_lock(tmp_path: Path) -> None:
    result = _run_session_probe(tmp_path, HOME_PROBE, "-n", "1", lock_held=True)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "TimeoutError" in result.stdout + result.stderr or "lock" in (result.stdout + result.stderr).lower()


def test_the_documented_lock_bypass_is_honored_by_a_parallel_run(tmp_path: Path) -> None:
    result = _run_session_probe(
        tmp_path, HOME_PROBE, "-n", "1", extra_env={"BENCHBOX_SKIP_TEST_LOCK": "1"}, lock_held=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


LIVE_PROBE = (
    "import os\nimport pytest\nfrom pathlib import Path\n"
    "@pytest.mark.live_integration\n"
    "def test_live_keeps_the_caller_home():\n"
    '    assert str(Path.home()) == os.environ["PROBE_CALLER_HOME"]\n'
    "def test_other_is_isolated():\n"
    '    assert str(Path.home()) != os.environ["PROBE_CALLER_HOME"]\n'
)


def test_a_run_that_selects_live_tests_keeps_the_callers_home(tmp_path: Path) -> None:
    result = _run_session_probe(tmp_path, LIVE_PROBE, "-n", "0", "-m", "live_integration")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_a_run_that_deselects_live_tests_is_isolated(tmp_path: Path) -> None:
    result = _run_session_probe(tmp_path, LIVE_PROBE, "-n", "0", "-m", "not live_integration")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


ISOLATED_PROBE = (
    "import os\nimport pytest\nfrom pathlib import Path\n"
    "@pytest.mark.live_integration\n"
    "def test_live_is_isolated():\n"
    '    assert str(Path.home()) != os.environ["PROBE_CALLER_HOME"]\n'
    "def test_other_is_isolated():\n"
    '    assert str(Path.home()) != os.environ["PROBE_CALLER_HOME"]\n'
)


def test_a_run_that_also_selects_unmarked_tests_stays_isolated(tmp_path: Path) -> None:
    result = _run_session_probe(tmp_path, ISOLATED_PROBE, "-n", "0", "-m", "not stress")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("live_integration", True),
        ("live_integration and not stress", True),
        ("not stress", False),
        ("not live_integration", False),
        ("live_integration or fast", True),
        ("not (slow or stress)", False),
        ("", False),
    ],
)
def test_isolation_is_dropped_only_for_runs_selecting_nothing_but_live_tests(expression: str, expected: bool) -> None:
    import _benchbox_pytest_xdist_safety as plugin

    assert plugin._selects_only_live_tests(["-m", expression]) is expected
    assert plugin._selects_only_live_tests([]) is False


SENTINEL = "sentinel-secret-7f3a91c2"
SAVED_CREDENTIALS = f"snowflake:\n  account: sentinel-account\n  username: sentinel-user\n  password: {SENTINEL}\n"
CREDENTIAL_PROBE = (
    "import json, os, pytest\n"
    "from benchbox.core.hooks.platform_hooks import PlatformHookRegistry\n"
    "\n"
    "def _build():\n"
    '    config = PlatformHookRegistry.build_database_config("snowflake", {})\n'
    "    return config, json.dumps(config.model_dump(), default=str)\n"
    "\n"
    "@pytest.mark.live_integration\n"
    "def test_live_run_sees_the_saved_credential():\n"
    "    config, dumped = _build()\n"
    '    assert os.environ["PROBE_SENTINEL"] in dumped\n'
    '    assert os.environ["PROBE_SENTINEL"] not in repr(config) + str(config)\n'
    "\n"
    "def test_isolated_run_never_sees_the_saved_credential():\n"
    "    config, dumped = _build()\n"
    '    assert os.environ["PROBE_SENTINEL"] not in dumped + repr(config) + str(config)\n'
    "    assert not config.options.get('password')\n"
)


@pytest.mark.parametrize(
    ("selection", "expected_passed"),
    [("not live_integration", "1 passed"), ("live_integration", "1 passed")],
    ids=["isolated-run-cannot-read-them", "live-only-run-can-and-masks-them"],
)
def test_a_built_config_only_carries_saved_credentials_the_run_is_allowed_to_read(
    tmp_path: Path, selection: str, expected_passed: str
) -> None:
    result = _run_session_probe(
        tmp_path,
        CREDENTIAL_PROBE,
        "-n",
        "0",
        "-m",
        selection,
        extra_env={"PROBE_SENTINEL": SENTINEL},
        caller_files={".benchbox/credentials.yaml": SAVED_CREDENTIALS},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert expected_passed in result.stdout
    assert SENTINEL not in result.stdout + result.stderr


PREFERENCES_PROBE = (
    "import pytest\n"
    "from benchbox.cli.preferences import save_last_run_config\n"
    "from benchbox.security.credentials import CredentialManager\n"
    "\n"
    "def _write_state():\n"
    '    save_last_run_config(database="duckdb", benchmark="tpch", scale=0.01, tuning_mode="notuning")\n'
    "    manager = CredentialManager()\n"
    '    manager.set_platform_credentials("duckdb", {"path": "x"})\n'
    "    manager.save_credentials()\n"
    "\n"
    "@pytest.mark.live_integration\n"
    "def test_live_run_writes_to_the_callers_home():\n"
    "    _write_state()\n"
    "\n"
    "def test_isolated_run_writes_nowhere_near_the_callers_home():\n"
    "    _write_state()\n"
)


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_an_isolated_run_leaves_the_callers_preferences_and_credentials_untouched(tmp_path: Path) -> None:
    seeded = {".benchbox/last_run.yaml": "database: caller\n", ".benchbox/credentials.yaml": SAVED_CREDENTIALS}
    result = _run_session_probe(
        tmp_path, PREFERENCES_PROBE, "-n", "0", "-m", "not live_integration", caller_files=seeded
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert _snapshot(tmp_path / "caller-home") == seeded


def test_the_write_probe_reaches_the_callers_home_when_isolation_is_off(tmp_path: Path) -> None:
    seeded = {".benchbox/last_run.yaml": "database: caller\n"}
    result = _run_session_probe(tmp_path, PREFERENCES_PROBE, "-n", "0", "-m", "live_integration", caller_files=seeded)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _snapshot(tmp_path / "caller-home") != seeded


class _EarlyConfig:
    def __init__(self) -> None:
        self.cleanups: list[object] = []

    def add_cleanup(self, func: object) -> None:
        self.cleanups.append(func)


@pytest.mark.parametrize(
    ("worker", "args", "expected_lock"),
    [
        (None, ["-n", "2"], True),
        (None, ["-n", "0"], False),
        ("gw0", ["-n", "2"], False),
    ],
    ids=["parallel-controller", "serial", "xdist-worker"],
)
def test_only_a_parallel_controller_takes_the_lock(
    monkeypatch: pytest.MonkeyPatch, worker: str | None, args: list[str], expected_lock: bool
) -> None:
    import _benchbox_pytest_xdist_safety as plugin

    calls: list[bool] = []
    monkeypatch.setattr(
        plugin.session_isolation, "start", lambda acquire_lock=True: calls.append(acquire_lock) or False
    )
    monkeypatch.delenv("BENCHBOX_SKIP_TEST_LOCK", raising=False)
    if worker is None:
        monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    else:
        monkeypatch.setenv("PYTEST_XDIST_WORKER", worker)
    hook = plugin.pytest_load_initial_conftests(_EarlyConfig(), None, list(args))
    next(hook)
    hook.close()
    assert calls == [expected_lock]
