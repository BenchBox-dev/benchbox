# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import hashlib
import logging
import os
import platform
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.run_deadline import RunTimeoutError, current_run_deadline, run_child

logger = logging.getLogger(__name__)

_discovered_paths: dict[str, Optional[Path]] = {}

_checksum_cache: dict[Path, bool] = {}

_exec_probe_cache: dict[Path, bool] = {}


def _discover_tpc_paths() -> dict[str, Optional[Path]]:
    global _discovered_paths

    if _discovered_paths:
        return _discovered_paths

    tpc_h_source = None
    for path in [
        Path(__file__).parent.parent.parent / "_sources/tpc-h/dbgen",
        Path.cwd() / "_sources/tpc-h/dbgen",
        Path.home() / "tpc-h/dbgen",
    ]:
        if path.exists() and (path / "build.c").exists():
            logger.debug(f"Found TPC-H source at: {path}")
            tpc_h_source = path
            break
    if tpc_h_source is None:
        logger.debug("TPC-H source directory not found")

    tpc_ds_source = None
    for path in [
        Path(__file__).parent.parent.parent / "_sources/tpc-ds/tools",
        Path.cwd() / "_sources/tpc-ds/tools",
        Path.home() / "tpc-ds/tools",
    ]:
        if path.exists() and (path / "driver.c").exists():
            logger.debug(f"Found TPC-DS source at: {path}")
            tpc_ds_source = path
            break
    if tpc_ds_source is None:
        logger.debug("TPC-DS source directory not found")

    precompiled_base = None
    for path in [
        Path(__file__).parent.parent / "_binaries",
        Path(__file__).parent.parent.parent / "_binaries",
        Path.cwd() / "_binaries",
    ]:
        if path.exists() and path.is_dir():
            logger.debug(f"Found pre-compiled binaries at: {path}")
            precompiled_base = path
            break
    if precompiled_base is None:
        logger.debug("Pre-compiled binaries directory not found")

    _discovered_paths = {
        "tpc_h_source": tpc_h_source,
        "tpc_ds_source": tpc_ds_source,
        "precompiled_base": precompiled_base,
    }
    return _discovered_paths


def get_tpc_templates_dir(benchmark: str) -> Path:
    import benchbox

    package_dir = Path(benchbox.__file__).parent

    bundled = package_dir / "_binaries" / benchmark / "templates"
    if bundled.exists():
        return bundled

    repo_root = package_dir.parent
    if benchmark == "tpc-h":
        dev_path = repo_root / "_sources" / "tpc-h" / "dbgen"
    elif benchmark == "tpc-ds":
        dev_path = repo_root / "_sources" / "tpc-ds"
    else:
        raise ValueError(f"Unknown benchmark: {benchmark}")

    if dev_path.exists():
        return dev_path

    raise RuntimeError(f"TPC template directory not found for {benchmark}. Checked: {bundled}, {dev_path}")


class CompilationStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DISABLED = "disabled"
    NOT_NEEDED = "not_needed"
    PRECOMPILED = "precompiled"


@dataclass
class BinaryInfo:
    name: str
    source_dir: Optional[Path]
    binary_path: Optional[Path]
    precompiled_path: Optional[Path] = None
    makefile_path: Optional[Path] = None
    dependencies: list[str] = None

    def __post_init__(self):
        if self.dependencies is None:
            self.dependencies = []


@dataclass
class CompilationResult:
    binary_name: str
    status: CompilationStatus
    binary_path: Optional[Path] = None
    error_message: Optional[str] = None
    compilation_time: Optional[float] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None


class TPCCompiler:
    def __init__(self, auto_compile: bool = True, verbose: bool = False):
        self.auto_compile = auto_compile
        self.verbose = verbose

        paths = _discover_tpc_paths()
        self.tpc_h_source = paths["tpc_h_source"]
        self.tpc_ds_source = paths["tpc_ds_source"]
        self.precompiled_base = paths["precompiled_base"]

        self._setup_binary_configs()

        if verbose:
            logger.setLevel(logging.DEBUG)

    def _get_platform_string(self) -> str:
        system = platform.system().lower()
        machine = platform.machine().lower()

        if machine in ["x86_64", "amd64"]:
            arch = "x86_64"
        elif machine in ["arm64", "aarch64"]:
            arch = "arm64"
        else:
            arch = machine

        if system == "darwin":
            system = "darwin"
        elif system == "linux":
            system = "linux"
        elif system == "windows":
            system = "windows"
        else:
            system = system

        return f"{system}-{arch}"

    def _setup_binary_configs(self):
        self.binaries: dict[str, BinaryInfo] = {}
        platform_str = self._get_platform_string()
        is_windows = platform.system() == "Windows"
        exe_suffix = ".exe" if is_windows else ""

        def get_precompiled_path(benchmark: str, binary_name: str) -> Optional[Path]:
            if self.precompiled_base:
                return self.precompiled_base / benchmark / platform_str / f"{binary_name}{exe_suffix}"
            return None

        if self.tpc_h_source or self.precompiled_base:
            self.binaries["dbgen"] = BinaryInfo(
                name="dbgen",
                source_dir=self.tpc_h_source,
                binary_path=(self.tpc_h_source / f"dbgen{exe_suffix}") if self.tpc_h_source else None,
                precompiled_path=get_precompiled_path("tpc-h", "dbgen"),
                makefile_path=(self.tpc_h_source / "makefile.suite") if self.tpc_h_source else None,
                dependencies=["gcc", "make"],
            )

            self.binaries["qgen"] = BinaryInfo(
                name="qgen",
                source_dir=self.tpc_h_source,
                binary_path=(self.tpc_h_source / f"qgen{exe_suffix}") if self.tpc_h_source else None,
                precompiled_path=get_precompiled_path("tpc-h", "qgen"),
                makefile_path=(self.tpc_h_source / "makefile.suite") if self.tpc_h_source else None,
                dependencies=["gcc", "make"],
            )

        if self.tpc_ds_source or self.precompiled_base:
            self.binaries["dsdgen"] = BinaryInfo(
                name="dsdgen",
                source_dir=self.tpc_ds_source,
                binary_path=(self.tpc_ds_source / f"dsdgen{exe_suffix}") if self.tpc_ds_source else None,
                precompiled_path=get_precompiled_path("tpc-ds", "dsdgen"),
                makefile_path=(self.tpc_ds_source / "makefile") if self.tpc_ds_source else None,
                dependencies=["gcc", "make", "yacc"],
            )

            self.binaries["dsqgen"] = BinaryInfo(
                name="dsqgen",
                source_dir=self.tpc_ds_source,
                binary_path=(self.tpc_ds_source / f"dsqgen{exe_suffix}") if self.tpc_ds_source else None,
                precompiled_path=get_precompiled_path("tpc-ds", "dsqgen"),
                makefile_path=(self.tpc_ds_source / "makefile") if self.tpc_ds_source else None,
                dependencies=["gcc", "make", "yacc"],
            )

    def check_dependencies(self, binary_name: str) -> tuple[bool, list[str]]:
        if binary_name not in self.binaries:
            return False, [f"Unknown binary: {binary_name}"]

        binary_info = self.binaries[binary_name]
        missing = []

        for dep in binary_info.dependencies:
            if not shutil.which(dep):
                missing.append(dep)

        return len(missing) == 0, missing

    def _verify_checksum(self, binary_path: Path) -> bool:
        if not binary_path.exists():
            return False

        if binary_path in _checksum_cache:
            return _checksum_cache[binary_path]

        checksum_file = binary_path.parent / "checksums.md5"
        if not checksum_file.exists():
            logger.debug(f"No checksum file found for {binary_path}")
            _checksum_cache[binary_path] = True
            return True

        try:
            with open(binary_path, "rb") as f:
                binary_hash = hashlib.md5(f.read()).hexdigest()

            def _parse_manifest_line(raw: str) -> tuple[str, str] | None:
                s = raw.strip()
                if not s:
                    return None
                if s.startswith("MD5") and "(" in s and ")" in s and "=" in s:
                    try:
                        lpar = s.index("(")
                        rpar = s.index(")", lpar)
                        eq = s.index("=", rpar)
                        filename = s[lpar + 1 : rpar].strip().lstrip("./")
                        digest_fields = s[eq + 1 :].strip().split()
                        expected_hash = digest_fields[0] if digest_fields else ""
                        if filename and expected_hash:
                            return expected_hash, filename
                    except ValueError:
                        pass
                parts = s.split()
                if len(parts) >= 2:
                    expected_hash = parts[0]
                    filename = parts[-1].lstrip("*./")
                    if expected_hash and filename:
                        return expected_hash, filename
                return None

            with open(checksum_file, encoding="utf-8") as f:
                for line in f:
                    parsed = _parse_manifest_line(line)
                    if parsed is None:
                        continue
                    expected_hash, filename = parsed
                    if filename == binary_path.name:
                        if binary_hash == expected_hash:
                            logger.debug(f"Checksum verified for {binary_path.name}")
                            _checksum_cache[binary_path] = True
                            return True
                        logger.warning(
                            f"Checksum mismatch for {binary_path.name}: expected {expected_hash}, got {binary_hash}"
                        )
                        _checksum_cache[binary_path] = False
                        return False

            logger.warning(f"No checksum entry found for {binary_path.name}")
            _checksum_cache[binary_path] = True
            return True

        except Exception as e:
            logger.warning(f"Failed to verify checksum for {binary_path}: {e}")
            _checksum_cache[binary_path] = True
            return True

    def _verify_executable(self, binary_path: Path) -> bool:
        if binary_path in _exec_probe_cache:
            return _exec_probe_cache[binary_path]

        runnable = True
        try:
            with tempfile.TemporaryDirectory(prefix="benchbox-exec-probe-") as probe_cwd:
                proc = subprocess.run(
                    [str(binary_path), "-h"],
                    cwd=probe_cwd,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=15,
                )
            if proc.returncode < 0:
                runnable = False
                logger.warning(
                    f"Precompiled binary {binary_path} died with signal {-proc.returncode} during exec probe; "
                    "treating it as unrunnable on this host and falling back to source compilation"
                )
        except subprocess.TimeoutExpired:
            runnable = True
        except OSError as e:
            runnable = False
            logger.warning(
                f"Precompiled binary {binary_path} cannot be executed on this host ({e}); "
                "it was likely built for a different or newer OS - falling back to source compilation"
            )

        _exec_probe_cache[binary_path] = runnable
        return runnable

    def is_precompiled_available(self, binary_name: str) -> bool:
        if binary_name not in self.binaries:
            return False

        binary_info = self.binaries[binary_name]
        if not binary_info.precompiled_path:
            return False

        precompiled_path = binary_info.precompiled_path

        if not (precompiled_path.exists() and (os.name == "nt" or os.access(precompiled_path, os.X_OK))):
            return False

        if not self._verify_checksum(precompiled_path):
            return False

        return self._verify_executable(precompiled_path)

    def is_binary_available(self, binary_name: str) -> bool:
        if binary_name not in self.binaries:
            return False

        if self.is_precompiled_available(binary_name):
            return True

        binary_info = self.binaries[binary_name]
        binary_path = binary_info.binary_path

        return binary_path and binary_path.exists() and (os.name == "nt" or os.access(binary_path, os.X_OK))

    def needs_compilation(self, binary_name: str) -> bool:
        if binary_name not in self.binaries:
            return False

        binary_info = self.binaries[binary_name]

        if binary_info.source_dir is None or not binary_info.source_dir.exists():
            return False

        return not self.is_binary_available(binary_name)

    def get_binary_path(self, binary_name: str) -> Optional[Path]:
        if binary_name not in self.binaries:
            return None

        binary_info = self.binaries[binary_name]

        if self.is_precompiled_available(binary_name):
            return binary_info.precompiled_path

        if (
            binary_info.binary_path
            and binary_info.binary_path.exists()
            and (os.name == "nt" or os.access(binary_info.binary_path, os.X_OK))
        ):
            return binary_info.binary_path

        return None

    def compile_binary(self, binary_name: str) -> CompilationResult:

        start_time = mono_time()

        if not self.auto_compile:
            return CompilationResult(
                binary_name=binary_name,
                status=CompilationStatus.DISABLED,
                error_message="Auto-compilation is disabled",
            )

        if binary_name not in self.binaries:
            return CompilationResult(
                binary_name=binary_name,
                status=CompilationStatus.FAILED,
                error_message=f"Unknown binary: {binary_name}",
            )

        binary_info = self.binaries[binary_name]

        if self.is_precompiled_available(binary_name):
            logger.info(f"Using pre-compiled binary for {binary_name}")
            return CompilationResult(
                binary_name=binary_name,
                status=CompilationStatus.PRECOMPILED,
                binary_path=binary_info.precompiled_path,
            )

        if not self.needs_compilation(binary_name):
            if self.is_binary_available(binary_name):
                return CompilationResult(
                    binary_name=binary_name,
                    status=CompilationStatus.NOT_NEEDED,
                    binary_path=self.get_binary_path(binary_name),
                )
            else:
                return CompilationResult(
                    binary_name=binary_name,
                    status=CompilationStatus.FAILED,
                    error_message=f"Source directory not found: {binary_info.source_dir}",
                )

        deps_available, missing_deps = self.check_dependencies(binary_name)
        if not deps_available:
            return CompilationResult(
                binary_name=binary_name,
                status=CompilationStatus.FAILED,
                error_message=f"Missing dependencies: {', '.join(missing_deps)}",
            )

        logger.info(f"Compiling {binary_name}...")

        try:
            if binary_name in ["dbgen", "qgen"]:
                result = self._compile_tpc_h_binary(binary_name, binary_info)
            else:
                result = self._compile_tpc_ds_binary(binary_name, binary_info)

            result.compilation_time = elapsed_seconds(start_time)

            if result.status == CompilationStatus.SUCCESS:
                logger.info(f"Successfully compiled {binary_name} in {result.compilation_time:.2f}s")
            else:
                logger.error(f"Failed to compile {binary_name}: {result.error_message}")

            return result

        except RunTimeoutError:
            raise
        except Exception as e:
            return CompilationResult(
                binary_name=binary_name,
                status=CompilationStatus.FAILED,
                error_message=f"Compilation failed with exception: {str(e)}",
                compilation_time=elapsed_seconds(start_time),
            )

    def _compile_tpc_h_binary(self, binary_name: str, binary_info: BinaryInfo) -> CompilationResult:

        if platform.system().lower() == "darwin":
            self._apply_macos_patches(binary_info.source_dir)

        makefile_path = binary_info.source_dir / "Makefile.auto"
        self._create_tpc_h_makefile(makefile_path)

        try:
            cmd = ["make", "-f", "Makefile.auto", binary_name]

            result = run_child(
                cmd,
                tool=f"make {binary_name} (TPC-H tools build)",
                deadline=current_run_deadline(),
                cwd=binary_info.source_dir,
                capture_output=True,
                text=True,
            )

            if result.returncode == 0 and binary_info.binary_path.exists():
                return CompilationResult(
                    binary_name=binary_name,
                    status=CompilationStatus.SUCCESS,
                    binary_path=binary_info.binary_path,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            else:
                return CompilationResult(
                    binary_name=binary_name,
                    status=CompilationStatus.FAILED,
                    error_message=f"Make failed with exit code {result.returncode}",
                    stdout=result.stdout,
                    stderr=result.stderr,
                )

        except RunTimeoutError:
            raise
        except Exception as e:
            return CompilationResult(
                binary_name=binary_name,
                status=CompilationStatus.FAILED,
                error_message=f"Compilation error: {str(e)}",
            )

    def _compile_tpc_ds_binary(self, binary_name: str, binary_info: BinaryInfo) -> CompilationResult:

        try:
            makefile_name = "makefile" if (binary_info.source_dir / "makefile").exists() else "Makefile"

            cmd = ["make", "-f", makefile_name, binary_name]

            result = run_child(
                cmd,
                tool=f"make {binary_name} (TPC-DS tools build)",
                deadline=current_run_deadline(),
                cwd=binary_info.source_dir,
                capture_output=True,
                text=True,
            )

            if result.returncode == 0 and binary_info.binary_path.exists():
                return CompilationResult(
                    binary_name=binary_name,
                    status=CompilationStatus.SUCCESS,
                    binary_path=binary_info.binary_path,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            else:
                return CompilationResult(
                    binary_name=binary_name,
                    status=CompilationStatus.FAILED,
                    error_message=f"Make failed with exit code {result.returncode}",
                    stdout=result.stdout,
                    stderr=result.stderr,
                )

        except RunTimeoutError:
            raise
        except Exception as e:
            return CompilationResult(
                binary_name=binary_name,
                status=CompilationStatus.FAILED,
                error_message=f"Compilation error: {str(e)}",
            )

    def _create_tpc_h_makefile(self, makefile_path: Path):
        platform_name = platform.system().lower()

        if platform_name == "linux":
            machine_flag = "LINUX"
            extra_defines = "-DORACLE"
        elif platform_name == "darwin":
            machine_flag = "LINUX"
            extra_defines = "-DORACLE -DMACOS_COMPAT"
        elif platform_name == "windows":
            machine_flag = "WIN32"
            extra_defines = "-DSQLSERVER"
        else:
            machine_flag = "LINUX"
            extra_defines = "-DORACLE"

        makefile_content = f"""# Auto-generated Makefile for TPC-H compilation
# Platform: {platform_name}
CC = gcc
# Let config.h handle most definitions to avoid redefinition warnings
# -DEOL_HANDLING: no trailing field separator. Must match makefile.suite's
# default CFLAGS and compile-all-platforms.sh so a runtime-built dbgen frames
# rows identically to the bundled binaries (BenchBox canonical convention).
CFLAGS = -O2 -DDBNAME=\\"dss\\" -D{machine_flag} -DTPCH -DRNG_TEST -D_FILE_OFFSET_BITS=64 \\
         -DEOL_HANDLING {extra_defines} -I.

# macOS-specific compiler adjustments
ifeq ($(shell uname),Darwin)
    CFLAGS += -Dmalloc=malloc -D_DARWIN_C_SOURCE
    # Suppress getopt warnings on macOS by using system getopt
    CFLAGS += -DUSE_SYSTEM_GETOPT
endif

# Object files for dbgen
DBGEN_OBJECTS = build.o driver.o bm_utils.o rnd.o print.o load_stub.o bcd2.o speed_seed.o text.o permute.o rng64.o

# Object files for qgen
QGEN_OBJECTS = build.o bm_utils.o qgen.o rnd.o varsub.o text.o bcd2.o permute.o rng64.o speed_seed.o

all: dbgen qgen

dbgen: $(DBGEN_OBJECTS)
	$(CC) $(CFLAGS) -o dbgen $(DBGEN_OBJECTS) -lm

qgen: $(QGEN_OBJECTS)
	$(CC) $(CFLAGS) -o qgen $(QGEN_OBJECTS) -lm

# Special compilation rules for macOS compatibility
%.o: %.c
	@if [ "$$(uname)" = "Darwin" ]; then \\
		$(CC) $(CFLAGS) -c $< -o $@ 2>/dev/null || \\
		$(CC) $(CFLAGS) -include stdlib.h -Dmalloc_h_included=1 -c $< -o $@; \\
	else \\
		$(CC) $(CFLAGS) -c $< -o $@; \\
	fi

clean:
	rm -f *.o dbgen qgen

.PHONY: all clean dbgen qgen

# Debug target to show configuration
debug:
	@echo "Platform: {platform_name}"
	@echo "Machine flag: {machine_flag}"
	@echo "CFLAGS: $(CFLAGS)"
"""
        with open(makefile_path, "w", encoding="utf-8") as f:
            f.write(makefile_content)

    def _apply_macos_patches(self, source_dir: Path):

        files_to_patch = ["bm_utils.c", "varsub.c"]

        for filename in files_to_patch:
            file_path = source_dir / filename
            if file_path.exists():
                try:
                    with open(file_path, encoding="utf-8") as f:
                        content = f.read()

                    if "#include <malloc.h>" in content:
                        logger.debug(f"Patching {filename} for macOS compatibility")

                        content = content.replace("#include <malloc.h>", "#include <stdlib.h>")

                        with open(file_path, "w", encoding="utf-8") as f:
                            f.write(content)

                except Exception as e:
                    logger.warning(f"Failed to patch {filename}: {e}")

        compat_header = source_dir / "macos_compat.h"
        if not compat_header.exists():
            compat_content = """/* macOS compatibility header - auto-generated */
#ifndef MACOS_COMPAT_H
#define MACOS_COMPAT_H

#ifdef MACOS_COMPAT
/* Use system getopt on macOS to avoid conflicts */
#include <unistd.h>
#ifdef getopt
#undef getopt
#endif
#endif

#endif /* MACOS_COMPAT_H */
"""
            try:
                with open(compat_header, "w", encoding="utf-8") as f:
                    f.write(compat_content)
            except Exception as e:
                logger.warning(f"Failed to create compatibility header: {e}")

    def compile_all_needed(self) -> dict[str, CompilationResult]:
        results = {}

        for binary_name in self.binaries:
            if self.needs_compilation(binary_name):
                results[binary_name] = self.compile_binary(binary_name)
            else:
                results[binary_name] = CompilationResult(
                    binary_name=binary_name,
                    status=CompilationStatus.NOT_NEEDED,
                    binary_path=self.binaries[binary_name].binary_path
                    if self.is_binary_available(binary_name)
                    else None,
                )

        return results

    def get_status_report(self) -> dict[str, Any]:
        report = {
            "auto_compile_enabled": self.auto_compile,
            "tpc_h_source": str(self.tpc_h_source) if self.tpc_h_source else None,
            "tpc_ds_source": str(self.tpc_ds_source) if self.tpc_ds_source else None,
            "precompiled_base": str(self.precompiled_base) if self.precompiled_base else None,
            "platform": self._get_platform_string(),
            "binaries": {},
        }

        for binary_name, binary_info in self.binaries.items():
            deps_available, missing_deps = self.check_dependencies(binary_name)

            report["binaries"][binary_name] = {
                "source_dir": str(binary_info.source_dir) if binary_info.source_dir else None,
                "binary_path": str(binary_info.binary_path) if binary_info.binary_path else None,
                "precompiled_path": str(binary_info.precompiled_path) if binary_info.precompiled_path else None,
                "precompiled_available": self.is_precompiled_available(binary_name),
                "exists": self.is_binary_available(binary_name),
                "actual_path": str(self.get_binary_path(binary_name)) if self.get_binary_path(binary_name) else None,
                "needs_compilation": self.needs_compilation(binary_name),
                "dependencies_available": deps_available,
                "missing_dependencies": missing_deps,
            }

        return report


_compiler_cache: dict[tuple[bool, bool], TPCCompiler] = {}


def get_tpc_compiler(auto_compile: bool = True, verbose: bool = False) -> TPCCompiler:
    cache_key = (auto_compile, verbose)
    if cache_key not in _compiler_cache:
        _compiler_cache[cache_key] = TPCCompiler(auto_compile=auto_compile, verbose=verbose)
    return _compiler_cache[cache_key]


def get_precompiled_bundle_root(binary_name: str) -> Optional[Path]:

    compiler = get_tpc_compiler(auto_compile=False)
    if compiler.precompiled_base is None:
        return None

    platform_str = compiler._get_platform_string()
    if binary_name in {"dsqgen", "dsdgen"}:
        candidate = compiler.precompiled_base / "tpc-ds" / platform_str
    elif binary_name in {"dbgen", "qgen"}:
        candidate = compiler.precompiled_base / "tpc-h" / platform_str
    else:
        return None

    return candidate if candidate.exists() else None


def ensure_tpc_binaries(binaries: list[str], auto_compile: bool = True) -> dict[str, CompilationResult]:
    compiler = get_tpc_compiler(auto_compile=auto_compile)
    results = {}

    for binary_name in binaries:
        if binary_name in compiler.binaries:
            if compiler.needs_compilation(binary_name):
                results[binary_name] = compiler.compile_binary(binary_name)
            else:
                if compiler.is_precompiled_available(binary_name):
                    results[binary_name] = CompilationResult(
                        binary_name=binary_name,
                        status=CompilationStatus.PRECOMPILED,
                        binary_path=compiler.get_binary_path(binary_name),
                    )
                else:
                    results[binary_name] = CompilationResult(
                        binary_name=binary_name,
                        status=CompilationStatus.NOT_NEEDED,
                        binary_path=compiler.get_binary_path(binary_name),
                    )
        else:
            results[binary_name] = CompilationResult(
                binary_name=binary_name,
                status=CompilationStatus.FAILED,
                error_message=f"Unknown binary: {binary_name}",
            )

    return results
