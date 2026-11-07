from __future__ import annotations

import bz2
import contextlib
import csv
import gzip
import hashlib
import io
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Union

from benchbox.core.data_fetch.locking import interprocess_lock
from benchbox.core.tpch.generator import TPCHDataGenerator
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.cloud_storage import CloudStorageGeneratorMixin, create_path_handler, is_cloud_path
from benchbox.utils.compression_mixin import CompressionMixin
from benchbox.utils.datagen_manifest import DataGenerationManifest, require_manifest_files, resolve_compression_metadata
from benchbox.utils.file_format import detect_compression
from benchbox.utils.path_utils import get_benchmark_runs_datagen_path
from benchbox.utils.verbosity import VerbosityMixin, compute_verbosity

if TYPE_CHECKING:
    import pyarrow as pa
    from cloudpathlib import CloudPath

PathLike = Union[Path, "CloudPath"]


class PrimitivesDataGeneratorBase(CompressionMixin, CloudStorageGeneratorMixin, VerbosityMixin):
    _benchmark_name: str
    _display_name: str
    _auxiliary_dir: str
    _logger_name: str

    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Union[str, Path] | None = None,
        verbose: int | bool = 0,
        quiet: bool = False,
        parallel: int = 1,
        force_regenerate: bool = False,
        compress_data: bool = False,
        compression_type: str | None = None,
        compression_level: int | None = None,
        **kwargs,
    ) -> None:
        compression_kwargs = {}
        if compress_data:
            compression_kwargs["compress_data"] = compress_data
        if compression_type is not None:
            compression_kwargs["compression_type"] = compression_type
        if compression_level is not None:
            compression_kwargs["compression_level"] = compression_level

        super().__init__(**compression_kwargs, **kwargs)

        self.scale_factor = scale_factor

        if output_dir is None:
            output_dir = get_benchmark_runs_datagen_path("tpch", scale_factor)

        self.output_dir = create_path_handler(output_dir)

        verbosity_settings = compute_verbosity(verbose, quiet)
        self.apply_verbosity(verbosity_settings)
        self.logger = logging.getLogger(self._logger_name)

        self.parallel = parallel
        self.force_regenerate = force_regenerate

        tpch_kwargs = {
            "scale_factor": scale_factor,
            "output_dir": self.output_dir,
            "verbose": verbose,
            "quiet": quiet,
            "parallel": parallel,
            "force_regenerate": force_regenerate,
        }
        if compress_data:
            tpch_kwargs["compress_data"] = compress_data
        if compression_type is not None:
            tpch_kwargs["compression_type"] = compression_type
        if compression_level is not None:
            tpch_kwargs["compression_level"] = compression_level

        self.tpch_generator = TPCHDataGenerator(**tpch_kwargs, **kwargs)

        self.files_dir = self.output_dir / self._auxiliary_dir
        self.staging_data: dict[str, Path] = {}

    def generate(self) -> dict[str, Path]:
        with interprocess_lock(self._generation_lock_target()):
            return self._generate_locked()

    def _generation_lock_target(self) -> Path:
        if not is_cloud_path(self.output_dir):
            return Path(self.output_dir) / f".{self._auxiliary_dir}.generation"
        digest = hashlib.sha256(str(self.output_dir).encode("utf-8")).hexdigest()
        return Path(tempfile.gettempdir()) / "benchbox-primitives-locks" / digest

    def _generate_locked(self) -> dict[str, Path]:
        self.log_verbose(f"Generating {self._display_name} data at scale factor {self.scale_factor}...")

        tpch_tables = self.tpch_generator.generate()
        self.log_verbose(f"TPC-H base data available: {len(tpch_tables)} tables")

        staging_tables = self._generate_staging_table_files()
        self.log_verbose(f"Generated {len(staging_tables)} staging table files")

        bulk_files_exist = self.check_bulk_load_files_exist()
        if not bulk_files_exist or self.force_regenerate:
            if not bulk_files_exist:
                self.log_verbose("Bulk load files missing - will generate after acquiring lock")
            elif self.force_regenerate:
                self.log_verbose("Force regenerate enabled - will regenerate bulk load files")

            if self._acquire_bulk_load_lock(timeout=300):
                try:
                    if not self.check_bulk_load_files_exist() or self.force_regenerate:
                        self.log_verbose("Lock acquired - starting bulk load file generation...")
                        bulk_files = self.generate_bulk_load_files()
                        self.log_verbose(f"✅ Generated {len(bulk_files)} bulk load files")
                    else:
                        self.log_verbose("✅ Files generated by another process while waiting for lock - skipping")
                finally:
                    self._release_bulk_load_lock()
            else:
                self.log_verbose(
                    "⚠️ Warning: Could not acquire lock for bulk load generation after 5 minutes. "
                    "Another process may be generating files, or a stale lock exists. "
                    "Some BULK_LOAD operations may fail if files are missing."
                )
        else:
            self.log_verbose("✅ Bulk load files already exist - skipping generation")

        all_tables = {**tpch_tables, **staging_tables}
        self._write_manifest(all_tables)

        return all_tables

    def _generate_staging_table_files(self) -> dict[str, Path]:
        staging_files: dict[str, Path] = {}

        orders_files = sorted(self.output_dir.glob("orders.tbl*"))
        if orders_files:
            self.log_verbose("Generating orders_stage.tbl from orders data...")
            orders_stage_path = self._write_staging_file("orders_stage.tbl", "orders.tbl*")
            staging_files["orders_stage"] = orders_stage_path
        else:
            self.log_verbose("No orders data found, skipping orders_stage.tbl generation")

        lineitem_files = sorted(self.output_dir.glob("lineitem.tbl*"))
        if lineitem_files:
            self.log_verbose("Generating lineitem_stage.tbl from lineitem data...")
            lineitem_stage_path = self._write_staging_file("lineitem_stage.tbl", "lineitem.tbl*")
            staging_files["lineitem_stage"] = lineitem_stage_path
        else:
            self.log_verbose("No lineitem data found, skipping lineitem_stage.tbl generation")

        return staging_files

    @contextlib.contextmanager
    def _open_tbl_binary(self, file_path: Path):
        compression = detect_compression(file_path)
        if compression == "zstd":
            import zstandard as zstd

            with open(file_path, "rb") as raw, zstd.ZstdDecompressor().stream_reader(raw) as stream:
                yield stream
        elif compression == "gzip":
            with gzip.open(file_path, "rb") as stream:
                yield stream
        elif compression == "bzip2":
            with bz2.open(file_path, "rb") as stream:
                yield stream
        elif compression == "xz":
            import lzma

            with lzma.open(file_path, "rb") as stream:
                yield stream
        else:
            with open(file_path, "rb") as stream:
                yield stream

    def _write_staging_file(self, filename: str, file_pattern: str, limit: int = 1_000_000) -> PathLike:
        import pyarrow as pa
        import pyarrow.csv as pacsv

        output_path = self.output_dir / filename
        write_options = pacsv.WriteOptions(include_header=False, delimiter="|", quoting_style="none")
        parse_options = pacsv.ParseOptions(delimiter="|", quote_char=False, ignore_empty_lines=False)
        written = 0

        with open(output_path, "wb") as sink:
            for file_path in sorted(self.output_dir.glob(file_pattern)):
                if written >= limit:
                    break
                with self._open_tbl_binary(file_path) as stream:
                    first_line = stream.readline().rstrip(b"\r\n")
                if not first_line:
                    continue
                names = [f"c{index}" for index in range(first_line.count(b"|") + 1)]
                keep = names[:-1] if first_line.endswith(b"|") else names
                convert_options = pacsv.ConvertOptions(
                    column_types=dict.fromkeys(names, pa.string()), include_columns=keep
                )
                with self._open_tbl_binary(file_path) as stream:
                    reader = pacsv.open_csv(
                        stream,
                        read_options=pacsv.ReadOptions(column_names=names),
                        parse_options=parse_options,
                        convert_options=convert_options,
                    )
                    with pacsv.CSVWriter(sink, reader.schema, write_options=write_options) as writer:
                        for batch in reader:
                            batch = batch.slice(0, limit - written)
                            writer.write_batch(batch)
                            written += batch.num_rows
                            if written >= limit:
                                break

        self.log_verbose(f"Generated {output_path} ({written} rows)")

        if self.tpch_generator.should_use_compression():
            compressor = self.tpch_generator.get_compressor()
            compressed_path = compressor.compress_file(output_path)
            output_path.unlink()
            self.log_verbose(f"Compressed {filename} to {compressed_path.name}")
            return compressed_path

        return output_path

    def _write_manifest(self, table_paths: dict[str, Path]) -> None:
        if not table_paths:
            return

        manifest = DataGenerationManifest(
            output_dir=self.output_dir,
            benchmark=self._benchmark_name,
            scale_factor=self.scale_factor,
            compression=resolve_compression_metadata(self.tpch_generator),
            parallel=self.parallel,
            seed=getattr(self.tpch_generator, "seed", None),
        )

        file_count = 0
        for table_name, file_path in table_paths.items():
            if table_name in ["orders_stage", "orders"]:
                expected_rows = self._get_tpch_row_count("orders")
            elif table_name in ["lineitem_stage", "lineitem"]:
                expected_rows = self._get_tpch_row_count("lineitem")
            elif table_name in [
                "orders_new",
                "orders_summary",
                "lineitem_enriched",
                "bulk_load_target",
                "write_ops_log",
                "batch_metadata",
            ]:
                continue
            else:
                expected_rows = self._get_tpch_row_count(table_name)

            if isinstance(file_path, list):
                chunk_count = len(file_path)
                rows_per_chunk = expected_rows // chunk_count if chunk_count else expected_rows
                remainder = expected_rows - rows_per_chunk * chunk_count
                for i, chunk_file in enumerate(file_path):
                    rc = rows_per_chunk + (remainder if i == chunk_count - 1 else 0)
                    manifest.add_entry(table_name, chunk_file, row_count=rc)
                file_count += chunk_count
            else:
                manifest.add_entry(table_name, file_path, row_count=expected_rows)
                file_count += 1

        require_manifest_files(file_count, label=self._display_name, output_dir=self.output_dir)
        manifest.write()
        self.log_verbose(f"Wrote manifest with {len(table_paths)} tables")

    def _get_tpch_row_count(self, table_name: str) -> int:
        base_row_counts = {
            "region": 5,
            "nation": 25,
            "supplier": 10_000,
            "customer": 150_000,
            "part": 200_000,
            "partsupp": 800_000,
            "orders": 1_500_000,
            "lineitem": 6_001_215,
        }

        base = base_row_counts.get(table_name.lower(), 0)
        if table_name.lower() in {"nation", "region"}:
            return base
        return max(0, int(round(base * float(self.scale_factor))))

    def _acquire_bulk_load_lock(self, timeout: int = 300) -> bool:
        self.files_dir.mkdir(parents=True, exist_ok=True)

        lock_file = self.files_dir / ".bulk_load_generation.lock"
        start_time = mono_time()

        while elapsed_seconds(start_time) < timeout:
            try:
                fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"pid:{os.getpid()}\n".encode())
                os.close(fd)
                self._lock_file = lock_file
                return True
            except FileExistsError:
                time.sleep(0.5)

        if lock_file.exists():
            try:
                with open(lock_file, encoding="utf-8") as f:
                    lock_content = f.read().strip()

                lock_pid = None
                if lock_content.startswith("pid:"):
                    try:
                        lock_pid = int(lock_content.split(":")[1])
                    except (IndexError, ValueError):
                        pass

                is_stale = False
                if lock_pid is not None and not self._is_process_running(lock_pid):
                    self.log_verbose(f"Lock held by dead process (PID {lock_pid}) - removing")
                    is_stale = True
                else:
                    age = time.time() - lock_file.stat().st_mtime
                    if age > 300:
                        self.log_verbose(f"Removing stale lock file (age: {age:.0f}s, PID: {lock_pid})")
                        is_stale = True

                if is_stale:
                    lock_file.unlink()
                    return self._acquire_bulk_load_lock(timeout=30)

            except Exception as e:
                self.log_verbose(f"Error checking stale lock: {e}")

        return False

    def _is_process_running(self, pid: int) -> bool:
        import sys

        try:
            if sys.platform == "win32":
                import ctypes

                PROCESS_QUERY_INFORMATION = 0x0400
                handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                    return True
                return False
            else:
                os.kill(pid, 0)
                return True
        except (OSError, AttributeError):
            return False

    def _release_bulk_load_lock(self) -> None:
        if hasattr(self, "_lock_file") and self._lock_file.exists():
            try:
                self._lock_file.unlink()
            except Exception as e:
                self.log_verbose(f"Warning: Failed to release lock: {e}")

    def check_bulk_load_files_exist(self) -> bool:
        expected_files = [
            "csv_small_1k.csv",
            "csv_medium_100k.csv",
            "csv_large_1m.csv",
            "parquet_small_1k.parquet",
            "parquet_medium_100k.parquet",
            "parquet_large_1m.parquet",
            "csv_with_errors.csv",
            "csv_with_nulls.csv",
            "csv_parallel_part1.csv",
            "csv_parallel_part2.csv",
            "csv_parallel_part3.csv",
            "csv_parallel_part4.csv",
        ]

        for filename in expected_files:
            file_path = self.files_dir / filename
            if not file_path.exists():
                self.log_verbose(f"Missing bulk load file: {filename}")
                return False

        metadata_file = self.files_dir / ".bulk_load_metadata.json"
        if metadata_file.exists():
            try:
                import json

                with open(metadata_file, encoding="utf-8") as f:
                    metadata = json.load(f)
                stored_sf = metadata.get("scale_factor")
                if stored_sf != self.scale_factor:
                    self.log_verbose(
                        f"Bulk load files scale factor mismatch: stored={stored_sf}, current={self.scale_factor}"
                    )
                    return False
            except Exception as e:
                self.log_verbose(f"Warning: Could not read metadata file: {e}")

        return True

    def generate_bulk_load_files(self) -> dict[str, Path]:
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.log_verbose(f"Bulk load files directory: {self.files_dir}")

        generated_files: dict[str, Path] = {}

        orders_files = sorted(self.output_dir.glob("orders.tbl*"))
        if not orders_files:
            self.log_verbose("No TPC-H orders data files found, skipping bulk load file generation")
            return generated_files

        self.log_verbose(f"Reading source data from {len(orders_files)} file(s)")
        rows = self._read_tbl_files("orders.tbl*", limit=1_000_000)

        size_configs = [
            ("small", 1_000),
            ("medium", 100_000),
            ("large", 1_000_000),
        ]

        for size_name, row_count in size_configs:
            subset = rows[:row_count]

            size_suffix = "1m" if row_count >= 1_000_000 else f"{row_count // 1000}k"
            csv_file = self._write_csv(f"csv_{size_name}_{size_suffix}.csv", subset)
            generated_files[f"csv_{size_name}_uncompressed"] = csv_file

            gz_file = self._compress_file(csv_file, "gzip")
            generated_files[f"csv_{size_name}_gzip"] = gz_file

            zst_file = self._compress_file(csv_file, "zstd")
            generated_files[f"csv_{size_name}_zstd"] = zst_file

            bz2_file = self._compress_file(csv_file, "bzip2")
            generated_files[f"csv_{size_name}_bzip2"] = bz2_file

        try:
            import pyarrow.parquet as pq

            for size_name, row_count in size_configs:
                subset = rows[:row_count]

                pa_table = self._rows_to_pyarrow_table(subset)

                size_suffix = "1m" if row_count >= 1_000_000 else f"{row_count // 1000}k"
                parquet_file = self.files_dir / f"parquet_{size_name}_{size_suffix}.parquet"
                pq.write_table(pa_table, parquet_file, compression="none")
                generated_files[f"parquet_{size_name}_uncompressed"] = parquet_file
                self.log_verbose(f"Generated {parquet_file}")

                parquet_snappy = self.files_dir / f"parquet_{size_name}_{size_suffix}_snappy.parquet"
                pq.write_table(pa_table, parquet_snappy, compression="snappy")
                generated_files[f"parquet_{size_name}_snappy"] = parquet_snappy
                self.log_verbose(f"Generated {parquet_snappy}")

                parquet_gzip = self.files_dir / f"parquet_{size_name}_{size_suffix}_gzip.parquet"
                pq.write_table(pa_table, parquet_gzip, compression="gzip")
                generated_files[f"parquet_{size_name}_gzip"] = parquet_gzip
                self.log_verbose(f"Generated {parquet_gzip}")

                parquet_zstd = self.files_dir / f"parquet_{size_name}_{size_suffix}_zstd.parquet"
                pq.write_table(pa_table, parquet_zstd, compression="zstd")
                generated_files[f"parquet_{size_name}_zstd"] = parquet_zstd
                self.log_verbose(f"Generated {parquet_zstd}")

        except ImportError:
            self.log_verbose("PyArrow not available, skipping Parquet file generation")

        special = self._generate_special_test_files(rows)
        generated_files.update(special)
        self.log_verbose(f"Generated {len(special)} special test files")

        self._write_bulk_load_metadata()

        self.log_verbose(f"Generated {len(generated_files)} total bulk load files")
        return generated_files

    def _write_bulk_load_metadata(self) -> None:
        import json
        from datetime import datetime, timezone

        metadata = {
            "scale_factor": self.scale_factor,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "file_count": len(list(self.files_dir.glob("*"))),
        }

        metadata_file = self.files_dir / ".bulk_load_metadata.json"
        try:
            with open(metadata_file, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)
            self.log_verbose(f"Wrote bulk load metadata: {metadata_file}")
        except Exception as e:
            self.log_verbose(f"Warning: Could not write metadata file: {e}")

    def _read_tbl_file(self, path: Path, limit: int = 1_000_000) -> list[tuple]:
        rows = []
        with open(path, encoding="utf-8") as f:
            reader = csv.reader(f, delimiter="|")
            for i, row in enumerate(reader):
                if i >= limit:
                    break
                if row and row[-1] == "":
                    row = row[:-1]
                rows.append(tuple(row))
        return rows

    def _read_tbl_files(self, file_pattern: str, limit: int = 1_000_000) -> list[tuple]:
        files = sorted(self.output_dir.glob(file_pattern))
        rows = []

        for file_path in files:
            if len(rows) >= limit:
                break

            compression = detect_compression(file_path)
            if compression == "zstd":
                try:
                    import zstandard as zstd

                    with open(file_path, "rb") as f_in:
                        dctx = zstd.ZstdDecompressor()
                        with dctx.stream_reader(f_in) as reader:
                            text_stream = io.TextIOWrapper(reader, encoding="utf-8")
                            rows.extend(self._parse_tbl_stream(text_stream, limit - len(rows)))
                except ImportError:
                    self.log_verbose(f"zstandard not available, skipping {file_path}")
                    continue
            elif compression == "gzip":
                with gzip.open(file_path, "rt", encoding="utf-8") as f:
                    rows.extend(self._parse_tbl_stream(f, limit - len(rows)))
            elif compression == "bzip2":
                with bz2.open(file_path, "rt", encoding="utf-8") as f:
                    rows.extend(self._parse_tbl_stream(f, limit - len(rows)))
            elif compression == "xz":
                import lzma

                with lzma.open(file_path, "rt", encoding="utf-8") as f:
                    rows.extend(self._parse_tbl_stream(f, limit - len(rows)))
            else:
                with open(file_path, encoding="utf-8") as f:
                    rows.extend(self._parse_tbl_stream(f, limit - len(rows)))

        return rows

    def _parse_tbl_stream(self, stream, limit: int) -> list[tuple]:
        rows = []
        reader = csv.reader(stream, delimiter="|")
        for i, row in enumerate(reader):
            if i >= limit:
                break
            if row and row[-1] == "":
                row = row[:-1]
            rows.append(tuple(row))
        return rows

    def _validate_filename(self, filename: str) -> str:
        if not filename or not filename.strip():
            raise ValueError("Filename cannot be empty")

        if "\x00" in filename:
            raise ValueError(f"Filename contains null byte: {filename!r}")

        basename = os.path.basename(filename)

        if not basename or basename in (".", ".."):
            raise ValueError(f"Invalid filename: {filename}")

        import re

        if not re.match(r"^[a-zA-Z0-9._-]+$", basename):
            raise ValueError(
                f"Filename contains invalid characters: {basename}. "
                "Only alphanumeric, dots, dashes, and underscores allowed."
            )

        if basename.startswith(".") and basename.count(".") == 1:
            raise ValueError(f"Hidden files not allowed: {basename}")

        return basename

    def _write_csv(self, filename: str, rows: list[tuple]) -> PathLike:
        safe_filename = self._validate_filename(filename)
        output_path = self.files_dir / safe_filename

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "o_orderkey",
                    "o_custkey",
                    "o_orderstatus",
                    "o_totalprice",
                    "o_orderdate",
                    "o_orderpriority",
                    "o_clerk",
                    "o_shippriority",
                    "o_comment",
                ]
            )
            writer.writerows(rows)

        self.log_verbose(f"Generated {output_path} ({len(rows)} rows)")
        return output_path

    def _compress_file(self, source_path: Path, compression: str) -> Path:
        if compression == "gzip":
            output_path = source_path.with_suffix(source_path.suffix + ".gz")
            with open(source_path, "rb") as f_in, gzip.open(output_path, "wb") as f_out:
                f_out.writelines(f_in)
        elif compression == "zstd":
            try:
                import zstandard as zstd

                output_path = source_path.with_suffix(source_path.suffix + ".zst")
                with open(source_path, "rb") as f_in, open(output_path, "wb") as f_out:
                    compressor = zstd.ZstdCompressor()
                    compressor.copy_stream(f_in, f_out)
            except ImportError:
                self.log_verbose("zstandard not available, skipping zstd compression")
                return source_path
        elif compression == "bzip2":
            output_path = source_path.with_suffix(source_path.suffix + ".bz2")
            with open(source_path, "rb") as f_in, bz2.open(output_path, "wb") as f_out:
                f_out.writelines(f_in)
        else:
            raise ValueError(f"Unsupported compression: {compression}")

        self.log_verbose(f"Compressed {source_path} to {output_path}")
        return output_path

    def _rows_to_pyarrow_table(self, rows: list[tuple]) -> pa.Table:
        import pyarrow as pa

        schema = pa.schema(
            [
                ("o_orderkey", pa.int64()),
                ("o_custkey", pa.int64()),
                ("o_orderstatus", pa.string()),
                ("o_totalprice", pa.float64()),
                ("o_orderdate", pa.string()),
                ("o_orderpriority", pa.string()),
                ("o_clerk", pa.string()),
                ("o_shippriority", pa.int64()),
                ("o_comment", pa.string()),
            ]
        )

        columns = list(zip(*rows))
        arrays = [
            pa.array([int(v) for v in columns[0]]),
            pa.array([int(v) for v in columns[1]]),
            pa.array(columns[2]),
            pa.array([float(v) for v in columns[3]]),
            pa.array(columns[4]),
            pa.array(columns[5]),
            pa.array(columns[6]),
            pa.array([int(v) for v in columns[7]]),
            pa.array(columns[8]),
        ]

        return pa.Table.from_arrays(arrays, schema=schema)

    _ORDERS_HEADER = [
        "o_orderkey",
        "o_custkey",
        "o_orderstatus",
        "o_totalprice",
        "o_orderdate",
        "o_orderpriority",
        "o_clerk",
        "o_shippriority",
        "o_comment",
    ]

    def _generate_special_test_files(self, rows: list[tuple]) -> dict[str, Path]:
        special_files: dict[str, Path] = {}

        special_files.update(self._generate_error_and_null_files(rows))
        special_files.update(self._generate_format_variant_files(rows))
        special_files.update(self._generate_parallel_load_files(rows))

        return special_files

    def _generate_error_and_null_files(self, rows: list[tuple]) -> dict[str, Path]:
        header = self._ORDERS_HEADER
        result: dict[str, Path] = {}

        error_file = self.files_dir / "csv_with_errors.csv"
        with open(error_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            good_rows_count = min(100, len(rows))
            for row in rows[:good_rows_count]:
                writer.writerow(row)
            if len(rows) > 100:
                writer.writerow([rows[100][0], rows[100][1]])
            if len(rows) > 101:
                writer.writerow([rows[101][0], "INVALID_NUMBER", rows[101][2]])
            if len(rows) > 102:
                for row in rows[102 : min(200, len(rows))]:
                    writer.writerow(row)
        result["csv_with_errors"] = error_file
        self.log_verbose(f"Generated {error_file}")

        null_file = self.files_dir / "csv_with_nulls.csv"
        with open(null_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            for i, row in enumerate(rows[:1000]):
                row_list = list(row)
                if i % 10 == 0:
                    row_list[8] = ""
                if i % 20 == 0:
                    row_list[6] = ""
                writer.writerow(row_list)
        result["csv_with_nulls"] = null_file
        self.log_verbose(f"Generated {null_file}")

        upsert_file = self.files_dir / "csv_upsert_data.csv"
        with open(upsert_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            for row in rows[:50]:
                row_list = list(row)
                try:
                    row_list[3] = str(float(row_list[3]) * 1.5)
                except (ValueError, IndexError):
                    pass
                writer.writerow(row_list)
        result["csv_upsert_data"] = upsert_file
        self.log_verbose(f"Generated {upsert_file}")

        return result

    def _generate_format_variant_files(self, rows: list[tuple]) -> dict[str, Path]:
        header = self._ORDERS_HEADER
        result: dict[str, Path] = {}

        quoted_file = self.files_dir / "csv_quoted_fields.csv"
        with open(quoted_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            writer.writerow(header)
            for row in rows[:1000]:
                row_list = list(row)
                row_list[8] = 'Comment with "quotes" and, commas'
                writer.writerow(row_list)
        result["csv_quoted_fields"] = quoted_file
        self.log_verbose(f"Generated {quoted_file}")

        psv_file = self.files_dir / "csv_custom_delim.psv"
        with open(psv_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter="|")
            writer.writerow(header)
            for row in rows[:1000]:
                writer.writerow(row)
        result["csv_custom_delim"] = psv_file
        self.log_verbose(f"Generated {psv_file}")

        custom_dates_file = self.files_dir / "csv_custom_dates.csv"
        with open(custom_dates_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            for row in rows[:1000]:
                row_list = list(row)
                if row_list[4]:
                    row_list[4] = row_list[4].replace("-", "/")
                writer.writerow(row_list)
        result["csv_custom_dates"] = custom_dates_file
        self.log_verbose(f"Generated {custom_dates_file}")

        utf8_file = self.files_dir / "csv_utf8_encoded.csv"
        with open(utf8_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            for row in rows[:1000]:
                row_list = list(row)
                row_list[8] = "Comment with UTF-8: café, naïve, 日本語"
                writer.writerow(row_list)
        result["csv_utf8_encoded"] = utf8_file
        self.log_verbose(f"Generated {utf8_file}")

        return result

    def _generate_parallel_load_files(self, rows: list[tuple]) -> dict[str, Path]:
        header = self._ORDERS_HEADER
        result: dict[str, Path] = {}

        min_rows_needed = 2000
        if len(rows) < min_rows_needed:
            self.log_verbose(
                f"Warning: Only {len(rows)} rows available for parallel parts "
                f"(minimum {min_rows_needed} recommended). Using available data."
            )

        num_parts = 4
        rows_per_part = max(len(rows) // num_parts, 1)

        for part_num in range(1, num_parts + 1):
            start_idx = (part_num - 1) * rows_per_part
            end_idx = len(rows) if part_num == num_parts else start_idx + rows_per_part

            parallel_file = self.files_dir / f"csv_parallel_part{part_num}.csv"
            with open(parallel_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(header)
                rows_written = 0
                for row in rows[start_idx:end_idx]:
                    writer.writerow(row)
                    rows_written += 1
            result[f"csv_parallel_part{part_num}"] = parallel_file
            self.log_verbose(f"Generated {parallel_file} ({rows_written} rows)")

        return result

    def get_data_source_benchmark(self) -> str:
        return "tpch"
