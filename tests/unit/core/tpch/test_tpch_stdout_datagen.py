# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

from benchbox.core.tpch.generator import _TPCH_TABLE_CODES, TPCHDataGenerator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.slow,
]


class TestDbgenStdoutSupport:
    @pytest.fixture
    def dbgen_exe(self) -> Path | None:
        try:
            generator = TPCHDataGenerator(scale_factor=0.01)
            return generator.dbgen_exe
        except (RuntimeError, FileNotFoundError):
            pytest.skip("dbgen binary not available")

    def test_dbgen_help_shows_z_flag(self, dbgen_exe: Path):

        result = subprocess.run(
            [str(dbgen_exe), "-h"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert "-z" in result.stderr or "-z" in result.stdout, "dbgen binary does not support -z flag for stdout output"

    def test_dbgen_z_flag_produces_output(self, dbgen_exe: Path):

        with tempfile.TemporaryDirectory() as tmpdir:
            generator = TPCHDataGenerator(scale_factor=0.01)
            dists_file = generator.dbgen_path / "dists.dss"
            if dists_file.exists():
                import shutil

                shutil.copy2(dists_file, Path(tmpdir) / "dists.dss")

            result = subprocess.run(
                [str(dbgen_exe), "-z", "-s", "0.01", "-T", "r", "-q"],
                capture_output=True,
                cwd=tmpdir,
                timeout=10,
            )

            assert result.returncode == 0, f"dbgen failed: {result.stderr}"
            assert len(result.stdout) > 0, "dbgen -z produced no output"

            lines = result.stdout.decode().strip().split("\n")
            assert len(lines) == 5, f"Expected 5 region rows, got {len(lines)}"
            for line in lines:
                parts = line.split("|")
                assert len(parts) >= 3, f"Invalid region row format: {line}"

    @pytest.mark.parametrize("table_name,table_code", list(_TPCH_TABLE_CODES.items()))
    def test_dbgen_z_flag_all_tables(self, dbgen_exe: Path, table_name: str, table_code: str):

        with tempfile.TemporaryDirectory() as tmpdir:
            generator = TPCHDataGenerator(scale_factor=0.01)
            dists_file = generator.dbgen_path / "dists.dss"
            if dists_file.exists():
                import shutil

                shutil.copy2(dists_file, Path(tmpdir) / "dists.dss")

            result = subprocess.run(
                [str(dbgen_exe), "-z", "-s", "0.01", "-T", table_code, "-q"],
                capture_output=True,
                cwd=tmpdir,
                timeout=60,
            )

            assert result.returncode == 0, f"dbgen -z failed for {table_name}: {result.stderr}"
            assert len(result.stdout) > 0, f"dbgen -z produced no output for {table_name}"


class TestStdoutMatchesFileOutput:
    @pytest.fixture
    def dbgen_exe(self) -> Path | None:
        try:
            generator = TPCHDataGenerator(scale_factor=0.01)
            return generator.dbgen_exe
        except (RuntimeError, FileNotFoundError):
            pytest.skip("dbgen binary not available")

    def test_stdout_matches_file_output_region(self, dbgen_exe: Path):

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            generator = TPCHDataGenerator(scale_factor=0.01)
            dists_file = generator.dbgen_path / "dists.dss"
            if dists_file.exists():
                import shutil

                shutil.copy2(dists_file, tmpdir_path / "dists.dss")

            stdout_result = subprocess.run(
                [str(dbgen_exe), "-z", "-s", "0.01", "-T", "r", "-q"],
                capture_output=True,
                cwd=tmpdir,
                timeout=10,
            )
            assert stdout_result.returncode == 0

            subprocess.run(
                [str(dbgen_exe), "-s", "0.01", "-T", "r", "-f", "-q"],
                capture_output=True,
                cwd=tmpdir,
                timeout=10,
            )

            file_output = (tmpdir_path / "region.tbl").read_bytes()

            assert stdout_result.stdout == file_output, "stdout output does not match file output for region table"

    def test_stdout_matches_file_output_customer(self, dbgen_exe: Path):

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            generator = TPCHDataGenerator(scale_factor=0.01)
            dists_file = generator.dbgen_path / "dists.dss"
            if dists_file.exists():
                import shutil

                shutil.copy2(dists_file, tmpdir_path / "dists.dss")

            stdout_result = subprocess.run(
                [str(dbgen_exe), "-z", "-s", "0.01", "-T", "c", "-q"],
                capture_output=True,
                cwd=tmpdir,
                timeout=30,
            )
            assert stdout_result.returncode == 0

            subprocess.run(
                [str(dbgen_exe), "-s", "0.01", "-T", "c", "-f", "-q"],
                capture_output=True,
                cwd=tmpdir,
                timeout=30,
            )

            file_output = (tmpdir_path / "customer.tbl").read_bytes()

            assert stdout_result.stdout == file_output, "stdout output does not match file output for customer table"


class TestGeneratorStdoutDetection:
    def test_check_stdout_support_returns_bool(self):

        try:
            generator = TPCHDataGenerator(scale_factor=0.01)
            result = generator._check_stdout_support()
            assert isinstance(result, bool)
        except (RuntimeError, FileNotFoundError):
            pytest.skip("dbgen binary not available")

    def test_check_stdout_support_is_cached(self):

        try:
            generator = TPCHDataGenerator(scale_factor=0.01)
            result1 = generator._check_stdout_support()
            result2 = generator._check_stdout_support()
            assert result1 == result2
            assert hasattr(generator, "_stdout_support_cached")
        except (RuntimeError, FileNotFoundError):
            pytest.skip("dbgen binary not available")

    def test_updated_binary_supports_z_flag(self):

        try:
            generator = TPCHDataGenerator(scale_factor=0.01)
            assert generator._check_stdout_support(), "dbgen binary should support -z flag after patches are applied"
        except (RuntimeError, FileNotFoundError):
            pytest.skip("dbgen binary not available")


class TestBackwardCompatibility:
    def test_file_mode_generation_works(self):
        try:
            generator = TPCHDataGenerator(scale_factor=0.01)
            dbgen_exe = generator.dbgen_exe
        except (RuntimeError, FileNotFoundError):
            pytest.skip("dbgen binary not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            dists_file = generator.dbgen_path / "dists.dss"
            if dists_file.exists():
                import shutil

                shutil.copy2(dists_file, tmpdir_path / "dists.dss")

            result = subprocess.run(
                [str(dbgen_exe), "-s", "0.01", "-T", "r", "-f", "-q"],
                capture_output=True,
                cwd=tmpdir,
                timeout=10,
            )

            assert result.returncode == 0, f"File-mode generation failed: {result.stderr}"
            assert (tmpdir_path / "region.tbl").exists(), "region.tbl not created"
            assert (tmpdir_path / "region.tbl").stat().st_size > 0, "region.tbl is empty"

    def test_file_mode_creates_correct_format(self):

        try:
            generator = TPCHDataGenerator(scale_factor=0.01)
            dbgen_exe = generator.dbgen_exe
        except (RuntimeError, FileNotFoundError):
            pytest.skip("dbgen binary not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            dists_file = generator.dbgen_path / "dists.dss"
            if dists_file.exists():
                import shutil

                shutil.copy2(dists_file, tmpdir_path / "dists.dss")

            subprocess.run(
                [str(dbgen_exe), "-s", "0.01", "-T", "r", "-f", "-q"],
                capture_output=True,
                cwd=tmpdir,
                timeout=10,
            )

            content = (tmpdir_path / "region.tbl").read_text()
            lines = content.strip().split("\n")

            assert len(lines) == 5, f"Expected 5 regions, got {len(lines)}"
            for i, line in enumerate(lines):
                parts = line.split("|")
                assert len(parts) >= 3, f"Invalid format in line {i}: {line}"
                assert parts[0].isdigit()


class TestMoneyFormatFix:
    def test_money_format_is_correct(self):
        try:
            generator = TPCHDataGenerator(scale_factor=0.01)
            dbgen_exe = generator.dbgen_exe
        except (RuntimeError, FileNotFoundError):
            pytest.skip("dbgen binary not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            dists_file = generator.dbgen_path / "dists.dss"
            if dists_file.exists():
                import shutil

                shutil.copy2(dists_file, tmpdir_path / "dists.dss")

            result = subprocess.run(
                [str(dbgen_exe), "-z", "-s", "0.01", "-T", "c", "-q"],
                capture_output=True,
                cwd=tmpdir,
                timeout=30,
            )

            assert result.returncode == 0

            lines = result.stdout.decode().strip().split("\n")
            for i, line in enumerate(lines[:10]):
                parts = line.split("|")
                assert len(parts) >= 7, f"Invalid customer row {i}: {line}"

                acctbal = parts[5]
                assert "." in acctbal, f"Money format missing decimal: {acctbal}"
                whole, decimal = acctbal.replace("-", "").split(".")
                assert len(decimal) == 2, f"Money should have 2 decimal places: {acctbal}"
                try:
                    value = float(acctbal)
                    assert -10000 <= value <= 10000, f"Account balance out of range: {value}"
                except ValueError:
                    pytest.fail(f"Invalid money value: {acctbal}")


