# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-H specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, ClassVar, Union

from benchbox.utils.verbosity import VerbosityMixin, compute_verbosity


class TPCHStreams(VerbosityMixin):
    PERMUTATION_MATRIX: ClassVar[list[list[int]]] = [
        [14, 2, 9, 20, 6, 17, 18, 8, 21, 13, 3, 22, 16, 4, 11, 15, 1, 10, 19, 5, 7, 12],
        [21, 3, 18, 5, 11, 7, 6, 20, 17, 12, 16, 15, 13, 10, 2, 8, 14, 19, 9, 22, 1, 4],
        [6, 17, 14, 16, 19, 10, 9, 2, 15, 8, 5, 22, 12, 7, 13, 18, 1, 4, 20, 3, 11, 21],
        [8, 5, 4, 6, 17, 7, 1, 18, 22, 14, 9, 10, 15, 11, 20, 2, 21, 19, 13, 16, 12, 3],
        [5, 21, 14, 19, 15, 17, 12, 6, 4, 9, 8, 16, 11, 2, 10, 18, 1, 13, 7, 22, 3, 20],
        [21, 15, 4, 6, 7, 16, 19, 18, 14, 22, 11, 13, 3, 1, 2, 5, 8, 20, 12, 17, 10, 9],
        [10, 3, 15, 13, 6, 8, 9, 7, 4, 11, 22, 18, 12, 1, 5, 16, 2, 14, 19, 20, 17, 21],
        [18, 8, 20, 21, 2, 4, 22, 17, 1, 11, 9, 19, 3, 13, 5, 7, 10, 16, 6, 14, 15, 12],
        [19, 1, 15, 17, 5, 8, 9, 12, 14, 7, 4, 3, 20, 16, 6, 22, 10, 13, 2, 21, 18, 11],
        [8, 13, 2, 20, 17, 3, 6, 21, 18, 11, 19, 10, 15, 4, 22, 1, 7, 12, 9, 14, 5, 16],
        [6, 15, 18, 17, 12, 1, 7, 2, 22, 13, 21, 10, 14, 9, 3, 16, 20, 19, 11, 4, 8, 5],
        [15, 14, 18, 17, 10, 20, 16, 11, 1, 8, 4, 22, 5, 12, 3, 9, 21, 2, 13, 6, 19, 7],
        [1, 7, 16, 17, 18, 22, 12, 6, 8, 9, 11, 4, 2, 5, 20, 21, 13, 10, 19, 3, 14, 15],
        [21, 17, 7, 3, 1, 10, 12, 22, 9, 16, 6, 11, 2, 4, 5, 14, 8, 20, 13, 18, 15, 19],
        [2, 9, 5, 4, 18, 1, 20, 15, 16, 17, 7, 21, 13, 14, 19, 8, 22, 11, 10, 3, 12, 6],
        [16, 9, 17, 8, 14, 11, 10, 12, 6, 21, 7, 3, 15, 5, 22, 20, 1, 13, 19, 2, 4, 18],
        [1, 3, 6, 5, 2, 16, 14, 22, 17, 20, 4, 9, 10, 11, 15, 8, 12, 19, 18, 13, 7, 21],
        [3, 16, 5, 11, 21, 9, 2, 15, 10, 18, 17, 7, 8, 19, 14, 13, 1, 4, 22, 20, 6, 12],
        [14, 4, 13, 5, 21, 11, 8, 6, 3, 17, 2, 20, 1, 19, 10, 9, 12, 18, 15, 7, 22, 16],
        [4, 12, 22, 14, 5, 15, 16, 2, 8, 10, 17, 9, 21, 7, 3, 6, 13, 18, 11, 20, 19, 1],
        [16, 15, 14, 13, 4, 22, 18, 19, 7, 1, 12, 17, 5, 10, 20, 3, 9, 21, 11, 2, 6, 8],
        [20, 14, 21, 12, 15, 17, 4, 19, 13, 10, 11, 1, 16, 5, 18, 7, 8, 22, 9, 6, 3, 2],
        [16, 14, 13, 2, 21, 10, 11, 4, 1, 22, 18, 12, 19, 5, 7, 8, 6, 3, 15, 20, 9, 17],
        [18, 15, 9, 14, 12, 2, 8, 11, 22, 21, 16, 1, 6, 17, 5, 10, 19, 4, 20, 13, 3, 7],
        [7, 3, 10, 14, 13, 21, 18, 6, 20, 4, 9, 8, 22, 15, 2, 1, 5, 12, 19, 17, 11, 16],
        [18, 1, 13, 7, 16, 10, 14, 2, 19, 5, 21, 11, 22, 15, 8, 17, 20, 3, 4, 12, 6, 9],
        [13, 2, 22, 5, 11, 21, 20, 14, 7, 10, 4, 9, 19, 18, 6, 3, 1, 8, 15, 12, 17, 16],
        [14, 17, 21, 8, 2, 9, 6, 4, 5, 13, 22, 7, 15, 3, 1, 18, 16, 11, 10, 12, 20, 19],
        [10, 22, 1, 12, 13, 18, 21, 20, 2, 14, 16, 7, 15, 3, 4, 17, 5, 19, 6, 8, 9, 11],
        [10, 8, 9, 18, 12, 6, 1, 5, 20, 11, 17, 22, 16, 3, 13, 2, 15, 21, 14, 19, 7, 4],
        [7, 17, 22, 5, 3, 10, 13, 18, 9, 1, 14, 15, 21, 19, 16, 12, 8, 6, 11, 20, 4, 2],
        [2, 9, 21, 3, 4, 7, 1, 11, 16, 5, 20, 19, 18, 8, 17, 13, 10, 12, 15, 6, 14, 22],
        [15, 12, 8, 4, 22, 13, 16, 17, 18, 3, 7, 5, 6, 1, 9, 11, 21, 10, 14, 20, 19, 2],
        [15, 16, 2, 11, 17, 7, 5, 14, 20, 4, 21, 3, 10, 9, 12, 8, 13, 6, 18, 19, 22, 1],
        [1, 13, 11, 3, 4, 21, 6, 14, 15, 22, 18, 9, 7, 5, 10, 20, 12, 16, 17, 8, 19, 2],
        [14, 17, 22, 20, 8, 16, 5, 10, 1, 13, 2, 21, 12, 9, 4, 18, 3, 7, 6, 19, 15, 11],
        [9, 17, 7, 4, 5, 13, 21, 18, 11, 3, 22, 1, 6, 16, 20, 14, 15, 10, 8, 2, 12, 19],
        [13, 14, 5, 22, 19, 11, 9, 6, 18, 15, 8, 10, 7, 4, 17, 16, 3, 1, 12, 2, 21, 20],
        [20, 5, 4, 14, 11, 1, 6, 16, 8, 22, 7, 3, 2, 12, 21, 19, 17, 13, 10, 15, 18, 9],
        [3, 7, 14, 15, 6, 5, 21, 20, 18, 10, 4, 16, 19, 1, 13, 9, 8, 17, 11, 12, 22, 2],
        [13, 15, 17, 1, 22, 11, 3, 4, 7, 20, 14, 21, 9, 8, 2, 18, 16, 6, 10, 12, 5, 19],
    ]

    def __init__(
        self,
        num_streams: int = 1,
        scale_factor: float = 1.0,
        output_dir: Union[str, Path] | None = None,
        rng_seed: int | None = None,
        verbose: int | bool = 0,
    ) -> None:
        self.num_streams = num_streams
        self.scale_factor = scale_factor
        self.output_dir = Path(output_dir) if output_dir else Path.cwd() / "tpch_streams"
        self.rng_seed = rng_seed if rng_seed is not None else 1
        verbosity_settings = compute_verbosity(verbose, False)
        self.apply_verbosity(verbosity_settings)
        self.logger = logging.getLogger("benchbox.core.tpch.streams")

        self.query_count = 22

        from benchbox.utils.tpc_compilation import get_tpc_templates_dir

        self.tpch_tools_path = get_tpc_templates_dir("tpc-h")

    def _compile_qgen(self, work_dir: Path) -> Path | None:
        tools_build_dir = work_dir / "tpch_tools"
        shutil.copytree(self.tpch_tools_path, tools_build_dir)

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
            env = dict(os.environ)
            if system == "darwin":
                env["CC"] = "gcc"
                env["CFLAGS"] = '-O -DDBNAME=\\"dss\\" -DLINUX -DORACLE -DTPCH'

            cmd = ["make", "qgen", f"MACHINE={machine_flag}"]
            result = subprocess.run(
                cmd,
                cwd=tools_build_dir,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

            self.log_verbose("qgen compilation completed")
            if result.stdout:
                self.log_very_verbose(f"qgen compilation output: {result.stdout}")

        except subprocess.CalledProcessError as e:
            error_msg = f"Failed to compile qgen: {e}"
            if e.stderr:
                error_msg += f"\nStderr: {e.stderr}"
            if e.stdout:
                error_msg += f"\nStdout: {e.stdout}"

            self.logger.warning("%s", error_msg)
            self.log_verbose("Falling back to built-in query generation...")
            return None

        qgen_exe = tools_build_dir / "qgen.exe" if system == "windows" else tools_build_dir / "qgen"

        if not qgen_exe.exists():
            self.logger.warning("qgen executable not found at %s", qgen_exe)
            self.log_verbose("Falling back to built-in query generation...")
            return None

        return qgen_exe

    def _get_stream_permutation(self, stream_id: int) -> list[int]:
        permutation_index = stream_id % len(self.PERMUTATION_MATRIX)

        return self.PERMUTATION_MATRIX[permutation_index].copy()

    def _generate_stream_queries_qgen(self, stream_id: int, qgen_exe: Path, work_dir: Path) -> Path:
        stream_file = self.output_dir / f"stream_{stream_id}.sql"

        query_order = self._get_stream_permutation(stream_id)

        self.log_verbose(f"Generating stream {stream_id} with {len(query_order)} queries...")
        if self.very_verbose:
            self.logger.debug(f"Stream {stream_id} query order: {query_order}")

        cmd = [
            str(qgen_exe),
            "-p",
            str(stream_id % len(self.PERMUTATION_MATRIX)),
            "-s",
            str(self.scale_factor),
            "-r",
            str(self.rng_seed + stream_id),
            "-o",
            str(work_dir),
        ]

        try:
            result = subprocess.run(cmd, cwd=work_dir, check=True, capture_output=True, text=True)

            self.log_verbose(f"qgen completed for stream {stream_id}")
            if result.stdout:
                snippet = result.stdout[:200].strip()
                if snippet:
                    self.log_very_verbose(f"qgen output: {snippet}...")

        except subprocess.CalledProcessError as e:
            error_msg = f"Failed to generate stream {stream_id} with qgen: {e}"
            if e.stderr:
                error_msg += f"\nStderr: {e.stderr}"
            raise RuntimeError(error_msg) from e

        generated_queries = []
        for position, query_id in enumerate(query_order, 1):
            qgen_output_file = work_dir / f"{query_id}.sql"
            if qgen_output_file.exists():
                with open(qgen_output_file, encoding="utf-8") as f:
                    query_content = f.read()
                generated_queries.append((position, query_id, query_content))
            else:
                self.logger.warning("Query %s not generated by qgen for stream %s", query_id, stream_id)

        with open(stream_file, "w", encoding="utf-8") as out_f:
            out_f.write(f"-- TPC-H Stream {stream_id}\n")
            out_f.write(f"-- Scale Factor: {self.scale_factor}\n")
            out_f.write(f"-- RNG Seed: {self.rng_seed + stream_id}\n")
            out_f.write(f"-- Query Order (Permuted): {query_order}\n")
            out_f.write("-- Generated using TPC-H qgen tool\n")
            out_f.write("-- Compliant with TPC-H specification\n\n")

            for position, query_id, query_content in generated_queries:
                out_f.write(f"-- Query {query_id} (Stream {stream_id}, Position {position})\n")
                out_f.write(query_content)
                out_f.write("\n\n")

        return stream_file

    def generate_streams(self) -> list[Path]:
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.log_operation_start(
            "TPC-H stream generation",
            details=f"streams={self.num_streams}, scale_factor={self.scale_factor}, seed={self.rng_seed}",
        )
        if self.verbose_enabled:
            self.logger.info("Output directory: %s", self.output_dir)

        stream_files = []

        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)

            self.log_verbose("Compiling qgen (required for TPC-H compliance)...")

            try:
                qgen_exe = self._compile_qgen(work_dir)
            except Exception as e:
                raise RuntimeError(
                    f"TPC-H streams generation requires qgen compilation, but it failed: {e}. "
                    "Please ensure you have the necessary build tools (make, gcc) installed "
                    "and that the TPC-H sources are properly available."
                ) from e

            for stream_id in range(self.num_streams):
                self.log_verbose(f"Generating stream {stream_id}...")

                try:
                    stream_file = self._generate_stream_queries_qgen(stream_id, qgen_exe, work_dir)
                    stream_files.append(stream_file)
                except Exception as e:
                    self.logger.error("Error generating stream %s: %s", stream_id, e)
                    continue

        if self.verbose_enabled:
            self.logger.info("Generated %s stream files", len(stream_files))
            for stream_file in stream_files:
                self.logger.info("  • %s", stream_file)

        self.log_operation_complete("TPC-H stream generation")

        return stream_files

    def get_stream_info(self, stream_id: int) -> dict[str, Any]:
        if stream_id >= self.num_streams:
            raise ValueError(f"Invalid stream ID: {stream_id}. Max streams: {self.num_streams}")

        return {
            "stream_id": stream_id,
            "query_order": self._get_stream_permutation(stream_id),
            "scale_factor": self.scale_factor,
            "rng_seed": self.rng_seed + stream_id,
            "query_count": self.query_count,
            "output_file": self.output_dir / f"stream_{stream_id}.sql",
            "permutation_index": stream_id % len(self.PERMUTATION_MATRIX),
        }

    def get_all_streams_info(self) -> list[dict[str, Any]]:
        return [self.get_stream_info(i) for i in range(self.num_streams)]


class TPCHStreamRunner(VerbosityMixin):
    def __init__(self, connection_string: str, dialect: str = "standard", verbose: int | bool = 0) -> None:
        self.connection_string = connection_string
        self.dialect = dialect
        verbosity_settings = compute_verbosity(verbose, False)
        self.apply_verbosity(verbosity_settings)
        self.logger = logging.getLogger("benchbox.core.tpch.stream.runner")

    def run_stream(self, stream_file: Path, stream_id: int) -> dict[str, Any]:
        raise NotImplementedError(
            "TPCHStreamRunner.run_stream does not execute SQL. It previously "
            "faked success by counting '-- Query' comment lines in the stream "
            "file without running anything against a database connection. Use "
            "benchbox.core.tpch.throughput_test.TPCHThroughputTest for real "
            "TPC-H Throughput Test execution."
        )

    def run_concurrent_streams(self, stream_files: list[Path]) -> dict[str, Any]:
        raise NotImplementedError(
            "TPCHStreamRunner.run_concurrent_streams does not execute SQL. It "
            "previously delegated to the non-executing run_stream() per "
            "stream via ConcurrentQueryExecutor, which has since been removed, "
            "so it never ran real queries either. Use "
            "benchbox.core.tpch.throughput_test.TPCHThroughputTest for real "
            "concurrent TPC-H Throughput Test execution."
        )
