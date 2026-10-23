from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Union

from benchbox.base import BaseBenchmark
from benchbox.core.tpcds.generator import TPCDSDataGenerator
from benchbox.core.tpcds_obt.etl.transformer import SUPPORTED_CHANNELS, TPCDSOBTTransformer
from benchbox.core.tpcds_obt.queries import TPCDSOBTQueryManager
from benchbox.utils.cloud_storage import normalize_output_dir
from benchbox.utils.path_utils import get_benchmark_runs_datagen_path

logger = logging.getLogger(__name__)


_DERIVED_TABLE_ALIAS_DIALECTS = frozenset({"doris", "starrocks"})


_ALTERNATE_FORMAT: dict[str, str] = {"parquet": "dat", "dat": "parquet"}


class TPCDSOBTBenchmark(BaseBenchmark):
    GENERATES_OWN_OUTPUT = True
    REQUIRED_LOADED_TABLES = ("tpcds_sales_returns_obt",)

    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Union[str, Path] | None = None,
        tpcds_source_dir: Union[str, Path] | None = None,
        parallel: int = 1,
        force_regenerate: bool = False,
        dimension_mode: str = "full",
        channels: list[str] | None = None,
        output_format: str = "parquet",
        **kwargs: Any,
    ) -> None:
        super().__init__(scale_factor=scale_factor, **kwargs)

        if scale_factor < 1.0:
            raise ValueError("TPC-DS-OBT requires scale_factor >= 1.0 to align with TPC-DS generation.")

        self._name = "TPC-DS One Big Table Benchmark"
        self._version = "0.1"
        self._description = (
            "TPC-DS benchmark adapted to a single wide One Big Table with sales + returns merged across channels."
        )

        if output_dir:
            self.output_dir = normalize_output_dir(output_dir)
        else:
            self.output_dir = get_benchmark_runs_datagen_path("tpcds_obt", scale_factor)

        if tpcds_source_dir:
            self.tpcds_source_dir = normalize_output_dir(tpcds_source_dir)
        else:
            self.tpcds_source_dir = get_benchmark_runs_datagen_path("tpcds", scale_factor)

        self.parallel = parallel
        self.force_regenerate = force_regenerate
        self.dimension_mode = dimension_mode
        self.channels = channels or list(SUPPORTED_CHANNELS)
        self.output_format = output_format

        self._compression_kwargs = {
            k: v for k, v in kwargs.items() if k in ("compress_data", "compression_type", "compression_level")
        }

        self._data_generator: TPCDSDataGenerator | None = None
        self._obt_transformer: TPCDSOBTTransformer | None = None
        self._query_manager: TPCDSOBTQueryManager | None = None
        self.tables: dict[str, Path] = {}
        self.manifest: Path | None = None

    @property
    def data_generator(self) -> TPCDSDataGenerator:
        if self._data_generator is None:
            self._data_generator = TPCDSDataGenerator(
                scale_factor=self.scale_factor,
                output_dir=self.tpcds_source_dir,
                parallel=self.parallel,
                force_regenerate=self.force_regenerate,
                **self._compression_kwargs,
            )
        return self._data_generator

    @property
    def obt_transformer(self) -> TPCDSOBTTransformer:
        if self._obt_transformer is None:
            self._obt_transformer = TPCDSOBTTransformer()
        return self._obt_transformer

    @property
    def query_manager(self) -> TPCDSOBTQueryManager:
        if self._query_manager is None:
            self._query_manager = TPCDSOBTQueryManager()
        return self._query_manager

    def get_data_source_benchmark(self) -> str:
        return "tpcds"

    def generate_data(
        self,
        tables: list[str] | None = None,
        output_format: str | None = None,
    ) -> dict[str, Any]:
        if tables is not None:
            logger.warning("TPC-DS-OBT ignores table selection and always emits a single OBT table.")

        self.output_dir.mkdir(parents=True, exist_ok=True)

        obt_output_format = output_format or self.output_format

        if not self.force_regenerate and self._existing_obt(obt_output_format):
            logger.info("Reusing existing OBT output at %s", self.tables.get("tpcds_sales_returns_obt"))
            return {"table": self.tables.get("tpcds_sales_returns_obt"), "manifest": self.manifest}

        logger.info(
            "Generating base TPC-DS data (scale factor %s) in %s...",
            self.scale_factor,
            self.tpcds_source_dir,
        )
        self.data_generator.generate()

        logger.info(
            "Transforming TPC-DS data from %s into OBT at %s...",
            self.tpcds_source_dir,
            self.output_dir,
        )
        result = self.obt_transformer.transform(
            tpcds_dir=self.tpcds_source_dir,
            output_dir=self.output_dir,
            mode=self.dimension_mode,
            channels=self.channels,
            output_format=obt_output_format,
            scale_factor=self.scale_factor,
        )

        self.tables["tpcds_sales_returns_obt"] = Path(result["table"])
        self.manifest = Path(result["manifest"])

        return result

    def _existing_obt(self, output_format: str) -> bool:
        existing_path = self.output_dir / f"tpcds_sales_returns_obt.{output_format}"
        manifest_path = self.output_dir / "tpcds_sales_returns_obt_manifest.json"
        if existing_path.exists() and manifest_path.exists():
            self.tables["tpcds_sales_returns_obt"] = existing_path
            self.manifest = manifest_path
            return True
        other_format = _ALTERNATE_FORMAT.get(output_format)
        if other_format is None:
            return False
        other_path = self.output_dir / f"tpcds_sales_returns_obt.{other_format}"
        if other_path.exists():
            if output_format == "parquet":
                logger.info(
                    "Found stale .dat output at %s. Regenerating as parquet. "
                    "Delete the .dat manually to reclaim disk space.",
                    other_path,
                )
            else:
                logger.info(
                    "Found existing .parquet output at %s. Regenerating as dat "
                    "because --benchmark-option output_format=dat was specified.",
                    other_path,
                )
        return False

    def get_query(
        self,
        query_id: Union[int, str],
        *,
        params: dict[str, Any] | None = None,
        seed: int | None = None,
        scale_factor: float | None = None,
        dialect: str | None = None,
        **kwargs: Any,
    ) -> str:
        sql = self.query_manager.get_query(
            query_id, params, seed=seed, scale_factor=scale_factor if scale_factor is not None else self.scale_factor
        )
        if dialect:
            sql = self.translate_query_text(sql, dialect, query_id=int(query_id))
        return sql

    def get_all_queries(self) -> dict[str, str]:
        return {str(k): v for k, v in self.query_manager.get_queries(scale_factor=self.scale_factor).items()}

    @staticmethod
    def _rewrite_clickhouse_query(query_id: int, query_text: str) -> str:
        if query_id not in {47, 57, 66}:
            return query_text

        from benchbox.core.tpcds.benchmark.runner import TPCDSBenchmark

        normalized = " ".join(query_text.split()).replace("( ", "(").replace(" )", ")")
        if query_id in {47, 57}:
            rewritten = TPCDSBenchmark._rewrite_clickhouse_monthly_avg_query(query_id, normalized)
            first_select = re.search(r"WITH v1_monthly AS \(SELECT (?P<select>.+?) FROM ", rewritten, re.IGNORECASE)
            v1_marker = rewritten.find(", v1 AS (")
            v1_open = v1_marker + len(", v1 AS ")
            if first_select is None or v1_marker == -1:
                raise ValueError(f"Unsupported ClickHouse OBT Q{query_id} rewrite shape")

            v1_close = TPCDSBenchmark._find_matching_parenthesis(rewritten, v1_open)
            alias_map = dict(
                re.findall(
                    r"\b(obt\.[a-zA-Z_][a-zA-Z0-9_]*)\s+AS\s+([a-zA-Z_][a-zA-Z0-9_]*)", first_select.group("select")
                )
            )
            v1_body = rewritten[v1_open + 1 : v1_close]
            for source_column, alias in alias_map.items():
                v1_body = re.sub(rf"\b{re.escape(source_column)}\b", alias, v1_body)
            rewritten = f"{rewritten[: v1_open + 1]}{v1_body}{rewritten[v1_close:]}"

            marker = ") SELECT * FROM v2 WHERE "
            if marker in rewritten:
                prefix, outer = rewritten.split(marker, 1)
                outer = re.sub(
                    r"\b(d_year|avg_monthly_sales|sum_sales)\b",
                    r"v2.\1",
                    outer,
                    flags=re.IGNORECASE,
                )
                rewritten = f"{prefix}) SELECT v2.* FROM v2 AS v2 WHERE {outer}"
            return rewritten

        normalized = normalized.replace("obt.sold_date_d_year", "d_year").replace("obt.sold_date_d_moy", "d_moy")
        rewritten = TPCDSBenchmark._rewrite_clickhouse_q66(normalized)
        channel_body_end = rewritten.find(") SELECT ")
        if channel_body_end == -1:
            raise ValueError("Unsupported ClickHouse OBT Q66 rewrite shape")
        channel_body = rewritten[len("WITH channel_sales AS (") : channel_body_end]
        branches = re.split(r"\s+UNION ALL\s+", channel_body, maxsplit=1, flags=re.IGNORECASE)
        if len(branches) != 2:
            raise ValueError("Unsupported ClickHouse OBT Q66 channel shape")

        rewritten_branches = []
        for branch in branches:
            select_part, from_part = re.split(r"\s+FROM\s+", branch, maxsplit=1, flags=re.IGNORECASE)
            select_part = re.sub(
                r"(?<!\.)\bd_year\s+AS\s+year\b",
                "obt.sold_date_d_year AS year",
                select_part,
                flags=re.IGNORECASE,
            )
            select_part = re.sub(
                r"(?<!\.)\bd_moy\b",
                "obt.sold_date_d_moy AS d_moy",
                select_part,
                count=1,
                flags=re.IGNORECASE,
            )
            from_part = re.sub(r"(?<!\.)\bd_year\b", "obt.sold_date_d_year", from_part, flags=re.IGNORECASE)
            from_part = re.sub(r"(?<!\.)\bd_moy\b", "obt.sold_date_d_moy", from_part, flags=re.IGNORECASE)
            rewritten_branches.append(f"{select_part} FROM {from_part}")

        rewritten = (
            f"WITH channel_sales AS ({' UNION ALL '.join(rewritten_branches)}){rewritten[channel_body_end + 1 :]}"
        )
        return re.sub(r"\bw_warehouse_sq_ft\b", "warehouse_w_warehouse_sq_ft", rewritten)

    def translate_query_text(self, query_text: str, target_dialect: str, *, query_id: int | None = None) -> str:
        target = target_dialect.lower()
        if target not in {"duckdb", "standard", "ansi"}:
            from benchbox.utils.dialect_utils import translate_sql_query

            translation_target = "clickhouse" if target == "clickhouse-local" else target
            query_text = translate_sql_query(
                query_text,
                target_dialect=translation_target,
                source_dialect="duckdb",
                identify=True,
                rewrites=[("add_derived_table_aliases", {})] if target in _DERIVED_TABLE_ALIAS_DIALECTS else None,
            )

        if target in {"clickhouse", "clickhouse-local"} and query_id is not None:
            query_text = self._rewrite_clickhouse_query(query_id, query_text)
        return query_text

    def get_queries(self, dialect: str | None = None) -> dict[str, str]:
        queries = {str(k): v for k, v in self.query_manager.get_queries(scale_factor=self.scale_factor).items()}
        if dialect:
            queries = {qid: self.translate_query_text(sql, dialect, query_id=int(qid)) for qid, sql in queries.items()}
        return queries

    def supports_dataframe_mode(self) -> bool:
        return True

    def get_dataframe_queries(self) -> list[Any]:
        from benchbox.core.tpcds_obt.dataframe_queries import get_dataframe_queries

        return get_dataframe_queries()

    def execute_query(
        self,
        query_id: Union[int, str],
        connection: Any,
        params: Mapping[str, Any] | None = None,
    ) -> list[tuple[Any, ...]]:
        query = self.get_query(query_id)
        cursor = connection.cursor() if hasattr(connection, "cursor") else connection
        cursor.execute(query, params or {})
        return cursor.fetchall()

    def get_schema(self) -> dict[str, Any]:
        from benchbox.core.tpcds_obt import schema

        table = schema.get_obt_table(self.dimension_mode)
        return {table.name: table}

    def get_create_tables_sql(self, dialect: str = "standard", tuning_config=None) -> str:
        from benchbox.core.tpcds.schema import get_create_all_tables_sql
        from benchbox.core.tpcds_obt import schema
        from benchbox.utils.dialect_utils import translate_sql_query

        enable_primary_keys = tuning_config.primary_keys.enabled if tuning_config else False
        enable_foreign_keys = tuning_config.foreign_keys.enabled if tuning_config else False
        source_ddl = get_create_all_tables_sql(
            enable_primary_keys=enable_primary_keys,
            enable_foreign_keys=enable_foreign_keys,
        )
        source_ddl = re.sub(
            r"(?im)^CREATE TABLE (?!IF NOT EXISTS )",
            "CREATE TABLE IF NOT EXISTS ",
            source_ddl,
        )
        ddl = schema.get_obt_table(self.dimension_mode).get_create_table_sql()
        target = dialect.lower() if dialect else "duckdb"
        if target not in {"duckdb", "postgres", "ansi", "standard"}:
            source_statements = [stmt.strip() for stmt in source_ddl.split(";") if stmt.strip()]
            source_ddl = (
                ";\n\n".join(
                    translate_sql_query(
                        stmt,
                        target_dialect=target,
                        source_dialect="standard",
                        identify=True,
                        scope="schema_ddl",
                    )
                    for stmt in source_statements
                )
                + ";"
            )
            ddl = translate_sql_query(
                ddl, target_dialect=target, source_dialect="standard", identify=True, scope="schema_ddl"
            )
        return f"{source_ddl}\n\n{ddl}"

    def __enter__(self) -> TPCDSOBTBenchmark:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        cleanup = getattr(self, "cleanup", None)
        if callable(cleanup):
            cleanup()
        return False


from benchbox.core.hooks.benchmark_hooks import (
    BenchmarkHookRegistry,
    BenchmarkOptionSpec,
    parse_str_list,
)

BenchmarkHookRegistry.register_option_specs(
    "tpcds_obt",
    BenchmarkOptionSpec(
        name="tpcds_source_dir",
        help="Directory containing TPC-DS source data",
        aliases=("tpcds-source-dir",),
    ),
    BenchmarkOptionSpec(
        name="dimension_mode",
        default="full",
        help="OBT dimension mode",
        choices=("full", "minimal"),
        aliases=("dimension-mode",),
    ),
    BenchmarkOptionSpec(
        name="channels",
        parser=parse_str_list,
        help="Sales channels to include (store,web,catalog)",
    ),
    BenchmarkOptionSpec(
        name="output_format",
        default="parquet",
        help="Output format for OBT data",
        choices=("dat", "parquet"),
        aliases=("output-format",),
    ),
    BenchmarkOptionSpec(
        name="force_regenerate",
        parser=lambda v: v.strip().lower() in ("true", "1", "yes"),
        help="Force data regeneration",
        aliases=("force-regenerate",),
    ),
    benchmark_class=TPCDSOBTBenchmark,
)
