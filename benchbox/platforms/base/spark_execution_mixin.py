# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from benchbox.core.loaded_tables import is_data_loading_skipped
from benchbox.core.results.fetch_mode import FETCH_MODE_ARROW
from benchbox.platforms.base.arrow_result import ArrowRows, ArrowSource, fetch_result
from benchbox.utils.clock import elapsed_seconds, mono_time
from benchbox.utils.file_format import DATA_FORMAT_EXTENSIONS, detect_compression, strip_compression_suffix

logger = logging.getLogger(__name__)

SPARK_ARROW_PANDAS_CONF = "spark.sql.execution.arrow.pyspark.enabled"


def _spark_local_naive_timestamp(value: Any) -> Any:
    return value.astimezone().replace(tzinfo=None) if value.tzinfo is not None else value


def _none_safe(convert: Callable[[Any], Any] | None, value: Any) -> Any:
    return value if convert is None or value is None else convert(value)


def _spark_value_converter(data_type: Any) -> Callable[[Any], Any] | None:
    type_name = data_type.typeName()
    if type_name == "timestamp":
        return _spark_local_naive_timestamp
    if type_name == "daytimeinterval":
        return lambda value: value.to_pytimedelta() if hasattr(value, "to_pytimedelta") else value
    if type_name == "binary" and not spark_dataframe_supports_arrow():
        return bytearray
    if type_name == "array":
        element = _spark_value_converter(data_type.elementType)
        return None if element is None else lambda value: [_none_safe(element, item) for item in value]
    if type_name == "map":
        item = _spark_value_converter(data_type.valueType)
        return lambda value: {key: _none_safe(item, entry) for key, entry in dict(value).items()}
    if type_name == "struct":
        from pyspark.sql import Row

        names = [field.name for field in data_type.fields]
        members = [_spark_value_converter(field.dataType) for field in data_type.fields]
        row_type = Row(*names)
        return lambda value: row_type(
            *[_none_safe(member, value[name]) for member, name in zip(members, names, strict=True)]
        )
    return None


def spark_arrow_converters(schema: Any) -> dict[int, Callable[[Any], Any]]:
    converters: dict[int, Callable[[Any], Any]] = {}
    for index, field in enumerate(schema.fields):
        converter = _spark_value_converter(field.dataType)
        if converter is not None:
            converters[index] = converter
    return converters


def spark_pandas_to_arrow(result_df: Any) -> Any:
    import pyarrow as pa
    import pyarrow.compute as pc

    exact_types = {
        "byte": pa.int8(),
        "short": pa.int16(),
        "integer": pa.int32(),
        "long": pa.int64(),
        "timestamp": pa.timestamp("us"),
        "timestamp_ntz": pa.timestamp("us"),
    }
    pandas_frame = result_df.toPandas()
    pandas_frame.columns = [str(index) for index in range(len(pandas_frame.columns))]
    table = pa.Table.from_pandas(pandas_frame, preserve_index=False)
    session_time_zone = result_df.sparkSession.conf.get("spark.sql.session.timeZone")
    columns = []
    for column, field in zip(table.columns, result_df.schema.fields, strict=True):
        type_name = field.dataType.typeName()
        exact = exact_types.get(type_name)
        if exact is not None and pa.types.is_integer(exact) and pa.types.is_floating(column.type):
            largest = pc.max(pc.abs(column)).as_py()
            if largest is not None and largest >= 2**53:
                raise ValueError(
                    f"Column {field.name!r} holds integers beyond 2**53 that toPandas returned as float64; "
                    "set the fetch_mode platform option to rows"
                )
        if exact is not None and column.type != exact:
            column = column.cast(exact)
        if type_name == "timestamp":
            column = pc.assume_timezone(column, session_time_zone, ambiguous="earliest", nonexistent="earliest")
        columns.append(column)
    return pa.Table.from_arrays(columns, names=[field.name for field in result_df.schema.fields])


def spark_arrow_source(result_df: Any, pandas_enabled: bool) -> ArrowSource | None:
    if hasattr(result_df, "toArrow"):
        return result_df.toArrow
    if pandas_enabled and hasattr(result_df, "toPandas"):
        return lambda: spark_pandas_to_arrow(result_df)
    return None


def spark_dataframe_supports_arrow() -> bool:
    try:
        from pyspark.sql import DataFrame
    except ImportError:
        return False
    return hasattr(DataFrame, "toArrow")


class SparkDataLoadMixin:
    _csv_compression_codecs: frozenset[str] = frozenset({"gzip", "bzip2", "lz4", "snappy", "deflate"})

    _requires_csv_extension: bool = False

    _df_caching_supported: bool = True

    _COMPRESSION_EXT: dict[str, str] = {
        "zstd": ".zst",
        "gzip": ".gz",
        "bzip2": ".bz2",
        "xz": ".xz",
        "lz4": ".lz4",
        "snappy": ".snappy",
        "deflate": ".deflate",
    }

    @staticmethod
    def _row_count(spark: Any, table_name: str) -> int:
        safe_name = table_name.replace("`", "``")
        rows = spark.sql(f"SELECT COUNT(*) FROM `{safe_name}`").collect()
        return rows[0][0] if rows else 0

    @classmethod
    def _safe_row_count(cls, spark: Any, table_name: str) -> int:
        try:
            return cls._row_count(spark, table_name)
        except Exception as exc:
            logger.debug("Row count unavailable for table '%s'; assuming 0: %s", table_name, exc)
            return 0

    @staticmethod
    def _spark_physical_table_name(table_name: str) -> str:
        return table_name if any(char.isupper() for char in table_name) else table_name.lower()

    @classmethod
    def _csv_extension_suffix(cls, compression: str | None) -> str:
        if compression is None:
            return ""

        try:
            return cls._COMPRESSION_EXT[compression]
        except KeyError as exc:
            raise ValueError(f"Unsupported CSV compression type '{compression}' for Spark compatibility path") from exc

    def _csv_compat_path(self, file_path: Path, temp_dir: Path | None, *, compression: str | None = None) -> Path:
        if not self._requires_csv_extension:
            return file_path

        if temp_dir is None:
            raise ValueError("temp_dir is required when CSV extension compatibility is enabled")

        compression = detect_compression(file_path) if compression is None else compression
        base_path = strip_compression_suffix(file_path)

        if base_path.suffix.lower() == ".csv":
            new_name = base_path.name + self._csv_extension_suffix(compression)
        else:
            p = base_path
            trailing: list[str] = []
            while p.suffix and p.suffix.lower() not in DATA_FORMAT_EXTENSIONS:
                trailing.insert(0, p.suffix)
                p = p.with_suffix("")
            new_name = p.stem + "".join(trailing) + ".csv" + self._csv_extension_suffix(compression)

        compat_dir = temp_dir / new_name
        compat_dir.mkdir(exist_ok=True)
        link = compat_dir / new_name
        resolved_path = file_path.resolve()
        if link.exists() or link.is_symlink():
            try:
                points_to_source = link.samefile(resolved_path)
            except OSError:
                points_to_source = False

            if not points_to_source:
                logger.warning(
                    "CSV compatibility path collision: %s already exists, expected %s",
                    link,
                    resolved_path,
                )
        else:
            try:
                link.hardlink_to(resolved_path)
            except OSError:
                try:
                    link.symlink_to(resolved_path)
                except OSError:
                    shutil.copy2(resolved_path, link)
        return compat_dir

    def _csv_compat_temp_dir_parent(self, data_dir: Path) -> Path | None:
        if not self._requires_csv_extension:
            return None

        try:
            parent = Path(data_dir).expanduser().resolve()
            parent.mkdir(parents=True, exist_ok=True)
            return parent
        except OSError as exc:
            logger.debug(
                "Could not create CSV compatibility temp dir under data dir %s; falling back to system temp: %s",
                data_dir,
                exc,
            )
            return None

    @staticmethod
    def _detect_spark_table_format(path: Path) -> str | None:
        if not path.is_dir():
            return None
        if (path / "_delta_log").is_dir():
            return "delta"
        if (path / "metadata").is_dir():
            return "iceberg"
        if (path / ".hoodie").is_dir():
            return "hudi"
        return None

    def load_data(
        self, benchmark: Any, connection: Any, data_dir: Path
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        return self._load_data_spark(benchmark, data_dir, connection)

    def _load_data_spark(
        self,
        benchmark: Any,
        data_dir: Path,
        connection: Any,
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        from benchbox.platforms.base.data_loading import DataSourceResolver, resolve_csv_dialect
        from benchbox.platforms.base.utils import detect_file_format

        start_time = mono_time()
        table_stats: dict[str, int] = {}
        per_table_timings: dict[str, Any] = {}

        spark = connection

        try:
            resolver = DataSourceResolver(
                platform_name=self.platform_name,
                table_mode=self.table_mode,
                platform_config=self.platform_config,
                requested_format=self.requested_table_format,
            )
            data_source = resolver.resolve(benchmark, Path(data_dir))

            if not data_source or not data_source.tables:
                if is_data_loading_skipped(benchmark):
                    self.logger.info("Benchmark is schema-only; skipping Spark data load")
                    return table_stats, elapsed_seconds(start_time), per_table_timings
                raise ValueError(
                    f"No data files found in {data_dir}. Ensure benchmark.generate_data() was called first."
                )

            self.log_verbose(f"Data source type: {data_source.source_type}")

            with tempfile.TemporaryDirectory(
                prefix="benchbox_csv_",
                dir=self._csv_compat_temp_dir_parent(Path(data_dir)),
            ) as csv_tmp_name:
                csv_tmp_dir = Path(csv_tmp_name)

                for table_name, file_paths in data_source.tables.items():
                    valid_files = self._normalize_and_validate_file_paths(file_paths)

                    if not valid_files:
                        self.logger.warning(f"Skipping {table_name} - no valid data files")
                        table_stats[self._spark_physical_table_name(table_name)] = 0
                        continue

                    chunk_info = f" from {len(valid_files)} file(s)" if len(valid_files) > 1 else ""
                    self.log_verbose(f"Loading data for table: {table_name}{chunk_info}")

                    try:
                        load_start = mono_time()
                        physical_table_name = self._spark_physical_table_name(table_name)
                        total_rows_loaded = 0
                        table_start_row_count = (
                            self._safe_row_count(spark, physical_table_name) if not self._df_caching_supported else 0
                        )

                        table_schema = self._get_table_schema(spark, physical_table_name)
                        format_info = detect_file_format(valid_files)

                        for file_idx, raw_path in enumerate(valid_files, start=1):
                            file_path = Path(raw_path).resolve()
                            table_format = self._detect_spark_table_format(file_path)

                            if table_format is not None:
                                df = spark.read.format(table_format).load(str(file_path))
                            elif format_info.format_type == "parquet":
                                df = spark.read.parquet(str(file_path))
                            else:
                                compression = detect_compression(file_path)
                                csv_path = self._csv_compat_path(file_path, csv_tmp_dir, compression=compression)
                                dialect = resolve_csv_dialect(data_source, table_name, file_path, benchmark)
                                reader = (
                                    spark.read.option("header", str(dialect.has_header).lower())
                                    .option("delimiter", dialect.delimiter)
                                    .option("inferSchema", "false")
                                )
                                if dialect.null_marker is not None:
                                    reader = reader.option("nullValue", dialect.null_marker)
                                if compression is not None and compression in self._csv_compression_codecs:
                                    reader = reader.option("compression", compression)
                                df = reader.csv(str(csv_path))

                                if table_schema:
                                    existing_cols = [f.name for f in table_schema.fields]
                                    new_names = [
                                        existing_cols[i] if i < len(existing_cols) else df.columns[i]
                                        for i in range(len(df.columns))
                                    ]
                                    df = df.toDF(*new_names)

                            if table_schema:
                                df = self._cast_dataframe_to_schema(df, table_schema)

                            if self._df_caching_supported:
                                df.cache()
                                row_count = df.count()
                                df.write.mode("append").insertInto(physical_table_name)
                                df.unpersist()
                            else:
                                df.write.mode("append").insertInto(physical_table_name)
                                self.log_verbose(f"Wrote chunk {file_idx}/{len(valid_files)} for {physical_table_name}")
                                continue
                            total_rows_loaded += row_count

                        if not self._df_caching_supported:
                            table_end_row_count = self._row_count(spark, physical_table_name)
                            row_delta = table_end_row_count - table_start_row_count
                            if row_delta < 0:
                                self.logger.warning(
                                    "Negative row delta for %s (%d -> %d); reporting 0",
                                    physical_table_name,
                                    table_start_row_count,
                                    table_end_row_count,
                                )
                            total_rows_loaded = max(0, row_delta)

                        table_stats[physical_table_name] = total_rows_loaded

                        load_time = elapsed_seconds(load_start)
                        per_table_timings[physical_table_name] = {"total_ms": load_time * 1000}
                        self.logger.info(
                            f"Loaded {total_rows_loaded:,} rows into {physical_table_name}{chunk_info} "
                            f"in {load_time:.2f}s"
                        )

                    except Exception as e:
                        self.logger.error(f"Failed to load {table_name}: {e}")
                        table_stats[self._spark_physical_table_name(table_name)] = 0

            total_time = elapsed_seconds(start_time)
            total_rows = sum(table_stats.values())
            self.logger.info(f"Loaded {total_rows:,} total rows in {total_time:.2f}s")

        except Exception as e:
            self.logger.error(f"Data loading failed: {e}")
            raise

        return table_stats, total_time, per_table_timings

    def _get_table_schema(self, spark: Any, table_name: str) -> Any:
        try:
            return spark.table(table_name).schema
        except Exception:
            return None

    def _cast_dataframe_to_schema(self, df: Any, schema: Any) -> Any:
        from pyspark.sql import functions as spark_funcs

        df_cols = set(df.columns)
        exprs = []
        for field in schema.fields:
            if field.name not in df_cols:
                continue
            source_col = spark_funcs.col(field.name)
            if getattr(field.dataType, "typeName", lambda: None)() == "array":
                array_schema = getattr(field.dataType, "simpleString", lambda: "array<string>")()
                exprs.append(spark_funcs.from_json(source_col.cast("string"), array_schema).alias(field.name))
            else:
                exprs.append(source_col.cast(field.dataType).alias(field.name))
        if exprs:
            df = df.select(*exprs)
        return df


class SparkQueryExecutionMixin:
    _catalog_clear_cache_supported: bool = True
    _spark_arrow_conf_session: int | None = None
    _spark_arrow_pandas_enabled: bool = False

    def _ensure_spark_arrow_pandas(self, spark: Any) -> bool:
        if self.fetch_mode != FETCH_MODE_ARROW:
            return False
        if self._spark_arrow_conf_session != id(spark):
            try:
                spark.conf.set(SPARK_ARROW_PANDAS_CONF, "true")
                self._spark_arrow_pandas_enabled = True
            except Exception as exc:
                self.log_verbose(f"Spark Arrow transfer for toPandas unavailable: {exc}")
                self._spark_arrow_pandas_enabled = False
            self._spark_arrow_conf_session = id(spark)
        return self._spark_arrow_pandas_enabled

    def execute_query(
        self,
        connection: Any,
        query: str,
        query_id: str,
        benchmark_type: str | None = None,
        scale_factor: float | None = None,
        validate_row_count: bool = True,
        stream_id: int | None = None,
    ) -> dict[str, Any]:
        from benchbox.core.tpchavoc.dialect_compat import SPARK_VARIANT_IDS, normalize_variant_id, rewrite_spark_variant

        benchmark_slug = (benchmark_type or "").lower().replace("-", "")
        needs_rewrite = benchmark_slug == "tpchavoc" or (
            benchmark_type is None and normalize_variant_id(query_id) in SPARK_VARIANT_IDS
        )
        if needs_rewrite:
            rewritten = rewrite_spark_variant(query_id, query)
            if rewritten != query:
                self.log_very_verbose(f"Query {query_id}: Applied fork rewrite for Spark")
            query = rewritten
        return self._execute_query_spark(
            connection=connection,
            query=query,
            query_id=query_id,
            benchmark_type=benchmark_type,
            scale_factor=scale_factor,
            validate_row_count=validate_row_count,
            stream_id=stream_id,
        )

    def _execute_query_spark(
        self,
        connection: Any,
        query: str,
        query_id: str,
        benchmark_type: str | None = None,
        scale_factor: float | None = None,
        validate_row_count: bool = True,
        stream_id: int | None = None,
    ) -> dict[str, Any]:
        if self.dry_run_mode:
            self.capture_sql(query, "query", None)
            return self._build_dry_run_result(query_id)

        spark = connection

        if self.disable_cache and self._catalog_clear_cache_supported:
            clear_start = mono_time()
            try:
                spark.catalog.clearCache()
            except Exception as e:
                return self._build_query_failure_result(query_id, clear_start, e)

        pandas_enabled = self._ensure_spark_arrow_pandas(spark)

        start_time = mono_time()

        try:
            result_df = spark.sql(query)
            fetched = fetch_result(
                self.fetch_mode,
                fetch_rows=result_df.collect,
                fetch_arrow=spark_arrow_source(result_df, pandas_enabled),
            )

            execution_time = elapsed_seconds(start_time)
            actual_row_count = fetched.row_count
            if isinstance(fetched.rows, ArrowRows):
                fetched.rows.convert_columns(spark_arrow_converters(result_df.schema))

            query_stats = {"execution_time_seconds": execution_time}

            validation_result = None
            if validate_row_count and benchmark_type:
                from benchbox.core.validation.query_validation import QueryValidator

                validator = QueryValidator()
                validation_result = validator.validate_query_result(
                    benchmark_type=benchmark_type,
                    query_id=query_id,
                    actual_row_count=actual_row_count,
                    scale_factor=scale_factor,
                    stream_id=stream_id,
                )

                if validation_result.warning_message:
                    self.log_verbose(f"Row count validation: {validation_result.warning_message}")
                elif not validation_result.is_valid:
                    self.log_verbose(f"Row count validation FAILED: {validation_result.error_message}")
                else:
                    self.log_very_verbose(
                        f"Row count validation PASSED: {actual_row_count} rows "
                        f"(expected: {validation_result.expected_row_count})"
                    )

            result_dict = self._build_query_result_with_validation(
                query_id=query_id,
                execution_time=execution_time,
                actual_row_count=actual_row_count,
                first_row=None if fetched.first_row is None else tuple(fetched.first_row),
                validation_result=validation_result,
                materialized_rows=fetched.rows,
                fetch_mode=fetched.fetch_mode,
            )

            result_dict["query_statistics"] = query_stats
            result_dict["resource_usage"] = query_stats

        except Exception as e:
            return self._build_query_failure_result(query_id, start_time, e)

        self._merge_plan_capture_into_result(result_dict, connection, query, query_id)

        return result_dict

    def get_query_plan_parser(self):
        from benchbox.core.query_plans.parsers.spark import SparkQueryPlanParser

        return SparkQueryPlanParser()

    def _validate_data_integrity(
        self, benchmark: Any, connection: Any, table_stats: dict[str, int]
    ) -> tuple[str, dict[str, Any]]:
        validation_details: dict[str, Any] = {}

        try:
            spark = connection
            accessible_tables = []
            inaccessible_tables = []

            for table_name in table_stats:
                try:
                    safe_name = table_name.replace("`", "``")
                    spark.sql(f"SELECT 1 FROM `{safe_name}` LIMIT 1").collect()
                    accessible_tables.append(table_name)
                except Exception as e:
                    self.log_verbose(f"Table {table_name} inaccessible: {e}")
                    inaccessible_tables.append(table_name)

            if inaccessible_tables:
                validation_details["inaccessible_tables"] = inaccessible_tables
                validation_details["constraints_enabled"] = False
                return "FAILED", validation_details
            else:
                validation_details["accessible_tables"] = accessible_tables
                validation_details["constraints_enabled"] = True
                return "PASSED", validation_details

        except Exception as e:
            validation_details["error"] = str(e)
            return "FAILED", validation_details

    def get_table_row_count(self, connection: Any, table: str) -> int:
        try:
            safe_name = table.replace("`", "``")
            result = connection.sql(f"SELECT COUNT(*) FROM `{safe_name}`").collect()
            return result[0][0] if result else 0
        except Exception as e:
            self.log_verbose(f"Could not get row count for {table}: {e}")
            return 0
