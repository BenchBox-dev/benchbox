# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from benchbox.utils.tpc_compilation import (
    BinaryInfo,
    CompilationResult,
    CompilationStatus,
    TPCCompiler,
    ensure_tpc_binaries,
    get_tpc_compiler,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _mock_discover_paths(tpc_h=None, tpc_ds=None, precompiled=None):
    return {
        "tpc_h_source": tpc_h,
        "tpc_ds_source": tpc_ds,
        "precompiled_base": precompiled,
    }


class TestTPCCompiler:
    def test_init_no_sources(self):
        import benchbox.utils.tpc_compilation as tpc_mod

        original_cache = tpc_mod._discovered_paths.copy()
        tpc_mod._discovered_paths.clear()

        try:
            with patch(
                "benchbox.utils.tpc_compilation._discover_tpc_paths",
                return_value=_mock_discover_paths(),
            ):
                compiler = TPCCompiler()
                assert compiler.auto_compile is True
                assert compiler.tpc_h_source is None
                assert compiler.tpc_ds_source is None
                assert len(compiler.binaries) == 0
        finally:
            tpc_mod._discovered_paths.clear()
            tpc_mod._discovered_paths.update(original_cache)

    def test_init_with_sources(self):
        import benchbox.utils.tpc_compilation as tpc_mod

        tpc_h_path = Path("/fake/tpc-h")
        tpc_ds_path = Path("/fake/tpc-ds")

        original_cache = tpc_mod._discovered_paths.copy()
        tpc_mod._discovered_paths.clear()

        try:
            with patch(
                "benchbox.utils.tpc_compilation._discover_tpc_paths",
                return_value=_mock_discover_paths(tpc_h=tpc_h_path, tpc_ds=tpc_ds_path),
            ):
                compiler = TPCCompiler()

                assert compiler.tpc_h_source == tpc_h_path
                assert compiler.tpc_ds_source == tpc_ds_path
                assert len(compiler.binaries) == 4
        finally:
            tpc_mod._discovered_paths.clear()
            tpc_mod._discovered_paths.update(original_cache)

    def test_is_binary_available_exists(self):
        import benchbox.utils.tpc_compilation as tpc_mod

        tpc_ds_path = Path("/fake/tpc-ds")

        original_cache = tpc_mod._discovered_paths.copy()
        tpc_mod._discovered_paths.clear()

        try:
            with patch(
                "benchbox.utils.tpc_compilation._discover_tpc_paths",
                return_value=_mock_discover_paths(tpc_ds=tpc_ds_path),
            ):
                compiler = TPCCompiler()

                with (
                    patch("pathlib.Path.exists", return_value=True),
                    patch("os.access", return_value=True),
                ):
                    assert compiler.is_binary_available("dsdgen") is True
        finally:
            tpc_mod._discovered_paths.clear()
            tpc_mod._discovered_paths.update(original_cache)

    def test_is_binary_available_not_exists(self):
        import benchbox.utils.tpc_compilation as tpc_mod

        tpc_ds_path = Path("/fake/tpc-ds")

        original_cache = tpc_mod._discovered_paths.copy()
        tpc_mod._discovered_paths.clear()

        try:
            with patch(
                "benchbox.utils.tpc_compilation._discover_tpc_paths",
                return_value=_mock_discover_paths(tpc_ds=tpc_ds_path),
            ):
                compiler = TPCCompiler()

                with patch("pathlib.Path.exists", return_value=False):
                    assert compiler.is_binary_available("dsdgen") is False
        finally:
            tpc_mod._discovered_paths.clear()
            tpc_mod._discovered_paths.update(original_cache)

    def test_needs_compilation_source_missing(self):
        import benchbox.utils.tpc_compilation as tpc_mod

        original_cache = tpc_mod._discovered_paths.copy()
        tpc_mod._discovered_paths.clear()

        try:
            with patch(
                "benchbox.utils.tpc_compilation._discover_tpc_paths",
                return_value=_mock_discover_paths(),
            ):
                compiler = TPCCompiler()
                assert compiler.needs_compilation("dbgen") is False
        finally:
            tpc_mod._discovered_paths.clear()
            tpc_mod._discovered_paths.update(original_cache)

    def test_needs_compilation_binary_exists(self):
        import benchbox.utils.tpc_compilation as tpc_mod

        tpc_ds_path = Path("/fake/tpc-ds")

        original_cache = tpc_mod._discovered_paths.copy()
        tpc_mod._discovered_paths.clear()

        try:
            with patch(
                "benchbox.utils.tpc_compilation._discover_tpc_paths",
                return_value=_mock_discover_paths(tpc_ds=tpc_ds_path),
            ):
                compiler = TPCCompiler()

                with (
                    patch("pathlib.Path.exists", return_value=True),
                    patch("os.access", return_value=True),
                ):
                    assert compiler.needs_compilation("dsdgen") is False
        finally:
            tpc_mod._discovered_paths.clear()
            tpc_mod._discovered_paths.update(original_cache)

    def test_needs_compilation_binary_missing(self):
        import benchbox.utils.tpc_compilation as tpc_mod

        tpc_ds_path = Path("/fake/tpc-ds")

        original_cache = tpc_mod._discovered_paths.copy()
        tpc_mod._discovered_paths.clear()

        try:
            with patch(
                "benchbox.utils.tpc_compilation._discover_tpc_paths",
                return_value=_mock_discover_paths(tpc_ds=tpc_ds_path),
            ):
                compiler = TPCCompiler()

                with patch.object(compiler.binaries["dsdgen"], "source_dir") as mock_source_dir:
                    mock_source_dir.exists.return_value = True
                    with patch("pathlib.Path.exists", return_value=False):
                        assert compiler.needs_compilation("dsdgen") is True
        finally:
            tpc_mod._discovered_paths.clear()
            tpc_mod._discovered_paths.update(original_cache)

    def test_check_dependencies_all_available(self):
        import benchbox.utils.tpc_compilation as tpc_mod

        tpc_ds_path = Path("/fake/tpc-ds")

        original_cache = tpc_mod._discovered_paths.copy()
        tpc_mod._discovered_paths.clear()

        try:
            with patch(
                "benchbox.utils.tpc_compilation._discover_tpc_paths",
                return_value=_mock_discover_paths(tpc_ds=tpc_ds_path),
            ):
                compiler = TPCCompiler()

                with patch("shutil.which", return_value="/usr/bin/gcc"):
                    available, missing = compiler.check_dependencies("dsdgen")
                    assert available is True
                    assert missing == []
        finally:
            tpc_mod._discovered_paths.clear()
            tpc_mod._discovered_paths.update(original_cache)

    def test_check_dependencies_missing(self):
        import benchbox.utils.tpc_compilation as tpc_mod

        tpc_ds_path = Path("/fake/tpc-ds")

        original_cache = tpc_mod._discovered_paths.copy()
        tpc_mod._discovered_paths.clear()

        try:
            with patch(
                "benchbox.utils.tpc_compilation._discover_tpc_paths",
                return_value=_mock_discover_paths(tpc_ds=tpc_ds_path),
            ):
                compiler = TPCCompiler()

                with patch("shutil.which", return_value=None):
                    available, missing = compiler.check_dependencies("dsdgen")
                    assert available is False
                    assert "gcc" in missing
                    assert "make" in missing
                    assert "yacc" in missing
        finally:
            tpc_mod._discovered_paths.clear()
            tpc_mod._discovered_paths.update(original_cache)

    def test_compile_binary_disabled(self):
        compiler = TPCCompiler(auto_compile=False)
        result = compiler.compile_binary("dbgen")

        assert result.status == CompilationStatus.DISABLED
        assert "disabled" in result.error_message.lower()

    def test_compile_binary_unknown(self):
        compiler = TPCCompiler()
        result = compiler.compile_binary("unknown_binary")

        assert result.status == CompilationStatus.FAILED
        assert "unknown binary" in result.error_message.lower()

    def test_compile_binary_not_needed(self):
        import benchbox.utils.tpc_compilation as tpc_mod

        tpc_ds_path = Path("/fake/tpc-ds")
        expected_binary_path = tpc_ds_path / "dsdgen"

        original_cache = tpc_mod._discovered_paths.copy()
        tpc_mod._discovered_paths.clear()

        try:
            with patch(
                "benchbox.utils.tpc_compilation._discover_tpc_paths",
                return_value=_mock_discover_paths(tpc_ds=tpc_ds_path),
            ):
                compiler = TPCCompiler()

                with (
                    patch.object(compiler, "needs_compilation", return_value=False),
                    patch.object(compiler, "is_binary_available", return_value=True),
                    patch.object(compiler, "get_binary_path", return_value=expected_binary_path),
                ):
                    result = compiler.compile_binary("dsdgen")

                    assert result.status in [
                        CompilationStatus.NOT_NEEDED,
                        CompilationStatus.PRECOMPILED,
                    ]
                    assert result.binary_path == expected_binary_path
        finally:
            tpc_mod._discovered_paths.clear()
            tpc_mod._discovered_paths.update(original_cache)

    def test_compile_binary_missing_dependencies(self):
        import benchbox.utils.tpc_compilation as tpc_mod

        tpc_ds_path = Path("/fake/tpc-ds")

        original_cache = tpc_mod._discovered_paths.copy()
        tpc_mod._discovered_paths.clear()

        try:
            with patch(
                "benchbox.utils.tpc_compilation._discover_tpc_paths",
                return_value=_mock_discover_paths(tpc_ds=tpc_ds_path),
            ):
                compiler = TPCCompiler()

                with (
                    patch.object(compiler, "needs_compilation", return_value=True),
                    patch.object(
                        compiler,
                        "check_dependencies",
                        return_value=(False, ["gcc", "make"]),
                    ),
                ):
                    result = compiler.compile_binary("dsdgen")

                    assert result.status == CompilationStatus.FAILED
                    assert "missing dependencies" in result.error_message.lower()
        finally:
            tpc_mod._discovered_paths.clear()
            tpc_mod._discovered_paths.update(original_cache)

    def test_get_status_report(self):
        import benchbox.utils.tpc_compilation as tpc_mod

        tpc_ds_path = Path("/fake/tpc-ds")

        original_cache = tpc_mod._discovered_paths.copy()
        tpc_mod._discovered_paths.clear()

        try:
            with patch(
                "benchbox.utils.tpc_compilation._discover_tpc_paths",
                return_value=_mock_discover_paths(tpc_ds=tpc_ds_path),
            ):
                compiler = TPCCompiler()

                report = compiler.get_status_report()

                assert "auto_compile_enabled" in report
                assert "tpc_h_source" in report
                assert "tpc_ds_source" in report
                assert "binaries" in report
                assert report["auto_compile_enabled"] is True
                assert report["tpc_h_source"] is None
                assert report["tpc_ds_source"] == str(tpc_ds_path)
        finally:
            tpc_mod._discovered_paths.clear()
            tpc_mod._discovered_paths.update(original_cache)


class TestCompilationResult:
    def test_compilation_result_creation(self):
        result = CompilationResult(
            binary_name="dbgen",
            status=CompilationStatus.SUCCESS,
            binary_path=Path("/fake/dbgen"),
            compilation_time=1.5,
        )

        assert result.binary_name == "dbgen"
        assert result.status == CompilationStatus.SUCCESS
        assert result.binary_path == Path("/fake/dbgen")
        assert result.compilation_time == 1.5
        assert result.error_message is None


class TestBinaryInfo:
    def test_binary_info_creation(self):
        info = BinaryInfo(
            name="dbgen",
            source_dir=Path("/fake/source"),
            binary_path=Path("/fake/dbgen"),
            dependencies=["gcc", "make"],
        )

        assert info.name == "dbgen"
        assert info.source_dir == Path("/fake/source")
        assert info.binary_path == Path("/fake/dbgen")
        assert info.dependencies == ["gcc", "make"]

    def test_binary_info_post_init(self):
        info = BinaryInfo(
            name="dbgen",
            source_dir=Path("/fake/source"),
            binary_path=Path("/fake/dbgen"),
        )

        assert info.dependencies == []


class TestUtilityFunctions:
    def test_get_tpc_compiler(self):
        with patch("benchbox.utils.tpc_compilation.TPCCompiler") as mock_compiler:
            get_tpc_compiler(auto_compile=False, verbose=True)
            mock_compiler.assert_called_once_with(auto_compile=False, verbose=True)

    def test_ensure_tpc_binaries(self):
        with patch("benchbox.utils.tpc_compilation.get_tpc_compiler") as mock_get_compiler:
            mock_compiler = Mock()
            mock_get_compiler.return_value = mock_compiler

            mock_compiler.binaries = {"dbgen": Mock()}
            mock_compiler.needs_compilation.return_value = False
            mock_compiler.is_binary_available.return_value = True
            mock_compiler.binaries["dbgen"].binary_path = Path("/fake/dbgen")

            results = ensure_tpc_binaries(["dbgen"])

            assert "dbgen" in results
            assert results["dbgen"].status in [
                CompilationStatus.NOT_NEEDED,
                CompilationStatus.PRECOMPILED,
            ]

    def test_ensure_tpc_binaries_unknown(self):
        with patch("benchbox.utils.tpc_compilation.get_tpc_compiler") as mock_get_compiler:
            mock_compiler = Mock()
            mock_get_compiler.return_value = mock_compiler
            mock_compiler.binaries = {}

            results = ensure_tpc_binaries(["unknown"])

            assert "unknown" in results
            assert results["unknown"].status == CompilationStatus.FAILED
            assert "unknown binary" in results["unknown"].error_message.lower()


class TestCompilationStatus:
    def test_enum_values(self):
        assert CompilationStatus.SUCCESS.value == "success"
        assert CompilationStatus.FAILED.value == "failed"
        assert CompilationStatus.SKIPPED.value == "skipped"
        assert CompilationStatus.DISABLED.value == "disabled"
        assert CompilationStatus.NOT_NEEDED.value == "not_needed"


class TestPathDiscoveryCaching:
    def test_discover_tpc_paths_caches_results(self):
        import benchbox.utils.tpc_compilation as tpc_mod

        original_cache = tpc_mod._discovered_paths.copy()
        tpc_mod._discovered_paths.clear()

        try:
            result1 = tpc_mod._discover_tpc_paths()
            assert result1 is not None
            assert "tpc_h_source" in result1
            assert "tpc_ds_source" in result1
            assert "precompiled_base" in result1

            result2 = tpc_mod._discover_tpc_paths()
            assert result1 is result2

        finally:
            tpc_mod._discovered_paths.clear()
            tpc_mod._discovered_paths.update(original_cache)

    def test_multiple_compiler_instances_share_discovery(self):
        import benchbox.utils.tpc_compilation as tpc_mod

        original_paths = tpc_mod._discovered_paths.copy()
        original_checksums = tpc_mod._checksum_cache.copy()
        tpc_mod._discovered_paths.clear()
        tpc_mod._checksum_cache.clear()

        try:
            compiler1 = TPCCompiler(auto_compile=False)
            compiler2 = TPCCompiler(auto_compile=True)
            compiler3 = TPCCompiler(auto_compile=False, verbose=True)

            assert compiler1.tpc_h_source == compiler2.tpc_h_source == compiler3.tpc_h_source
            assert compiler1.tpc_ds_source == compiler2.tpc_ds_source == compiler3.tpc_ds_source
            assert compiler1.precompiled_base == compiler2.precompiled_base == compiler3.precompiled_base

        finally:
            tpc_mod._discovered_paths.clear()
            tpc_mod._discovered_paths.update(original_paths)
            tpc_mod._checksum_cache.clear()
            tpc_mod._checksum_cache.update(original_checksums)


class TestChecksumCaching:
    def test_checksum_cache_shared_across_instances(self):
        import benchbox.utils.tpc_compilation as tpc_mod

        original_cache = tpc_mod._checksum_cache.copy()
        tpc_mod._checksum_cache.clear()

        try:
            compiler1 = TPCCompiler(auto_compile=False)
            compiler2 = TPCCompiler(auto_compile=True)

            if compiler1.precompiled_base:
                result1 = compiler1.is_precompiled_available("dsqgen")

                cache_after_first = tpc_mod._checksum_cache.copy()

                result2 = compiler2.is_precompiled_available("dsqgen")

                assert len(tpc_mod._checksum_cache) == len(cache_after_first)

                assert result1 == result2

        finally:
            tpc_mod._checksum_cache.clear()
            tpc_mod._checksum_cache.update(original_cache)


