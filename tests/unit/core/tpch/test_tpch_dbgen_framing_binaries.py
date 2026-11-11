# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import hashlib
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

import benchbox

pytestmark = [
    pytest.mark.unit,
    pytest.mark.slow,
]

_BINARIES_ROOT = Path(benchbox.__file__).parent / "_binaries" / "tpc-h"


def _host_platform_arch() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "x86_64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = machine
    return f"{system}-{arch}"


def _dbgen_in(directory: Path) -> Path | None:
    for name in ("dbgen", "dbgen.exe"):
        candidate = directory / name
        if candidate.exists():
            return candidate
    return None


def _bundled_binary_dirs() -> list[Path]:
    if not _BINARIES_ROOT.is_dir():
        return []
    return sorted(p for p in _BINARIES_ROOT.iterdir() if p.is_dir() and _dbgen_in(p))


def _generate_rows(dbgen_exe: Path, table: str = "n") -> list[str] | None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dists = dbgen_exe.parent / "dists.dss"
        if dists.exists():
            shutil.copy2(dists, tmp_path / "dists.dss")
        try:
            result = subprocess.run(
                [str(dbgen_exe), "-z", "-s", "0.01", "-T", table, "-q"],
                capture_output=True,
                cwd=tmp,
                timeout=30,
            )
        except OSError:
            return None
        if result.returncode != 0 and not result.stdout:
            return None
        assert result.stdout, f"dbgen produced no output: {result.stderr!r}"
        return result.stdout.decode().splitlines()


def _assert_no_trailing_delimiter(rows: list[str], label: str) -> None:
    assert rows, f"{label}: no rows generated"
    for row in rows:
        assert row, f"{label}: unexpected empty row"
        assert not row.endswith("|"), (
            f"{label}: row carries a trailing '|' (dbgen built WITHOUT -DEOL_HANDLING). Row: {row!r}"
        )


def test_host_binary_has_no_trailing_delimiter() -> None:
    host_dir = _BINARIES_ROOT / _host_platform_arch()
    dbgen_exe = _dbgen_in(host_dir)
    if dbgen_exe is None:
        pytest.skip(f"no bundled dbgen for host platform {host_dir.name}")
    rows = _generate_rows(dbgen_exe)
    assert rows is not None, f"host binary {dbgen_exe} failed to execute"
    _assert_no_trailing_delimiter(rows, f"host:{host_dir.name}")


def test_all_runnable_bundled_binaries_agree() -> None:
    binary_dirs = _bundled_binary_dirs()
    if not binary_dirs:
        pytest.skip("no bundled TPC-H binaries found")

    checked: list[str] = []
    for directory in binary_dirs:
        dbgen_exe = _dbgen_in(directory)
        assert dbgen_exe is not None
        rows = _generate_rows(dbgen_exe)
        if rows is None:
            continue
        _assert_no_trailing_delimiter(rows, directory.name)
        checked.append(directory.name)

    assert checked, "no bundled TPC-H binary was executable on this host; cannot verify row framing"


_CANONICAL_SF001_SHA256 = {
    "s": "b199bef3350840676cfe4be096091851bc47b6a57cd8a71b3f559d42b7d9dacd",
    "c": "22156f2770387f5adadbc72774f2282d353aedd5092bd8fc54871b3bf5e74cba",
}


def _assert_canonical_rows(rows: list[str], table: str, label: str) -> None:
    digest = hashlib.sha256("".join(f"{row}\n" for row in rows).encode()).hexdigest()
    assert digest == _CANONICAL_SF001_SHA256[table], (
        f"{label}: dbgen -T {table} at SF 0.01 is not canonical (sha256 {digest}). First row: {rows[0]!r}"
    )


@pytest.mark.parametrize("table", sorted(_CANONICAL_SF001_SHA256))
def test_host_binary_generates_canonical_addresses(table: str) -> None:
    host_dir = _BINARIES_ROOT / _host_platform_arch()
    dbgen_exe = _dbgen_in(host_dir)
    if dbgen_exe is None:
        pytest.skip(f"no bundled dbgen for host platform {host_dir.name}")
    rows = _generate_rows(dbgen_exe, table)
    assert rows is not None, f"host binary {dbgen_exe} failed to execute"
    _assert_canonical_rows(rows, table, f"host:{host_dir.name}")


@pytest.mark.parametrize("table", sorted(_CANONICAL_SF001_SHA256))
def test_all_runnable_bundled_binaries_generate_canonical_addresses(table: str) -> None:
    checked: list[str] = []
    for directory in _bundled_binary_dirs():
        dbgen_exe = _dbgen_in(directory)
        assert dbgen_exe is not None
        rows = _generate_rows(dbgen_exe, table)
        if rows is None:
            continue
        _assert_canonical_rows(rows, table, directory.name)
        checked.append(directory.name)
    if not checked:
        pytest.skip("no bundled TPC-H binary is executable on this host")


