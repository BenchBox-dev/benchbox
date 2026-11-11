# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.utilities import posix_shell as posix_shell_module
from tests.utilities.posix_shell import posix_shell

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _write_wsl_stub(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stub = directory / ("bash.exe" if os.name == "nt" else "bash")
    stub.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "sys.stdout.buffer.write("
        "'Windows Subsystem for Linux has no installed distributions.'.encode('utf-16-le'))\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub


@pytest.fixture(autouse=True)
def _clear_resolver_cache():
    cached_resolver = posix_shell_module.posix_shell
    cached_resolver.cache_clear()
    yield
    cached_resolver.cache_clear()


@pytest.mark.skipif(os.name == "nt", reason="stub uses a POSIX shebang")
def test_stub_first_on_path_is_rejected_and_real_bash_is_found(tmp_path, monkeypatch):
    real_bash = shutil.which("bash")
    if real_bash is None:
        pytest.skip("no system bash to fall through to")

    stub_dir = tmp_path / "wslstub"
    _write_wsl_stub(stub_dir)
    monkeypatch.setenv("PATH", f"{stub_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("BENCHBOX_TEST_POSIX_SHELL", raising=False)

    resolved = posix_shell()

    assert resolved is not None, "resolver gave up instead of continuing past the stub"
    assert Path(resolved).parent != stub_dir, f"resolver returned the stub: {resolved}"
    completed = subprocess.run(
        [resolved, "-c", 'set -euo pipefail\nread -r v <<< "ok"\nprintf "%s" "$v"'],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0 and completed.stdout.strip() == "ok"


@pytest.mark.skipif(os.name == "nt", reason="stub uses a POSIX shebang")
def test_resolver_returns_none_when_only_a_stub_is_available(tmp_path, monkeypatch):
    stub_dir = tmp_path / "onlystub"
    _write_wsl_stub(stub_dir)
    monkeypatch.setenv("PATH", str(stub_dir))
    monkeypatch.delenv("BENCHBOX_TEST_POSIX_SHELL", raising=False)

    assert posix_shell() is None


@pytest.mark.skipif(os.name == "nt", reason="stub uses a POSIX shebang")
def test_env_override_is_probed_not_trusted(tmp_path, monkeypatch):
    stub_dir = tmp_path / "override"
    stub = _write_wsl_stub(stub_dir)
    monkeypatch.setenv("BENCHBOX_TEST_POSIX_SHELL", str(stub))
    monkeypatch.setenv("PATH", str(stub_dir))

    assert posix_shell() is None, "a broken override must not be trusted"


@pytest.mark.parametrize("helper_name", ("requires_posix_shell", "skip_without_posix_shell"))
def test_missing_shell_fails_closed_on_posix(monkeypatch, helper_name):
    monkeypatch.setattr(posix_shell_module, "posix_shell", lambda: None)
    monkeypatch.setattr(posix_shell_module.os, "name", "posix")

    with pytest.raises(RuntimeError, match="No working POSIX shell found"):
        getattr(posix_shell_module, helper_name)()


def test_missing_shell_module_guard_skips_on_windows(monkeypatch):
    monkeypatch.setattr(posix_shell_module, "posix_shell", lambda: None)
    monkeypatch.setattr(posix_shell_module.os, "name", "nt")

    marker = posix_shell_module.requires_posix_shell()

    assert marker.mark.name == "skipif"
    assert marker.mark.args == (True,)


def test_missing_shell_per_test_guard_skips_on_windows(monkeypatch):
    monkeypatch.setattr(posix_shell_module, "posix_shell", lambda: None)
    monkeypatch.setattr(posix_shell_module.os, "name", "nt")

    with pytest.raises(pytest.skip.Exception, match="no working POSIX shell"):
        posix_shell_module.skip_without_posix_shell()


