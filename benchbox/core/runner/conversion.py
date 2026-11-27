from __future__ import annotations

import logging
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from benchbox.core.manifest import (
    ConvertedFileEntry,
    ManifestV2,
    TableFormats,
    load_manifest,
    upgrade_v1_to_v2,
    write_manifest,
)
from benchbox.utils.datagen_backend import NATIVE_BACKEND_NAME, DatagenBackendError
from benchbox.utils.format_converters import (
    ConversionOptions,
    ConversionResult,
    DeltaConverter,
    DuckLakeConverter,
    IcebergConverter,
    ParquetConverter,
    VortexConverter,
)
from benchbox.utils.native_datagen import PARQUET_ROW_GROUP_ROWS, read_parquet_writer, write_native_tables

logger = logging.getLogger(__name__)

_DIGIT_RUN = re.compile(r"(\d+)")


def _chunk_order_key(entry: Any) -> list[str | int]:
    return [int(part) if index % 2 else part for index, part in enumerate(_DIGIT_RUN.split(str(entry.path)))]


class FormatConversionOrchestrator:
    def __init__(self) -> None:
        self._converters = {
            "parquet": ParquetConverter(),
            "delta": DeltaConverter(),
            "iceberg": IcebergConverter(),
            "ducklake": DuckLakeConverter(),
            "vortex": VortexConverter(),
        }

    def convert_benchmark_tables(
        self,
        manifest_path: Path,
        output_dir: Path,
        target_format: str,
        schemas: dict[str, dict[str, Any]],
        options: ConversionOptions | None = None,
    ) -> dict[str, ConversionResult]:
        logger.info(f"Loading manifest: {manifest_path}")
        manifest = load_manifest(manifest_path)

        if not isinstance(manifest, ManifestV2):
            logger.info("Upgrading manifest v1 to v2")
            manifest = upgrade_v1_to_v2(manifest)

        converter = self._get_converter(target_format)

        if target_format.lower() == "parquet" and manifest.extra_fields.get("generator_backend") == NATIVE_BACKEND_NAME:
            native_options = options or ConversionOptions()
            native_results = self._convert_native_parquet(manifest, output_dir, native_options)
            self._update_manifest_with_results(manifest, native_results, target_format, output_dir, native_options)
            write_manifest(manifest, manifest_path)
            return native_results

        results = {}
        tables_list = list(manifest.tables.keys())
        total_tables = len(tables_list)

        logger.info(f"Converting {total_tables} tables to {target_format}")

        base_options = options or ConversionOptions()

        for idx, table_name in enumerate(tables_list, 1):
            logger.info(f"[{idx}/{total_tables}] Converting table: {table_name}")

            source_entries = self._get_source_entries(manifest, table_name)
            source_files = [output_dir / f.path for f in source_entries]

            if not source_files:
                logger.warning(f"No source files found for table: {table_name}")
                continue

            missing_files = [f for f in source_files if not f.exists()]
            if missing_files:
                logger.error(f"Missing source files for {table_name}: {missing_files}")
                continue

            schema = schemas.get(table_name)
            if not schema:
                logger.warning(f"No schema found for table: {table_name}, skipping")
                continue

            table_options = self._resolve_conversion_options(manifest, base_options, source_entries)
            if table_options.expected_row_count is None:
                source_row_count = self._get_source_row_count(manifest, table_name)
                if source_row_count is not None:
                    table_options = replace(table_options, expected_row_count=source_row_count)

            try:
                result = converter.convert(
                    source_files=source_files,
                    table_name=table_name,
                    schema=schema,
                    options=table_options,
                )
                results[table_name] = result

                compression_info = f"{result.compression_ratio:.2f}x" if result.compression_ratio > 0 else "N/A"
                logger.info(
                    f"[{idx}/{total_tables}] ✓ Converted {table_name}: "
                    f"{result.row_count:,} rows, "
                    f"compression: {compression_info}"
                )

            except Exception as e:
                logger.error(f"[{idx}/{total_tables}] ✗ Failed to convert {table_name}: {e}")
                raise RuntimeError(f"Conversion failed for table {table_name}") from e

        if results:
            logger.info("Updating manifest with conversion results")
            self._update_manifest_with_results(manifest, results, target_format, output_dir, base_options)

            write_manifest(manifest, manifest_path)
            logger.info(f"Updated manifest: {manifest_path}")

        return results

    def _convert_native_parquet(
        self,
        manifest: ManifestV2,
        output_dir: Path,
        options: ConversionOptions,
    ) -> dict[str, ConversionResult]:
        if options.partition_cols or options.row_group_size != PARQUET_ROW_GROUP_ROWS:
            raise DatagenBackendError(
                "--datagen-backend native writes whole-table Parquet files with "
                f"{PARQUET_ROW_GROUP_ROWS}-row row groups and does not support partition columns or a custom "
                "row-group size; remove them or pass --datagen-backend legacy."
            )
        generation = write_native_tables(
            manifest.benchmark,
            manifest.scale_factor,
            output_dir,
            table_format="parquet",
            compression=options.compression or "none",
        )
        results: dict[str, ConversionResult] = {}
        for table_name, table_file in generation.files.items():
            source_rows = self._get_source_row_count(manifest, table_name)
            if source_rows is not None and source_rows != table_file.rows:
                raise RuntimeError(
                    f"benchbox-datagen wrote {table_file.rows} rows for {table_name}; "
                    f"the delimited source has {source_rows}"
                )
            source_entries = self._get_source_entries(manifest, table_name)
            results[table_name] = ConversionResult(
                output_files=[table_file.path],
                row_count=table_file.rows,
                source_size_bytes=sum(entry.size_bytes for entry in source_entries),
                output_size_bytes=table_file.size_bytes,
                metadata={
                    "row_groups": pq.read_metadata(table_file.path).num_row_groups,
                    "compression": options.compression,
                    "partitioned": False,
                    "parquet_writer": read_parquet_writer(table_file.path),
                },
                converted_at=datetime.now(timezone.utc).isoformat(),
                conversion_options={
                    "compression": options.compression,
                    "row_group_size": PARQUET_ROW_GROUP_ROWS,
                    "merge_shards": True,
                    "partition_cols": [],
                    "validate_row_count": True,
                },
            )
        return results

    def _resolve_conversion_options(
        self,
        manifest: ManifestV2,
        options: ConversionOptions | None,
        source_entries: list[Any] | None = None,
    ) -> ConversionOptions:
        base_opts = options or ConversionOptions()
        extras = manifest.extra_fields or {}
        entry_meta: dict[str, Any] = {}
        for entry in source_entries or ():
            meta = getattr(entry, "metadata", None)
            if isinstance(meta, dict) and meta:
                entry_meta = meta
                break

        delimiter = base_opts.delimiter
        if delimiter is None:
            raw_delim = entry_meta.get("csv_delimiter", extras.get("csv_delimiter"))
            if isinstance(raw_delim, str) and len(raw_delim) == 1:
                delimiter = raw_delim

        has_header = base_opts.has_header
        if has_header is None:
            raw_header = entry_meta.get("csv_has_header", extras.get("csv_has_header"))
            if isinstance(raw_header, bool):
                has_header = raw_header

        null_marker = base_opts.null_marker
        if not null_marker:
            raw_marker = entry_meta.get("csv_null_marker", extras.get("csv_null_marker"))
            if isinstance(raw_marker, str):
                null_marker = raw_marker

        if (delimiter, has_header, null_marker) == (base_opts.delimiter, base_opts.has_header, base_opts.null_marker):
            return base_opts
        return replace(base_opts, delimiter=delimiter, has_header=has_header, null_marker=null_marker)

    def _get_converter(self, format_name: str):
        converter = self._converters.get(format_name.lower())
        if not converter:
            available = ", ".join(self._converters.keys())
            raise ValueError(f"Unknown format: {format_name}. Available formats: {available}")
        return converter

    def _get_source_entries(self, manifest: ManifestV2, table_name: str) -> list[Any]:
        table_formats = manifest.tables.get(table_name)
        if not table_formats:
            return []

        if "tbl" in table_formats.formats:
            return sorted(table_formats.formats["tbl"], key=_chunk_order_key)

        for format_name, files in table_formats.formats.items():
            logger.info(f"Using {format_name} files as source for {table_name}")
            return sorted(files, key=_chunk_order_key)

        return []

    def _get_source_files(self, manifest: ManifestV2, table_name: str, output_dir: Path) -> list[Path]:
        return [output_dir / f.path for f in self._get_source_entries(manifest, table_name)]

    def _get_source_row_count(self, manifest: ManifestV2, table_name: str) -> int | None:
        if manifest.extra_fields.get("row_counts_source") != "measured":
            return None
        entries = self._get_source_entries(manifest, table_name)
        if not entries:
            return None
        total = 0
        has_nonzero_size = False
        for entry in entries:
            rc = getattr(entry, "row_count", None)
            if not isinstance(rc, int) or isinstance(rc, bool) or rc < 0:
                return None
            if getattr(entry, "size_bytes", 0) > 0:
                has_nonzero_size = True
            total += rc
        if total == 0 and has_nonzero_size:
            return None
        return total

    def _update_manifest_with_results(
        self,
        manifest: ManifestV2,
        results: dict[str, ConversionResult],
        target_format: str,
        output_dir: Path,
        effective_options: ConversionOptions | None = None,
    ) -> None:
        for table_name, result in results.items():
            table_formats = manifest.tables.get(table_name)
            if not table_formats:
                table_formats = TableFormats(formats={})
                manifest.tables[table_name] = table_formats

            converted_files = []
            for output_file in result.output_files:
                try:
                    relative_path = output_file.relative_to(output_dir)
                except ValueError:
                    relative_path = output_file.name

                file_entry = ConvertedFileEntry(
                    path=str(relative_path),
                    size_bytes=result.output_size_bytes,
                    row_count=result.row_count,
                    converted_from=result.source_format,
                    converted_at=result.converted_at,
                    compression=result.conversion_options.get("compression"),
                    row_groups=result.metadata.get("row_groups"),
                    is_directory=output_file.is_dir(),
                    conversion_options=result.conversion_options,
                )
                converted_files.append(file_entry)

            table_formats.formats[target_format] = converted_files

        if target_format in manifest.format_preference:
            manifest.format_preference.remove(target_format)
        manifest.format_preference.insert(0, target_format)

        formats_list = manifest.extra_fields.get("formats")
        if isinstance(formats_list, list) and target_format not in formats_list:
            manifest.extra_fields["formats"] = [*formats_list, target_format]

        if target_format.lower() == "parquet":
            self._stamp_parquet_provenance(manifest, results, effective_options)

    def _stamp_parquet_provenance(
        self,
        manifest: ManifestV2,
        results: dict[str, ConversionResult],
        effective_options: ConversionOptions | None,
    ) -> None:
        from benchbox.utils.datagen_version import (
            PARQUET_WRITER,
            PARTITIONED_PARQUET_WRITER,
            compute_datagen_identity_hash,
        )

        row_group_size: int | None = None
        if effective_options is not None and isinstance(effective_options.row_group_size, int):
            row_group_size = effective_options.row_group_size
        else:
            for result in results.values():
                candidate = result.conversion_options.get("row_group_size")
                if isinstance(candidate, int) and not isinstance(candidate, bool):
                    row_group_size = candidate
                    break

        native_writers = {
            writer
            for writer in (result.metadata.get("parquet_writer") for result in results.values())
            if writer is not None and writer not in {PARQUET_WRITER, PARTITIONED_PARQUET_WRITER}
        }
        parquet_writer = (
            "+".join(sorted(native_writers))
            if native_writers
            else PARTITIONED_PARQUET_WRITER
            if any(result.metadata.get("partitioned") for result in results.values())
            else PARQUET_WRITER
        )
        manifest.extra_fields["parquet_writer"] = parquet_writer
        if row_group_size is not None:
            manifest.extra_fields["parquet_row_group_size"] = row_group_size

        configuration = manifest.extra_fields.get("skew_configuration")
        backend = manifest.extra_fields.get("generator_backend")
        backend_version = manifest.extra_fields.get("generator_backend_version")
        manifest.extra_fields["parquet_data_generation_identity_hash"] = compute_datagen_identity_hash(
            manifest.benchmark,
            configuration if isinstance(configuration, dict) else None,
            backend=str(backend) if backend is not None else None,
            backend_version=str(backend_version) if backend_version is not None else None,
            parquet_writer=parquet_writer,
            parquet_row_group_size=row_group_size,
            scale_factor=manifest.scale_factor,
        )
