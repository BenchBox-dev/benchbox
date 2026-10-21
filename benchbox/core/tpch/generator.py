# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-H specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import contextlib
import json
import logging
import os
import platform
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NoReturn

import yaml

from benchbox.utils.cloud_storage import CloudStorageGeneratorMixin, create_path_handler
from benchbox.utils.compression_mixin import CompressionMixin
from benchbox.utils.data_validation import BenchmarkDataValidator
from benchbox.utils.datagen_manifest import (
    MANIFEST_FILENAME,
    DataGenerationManifest,
    get_table_files,
    load_manifest,
    require_manifest_files,
    resolve_compression_metadata,
)
from benchbox.utils.file_format import COMPRESSION_EXTENSIONS, detect_data_format
from benchbox.utils.filesystem_flush import flush_filesystem
from benchbox.utils.scale_factor import format_scale_factor
from benchbox.utils.stale_artifact_pruning import TableArtifactPattern, prune_stale_table_artifacts
from benchbox.utils.tpc_compilation import CompilationStatus, ensure_tpc_binaries
from benchbox.utils.verbosity import VerbosityMixin, compute_verbosity


def _load_generator_specs() -> dict[str, Any]:
    with (Path(__file__).with_name("generator_specs.yaml")).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


_GENERATOR_SPECS = _load_generator_specs()
_TPCH_TABLE_CODES = dict(_GENERATOR_SPECS["table_codes"])
_TPCH_BASE_ROW_COUNTS = dict(_GENERATOR_SPECS["base_row_counts"])

_NORMALIZE_CHUNK_SIZE = 1 << 20

_MEASURED_ROW_COUNTS_SOURCE = "measured"


def _has_trailing_delimiter(path: Path) -> bool:
    size = path.stat().st_size
    if size == 0:
        return False
    with path.open("rb") as handle:
        handle.seek(max(0, size - 3))
        tail = handle.read()
    return tail.rstrip(b"\r\n").endswith(b"|")


def normalize_tbl_trailing_delimiters(path: Path) -> bool:
    if not _has_trailing_delimiter(path):
        return False

    tmp_path = path.with_name(path.name + ".normalize.tmp")
    try:
        with path.open("rb") as src, tmp_path.open("wb") as dst:
            carry = b""
            while True:
                chunk = src.read(_NORMALIZE_CHUNK_SIZE)
                if not chunk:
                    break
                data = carry + chunk
                if data.endswith(b"|"):
                    carry = b"|"
                    data = data[:-1]
                elif data.endswith(b"|\r"):
                    carry = b"|\r"
                    data = data[:-2]
                else:
                    carry = b""
                dst.write(data.replace(b"|\r\n", b"\r\n").replace(b"|\n", b"\n"))
            if carry == b"|\r":
                dst.write(b"\r")
        with contextlib.suppress(OSError):
            shutil.copystat(path, tmp_path)
        os.replace(tmp_path, path)
    finally:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
    return True


def _stage_distributions(source: Path, work_dir: Path) -> None:
    target = work_dir / "dists.dss"
    if target.exists():
        return
    if not source.exists():
        raise FileNotFoundError(f"TPC-H dbgen distributions file not found: {source} (also missing from {target})")
    shutil.copy2(source, target)


class TPCHDataGenerator(CompressionMixin, CloudStorageGeneratorMixin, VerbosityMixin):
    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: str | Path | None = None,
        verbose: int | bool = 0,
        quiet: bool = False,
        parallel: int = 1,
        force_regenerate: bool = False,
        **kwargs,
    ) -> None:
        self._data_organization_config = kwargs.pop("data_organization", None)
        if self._data_organization_config is None:
            raw_config = os.getenv("BENCHBOX_DATA_ORGANIZATION_CONFIG_JSON")
            if raw_config:
                try:
                    from benchbox.core.data_organization.config import DataOrganizationConfig

                    self._data_organization_config = DataOrganizationConfig.from_dict(json.loads(raw_config))
                except Exception as exc:
                    logging.getLogger("benchbox.core.tpch.generator").warning(
                        "Ignoring invalid BENCHBOX_DATA_ORGANIZATION_CONFIG_JSON: %s", exc
                    )

        super().__init__(**kwargs)

        self.scale_factor = scale_factor
        self.output_dir = create_path_handler(output_dir) if output_dir else Path.cwd() / "tpch_data"

        verbosity_settings = compute_verbosity(verbose, quiet)
        self.apply_verbosity(verbosity_settings)
        self.logger = logging.getLogger("benchbox.core.tpch.generator")

        self.parallel = parallel
        self.force_regenerate = force_regenerate

        self.validator = BenchmarkDataValidator("tpch", scale_factor)

        self._package_root = self._package_root_dir()
        resolved_path = self.resolve_dbgen_path()

        self.dbgen_path = resolved_path or (self._package_root / "_sources/tpc-h/dbgen")
        self.dbgen_available = resolved_path is not None
        self._dbgen_error: Exception | None = None

        self._validate_parameters()

        self._dbgen_exe = None

    @property
    def dbgen_exe(self) -> Path:
        if self._dbgen_exe is None:
            try:
                self._dbgen_exe = self._find_or_build_dbgen()
                self.dbgen_available = self._dbgen_exe.exists()
                if self.dbgen_available:
                    self.dbgen_path = self._dbgen_exe.parent
            except (FileNotFoundError, RuntimeError, PermissionError) as exc:
                self.dbgen_available = False
                self._dbgen_error = exc
                self._raise_missing_dbgen()
        assert self._dbgen_exe is not None
        return self._dbgen_exe

    def _validate_parameters(self) -> None:
        if self.scale_factor <= 0:
            raise ValueError(f"Scale factor must be positive, got {self.scale_factor}")

        if self.scale_factor < 0.01:
            raise ValueError(
                f"Scale factor {self.scale_factor} is too small. "
                "TPC-H supports fractional scale factors down to 0.01 for development."
            )

        if self.scale_factor > 100000:
            raise ValueError(f"Scale factor {self.scale_factor} is too large (max 100000)")

        if self.parallel < 1:
            raise ValueError(f"Parallel processes must be >= 1, got {self.parallel}")

        if self.parallel > 64:
            raise ValueError(f"Too many parallel processes {self.parallel} (max 64)")

    @classmethod
    def _package_root_dir(cls) -> Path:
        return Path(__file__).parent.parent.parent.parent

    @classmethod
    def _candidate_dbgen_paths(cls) -> Iterator[Path]:
        package_root = cls._package_root_dir()

        yield package_root / "_sources/tpc-h/dbgen"

        yield Path(__file__).parent.parent / "_sources/tpc-h/dbgen"

        try:
            import benchbox

            if benchbox.__file__ is not None:
                module_path = Path(benchbox.__file__).parent
                yield module_path / "_sources/tpc-h/dbgen"
        except ImportError:
            return

    @classmethod
    def resolve_dbgen_path(cls) -> Path | None:
        for candidate in cls._candidate_dbgen_paths():
            if candidate.exists():
                return candidate
        return None

    def has_dbgen_sources(self) -> bool:
        return self.dbgen_available

    def _raise_missing_dbgen(self) -> NoReturn:
        import benchbox

        pkg_dir = Path(benchbox.__file__).parent
        binaries_path = pkg_dir / "_binaries/tpc-h"

        message = (
            "TPC-H dbgen tool not found. Pre-compiled binaries should be at "
            f"{binaries_path}/<platform>/ but were not found. "
            "This may indicate a corrupted or incomplete installation. "
            "Try reinstalling: pip install --force-reinstall benchbox"
        )
        if self._dbgen_error:
            message += f"\n\nDetails: {self._dbgen_error}"
        raise RuntimeError(message)

    def _find_or_build_dbgen(self) -> Path:
        results = ensure_tpc_binaries(["dbgen"], auto_compile=True)
        dbgen_result = results.get("dbgen")

        if (
            dbgen_result
            and dbgen_result.status
            in [
                CompilationStatus.SUCCESS,
                CompilationStatus.NOT_NEEDED,
                CompilationStatus.PRECOMPILED,
            ]
            and dbgen_result.binary_path
            and dbgen_result.binary_path.exists()
        ):
            self.log_verbose(f"Using dbgen binary: {dbgen_result.binary_path}")
            self.logger.info(f"Using dbgen binary: {dbgen_result.binary_path}")
            return dbgen_result.binary_path

        system = platform.system().lower()
        dbgen_exe = self.dbgen_path / "dbgen.exe" if system == "windows" else self.dbgen_path / "dbgen"

        self.logger.debug(f"dbgen_exe path: {dbgen_exe}")
        if dbgen_exe.exists():
            self.log_verbose(f"Using existing dbgen executable: {dbgen_exe}")
            if os.name != "nt" and not os.access(dbgen_exe, os.X_OK):
                raise PermissionError(f"dbgen executable at {dbgen_exe} is not executable")
            return dbgen_exe

        error_msg = f"dbgen binary required but not found at {dbgen_exe}."
        if dbgen_result and dbgen_result.error_message:
            error_msg += f" Auto-compilation failed: {dbgen_result.error_message}"
        error_msg += " TPC-H requires the compiled dbgen tool to function."

        raise RuntimeError(error_msg)

    def _check_stdout_support(self) -> bool:
        if not hasattr(self, "_stdout_support_cached"):
            try:
                result = subprocess.run(
                    [str(self.dbgen_exe), "-h"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                self._stdout_support_cached = "-z" in result.stderr or "-z" in result.stdout
            except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
                self._stdout_support_cached = False

        return self._stdout_support_cached

    def _generate_table_streaming(self, table_name: str, output_path: Path, work_dir: Path) -> Path:
        table_code = _TPCH_TABLE_CODES.get(table_name.lower())
        if not table_code:
            raise ValueError(f"Unknown TPC-H table: {table_name}")

        cmd = [
            str(self.dbgen_exe),
            "-z",
            "-q",
            "-f",
            "-s",
            str(self.scale_factor),
            "-T",
            table_code,
        ]

        env = os.environ.copy()
        env["DSS_PATH"] = "."
        env["DSS_CONFIG"] = "."

        compressor = self.get_compressor()

        try:
            with subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=work_dir,
                env=env,
            ) as proc:
                if proc.stdout is None:
                    raise RuntimeError(f"Failed to capture stdout for {table_name}")

                with compressor.open_for_write(output_path, "wb") as f:
                    chunk_size = 64 * 1024
                    while True:
                        chunk = proc.stdout.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)

                proc.wait()
                if proc.returncode != 0:
                    stderr = proc.stderr.read().decode(errors="ignore") if proc.stderr else ""
                    raise RuntimeError(f"dbgen failed for table {table_name}: {stderr}")

            return output_path

        except OSError as e:
            if output_path.exists():
                with contextlib.suppress(OSError):
                    output_path.unlink()
            raise RuntimeError(f"Streaming generation failed for {table_name}: {e}") from e

    def _run_streaming_dbgen(self, work_dir: Path) -> dict[str, Path | list[Path]]:
        import concurrent.futures

        work_dir_path = Path(work_dir)
        work_dir_path.mkdir(parents=True, exist_ok=True)

        _stage_distributions(self.dbgen_path / "dists.dss", work_dir_path)

        tables = list(_TPCH_TABLE_CODES.keys())

        ext = self.get_compressor().get_file_extension()

        results: dict[str, Path | list[Path]] = {}

        def generate_table(table_name: str) -> tuple[str, Path]:
            base_filename = f"{table_name}.tbl"
            output_path = work_dir_path / f"{base_filename}{ext}"
            result_path = self._generate_table_streaming(table_name, output_path, work_dir_path)
            return table_name, result_path

        if self.parallel > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(self.parallel, len(tables))) as executor:
                futures = {executor.submit(generate_table, table): table for table in tables}

                for future in concurrent.futures.as_completed(futures):
                    table_name, file_path = future.result()
                    results[table_name] = file_path
                    self.log_verbose(f"Generated {table_name} via streaming: {file_path.name}")
        else:
            for table in tables:
                table_name, file_path = generate_table(table)
                results[table_name] = file_path
                self.log_verbose(f"Generated {table_name} via streaming: {file_path.name}")

        return results

    def _compile_dbgen(self, work_dir: Path) -> Path:
        dbgen_build_dir = work_dir / "dbgen"
        shutil.copytree(self.dbgen_path, dbgen_build_dir)

        system = platform.system().lower()
        if system == "linux":
            machine_flag = "LINUX"
        elif system == "darwin":
            machine_flag = "MACOS"
        elif system == "windows":
            machine_flag = "WIN32"
        else:
            machine_flag = "LINUX"

        try:
            cmd = [
                "make",
                "-f",
                "makefile.suite",
                "DATABASE=SQLSERVER",
                f"MACHINE={machine_flag}",
            ]
            subprocess.run(
                cmd,
                cwd=dbgen_build_dir,
                check=True,
                stdout=subprocess.PIPE if not self.verbose else None,
                stderr=subprocess.PIPE if not self.verbose else None,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to compile dbgen: {e}") from e

        dbgen_exe = dbgen_build_dir / "dbgen.exe" if system == "windows" else dbgen_build_dir / "dbgen"

        if not dbgen_exe.exists():
            raise FileNotFoundError(f"dbgen executable not found at {dbgen_exe}")

        return dbgen_exe

    def _run_dbgen(self, dbgen_exe: Path, work_dir: Path) -> None:
        work_dir_path = Path(work_dir)
        work_dir_path.mkdir(parents=True, exist_ok=True)
        resolved_work_dir = work_dir_path.resolve()

        self.output_dir.mkdir(parents=True, exist_ok=True)
        _stage_distributions(dbgen_exe.parent / "dists.dss", work_dir_path)

        cmd = [
            str(dbgen_exe),
            "-vf",
            "-s",
            str(self.scale_factor),
        ]

        try:
            env = os.environ.copy()
            env["DSS_PATH"] = "."
            env["DSS_CONFIG"] = "."
            subprocess.run(
                cmd,
                cwd=resolved_work_dir,
                check=True,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to generate TPC-H data: {e}") from e

    def _move_data_files(self, work_dir: Path) -> dict[str, Path]:
        table_files = {
            "customer": "customer.tbl",
            "lineitem": "lineitem.tbl",
            "nation": "nation.tbl",
            "orders": "orders.tbl",
            "part": "part.tbl",
            "partsupp": "partsupp.tbl",
            "region": "region.tbl",
            "supplier": "supplier.tbl",
        }

        output_paths = {}

        if self.very_verbose:
            contents = ", ".join(sorted(p.name for p in work_dir.glob("*")))
            self.logger.debug(f"Work directory contents before move: {contents}")

        for table, filename in table_files.items():
            source_path = work_dir / filename
            target_path = self.output_dir / filename

            if source_path.exists():
                shutil.copy2(source_path, target_path)
                output_paths[table] = target_path
                self.log_verbose(f"Copied {filename} from work_dir to {target_path}")
            else:
                source_path_alt = self.dbgen_path / filename
                if source_path_alt.exists():
                    shutil.copy2(source_path_alt, target_path)
                    output_paths[table] = target_path
                    self.log_verbose(f"Copied {filename} from dbgen_path to {target_path}")
                    try:
                        source_path_alt.unlink()
                    except (OSError, PermissionError):
                        self.log_very_verbose(f"Could not clean up {source_path_alt}")
                else:
                    self.logger.warning(
                        "Generated file %s not found at %s or %s",
                        filename,
                        source_path,
                        source_path_alt,
                    )

        return output_paths

    def _run_dbgen_native(self, work_dir: Path) -> dict[str, Path | list[Path]] | None:
        work_dir_path = Path(work_dir)
        work_dir_path.mkdir(parents=True, exist_ok=True)
        resolved_work_dir = work_dir_path.resolve()

        self.output_dir.mkdir(parents=True, exist_ok=True)

        use_streaming = self.should_use_compression() and self.parallel == 1 and self._check_stdout_support()

        if use_streaming:
            self.log_verbose("Using streaming data generation with -z flag")
            return self._run_streaming_dbgen(work_dir_path)

        if self.should_use_compression() and not self._check_stdout_support():
            self.logger.warning("dbgen binary does not support -z flag; falling back to file-then-compress mode")

        try:
            patterns = [
                "customer.tbl*",
                "lineitem.tbl*",
                "nation.tbl*",
                "orders.tbl*",
                "part.tbl*",
                "partsupp.tbl*",
                "region.tbl*",
                "supplier.tbl*",
                "delete.*",
                "*.u*",
            ]
            for pat in patterns:
                for f in work_dir_path.glob(pat):
                    try:
                        if f.is_file():
                            f.unlink()
                    except Exception:
                        pass
        except Exception:
            pass

        _stage_distributions(self.dbgen_path / "dists.dss", work_dir_path)

        if self.parallel > 1:
            self._run_parallel_dbgen(work_dir_path)
        else:
            cmd = [
                str(self.dbgen_exe),
                "-vf",
                "-s",
                str(self.scale_factor),
            ]

            try:
                env = os.environ.copy()
                env["DSS_PATH"] = "."
                env["DSS_CONFIG"] = "."
                subprocess.run(
                    cmd,
                    cwd=resolved_work_dir,
                    check=True,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )

                flush_filesystem(logger=self.logger)

                import time

                time.sleep(0.2)

                if self.very_verbose:
                    created = ", ".join(sorted(p.name for p in work_dir_path.glob("*")))
                    self.logger.debug(f"Files after dbgen: {created}")

            except subprocess.CalledProcessError as e:
                stderr = (
                    e.stderr.decode(errors="ignore") if isinstance(e.stderr, (bytes, bytearray)) else (e.stderr or "")
                )
                stdout = (
                    e.stdout.decode(errors="ignore") if isinstance(e.stdout, (bytes, bytearray)) else (e.stdout or "")
                )
                error_msg = f"Failed to generate TPC-H data with exit code {e.returncode}: {stderr.strip()}"
                if stdout:
                    error_msg += f"\nOutput: {stdout.strip()}"
                raise RuntimeError(error_msg) from e

        return None

    def _run_parallel_dbgen(self, work_dir: Path) -> None:
        import concurrent.futures

        work_dir_path = Path(work_dir)
        work_dir_path.mkdir(parents=True, exist_ok=True)
        resolved_work_dir = work_dir_path.resolve()

        _stage_distributions(self.dbgen_path / "dists.dss", work_dir_path)

        def generate_chunk(chunk_id: int) -> None:
            cmd = [
                str(self.dbgen_exe),
                "-vf",
                "-s",
                str(self.scale_factor),
                "-S",
                str(chunk_id),
                "-C",
                str(self.parallel),
            ]

            try:
                env = os.environ.copy()
                env["DSS_PATH"] = "."
                env["DSS_CONFIG"] = "."
                subprocess.run(
                    cmd,
                    cwd=resolved_work_dir,
                    check=True,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            except subprocess.CalledProcessError as e:
                error_msg = f"Failed to generate TPC-H data chunk {chunk_id} with exit code {e.returncode}"
                if e.stderr:
                    error_msg += f": {e.stderr}"
                if e.stdout:
                    error_msg += f"\nOutput: {e.stdout}"
                raise RuntimeError(error_msg) from e

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.parallel) as executor:
            futures = []
            for chunk_id in range(1, self.parallel + 1):
                future = executor.submit(generate_chunk, chunk_id)
                futures.append(future)

            for future in concurrent.futures.as_completed(futures):
                future.result()

    def generate(self) -> dict[str, Path | list[Path]]:
        return self._handle_cloud_or_local_generation(self.output_dir, self._generate_local, self.verbose)

    def _generate_local(self, output_dir: Path | None = None) -> dict[str, Path | list[Path]]:
        _ = self.dbgen_exe

        target_dir = output_dir if output_dir is not None else self.output_dir

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            raise PermissionError(f"Cannot create output directory {target_dir}: {e}") from e

        if not os.access(target_dir, os.W_OK):
            raise PermissionError(f"Output directory {target_dir} is not writable")

        should_regenerate, validation_result = self.validator.should_regenerate_data(target_dir, self.force_regenerate)

        if not should_regenerate:
            if self.verbose_enabled:
                self.logger.info("✅ Valid TPC-H data found for scale factor %s", self.scale_factor)
                self.validator.print_validation_report(validation_result, verbose=False)
                self.logger.info("Skipping data generation (existing data is valid)")

            existing_manifest = self._load_existing_manifest(target_dir)
            manifest_paths: dict[str, Path | list[Path]] = {}
            if existing_manifest is not None:
                self._refresh_reused_manifest(target_dir, existing_manifest)
                manifest_paths = self._paths_from_manifest(target_dir, existing_manifest)
                if manifest_paths:
                    return manifest_paths

            existing_paths = self._collect_existing_table_files(target_dir)
            if existing_manifest is None or not manifest_paths:
                self._write_manifest(target_dir, existing_paths, stamp=False)
            return existing_paths

        removed_stale = self._prune_stale_table_artifacts(target_dir)
        if removed_stale and self.verbose_enabled:
            self.logger.info("Removed %d stale TPC-H table artifacts before regeneration", len(removed_stale))

        sample_dir = self._get_sample_data_dir()
        if sample_dir is not None:
            self.log_verbose(f"⚡ Using bundled TPC-H sample dataset for scale factor {self.scale_factor}")
            self._copy_sample_dataset(sample_dir, target_dir)
            return self._finalize_generation(target_dir)

        if output_dir is None:
            if validation_result and validation_result.issues:
                self.logger.warning("⚠️️  Data validation failed for scale factor %s", self.scale_factor)
                if self.verbose_enabled:
                    self.validator.print_validation_report(validation_result, verbose=True)
            elif self.force_regenerate and self.verbose_enabled:
                self.logger.info("⚠️️ Force regeneration requested")

        self.log_operation_start(
            "TPC-H data generation",
            details=f"scale_factor={self.scale_factor}, parallel={self.parallel}",
        )

        streaming_result = self._run_dbgen_native(target_dir)

        if streaming_result is not None:
            self._write_manifest(target_dir, streaming_result)
            self.log_operation_complete("TPC-H data generation (streaming)")
            return streaming_result

        result = self._finalize_generation(target_dir)
        self.log_operation_complete("TPC-H data generation")
        return result

    def _collect_existing_table_files(self, target_dir: Path) -> dict[str, Path | list[Path]]:
        table_files = {
            "customer": "customer.tbl",
            "lineitem": "lineitem.tbl",
            "nation": "nation.tbl",
            "orders": "orders.tbl",
            "part": "part.tbl",
            "partsupp": "partsupp.tbl",
            "region": "region.tbl",
            "supplier": "supplier.tbl",
        }

        existing: dict[str, Path | list[Path]] = {}
        for table, filename in table_files.items():
            for ext in COMPRESSION_EXTENSIONS:
                compressed_file = target_dir / f"{filename}{ext}"
                if compressed_file.exists():
                    existing[table] = compressed_file
                    break

                compressed_chunks = [
                    cf
                    for cf in target_dir.glob(f"{filename}.*{ext}")
                    if cf.name.replace(ext, "").split(".")[-1].isdigit()
                ]
                if compressed_chunks:
                    existing[table] = sorted(compressed_chunks, key=lambda f: f.name)
                    break
            else:
                tbl_file = target_dir / filename
                if tbl_file.exists():
                    existing[table] = tbl_file
                    continue

                chunk_files = [cf for cf in target_dir.glob(f"{filename}.*") if cf.name.split(".")[-1].isdigit()]
                if chunk_files:
                    existing[table] = sorted(chunk_files, key=lambda f: f.name)

        return existing

    def _load_existing_manifest(self, target_dir: Path) -> dict[str, Any] | None:
        manifest_path = target_dir / MANIFEST_FILENAME
        if not manifest_path.is_file():
            return None
        try:
            manifest = load_manifest(manifest_path)
        except (OSError, TypeError, ValueError) as exc:
            self.logger.warning("Ignoring unreadable TPC-H manifest %s: %s", manifest_path, exc)
            return None
        if not isinstance(manifest, dict):
            self.logger.warning("Ignoring non-object TPC-H manifest %s", manifest_path)
            return None
        return manifest

    @staticmethod
    def _resolve_manifest_path(target_dir: Path, manifest_path: object) -> Path | None:
        if not isinstance(manifest_path, str) or not manifest_path:
            return None
        path = Path(manifest_path)
        return path if path.is_absolute() else target_dir / path

    def _paths_from_manifest(self, target_dir: Path, manifest: dict[str, Any]) -> dict[str, Path | list[Path]]:
        tables = manifest.get("tables")
        if not isinstance(tables, dict):
            return {}

        table_paths: dict[str, Path | list[Path]] = {}
        for table_name in tables:
            entries = get_table_files(manifest, table_name)
            paths = [
                resolved
                for entry in entries
                if isinstance(entry, dict)
                for resolved in [self._resolve_manifest_path(target_dir, entry.get("path"))]
                if resolved is not None
            ]
            if paths:
                table_paths[table_name] = paths[0] if len(paths) == 1 else paths
        return table_paths

    def _refresh_reused_manifest(self, target_dir: Path, manifest: dict[str, Any]) -> None:
        if manifest.get("row_counts_source") == _MEASURED_ROW_COUNTS_SOURCE:
            return

        tables = manifest.get("tables")
        if not isinstance(tables, dict):
            return

        measured_counts: dict[Path, int] = {}
        count_complete = True

        def refresh_entries(entries: object) -> None:
            nonlocal count_complete
            if not isinstance(entries, list):
                return
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                file_path = self._resolve_manifest_path(target_dir, entry.get("path"))
                if file_path is None or detect_data_format(file_path) != "tbl":
                    continue
                if not file_path.is_file():
                    count_complete = False
                    continue
                try:
                    resolved_path = file_path.resolve()
                    if resolved_path not in measured_counts:
                        measured_counts[resolved_path] = self._count_file_rows(file_path)
                    entry["row_count"] = measured_counts[resolved_path]
                except (OSError, RuntimeError, ValueError) as exc:
                    count_complete = False
                    self.logger.warning("Unable to measure reused TPC-H file %s: %s", file_path, exc)

        for table_data in tables.values():
            if isinstance(table_data, list):
                refresh_entries(table_data)
            elif isinstance(table_data, dict):
                formats = table_data.get("formats")
                if isinstance(formats, dict):
                    for entries in formats.values():
                        refresh_entries(entries)

        if not count_complete:
            return

        manifest["row_counts_source"] = _MEASURED_ROW_COUNTS_SOURCE
        manifest_path = target_dir / MANIFEST_FILENAME
        temporary_path = manifest_path.with_name(f".{manifest_path.name}.tmp")
        temporary_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        temporary_path.replace(manifest_path)

    def _prune_stale_table_artifacts(self, target_dir: Path) -> list[Path]:
        table_files = (
            "customer",
            "lineitem",
            "nation",
            "orders",
            "part",
            "partsupp",
            "region",
            "supplier",
        )
        return prune_stale_table_artifacts(
            target_dir=target_dir,
            table_names=table_files,
            pattern=TableArtifactPattern(
                single_suffix=".tbl",
                raw_shard_glob_template="{table}.tbl.*",
                compressed_shard_glob_template="{table}.tbl.*{ext}",
                shard_regex_template=r"^{table}\.tbl\.\d+(?:\.[a-z0-9]+)?$",
            ),
            compression_extensions=COMPRESSION_EXTENSIONS,
            use_compression=self.should_use_compression(),
            expect_sharded=int(getattr(self, "parallel", 1) or 1) > 1,
        )

    def _get_sample_data_dir(self) -> Path | None:
        if self.scale_factor >= 1:
            return None
        if not self.should_use_compression():
            return None
        sf_label = format_scale_factor(self.scale_factor)
        candidate = self._package_root / "examples" / "data" / f"tpch_{sf_label}"
        return candidate if candidate.exists() else None

    def _copy_sample_dataset(self, sample_dir: Path, target_dir: Path) -> None:
        for child in target_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

        for item in sample_dir.iterdir():
            destination = target_dir / item.name
            if item.is_dir():
                shutil.copytree(item, destination)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, destination)

    def _finalize_generation(self, target_dir: Path) -> dict[str, Path | list[Path]]:
        table_paths, precompressed_tables = self._gather_generated_table_paths(target_dir)

        self._normalize_table_delimiters(table_paths, precompressed_tables)

        if self._data_organization_config is not None:
            table_paths = self._apply_data_organization(target_dir, table_paths)
            self._write_manifest(target_dir, table_paths)
            return table_paths

        if self.should_use_compression():
            compressed_paths = self._compress_table_paths(target_dir, table_paths, precompressed_tables)

            if self.verbose_enabled and compressed_paths:
                flat_paths = {k: (v[0] if isinstance(v, list) else v) for k, v in compressed_paths.items()}
                self.print_compression_report(flat_paths)

            self._validate_file_format_consistency(target_dir)
            self._write_manifest(target_dir, compressed_paths)
            return compressed_paths

        self._write_manifest(target_dir, table_paths)
        return table_paths

    def _normalize_table_delimiters(
        self,
        table_paths: dict[str, Path | list[Path]],
        precompressed_tables: set[str],
    ) -> None:
        for table_name, paths in table_paths.items():
            if table_name in precompressed_tables:
                continue
            for file_path in paths if isinstance(paths, list) else [paths]:
                if normalize_tbl_trailing_delimiters(file_path):
                    self.log_verbose(f"Stripped trailing row delimiters from {file_path.name}")

    def _apply_data_organization(
        self,
        target_dir: Path,
        table_paths: dict[str, Path | list[Path]],
    ) -> dict[str, Path | list[Path]]:
        from benchbox.core.data_organization.sorting import SortedParquetWriter

        config = self._data_organization_config
        writer = SortedParquetWriter(config, schema_registry=self._build_schema_registry())
        result = dict(table_paths)

        for table_name, paths in table_paths.items():
            sort_columns = config.get_sort_columns_for_table(table_name)
            partition_columns = config.get_partition_columns_for_table(table_name)
            cluster_columns = config.get_cluster_columns_for_table(table_name)
            if not sort_columns and not partition_columns and not cluster_columns:
                continue

            source_files = paths if isinstance(paths, list) else [paths]

            parquet_path = writer.write_sorted_parquet(
                table_name=table_name,
                source_files=source_files,
                output_dir=target_dir,
            )
            result[table_name] = parquet_path

        return result

    def _build_schema_registry(self) -> dict[str, dict[str, Any]]:
        from benchbox.core.tpch.schema import TABLES

        schema_registry: dict[str, dict[str, Any]] = {}
        for table in TABLES:
            schema_registry[table.name.lower()] = {
                "name": table.name.lower(),
                "columns": [
                    {
                        "name": col.name,
                        "type": col.get_sql_type(),
                    }
                    for col in table.columns
                ],
            }
        return schema_registry

    def _gather_generated_table_paths(self, target_dir: Path) -> tuple[dict[str, Path | list[Path]], set[str]]:
        table_files = {
            "customer": "customer.tbl",
            "lineitem": "lineitem.tbl",
            "nation": "nation.tbl",
            "orders": "orders.tbl",
            "part": "part.tbl",
            "partsupp": "partsupp.tbl",
            "region": "region.tbl",
            "supplier": "supplier.tbl",
        }

        table_paths: dict[str, Path | list[Path]] = {}
        precompressed_tables: set[str] = set()
        for table_name, filename in table_files.items():
            if self.should_use_compression():
                compressed_filename = self.get_compressed_filename(filename)
                compressed_path = target_dir / compressed_filename
                if compressed_path.exists():
                    table_paths[table_name] = compressed_path
                    precompressed_tables.add(table_name)
                    self.log_verbose(f"Found pre-compressed file {compressed_filename} for {table_name}")
                    continue

            tbl_file = target_dir / filename
            if tbl_file.exists():
                table_paths[table_name] = tbl_file
                self.log_verbose(f"Generated {filename} at {tbl_file}")
                continue

            chunk_files = [
                target_dir / f"{filename}.{chunk_id}"
                for chunk_id in range(1, self.parallel + 1)
                if (target_dir / f"{filename}.{chunk_id}").exists()
            ]
            if chunk_files:
                sorted_chunks = sorted(chunk_files, key=lambda f: f.name)
                table_paths[table_name] = sorted_chunks
                self.log_verbose(f"Generated {len(chunk_files)} chunk files for {table_name}")
                continue

            source_file = self.dbgen_path / filename
            binary_dir = self.dbgen_exe.parent
            binary_source_file = binary_dir / filename
            if source_file.exists():
                shutil.move(str(source_file), str(tbl_file))
                table_paths[table_name] = tbl_file
                self.log_verbose(f"Moved {filename} from dbgen source path to {tbl_file}")
                continue
            if binary_source_file.exists():
                shutil.move(str(binary_source_file), str(tbl_file))
                table_paths[table_name] = tbl_file
                self.log_verbose(f"Moved {filename} from dbgen binary path to {tbl_file}")
                continue

            self.logger.warning(
                "Generated file %s not found in %s, %s, or %s",
                filename,
                target_dir,
                source_file,
                binary_source_file,
            )

        return table_paths, precompressed_tables

    def _compress_table_paths(
        self,
        target_dir: Path,
        table_paths: dict[str, Path | list[Path]],
        precompressed_tables: set[str],
    ) -> dict[str, Path | list[Path]]:
        compressed_paths: dict[str, Path | list[Path]] = {}
        for table_name, file_path_or_paths in table_paths.items():
            if table_name in precompressed_tables:
                compressed_paths[table_name] = file_path_or_paths
                continue

            if isinstance(file_path_or_paths, list):
                compressed_chunks = self._compress_file_list(file_path_or_paths)
                if compressed_chunks:
                    compressed_paths[table_name] = sorted(compressed_chunks, key=lambda f: f.name)
            else:
                compressed_paths[table_name] = self._compress_single_or_legacy_shards(target_dir, file_path_or_paths)

        return compressed_paths

    def _compress_file_list(self, file_paths: list[Path]) -> list[Path]:
        compressed_chunk_files = []
        for chunk_file in file_paths:
            compressed_chunk = self.compress_existing_file(chunk_file, remove_original=True)
            compressed_chunk_files.append(compressed_chunk)
            self.log_verbose(f"Compressed {chunk_file.name} to {compressed_chunk.name}")
        return compressed_chunk_files

    def _compress_single_or_legacy_shards(self, target_dir: Path, file_path: Path) -> Path | list[Path]:
        filename = file_path.name
        if "." in filename and filename.split(".")[-1].isdigit():
            base_filename = ".".join(filename.split(".")[:-1])
            chunk_files = [cf for cf in target_dir.glob(f"{base_filename}.*") if cf.name.split(".")[-1].isdigit()]
            compressed_chunks = self._compress_file_list(chunk_files)
            if compressed_chunks:
                return sorted(compressed_chunks, key=lambda f: f.name)
            return file_path

        compressed_file = self.compress_existing_file(file_path, remove_original=True)
        self.log_verbose(f"Compressed {file_path.name} to {compressed_file.name}")
        return compressed_file

    def _validate_file_format_consistency(self, target_dir: Path) -> None:
        if not self.should_use_compression():
            return
        raw_tbl = list(target_dir.glob("*.tbl"))
        if raw_tbl:
            names = ", ".join(f.name for f in raw_tbl[:5])
            more = "..." if len(raw_tbl) > 5 else ""
            raise RuntimeError(
                f"File format consistency violation: Found raw .tbl files with compression enabled: {names}{more}"
            )
        ext = self.get_compressor().get_file_extension()
        compressed = list(target_dir.glob(f"*.tbl{ext}"))
        empties = [f for f in compressed if f.stat().st_size <= (9 if ext == ".zst" else 20)]
        if empties:
            names = ", ".join(f.name for f in empties[:5])
            more = "..." if len(empties) > 5 else ""
            raise RuntimeError(f"File format consistency violation: Found empty compressed files: {names}{more}")

    def _write_manifest(
        self, output_dir: Path, table_paths: dict[str, Path | list[Path]], *, stamp: bool = True
    ) -> None:
        if not table_paths:
            return

        manifest = DataGenerationManifest(
            output_dir=output_dir,
            benchmark="tpch",
            scale_factor=self.scale_factor,
            compression=resolve_compression_metadata(self),
            parallel=self.parallel,
            seed=getattr(self, "seed", None),
            extra_metadata={"row_counts_source": _MEASURED_ROW_COUNTS_SOURCE},
            stamp=stamp,
        )

        for table, file_path_or_paths in table_paths.items():
            expected_rows_total = self._expected_row_count(table)

            if isinstance(file_path_or_paths, list):
                chunk_files = file_path_or_paths
                if chunk_files:
                    for chunk_file in chunk_files:
                        manifest.add_entry(
                            table,
                            chunk_file,
                            row_count=self._manifest_row_count(chunk_file, expected_rows_total // len(chunk_files)),
                        )
                    self.log_very_verbose(f"Added {len(chunk_files)} chunk files for {table} to manifest")
                continue

            first_file_path = file_path_or_paths
            filename = first_file_path.name
            is_sharded = False
            pattern = ""

            parts = filename.split(".")
            if len(parts) >= 3 and parts[-2].isdigit():
                is_sharded = True
                base_parts = parts[:-2]
                compression_ext = parts[-1]
                pattern = f"{'.'.join(base_parts)}.*{compression_ext}"
            elif len(parts) >= 2 and parts[-1].isdigit():
                is_sharded = True
                base_parts = parts[:-1]
                pattern = f"{'.'.join(base_parts)}.*"

            if is_sharded and pattern:
                chunk_files = sorted(
                    [
                        f
                        for f in output_dir.glob(pattern)
                        if f.name.split(".")[-2 if self.should_use_compression() else -1].isdigit()
                    ]
                )

                if chunk_files:
                    for chunk_file in chunk_files:
                        manifest.add_entry(
                            table,
                            chunk_file,
                            row_count=self._manifest_row_count(chunk_file, expected_rows_total // len(chunk_files)),
                        )

                    self.log_very_verbose(f"Added {len(chunk_files)} chunk files for {table} to manifest")
                    continue

            manifest.add_entry(
                table,
                first_file_path,
                row_count=self._manifest_row_count(first_file_path, expected_rows_total),
            )

        require_manifest_files(manifest.file_counts()[1], label="TPC-H", output_dir=output_dir)
        manifest.write()

    def _manifest_row_count(self, file_path: Path, fallback: int) -> int:
        file_path = Path(file_path)
        if detect_data_format(file_path) != "tbl":
            return fallback
        return self._count_file_rows(file_path)

    def _count_file_rows(self, file_path: Path) -> int:
        compression_type = self.compression_manager.detect_compression(file_path)
        if compression_type == "none":
            with file_path.open("rt", encoding="utf-8", newline="") as stream:
                return sum(1 for _ in stream)

        compressor = self.compression_manager.get_compressor(compression_type)
        with compressor.open_for_read(file_path, "rt") as stream:
            return sum(1 for _ in stream)

    def _expected_row_count(self, table: str) -> int:
        base = _TPCH_BASE_ROW_COUNTS.get(table.lower())
        if base is None:
            return 0
        if table.lower() in {"nation", "region"}:
            return base
        return max(0, int(round(base * float(self.scale_factor))))
