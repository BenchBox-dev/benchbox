from __future__ import annotations

from types import SimpleNamespace

import pytest

import _benchbox_pytest_xdist_safety as safety_plugin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


def test_safe_worker_count_hard_caps_to_two_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BENCHBOX_MAX_XDIST_WORKERS", raising=False)
    monkeypatch.setattr(safety_plugin.sys, "platform", "darwin", raising=False)
    monkeypatch.setattr(safety_plugin.os, "cpu_count", lambda: 12)
    monkeypatch.setattr(
        "psutil.virtual_memory",
        lambda: SimpleNamespace(available=8 * 1024 * 1024 * 1024),
    )

    assert safety_plugin._safe_worker_count() == 6


def test_safe_worker_count_honors_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCHBOX_MAX_XDIST_WORKERS", "5")
    monkeypatch.setattr(safety_plugin.sys, "platform", "darwin", raising=False)

    assert safety_plugin._safe_worker_count() == 5


def test_pytest_xdist_auto_num_workers_uses_safe_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safety_plugin, "_safe_worker_count", lambda: 2)

    assert safety_plugin.pytest_xdist_auto_num_workers(SimpleNamespace()) == 2


def test_rewrite_numprocesses_args_caps_explicit_requests() -> None:
    args = ["-n", "8", "tests/unit/test_release_sync.py"]

    requested = safety_plugin._rewrite_numprocesses_args(args, 2)

    assert requested == "8"
    assert args == ["-n", "2", "tests/unit/test_release_sync.py"]


def test_rewrite_numprocesses_args_caps_auto_requests() -> None:
    args = ["--numprocesses=auto", "tests/unit/test_release_sync.py"]

    requested = safety_plugin._rewrite_numprocesses_args(args, 2)

    assert requested == "auto"
    assert args == ["--numprocesses=2", "tests/unit/test_release_sync.py"]


def test_rewrite_numprocesses_args_leaves_safe_requests_unchanged() -> None:
    args = ["-n2", "tests/unit/test_release_sync.py"]

    requested = safety_plugin._rewrite_numprocesses_args(args, 2)

    assert requested is None
    assert args == ["-n2", "tests/unit/test_release_sync.py"]


def test_rewrite_numprocesses_args_uses_last_numprocesses_occurrence() -> None:
    args = ["-n", "auto", "-n", "8", "tests/unit/test_release_sync.py"]

    requested = safety_plugin._rewrite_numprocesses_args(args, 2)

    assert requested == "8"
    assert args == ["-n", "auto", "-n", "2", "tests/unit/test_release_sync.py"]


def test_suppress_xdist_worker_title_patches_only_exec_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    outer_title_fn = lambda title: "OUTER_FRAME"
    monkeypatch.setitem(globals(), "worker_title", outer_title_fn)

    original_title_fn = lambda title: "INNER_FRAME"
    fake_remote_globals = {
        "__name__": "__channelexec__",
        "worker_title": original_title_fn,
    }

    exec(
        "from tests.conftest import _suppress_xdist_worker_title\nresult = _suppress_xdist_worker_title()",
        fake_remote_globals,
    )

    assert fake_remote_globals["result"] is True
    assert fake_remote_globals["worker_title"]("any title") is None
    assert fake_remote_globals["worker_title"] is not original_title_fn
    assert globals()["worker_title"] is outer_title_fn


def test_suppress_xdist_worker_title_ignores_non_exec_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.conftest import _suppress_xdist_worker_title

    original_title_fn = lambda title: "NOT_XDIST"
    monkeypatch.setitem(globals(), "worker_title", original_title_fn)

    _suppress_xdist_worker_title()
    assert globals()["worker_title"] is original_title_fn


class _IniConfig:
    def __init__(self, ini: dict[str, object], *, worker: bool = False) -> None:
        self._ini = ini
        if worker:
            self.workerinput = {"workerid": "gw0"}

    def getini(self, name: str) -> object:
        if name not in self._ini:
            raise ValueError(f"unknown configuration value: {name!r}")
        return self._ini[name]


@pytest.mark.parametrize("value", ["120", "0.5", 30.0])
def test_configure_refuses_faulthandler_timeout(value: object) -> None:
    with pytest.raises(pytest.exit.Exception) as excinfo:
        safety_plugin.pytest_configure(_IniConfig({"faulthandler_timeout": value}))

    assert excinfo.value.returncode == pytest.ExitCode.USAGE_ERROR
    assert "faulthandler_timeout" in str(excinfo.value.msg)
    assert "pytest-timeout" in str(excinfo.value.msg)


@pytest.mark.parametrize("ini", [{"faulthandler_timeout": 0.0}, {"faulthandler_timeout": ""}, {}])
def test_configure_allows_unset_or_unregistered_faulthandler_timeout(
    ini: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BENCHBOX_XDIST_CAP_REQUESTED", raising=False)
    monkeypatch.delenv("BENCHBOX_XDIST_CAP_EFFECTIVE", raising=False)

    safety_plugin.pytest_configure(_IniConfig(ini))


def test_configure_leaves_the_refusal_to_the_controller() -> None:
    safety_plugin.pytest_configure(_IniConfig({"faulthandler_timeout": "120"}, worker=True))


def test_pytest_run_with_faulthandler_timeout_exits_as_usage_error(tmp_path) -> None:
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    probe = tmp_path / "test_probe.py"
    probe.write_text("def test_probe():\n    pass\n")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-n",
            "0",
            "-p",
            "no:cacheprovider",
            "-m",
            "",
            "--rootdir",
            str(root),
            "-c",
            str(root / "pytest.ini"),
            "-o",
            "faulthandler_timeout=120",
            str(probe),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert completed.returncode == pytest.ExitCode.USAGE_ERROR, completed.stdout + completed.stderr
    assert "faulthandler_timeout is not supported" in completed.stdout + completed.stderr
    assert "1 passed" not in completed.stdout
