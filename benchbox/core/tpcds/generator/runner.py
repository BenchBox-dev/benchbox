from __future__ import annotations

import concurrent.futures
import os
import platform
import shutil
import subprocess
from pathlib import Path

from benchbox.core.tpcds.c_tools import tpcds_option
from benchbox.utils.filesystem_flush import flush_filesystem
from benchbox.utils.printing import emit
from benchbox.utils.tpc_compilation import CompilationStatus, ensure_tpc_binaries


class DsdgenRunnerMixin:
    def _find_or_build_dsdgen(self) -> Path:
        import logging

        logger = logging.getLogger(__name__)

        results = ensure_tpc_binaries(["dsdgen"], auto_compile=True)
        dsdgen_result = results.get("dsdgen")

        if (
            dsdgen_result
            and dsdgen_result.status
            in [
                CompilationStatus.SUCCESS,
                CompilationStatus.NOT_NEEDED,
                CompilationStatus.PRECOMPILED,
            ]
            and dsdgen_result.binary_path
            and dsdgen_result.binary_path.exists()
        ):
            if self.verbose:
                emit(f"Using dsdgen binary: {dsdgen_result.binary_path}")
            logger.info(f"Using dsdgen binary: {dsdgen_result.binary_path}")
            return dsdgen_result.binary_path

        system = platform.system().lower()
        dsdgen_exe = self.dsdgen_path / "dsdgen.exe" if system == "windows" else self.dsdgen_path / "dsdgen"

        if dsdgen_exe.exists():
            if self.verbose:
                emit(f"Using existing dsdgen executable: {dsdgen_exe}")
            if os.name != "nt" and not os.access(dsdgen_exe, os.X_OK):
                raise PermissionError(f"dsdgen executable at {dsdgen_exe} is not executable")
            return dsdgen_exe

        error_msg = f"dsdgen binary required but not found at {dsdgen_exe}."
        if dsdgen_result and dsdgen_result.error_message:
            error_msg += f" Auto-compilation failed: {dsdgen_result.error_message}"
        error_msg += " TPC-DS requires the compiled dsdgen tool to function."

        raise RuntimeError(error_msg)

    def _run_dsdgen_native(self, output_dir: Path) -> None:
        tpcds_idx = self.dsdgen_path / "tpcds.idx"
        if tpcds_idx.exists():
            shutil.copy2(tpcds_idx, output_dir / "tpcds.idx")

        for data_file in [
            "tpcds.dst",
            "english.dst",
            "names.dst",
            "streets.dst",
            "cities.dst",
            "fips.dst",
            "items.dst",
            "scaling.dst",
        ]:
            src_file = self.dsdgen_path / data_file
            if src_file.exists():
                shutil.copy2(src_file, output_dir / data_file)

        if self.parallel > 1:
            try:
                self._run_parallel_dsdgen(output_dir)
            except RuntimeError as e:
                raise RuntimeError(f"Parallel TPC-DS generation failed: {e}") from e
        else:
            self._run_single_threaded_dsdgen(output_dir)

    def _run_single_threaded_dsdgen(self, output_dir: Path) -> None:
        if self.should_use_compression():
            self._run_streaming_dsdgen(output_dir)
        else:
            self._run_file_based_dsdgen(output_dir)

    def _run_streaming_dsdgen(self, output_dir: Path) -> None:
        self._copy_distribution_files(output_dir)

        parent_table_names = [
            "call_center",
            "catalog_page",
            "catalog_sales",
            "customer",
            "customer_address",
            "customer_demographics",
            "date_dim",
            "household_demographics",
            "income_band",
            "inventory",
            "item",
            "promotion",
            "reason",
            "ship_mode",
            "store",
            "store_sales",
            "time_dim",
            "warehouse",
            "web_page",
            "web_sales",
            "web_site",
        ]

        for table_name in parent_table_names:
            self._generate_table_with_streaming(output_dir, table_name)

        self._generate_single_table_streaming(output_dir, "dbgen_version")

    def _run_file_based_dsdgen(self, output_dir: Path) -> None:
        cmd = [
            str(self.dsdgen_exe),
            tpcds_option("verbose"),
            tpcds_option("force"),
            tpcds_option("terminate"),
            "n",
            tpcds_option("scale"),
            str(self.scale_factor),
        ]

        try:
            self._copy_distribution_files(output_dir)

            env = os.environ.copy()
            subprocess.run(
                cmd,
                cwd=output_dir,
                check=True,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=None if self.verbose else subprocess.PIPE,
            )

            flush_filesystem()

            import time

            time.sleep(0.2)

            if self.verbose:
                emit(f"Files after dsdgen: {list(output_dir.glob('*.dat'))}")

        except subprocess.CalledProcessError as e:
            error_msg = f"Failed to generate TPC-DS data with exit code {e.returncode}"
            if e.stderr:
                error_msg += f": {e.stderr}"
            if e.stdout and self.verbose:
                error_msg += f"\nOutput: {e.stdout}"
            raise RuntimeError(error_msg) from e

    def _run_parallel_dsdgen(self, output_dir: Path) -> None:
        if self.should_use_compression():
            self._run_parallel_streaming_dsdgen(output_dir)
        else:
            self._run_parallel_file_based_dsdgen(output_dir)

    def _run_parallel_streaming_dsdgen(self, output_dir: Path) -> None:
        self._copy_distribution_files(output_dir)

        parent_table_names = [
            "call_center",
            "catalog_page",
            "catalog_sales",
            "customer",
            "customer_address",
            "customer_demographics",
            "date_dim",
            "household_demographics",
            "income_band",
            "inventory",
            "item",
            "promotion",
            "reason",
            "ship_mode",
            "store",
            "store_sales",
            "time_dim",
            "warehouse",
            "web_page",
            "web_sales",
            "web_site",
        ]

        def generate_table_chunk(args):
            table_name, chunk_id = args

            child_tables = {
                "catalog_sales": ["catalog_returns"],
                "store_sales": ["store_returns"],
                "web_sales": ["web_returns"],
            }

            if table_name in child_tables:
                self._generate_parent_table_chunk_with_children(
                    output_dir, table_name, chunk_id, child_tables[table_name]
                )
            else:
                self._generate_single_table_chunk_streaming(output_dir, table_name, chunk_id)

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.parallel) as executor:
            futures = []
            for table_name in parent_table_names:
                for chunk_id in range(1, self.parallel + 1):
                    future = executor.submit(generate_table_chunk, (table_name, chunk_id))
                    futures.append(future)

            errors = []
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    errors.append(str(e))

            if errors:
                error_summary = f"TPC-DS parallel generation failed with {len(errors)} errors:\n"
                error_summary += "\n".join(f"  - {err}" for err in errors[:5])
                if len(errors) > 5:
                    error_summary += f"\n  ... and {len(errors) - 5} more errors"
                raise RuntimeError(error_summary)

        self._generate_single_table_streaming(output_dir, "dbgen_version")

    def _run_parallel_file_based_dsdgen(self, output_dir: Path) -> None:

        def generate_chunk(chunk_id: int) -> None:
            cmd = [
                str(self.dsdgen_exe),
                tpcds_option("verbose"),
                tpcds_option("force"),
                tpcds_option("terminate"),
                "n",
                tpcds_option("scale"),
                str(self.scale_factor),
                tpcds_option("child"),
                str(chunk_id),
                tpcds_option("parallel"),
                str(self.parallel),
            ]

            try:
                env = os.environ.copy()
                subprocess.run(
                    cmd,
                    cwd=output_dir,
                    check=True,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=None if self.verbose else subprocess.PIPE,
                )
            except subprocess.CalledProcessError as e:
                error_msg = f"Failed to generate TPC-DS data chunk {chunk_id} with exit code {e.returncode}"
                if e.stderr:
                    error_msg += f": {e.stderr}"
                raise RuntimeError(error_msg) from e

        self._copy_distribution_files(output_dir)

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.parallel) as executor:
            futures = []
            for chunk_id in range(1, self.parallel + 1):
                future = executor.submit(generate_chunk, chunk_id)
                futures.append(future)

            errors = []
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    errors.append(str(e))

            if errors:
                error_summary = f"TPC-DS parallel file-based generation failed with {len(errors)} errors:\n"
                error_summary += "\n".join(f"  - {err}" for err in errors[:5])
                if len(errors) > 5:
                    error_summary += f"\n  ... and {len(errors) - 5} more errors"
                raise RuntimeError(error_summary)


__all__ = ["DsdgenRunnerMixin"]
