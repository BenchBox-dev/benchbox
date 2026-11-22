from __future__ import annotations

import contextlib
import os
import subprocess
from pathlib import Path

from benchbox.core.exceptions import RunTimeoutError
from benchbox.core.tpcds.c_tools import tpcds_option
from benchbox.utils.printing import emit
from benchbox.utils.run_deadline import current_run_deadline, run_child, stream_child


class StreamingGenerationMixin:
    def _generate_table_with_streaming(self, output_dir: Path, table_name: str) -> None:
        child_tables = {
            "catalog_sales": ["catalog_returns"],
            "store_sales": ["store_returns"],
            "web_sales": ["web_returns"],
        }

        if table_name in child_tables:
            self._generate_parent_table_with_children(output_dir, table_name, child_tables[table_name])
        else:
            self._generate_single_table_streaming(output_dir, table_name)

    def _generate_single_table_streaming(self, output_dir: Path, table_name: str) -> None:
        cmd = [
            str(self.dsdgen_exe),
            tpcds_option("verbose") if self.verbose else tpcds_option("quiet"),
            tpcds_option("force"),
            tpcds_option("terminate"),
            "n",
            tpcds_option("scale"),
            str(self.scale_factor),
            tpcds_option("table"),
            table_name,
        ]
        expected_filename = f"{table_name}.dat"
        dat_file = output_dir / expected_filename
        compressed_filename = self.get_compressed_filename(expected_filename)
        compressed_path = output_dir / compressed_filename
        try:
            env = os.environ.copy()
            self._copy_distribution_files(output_dir)
            run_child(
                cmd,
                tool="dsdgen",
                deadline=current_run_deadline(),
                cwd=output_dir,
                check=True,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=None if self.verbose else subprocess.PIPE,
            )
            if dat_file.exists() and self._is_valid_data_file(dat_file):
                row_count = 0

                if dat_file.resolve() == compressed_path.resolve():
                    with open(dat_file, encoding="utf-8") as src:
                        for line in src:
                            row_count += 1
                else:
                    with (
                        open(dat_file, encoding="utf-8") as src,
                        self.open_output_file(compressed_path, mode="wt") as dst,
                    ):
                        for line in src:
                            dst.write(line)
                            row_count += 1
                if dat_file.resolve() != compressed_path.resolve():
                    with contextlib.suppress(OSError):
                        dat_file.unlink()
                if self.verbose:
                    emit(f"✓ Generated and compressed {table_name} -> {compressed_path.name}")
                with self._manifest_lock:
                    self._manifest_entries.setdefault(table_name, []).append(
                        {
                            "path": compressed_path.name,
                            "size_bytes": compressed_path.stat().st_size if compressed_path.exists() else 0,
                            "row_count": row_count,
                        }
                    )
            else:
                if dat_file.exists():
                    with contextlib.suppress(OSError):
                        dat_file.unlink()
                if self.verbose:
                    emit(f"○ Skipped {table_name} (no data at scale factor {self.scale_factor})")
        except RunTimeoutError:
            raise
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to generate TPC-DS table {table_name} with exit code {e.returncode}") from e
        except Exception as e:
            raise RuntimeError(f"Failed to generate TPC-DS table {table_name}: {e}") from e

    def _generate_parent_table_with_children(
        self, output_dir: Path, parent_table: str, child_tables: list[str]
    ) -> None:
        try:
            env = os.environ.copy()

            if self.verbose:
                tables_str = f"{parent_table} + {', '.join(child_tables)}"
                emit(f"Generating {tables_str} with streaming compression...")

            cmd = [
                str(self.dsdgen_exe),
                tpcds_option("verbose") if self.verbose else tpcds_option("quiet"),
                tpcds_option("force"),
                tpcds_option("terminate"),
                "n",
                tpcds_option("scale"),
                str(self.scale_factor),
                tpcds_option("table"),
                parent_table,
            ]

            run_child(
                cmd,
                tool="dsdgen",
                deadline=current_run_deadline(),
                cwd=output_dir,
                check=True,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=None if self.verbose else subprocess.PIPE,
            )

            all_tables = [parent_table] + child_tables
            files_processed = 0

            for table_name in all_tables:
                dat_file = output_dir / f"{table_name}.dat"
                if dat_file.exists() and self._is_valid_data_file(dat_file):
                    expected_filename = f"{table_name}.dat"
                    compressed_name = self.get_compressed_filename(expected_filename)
                    compressed_path = output_dir / compressed_name
                    row_count = 0
                    try:
                        with (
                            open(dat_file, encoding="utf-8") as src,
                            self.open_output_file(compressed_path, mode="wt") as dst,
                        ):
                            for line in src:
                                dst.write(line)
                                row_count += 1
                        with contextlib.suppress(OSError):
                            dat_file.unlink()
                        files_processed += 1
                        if self.verbose:
                            emit(f"✓ Generated and compressed {table_name} -> {compressed_path.name}")
                        with self._manifest_lock:
                            self._manifest_entries.setdefault(table_name, []).append(
                                {
                                    "path": compressed_path.name,
                                    "size_bytes": compressed_path.stat().st_size if compressed_path.exists() else 0,
                                    "row_count": row_count,
                                }
                            )
                    except Exception as e:
                        raise RuntimeError(f"Failed to compress {dat_file.name}: {e}") from e
                elif self.verbose and dat_file.exists():
                    dat_file.unlink()
                    emit(f"○ Skipped {table_name} (no data at scale factor {self.scale_factor})")

            if files_processed == 0 and self.verbose:
                tables_str = f"{parent_table} + {', '.join(child_tables)}"
                emit(f"○ Skipped {tables_str} (no data at scale factor {self.scale_factor})")

        except RunTimeoutError:
            raise
        except subprocess.CalledProcessError as e:
            error_msg = f"Failed to generate TPC-DS table {parent_table} with exit code {e.returncode}"
            if e.stderr:
                error_msg += f": {e.stderr.decode() if isinstance(e.stderr, bytes) else e.stderr}"
            raise RuntimeError(error_msg) from e
        except Exception as e:
            raise RuntimeError(f"Failed to generate TPC-DS table {parent_table}: {e}") from e

    def _generate_single_table_chunk_streaming(self, output_dir: Path, table_name: str, chunk_id: int) -> None:
        cmd = [
            str(self.dsdgen_exe),
            tpcds_option("verbose") if self.verbose else tpcds_option("quiet"),
            tpcds_option("force"),
            tpcds_option("terminate"),
            "n",
            tpcds_option("scale"),
            str(self.scale_factor),
            tpcds_option("table"),
            table_name,
            tpcds_option("child"),
            str(chunk_id),
            tpcds_option("parallel"),
            str(self.parallel),
            tpcds_option("filter"),
            "Y",
        ]

        expected_filename = f"{table_name}_{chunk_id}_{self.parallel}.dat"
        compressed_filename = self.get_compressed_filename(expected_filename)
        output_file = output_dir / compressed_filename

        try:
            env = os.environ.copy()

            if self.verbose:
                emit(f"Generating {table_name} chunk {chunk_id}/{self.parallel} with streaming compression...")

            with stream_child(
                cmd,
                tool="dsdgen",
                deadline=current_run_deadline(),
                cwd=output_dir,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL if not self.verbose else None,
            ) as process:
                row_count, bytes_written = self._stream_process_output(process, output_file)

                process.wait()

                potential_dat_file = output_dir / expected_filename
                self._handle_chunk_result(
                    process,
                    cmd,
                    table_name,
                    chunk_id,
                    compressed_filename,
                    output_file,
                    potential_dat_file,
                    row_count,
                    bytes_written,
                )

        except RunTimeoutError:
            raise
        except subprocess.CalledProcessError as e:
            error_msg = f"Failed to generate TPC-DS table {table_name} chunk {chunk_id} with exit code {e.returncode}"
            if e.stderr:
                error_msg += f": {e.stderr.decode() if isinstance(e.stderr, bytes) else e.stderr}"
            raise RuntimeError(error_msg) from e
        except Exception as e:
            raise RuntimeError(f"Failed to generate TPC-DS table {table_name} chunk {chunk_id}: {e}") from e

    def _stream_process_output(self, process, output_file: Path) -> tuple[int, int]:
        row_count = 0
        bytes_written = 0
        chunk_size = 65536
        last_chunk = b""

        first_chunk = process.stdout.read(chunk_size)
        if first_chunk:
            with self.open_output_file(output_file, mode="wb") as f:
                f.write(first_chunk)
                bytes_written += len(first_chunk)
                row_count += first_chunk.count(b"\n")
                last_chunk = first_chunk

                for chunk in iter(lambda: process.stdout.read(chunk_size), b""):
                    f.write(chunk)
                    bytes_written += len(chunk)
                    row_count += chunk.count(b"\n")
                    last_chunk = chunk

            if last_chunk and not last_chunk.endswith(b"\n"):
                row_count += 1

        return row_count, bytes_written

    def _handle_chunk_result(
        self,
        process,
        cmd: list[str],
        table_name: str,
        chunk_id: int,
        compressed_filename: str,
        output_file: Path,
        potential_dat_file: Path,
        row_count: int,
        bytes_written: int,
    ) -> None:
        if bytes_written > 0:
            if potential_dat_file.exists():
                potential_dat_file.unlink()

            if process.returncode != 0:
                with contextlib.suppress(OSError):
                    output_file.unlink()
                stderr_output = ""
                if process.stderr:
                    stderr_output = process.stderr.read().decode("utf-8", errors="replace")
                raise subprocess.CalledProcessError(process.returncode, cmd, stderr=stderr_output)

            if self.verbose:
                emit(f"✓ Generated {table_name} chunk {chunk_id}/{self.parallel} -> {compressed_filename}")

            size_bytes = output_file.stat().st_size if output_file.exists() else 0
            with self._manifest_lock:
                self._manifest_entries.setdefault(table_name, []).append(
                    {"path": output_file.name, "size_bytes": size_bytes, "row_count": row_count}
                )
        else:
            if potential_dat_file.exists():
                potential_dat_file.unlink()

            if process.returncode != 0:
                stderr_output = ""
                if process.stderr:
                    stderr_output = process.stderr.read().decode("utf-8", errors="replace")
                raise subprocess.CalledProcessError(process.returncode, cmd, stderr=stderr_output)

            if self.verbose:
                emit(f"○ Skipped {table_name} chunk {chunk_id}/{self.parallel} (no data in this chunk)")

    def _generate_parent_table_chunk_with_children(
        self,
        output_dir: Path,
        parent_table: str,
        chunk_id: int,
        child_tables: list[str],
    ) -> None:
        try:
            env = os.environ.copy()

            if self.verbose:
                tables_str = f"{parent_table} + {', '.join(child_tables)}"
                emit(f"Generating {tables_str} chunk {chunk_id}/{self.parallel} with file-then-compress...")

            cmd = [
                str(self.dsdgen_exe),
                tpcds_option("verbose") if self.verbose else tpcds_option("quiet"),
                tpcds_option("force"),
                tpcds_option("terminate"),
                "n",
                tpcds_option("scale"),
                str(self.scale_factor),
                tpcds_option("table"),
                parent_table,
                tpcds_option("child"),
                str(chunk_id),
                tpcds_option("parallel"),
                str(self.parallel),
            ]

            run_child(
                cmd,
                tool="dsdgen",
                deadline=current_run_deadline(),
                cwd=output_dir,
                check=True,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=None if self.verbose else subprocess.PIPE,
            )

            all_tables = [parent_table] + child_tables
            files_processed = 0

            for table_name in all_tables:
                expected_filename = f"{table_name}_{chunk_id}_{self.parallel}.dat"
                dat_file = output_dir / expected_filename

                if dat_file.exists() and self._is_valid_data_file(dat_file):
                    if self.should_use_compression():
                        row_count = 0
                        with open(dat_file, "rb") as f:
                            row_count = sum(1 for _ in f)

                        compressed_file = self.compress_existing_file(dat_file, remove_original=True)
                        files_processed += 1

                        with self._manifest_lock:
                            self._manifest_entries.setdefault(table_name, []).append(
                                {
                                    "path": compressed_file.name,
                                    "size_bytes": compressed_file.stat().st_size if compressed_file.exists() else 0,
                                    "row_count": row_count,
                                }
                            )

                        if self.verbose:
                            emit(
                                f"✓ Generated and compressed {table_name} chunk {chunk_id}/{self.parallel} -> {compressed_file.name}"
                            )
                    else:
                        row_count = 0
                        with open(dat_file, "rb") as f:
                            row_count = sum(1 for _ in f)

                        files_processed += 1

                        with self._manifest_lock:
                            self._manifest_entries.setdefault(table_name, []).append(
                                {
                                    "path": dat_file.name,
                                    "size_bytes": dat_file.stat().st_size if dat_file.exists() else 0,
                                    "row_count": row_count,
                                }
                            )

                        if self.verbose:
                            emit(f"✓ Generated {table_name} chunk {chunk_id}/{self.parallel} -> {expected_filename}")
                elif dat_file.exists():
                    dat_file.unlink()
                    if self.verbose:
                        emit(f"○ Skipped {table_name} chunk {chunk_id}/{self.parallel} (no data in this chunk)")

            if files_processed == 0 and self.verbose:
                tables_str = f"{parent_table} + {', '.join(child_tables)}"
                emit(f"○ Skipped {tables_str} chunk {chunk_id}/{self.parallel} (no data in this chunk)")

        except RunTimeoutError:
            raise
        except subprocess.CalledProcessError as e:
            error_msg = f"Failed to generate TPC-DS table {parent_table} chunk {chunk_id} with exit code {e.returncode}"
            if e.stderr:
                error_msg += f": {e.stderr.decode() if isinstance(e.stderr, bytes) else e.stderr}"
            raise RuntimeError(error_msg) from e
        except Exception as e:
            raise RuntimeError(f"Failed to generate TPC-DS table {parent_table} chunk {chunk_id}: {e}") from e


__all__ = ["StreamingGenerationMixin"]
