from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


def get_dsdgen_path() -> Path | None:
    system = platform.system().lower()
    machine = platform.machine().lower()

    platform_map = {
        ("darwin", "arm64"): "darwin-arm64",
        ("darwin", "x86_64"): "darwin-x86_64",
        ("linux", "x86_64"): "linux-x86_64",
        ("linux", "aarch64"): "linux-arm64",
    }

    platform_dir = platform_map.get((system, machine))
    if not platform_dir:
        return None

    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "benchbox").is_dir():
            dsdgen = parent / "_binaries" / "tpc-ds" / platform_dir / "dsdgen"
            if dsdgen.exists():
                return dsdgen
    return None


def _dsdgen_has_filter_support() -> bool:
    dsdgen = get_dsdgen_path()
    if dsdgen is None:
        return False
    try:
        result = subprocess.run([str(dsdgen), "-help"], capture_output=True, text=True, timeout=5)
        help_text = result.stdout + result.stderr
        return "FILTER" in help_text and "_FILTER" not in help_text
    except Exception:
        return False


def get_tpcds_tools_dir() -> Path | None:
    dsdgen = get_dsdgen_path()
    if dsdgen is not None:
        binary_dir = dsdgen.parent
        if (binary_dir / "tpcds.idx").exists():
            return binary_dir

    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "benchbox").is_dir():
            tools_dir = parent / "_sources" / "tpc-ds" / "tools"
            if tools_dir.exists():
                return tools_dir
    return None


pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
    pytest.mark.tpcds,
    pytest.mark.skipif(
        get_dsdgen_path() is None,
        reason="dsdgen binary not available for this platform",
    ),
]


class TestDsdgenFilterFlag:
    @pytest.fixture
    def dsdgen_path(self) -> Path:
        path = get_dsdgen_path()
        if path is None:
            pytest.skip("dsdgen not available")
        assert path is not None
        return path

    @pytest.fixture
    def tools_dir(self) -> Path:
        path = get_tpcds_tools_dir()
        if path is None:
            pytest.skip("TPC-DS tools directory not available")
        assert path is not None
        return path

    def test_filter_flag_shows_without_underscore(self, dsdgen_path: Path) -> None:
        result = subprocess.run(
            [str(dsdgen_path), "-help"],
            capture_output=True,
            text=True,
        )

        help_text = result.stdout + result.stderr
        assert "FILTER" in help_text, "FILTER flag not found in help output"
        assert "_FILTER" not in help_text, "Old _FILTER flag still present (params.h not patched)"

    def test_filter_flag_accepted(self, dsdgen_path: Path, tools_dir: Path) -> None:
        result = subprocess.run(
            [
                str(dsdgen_path),
                "-TABLE",
                "ship_mode",
                "-SCALE",
                "1",
                "-FILTER",
                "Y",
                "-RNGSEED",
                "1",
            ],
            cwd=tools_dir,
            capture_output=True,
            text=True,
        )

        combined_output = result.stdout + result.stderr
        assert "option" not in combined_output.lower() or "unknown" not in combined_output.lower(), (
            f"FILTER flag rejected: {combined_output}"
        )

    def test_filter_outputs_to_stdout(self, dsdgen_path: Path, tools_dir: Path) -> None:
        result = subprocess.run(
            [
                str(dsdgen_path),
                "-TABLE",
                "ship_mode",
                "-SCALE",
                "1",
                "-FILTER",
                "Y",
                "-RNGSEED",
                "1",
                "-TERMINATE",
                "N",
            ],
            cwd=tools_dir,
            capture_output=True,
            text=True,
        )

        stdout_lines = [line for line in result.stdout.strip().split("\n") if line and "|" in line]
        assert len(stdout_lines) == 20, f"Expected 20 rows, got {len(stdout_lines)}: {result.stdout[:200]}"

        first_row = stdout_lines[0]
        assert "|" in first_row, f"Data not pipe-delimited: {first_row}"

    def test_filter_does_not_create_file(self, dsdgen_path: Path, tools_dir: Path) -> None:

        with tempfile.TemporaryDirectory() as tmpdir:
            for dist_file in ["tpcds.dst", "tpcds.idx"]:
                src = tools_dir / dist_file
                if src.exists():
                    shutil.copy(src, tmpdir)

            subprocess.run(
                [
                    str(dsdgen_path),
                    "-TABLE",
                    "ship_mode",
                    "-SCALE",
                    "1",
                    "-FILTER",
                    "Y",
                    "-RNGSEED",
                    "1",
                    "-DIR",
                    tmpdir,
                ],
                cwd=tmpdir,
                capture_output=True,
                text=True,
            )

            dat_files = list(Path(tmpdir).glob("*.dat"))
            assert len(dat_files) == 0, f"Unexpected .dat files created: {dat_files}"

    def test_without_filter_creates_file(self, dsdgen_path: Path, tools_dir: Path) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            for dist_file in ["tpcds.dst", "tpcds.idx"]:
                src = tools_dir / dist_file
                if src.exists():
                    shutil.copy(src, tmpdir)

            result = subprocess.run(
                [
                    str(dsdgen_path),
                    "-TABLE",
                    "ship_mode",
                    "-SCALE",
                    "1",
                    "-RNGSEED",
                    "1",
                    "-DIR",
                    tmpdir,
                    "-FORCE",
                ],
                cwd=tmpdir,
                capture_output=True,
                text=True,
            )

            dat_file = Path(tmpdir) / "ship_mode.dat"
            assert dat_file.exists(), f"Expected ship_mode.dat to be created. stderr: {result.stderr}"

            content = dat_file.read_text()
            lines = [line for line in content.strip().split("\n") if line]
            assert len(lines) == 20, f"Expected 20 rows in file, got {len(lines)}"


class TestStdoutDataEquivalence:
    @pytest.fixture
    def dsdgen_path(self) -> Path:
        path = get_dsdgen_path()
        if path is None:
            pytest.skip("dsdgen not available")
        assert path is not None
        return path

    @pytest.fixture
    def tools_dir(self) -> Path:
        path = get_tpcds_tools_dir()
        if path is None:
            pytest.skip("TPC-DS tools directory not available")
        assert path is not None
        return path

    def test_stdout_matches_file_output(self, dsdgen_path: Path, tools_dir: Path) -> None:

        with tempfile.TemporaryDirectory() as tmpdir:
            for dist_file in ["tpcds.dst", "tpcds.idx"]:
                src = tools_dir / dist_file
                if src.exists():
                    shutil.copy(src, tmpdir)

            stdout_result = subprocess.run(
                [
                    str(dsdgen_path),
                    "-TABLE",
                    "ship_mode",
                    "-SCALE",
                    "1",
                    "-FILTER",
                    "Y",
                    "-RNGSEED",
                    "12345",
                    "-TERMINATE",
                    "N",
                ],
                cwd=tools_dir,
                capture_output=True,
                text=True,
            )

            subprocess.run(
                [
                    str(dsdgen_path),
                    "-TABLE",
                    "ship_mode",
                    "-SCALE",
                    "1",
                    "-RNGSEED",
                    "12345",
                    "-DIR",
                    tmpdir,
                    "-FORCE",
                    "-TERMINATE",
                    "N",
                ],
                cwd=tmpdir,
                capture_output=True,
                text=True,
            )

            stdout_lines = [line for line in stdout_result.stdout.strip().split("\n") if line and "|" in line]
            file_content = (Path(tmpdir) / "ship_mode.dat").read_text()
            file_lines = [line for line in file_content.strip().split("\n") if line]

            assert len(stdout_lines) == len(file_lines), (
                f"Row count mismatch: stdout={len(stdout_lines)}, file={len(file_lines)}"
            )

            for i, (stdout_line, file_line) in enumerate(zip(stdout_lines, file_lines)):
                assert stdout_line == file_line, f"Row {i} mismatch:\n  stdout: {stdout_line}\n  file: {file_line}"


class TestStreamingCompression:
    @pytest.fixture
    def dsdgen_path(self) -> Path:
        path = get_dsdgen_path()
        if path is None:
            pytest.skip("dsdgen not available")
        assert path is not None
        return path

    @pytest.fixture
    def tools_dir(self) -> Path:
        path = get_tpcds_tools_dir()
        if path is None:
            pytest.skip("TPC-DS tools directory not available")
        assert path is not None
        return path

    def test_stdout_pipes_to_gzip(self, dsdgen_path: Path, tools_dir: Path) -> None:

        import gzip

        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "ship_mode.dat.gz"

            dsdgen_proc = subprocess.Popen(
                [
                    str(dsdgen_path),
                    "-TABLE",
                    "ship_mode",
                    "-SCALE",
                    "1",
                    "-FILTER",
                    "Y",
                    "-RNGSEED",
                    "1",
                    "-TERMINATE",
                    "N",
                ],
                cwd=tools_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )

            with gzip.open(output_file, "wt") as f:
                assert dsdgen_proc.stdout is not None, "Expected stdout from dsdgen"
                for line in dsdgen_proc.stdout:
                    f.write(line.decode("utf-8"))

            dsdgen_proc.wait()

            assert output_file.exists(), "Gzip file not created"
            with gzip.open(output_file, "rt") as f:
                lines = [line for line in f.read().strip().split("\n") if line]
            assert len(lines) == 20, f"Expected 20 rows in gzip, got {len(lines)}"

    @pytest.mark.skipif(
        not any((Path(p) / "zstd").exists() for p in os.environ.get("PATH", "").split(os.pathsep)),
        reason="zstd not available",
    )
    def test_stdout_pipes_to_zstd(self, dsdgen_path: Path, tools_dir: Path) -> None:

        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "ship_mode.dat.zst"

            dsdgen_proc = subprocess.Popen(
                [
                    str(dsdgen_path),
                    "-TABLE",
                    "ship_mode",
                    "-SCALE",
                    "1",
                    "-FILTER",
                    "Y",
                    "-RNGSEED",
                    "1",
                    "-TERMINATE",
                    "N",
                ],
                cwd=tools_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )

            zstd_proc = subprocess.Popen(
                ["zstd", "-o", str(output_file)],
                stdin=dsdgen_proc.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            dsdgen_proc.stdout.close()
            zstd_proc.wait()
            dsdgen_proc.wait()

            assert output_file.exists(), "Zstd file not created"

            result = subprocess.run(
                ["zstd", "-d", "-c", str(output_file)],
                capture_output=True,
                text=True,
            )
            lines = [line for line in result.stdout.strip().split("\n") if line]
            assert len(lines) == 20, f"Expected 20 rows in zstd, got {len(lines)}"


