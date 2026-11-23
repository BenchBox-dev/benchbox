from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as csv
import pyarrow.parquet as pq

from benchbox.core.data_organization.clustering import HilbertClusterer, ZOrderClusterer
from benchbox.core.data_organization.config import (
    DataOrganizationConfig,
    SortColumn,
    SortOrder,
)
from benchbox.utils.file_format import has_trailing_delimiter

logger = logging.getLogger(__name__)

_TRAILING_DELIMITER_COLUMN = "__benchbox_trailing_delimiter__"
_DEFAULT_MAX_IN_MEMORY_BYTES = 2 * 1024 * 1024 * 1024


def _source_has_trailing_delimiter(source_files: list[Path], column_names: list[str]) -> bool:
    if not source_files:
        return False
    return has_trailing_delimiter(source_files[0], "|", column_names)


class SortedParquetWriter:
    def __init__(self, config: DataOrganizationConfig, schema_registry: dict[str, Any] | None = None) -> None:
        self.config = config
        self.schema_registry = schema_registry or {}

    def write_sorted_parquet(
        self,
        table_name: str,
        source_files: list[Path],
        output_dir: Path,
    ) -> Path:
        for f in source_files:
            if not f.exists():
                raise FileNotFoundError(f"Source file not found: {f}")

        sort_columns = self.config.get_sort_columns_for_table(table_name)
        partition_columns = self.config.get_partition_columns_for_table(table_name)
        cluster_columns = self.config.get_cluster_columns_for_table(table_name)
        clustering_method = self.config.get_clustering_method_for_table(table_name)
        column_names = self._get_column_names(table_name)

        self._validate_input_size_for_in_memory_ops(
            source_files, bool(sort_columns or partition_columns or cluster_columns)
        )
        arrow_table = self._read_tbl_files(source_files, column_names)

        if sort_columns or partition_columns:
            arrow_table = self._sort_table(arrow_table, sort_columns, partition_columns)
        if cluster_columns:
            arrow_table = self._cluster_table(arrow_table, cluster_columns, clustering_method)

        output_path: Path
        if self.config.output_format == "delta":
            output_path = self._write_delta_table(table_name, arrow_table, output_dir)
        elif self.config.output_format == "iceberg":
            output_path = self._write_iceberg_table(table_name, arrow_table, output_dir)
        else:
            output_path = self._write_parquet_table(table_name, arrow_table, output_dir)

        row_count = arrow_table.num_rows
        file_size = output_path.stat().st_size
        logger.info(
            "Wrote sorted %s output: %s (%s rows, %.1f MB)",
            self.config.output_format,
            output_path.name,
            f"{row_count:,}",
            file_size / (1024 * 1024),
        )

        return output_path

    def _validate_input_size_for_in_memory_ops(self, source_files: list[Path], requires_reordering: bool) -> None:
        if not requires_reordering:
            return

        raw_limit = os.getenv("BENCHBOX_DATA_ORG_MAX_IN_MEMORY_BYTES")
        limit = _DEFAULT_MAX_IN_MEMORY_BYTES
        if raw_limit is not None:
            try:
                parsed = int(raw_limit)
                if parsed > 0:
                    limit = parsed
            except ValueError:
                logger.warning("Ignoring invalid BENCHBOX_DATA_ORG_MAX_IN_MEMORY_BYTES=%r", raw_limit)

        total_size = sum(path.stat().st_size for path in source_files)
        if total_size > limit:
            raise RuntimeError(
                "Data organization currently uses in-memory reordering; "
                f"input size {total_size:,} bytes exceeds safety limit {limit:,} bytes. "
                "Reduce scale factor or increase BENCHBOX_DATA_ORG_MAX_IN_MEMORY_BYTES."
            )

    def _write_parquet_table(self, table_name: str, arrow_table: pa.Table, output_dir: Path) -> Path:
        output_path = output_dir / f"{table_name}.parquet"
        output_dir.mkdir(parents=True, exist_ok=True)

        compression_map = {
            "snappy": "SNAPPY",
            "gzip": "GZIP",
            "zstd": "ZSTD",
            "none": None,
        }
        compression = compression_map.get(self.config.compression)

        pq.write_table(
            arrow_table,
            output_path,
            compression=compression,
            use_dictionary=True,
            write_statistics=True,
            row_group_size=self.config.row_group_size,
        )
        return output_path

    def _write_delta_table(self, table_name: str, arrow_table: pa.Table, output_dir: Path) -> Path:
        try:
            from deltalake import write_deltalake
            from deltalake.writer import WriterProperties
        except ImportError as exc:
            raise RuntimeError(
                "Delta output requires the 'deltalake' package. Install with: uv add deltalake --optional table-formats"
            ) from exc

        output_path = output_dir / table_name
        output_path.mkdir(parents=True, exist_ok=True)

        compression_map = {
            "snappy": "SNAPPY",
            "gzip": "GZIP",
            "zstd": "ZSTD",
            "none": "UNCOMPRESSED",
        }
        writer_properties = WriterProperties(compression=compression_map.get(self.config.compression, "SNAPPY"))
        write_deltalake(
            str(output_path),
            arrow_table,
            mode="overwrite",
            writer_properties=writer_properties,
            name=table_name,
            description=f"BenchBox sorted data for {table_name}",
        )
        return output_path

    def _write_iceberg_table(self, table_name: str, arrow_table: pa.Table, output_dir: Path) -> Path:
        try:
            from benchbox.utils.format_converters import ConversionOptions, IcebergConverter
        except ImportError as exc:
            raise RuntimeError(
                "Iceberg output requires conversion support dependencies (pyiceberg). "
                "Install with: uv add pyiceberg --optional table-formats"
            ) from exc

        converter = IcebergConverter()
        schema = self._build_converter_schema(table_name, arrow_table)

        with tempfile.TemporaryDirectory(prefix=f"benchbox_sorted_{table_name}_") as temp_dir:
            temp_tbl = Path(temp_dir) / f"{table_name}.tbl"
            self._write_table_as_tbl(arrow_table, temp_tbl)
            result = converter.convert(
                source_files=[temp_tbl],
                table_name=table_name,
                schema=schema,
                options=ConversionOptions(
                    compression=self.config.compression,
                    output_dir=output_dir,
                    validate_row_count=True,
                ),
            )
        return result.output_files[0]

    def _build_converter_schema(self, table_name: str, arrow_table: pa.Table) -> dict[str, Any]:
        schema = self.schema_registry.get(table_name)
        if schema and "columns" in schema and all("type" in col for col in schema["columns"]):
            return schema

        type_map = {
            pa.int8(): "BIGINT",
            pa.int16(): "BIGINT",
            pa.int32(): "BIGINT",
            pa.int64(): "BIGINT",
            pa.uint8(): "BIGINT",
            pa.uint16(): "BIGINT",
            pa.uint32(): "BIGINT",
            pa.uint64(): "BIGINT",
            pa.float32(): "DOUBLE",
            pa.float64(): "DOUBLE",
            pa.string(): "VARCHAR",
            pa.large_string(): "VARCHAR",
            pa.bool_(): "BOOLEAN",
            pa.date32(): "DATE",
            pa.date64(): "DATE",
            pa.timestamp("us"): "TIMESTAMP",
        }
        columns = []
        for name, dtype in zip(arrow_table.column_names, arrow_table.schema.types, strict=False):
            sql_type = type_map.get(dtype, "VARCHAR")
            columns.append({"name": name, "type": sql_type})
        return {"name": table_name, "columns": columns}

    def _write_table_as_tbl(self, table: pa.Table, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        columns = [table.column(name).to_pylist() for name in table.column_names]

        with output_path.open("w", encoding="utf-8") as f:
            for row_idx in range(table.num_rows):
                values = [columns[col_idx][row_idx] for col_idx in range(len(columns))]
                rendered = ["" if value is None else str(value) for value in values]
                f.write("|".join(rendered) + "|\n")

    def _get_column_names(self, table_name: str) -> list[str] | None:
        schema = self.schema_registry.get(table_name)
        if schema and "columns" in schema:
            return [col["name"] for col in schema["columns"]]
        return None

    def _read_tbl_files(
        self,
        source_files: list[Path],
        column_names: list[str] | None,
    ) -> pa.Table:
        read_column_names = column_names
        if column_names is not None and _source_has_trailing_delimiter(source_files, column_names):
            read_column_names = [*column_names, _TRAILING_DELIMITER_COLUMN]

        read_options = csv.ReadOptions(
            autogenerate_column_names=read_column_names is None,
            column_names=read_column_names,
        )
        parse_options = csv.ParseOptions(delimiter="|")
        convert_options = csv.ConvertOptions(null_values=[""], strings_can_be_null=True)

        tables = []
        for source_file in source_files:
            table = csv.read_csv(
                source_file, read_options=read_options, parse_options=parse_options, convert_options=convert_options
            )
            tables.append(table)

        combined = pa.concat_tables(tables) if len(tables) > 1 else tables[0]

        last_col = combined.column_names[-1]
        if last_col == _TRAILING_DELIMITER_COLUMN or column_names is None:
            last_values = combined.column(last_col)
            if last_values.null_count == len(last_values) or (
                pa.types.is_string(last_values.type) and pc.all(pc.equal(last_values, "")).as_py()
            ):
                combined = combined.drop(last_col)

        return combined

    def _sort_table(
        self,
        table: pa.Table,
        sort_columns: list[SortColumn],
        partition_columns: list[str] | None = None,
    ) -> pa.Table:
        available = set(table.column_names)
        partition_columns = partition_columns or []
        for partition_col in partition_columns:
            if partition_col not in available:
                raise ValueError(
                    f"Partition column '{partition_col}' not found in table. Available columns: {sorted(available)}"
                )
        for sc in sort_columns:
            if sc.name not in available:
                raise ValueError(f"Sort column '{sc.name}' not found in table. Available columns: {sorted(available)}")

        sort_keys: list[tuple[str, str]] = [(partition_col, "ascending") for partition_col in partition_columns]
        seen = set(partition_columns)
        for sc in sort_columns:
            if sc.name in seen:
                continue
            sort_keys.append((sc.name, "ascending" if sc.order == SortOrder.ASC else "descending"))
            seen.add(sc.name)

        indices = pc.sort_indices(table, sort_keys=sort_keys)
        return table.take(indices)

    def _cluster_table(self, table: pa.Table, cluster_columns: list[str], clustering_method: str) -> pa.Table:
        if clustering_method == "z_order":
            return ZOrderClusterer().cluster_table(table, cluster_columns)
        if clustering_method == "hilbert":
            return HilbertClusterer().cluster_table(table, cluster_columns)
        raise ValueError(f"Unsupported clustering method: {clustering_method}")
