# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

from benchbox.base import BaseBenchmark
from benchbox.core.query_catalog_base import TranslatableQueryMixin

if TYPE_CHECKING:
    from cloudpathlib import CloudPath

    from benchbox.utils.cloud_storage import DatabricksPath

PathLike = Union[Path, "CloudPath", "DatabricksPath"]

from benchbox.core.datavault.schema import (
    TABLES,
    TABLES_BY_NAME,
    get_create_all_tables_sql,
    get_table_loading_order,
)
from benchbox.utils.cloud_storage import normalize_output_dir
from benchbox.utils.path_utils import get_benchmark_runs_datagen_path
from benchbox.utils.scale_factor import format_scale_factor

logger = logging.getLogger(__name__)


class DataVaultBenchmark(TranslatableQueryMixin, BaseBenchmark):
    SUPPORTED_HASH_ALGORITHMS = ("md5", "sha256")

    _source_dialect = "duckdb"
    _native_source_passthrough = True

    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Optional[Union[str, Path]] = None,
        parallel: int = 1,
        force_regenerate: bool = False,
        hash_algorithm: str = "md5",
        record_source: str = "TPCH",
        compress_data: bool = False,
        compression_type: str = "none",
        compression_level: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        if hash_algorithm not in self.SUPPORTED_HASH_ALGORITHMS:
            raise ValueError(
                f"Unsupported hash algorithm: '{hash_algorithm}'. "
                f"Supported algorithms: {self.SUPPORTED_HASH_ALGORITHMS}."
            )

        super().__init__(scale_factor=scale_factor, output_dir=output_dir, **kwargs)

        self._name = "Data Vault Benchmark"
        self._version = "1.0"
        self._description = (
            "Data Vault 2.0 benchmark based on TPC-H source data. "
            "Transforms 8 TPC-H tables into 21 Data Vault tables (7 Hubs, 6 Links, 8 Satellites)."
        )

        self.parallel = parallel
        self.force_regenerate = force_regenerate
        self.hash_algorithm = hash_algorithm
        self.record_source = record_source

        self.compress_data = compress_data
        self.compression_type = compression_type
        self.compression_level = compression_level

        if output_dir is not None:
            self.output_dir = normalize_output_dir(output_dir)
        elif not hasattr(self, "output_dir") or self.output_dir is None:
            self.output_dir = get_benchmark_runs_datagen_path(self._get_benchmark_name(), self.scale_factor)

        self._tpch_generator: Optional[Any] = None
        self._etl_transformer: Optional[Any] = None
        self._query_manager: Optional[Any] = None

        self._tpch_source_dir: Optional[PathLike] = None

    @property
    def tpch_source_dir(self) -> PathLike:
        if self._tpch_source_dir is None:
            sf_str = format_scale_factor(self.scale_factor)
            if self.output_dir is not None:
                parent = self.output_dir.parent
            else:
                parent = Path.cwd() / "benchmark_runs" / "datagen"
            self._tpch_source_dir = parent / f"tpch_{sf_str}"
        result = self._tpch_source_dir
        assert result is not None
        return result

    @property
    def tpch_generator(self) -> Any:
        if self._tpch_generator is None:
            from benchbox.core.tpch.generator import TPCHDataGenerator

            self._tpch_generator = TPCHDataGenerator(
                scale_factor=self.scale_factor,
                output_dir=self.tpch_source_dir,
                parallel=self.parallel,
                compress_data=self.compress_data,
                compression_type=self.compression_type,
                compression_level=self.compression_level,
            )
        return self._tpch_generator

    @property
    def etl_transformer(self) -> Any:
        if self._etl_transformer is None:
            from benchbox.core.datavault.etl.transformer import DataVaultETLTransformer

            self._etl_transformer = DataVaultETLTransformer(
                scale_factor=self.scale_factor,
                hash_algorithm=self.hash_algorithm,
                record_source=self.record_source,
                compress_data=self.compress_data,
                compression_type=self.compression_type,
                compression_level=self.compression_level,
            )
        return self._etl_transformer

    @property
    def query_manager(self) -> Any:
        if self._query_manager is None:
            from benchbox.core.datavault.queries import DataVaultQueryManager

            self._query_manager = DataVaultQueryManager()
        return self._query_manager

    def _check_existing_manifest(self) -> Optional[dict[str, Path]]:
        from benchbox.utils.datagen_manifest import MANIFEST_FILENAME, get_table_files, load_manifest

        manifest_path = self.output_dir / MANIFEST_FILENAME
        if not manifest_path.exists():
            return None

        try:
            manifest = load_manifest(manifest_path)

            if manifest.get("benchmark", "").lower() != "datavault":
                logger.debug("Manifest benchmark mismatch, regenerating")
                return None

            if float(manifest.get("scale_factor", 0)) != float(self.scale_factor):
                logger.debug("Manifest scale factor mismatch, regenerating")
                return None

            tables = manifest.get("tables", {})
            file_paths: dict[str, Path] = {}

            for table_name in tables:
                entries = get_table_files(manifest, table_name)
                if entries:
                    rel_path = entries[0].get("path", "")
                    full_path = self.output_dir / rel_path
                    if full_path.exists():
                        file_paths[table_name] = full_path
                    else:
                        logger.debug(f"File {full_path} from manifest does not exist")
                        return None

            if len(file_paths) < 21:
                logger.debug(f"Only found {len(file_paths)} tables in manifest, expected 21")
                return None

            return file_paths

        except Exception as e:
            logger.debug(f"Error reading manifest: {e}")
            return None

    def generate_data(
        self,
        tables: Optional[list[str]] = None,
        output_format: str = "tbl",
    ) -> dict[str, Any]:
        if self.output_dir is None:
            raise ValueError("output_dir must be set before generating data")

        self.output_dir.mkdir(parents=True, exist_ok=True)

        if not self.force_regenerate:
            existing_files = self._check_existing_manifest()
            if existing_files:
                logger.info(
                    f"Valid Data Vault data found at scale factor {self.scale_factor} "
                    f"({len(existing_files)} tables). Skipping regeneration."
                )
                self.tables = existing_files
                return existing_files

        if self.force_regenerate:
            logger.info("Force regeneration requested")

        logger.info(f"Generating Data Vault data at scale factor {self.scale_factor}")

        logger.info("Step 1/2: Generating TPC-H source data...")
        logger.info(f"  TPC-H source directory: {self.tpch_source_dir}")
        tpch_files = self.tpch_generator.generate()
        logger.info(f"Generated {len(tpch_files)} TPC-H source files")

        logger.info("Step 2/2: Transforming to Data Vault format...")
        dv_files = self.etl_transformer.transform(
            tpch_dir=self.tpch_source_dir,
            output_dir=self.output_dir,
            tables=tables,
            output_format=output_format,
        )
        logger.info(f"Generated {len(dv_files)} Data Vault tables")

        self.tables = dv_files
        return dv_files

    def get_query(self, query_id: Union[int, str], dialect: Optional[str] = None) -> str:
        return self.translate_for_dialect(self.query_manager.get_query(query_id), dialect)

    def get_all_queries(self) -> dict[str, str]:
        return self.query_manager.get_all_queries()

    def get_queries(self, dialect: Optional[str] = None) -> dict[str, str]:
        return {str(k): self.translate_for_dialect(v, dialect) for k, v in self.query_manager.get_all_queries().items()}

    def execute_query(
        self,
        query_id: Union[int, str],
        connection: Any,
        params: Optional[Mapping[str, Any]] = None,
    ) -> list[tuple[Any, ...]]:
        query = self.get_query(query_id)
        cursor = connection.cursor() if hasattr(connection, "cursor") else connection
        cursor.execute(query)
        return cursor.fetchall()

    def get_schema(self) -> dict[str, Any]:
        return TABLES_BY_NAME.copy()

    def get_create_tables_sql(
        self,
        dialect: str = "duckdb",
        tuning_config: Optional[Any] = None,
    ) -> str:
        from benchbox.utils.dialect_utils import translate_sql_query

        enable_pk = True
        enable_fk = True

        if tuning_config is not None:
            enable_pk = getattr(tuning_config.primary_keys, "enabled", True)
            enable_fk = getattr(tuning_config.foreign_keys, "enabled", True)

        ddl = get_create_all_tables_sql(
            enable_primary_keys=enable_pk,
            enable_foreign_keys=enable_fk,
        )

        target = dialect.lower() if dialect else "duckdb"
        if target not in {"duckdb", "postgres", "ansi", "standard"}:
            statements = [stmt.strip() for stmt in ddl.split(";\n") if stmt.strip()]
            translated = [
                translate_sql_query(
                    stmt,
                    target_dialect=target,
                    source_dialect="standard",
                    identify=True,
                    scope="schema_ddl",
                )
                for stmt in statements
            ]
            ddl = ";\n\n".join(translated) + ";"

        return ddl

    def get_table_loading_order(self, available_tables: Optional[list[str]] = None) -> list[str]:
        full_order = get_table_loading_order()
        if available_tables is None:
            return full_order
        available_set = set(available_tables)
        return [t for t in full_order if t in available_set]

    def get_table_count(self) -> int:
        return len(TABLES)

    def supports_dataframe_mode(self) -> bool:
        return True

    def get_dataframe_queries(self) -> list[Any]:
        from benchbox.core.datavault.dataframe_queries import DATAVAULT_DATAFRAME_QUERIES

        return DATAVAULT_DATAFRAME_QUERIES.get_all_queries()

    def _get_benchmark_name(self) -> str:
        return "datavault"

    def cleanup(self) -> None:
        if self._tpch_generator is not None and hasattr(self._tpch_generator, "cleanup"):
            self._tpch_generator.cleanup()
        super().cleanup()

    def __enter__(self) -> "DataVaultBenchmark":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        self.cleanup()
        return False


from benchbox.core.hooks.benchmark_hooks import (
    BenchmarkHookRegistry,
    BenchmarkOptionSpec,
)

BenchmarkHookRegistry.register_option_specs(
    "datavault",
    BenchmarkOptionSpec(
        name="hash_algorithm",
        default="md5",
        help="Hash algorithm for hub/link keys",
        choices=DataVaultBenchmark.SUPPORTED_HASH_ALGORITHMS,
        aliases=("hash-algorithm",),
    ),
    BenchmarkOptionSpec(
        name="record_source",
        default="TPCH",
        help="Record source identifier for audit columns",
        aliases=("record-source",),
    ),
    BenchmarkOptionSpec(
        name="force_regenerate",
        parser=lambda v: v.strip().lower() in ("true", "1", "yes"),
        help="Force data regeneration",
        aliases=("force-regenerate",),
    ),
    benchmark_class=DataVaultBenchmark,
)
