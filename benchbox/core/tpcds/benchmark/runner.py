import dataclasses
import logging
import re
import time
import warnings
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import UnifiedTuningConfiguration

from benchbox.base import BaseBenchmark, GeneratorOutputDirMixin
from benchbox.core.connection import DatabaseConnection as _DatabaseConnection
from benchbox.core.throughput.containment import check_phase_boundary
from benchbox.core.throughput.entrypoints import (
    require_adapter,
    require_stream_minimum,
    warn_legacy_throughput_api,
)
from benchbox.core.validation import (
    DatabaseValidationEngine,
    DataValidationEngine,
    ValidationResult,
)
from benchbox.utils.file_format import get_delimiter_for_file
from benchbox.utils.printing import emit
from benchbox.utils.sql_parsing import find_matching_parenthesis

from ..c_tools import TPCDSCTools
from ..compliance import validate_tpcds_scale
from ..generator import TPCDSDataGenerator
from ..queries import TPCDSQueryManager
from ..schema import TABLES
from .clickhouse_overrides import rewrite_q10_for_clickhouse, rewrite_q35_for_clickhouse
from .config import MaintenanceTestConfig, ThroughputTestConfig
from .results import (
    MaintenanceTestResult,
    ThroughputTestResult,
)


def _execute_single_stream(stream_id: int, stream_file: Path) -> dict[str, Any]:
    raise NotImplementedError(
        f"_execute_single_stream (stream {stream_id}, {stream_file}) does not "
        "execute SQL. It previously faked success by counting '-- Query' "
        "comment lines in the stream file without running anything against a "
        "database. Use `benchbox run --phases throughput` (TPCDSThroughputTest) "
        "for real TPC-DS throughput execution."
    )


def _aggregate_stream_results(stream_results: list[dict[str, Any]], start_time: float) -> dict[str, Any]:
    import time

    end_time = time.time()
    return {
        "start_time": start_time,
        "end_time": end_time,
        "total_duration": end_time - start_time,
        "num_streams": len(stream_results),
        "streams_executed": len(stream_results),
        "streams_successful": len([r for r in stream_results if r.get("success", False)]),
        "streams_failed": len([r for r in stream_results if not r.get("success", False)]),
        "total_queries_executed": sum(r["queries_executed"] for r in stream_results),
        "total_queries_successful": sum(r["queries_successful"] for r in stream_results),
        "total_queries_failed": sum(r["queries_failed"] for r in stream_results),
        "success": all(r.get("success", False) for r in stream_results),
        "errors": [r["error"] for r in stream_results if r.get("error")],
        "stream_results": stream_results,
    }


class TPCDSBenchmark(GeneratorOutputDirMixin, BaseBenchmark):
    _MONTH_ALIASES: tuple[tuple[str, int], ...] = (
        ("jan", 1),
        ("feb", 2),
        ("mar", 3),
        ("apr", 4),
        ("may", 5),
        ("jun", 6),
        ("jul", 7),
        ("aug", 8),
        ("sep", 9),
        ("oct", 10),
        ("nov", 11),
        ("dec", 12),
    )

    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Optional[Union[str, Path]] = None,
        verbose: Union[int, bool] = 0,
        parallel: int = 1,
        force_regenerate: bool = False,
        official: bool = False,
        **kwargs: Any,
    ) -> None:
        if not isinstance(scale_factor, (int, float)):
            raise TypeError(f"scale_factor must be a number, got {type(scale_factor).__name__}")

        self.compliance_class = validate_tpcds_scale(scale_factor, official=official)

        if not isinstance(parallel, int):
            raise TypeError(f"parallel must be an integer, got {type(parallel).__name__}")
        if parallel < 1:
            raise ValueError(f"parallel must be positive, got {parallel}")

        kwargs = dict(kwargs)
        quiet = kwargs.pop("quiet", False)

        super().__init__(scale_factor=scale_factor, output_dir=output_dir, verbose=verbose, quiet=quiet, **kwargs)
        self._name = "TPC-DS Benchmark"
        self.parallel = parallel

        self.query_manager = TPCDSQueryManager()
        self.data_generator = TPCDSDataGenerator(
            scale_factor=scale_factor,
            parallel=parallel,
            output_dir=self.output_dir,
            verbose=verbose,
            quiet=quiet,
            force_regenerate=force_regenerate,
            **kwargs,
        )
        self.c_tools = TPCDSCTools()
        self.tables: dict[str, Path] = {}

        self._data_validation_engine = DataValidationEngine()
        self._db_validation_engine = DatabaseValidationEngine()
        self.enable_validation = kwargs.get("enable_validation", False)

    @property
    def queries(self) -> TPCDSQueryManager:
        return self.query_manager

    @property
    def generator(self) -> TPCDSDataGenerator:
        return self.data_generator

    def generate_data(self) -> list[Union[str, Path]]:
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.data_generator.output_dir = self.output_dir

        self.log_verbose(f"Generating TPC-DS data at scale factor {self.scale_factor}...")
        self.log_verbose(f"Output directory: {self.output_dir}")

        self.tables = self.data_generator.generate()

        if self.verbose_enabled:
            self.logger.info(f"Generated {len(self.tables)} TPC-DS tables:")
            for table_name, file_path in self.tables.items():
                self.logger.info(f"  - {table_name}: {file_path}")

        return list(self.tables.values())

    def get_queries(self, dialect: Optional[str] = None, base_dialect: Optional[str] = None) -> dict[str, str]:
        src = (base_dialect or "netezza").lower()
        tgt = (dialect or src).lower()

        int_queries = self.query_manager.get_all_queries(dialect=src, scale_factor=self.data_generator.scale_factor)
        base_queries = {str(k): v for k, v in int_queries.items()}

        translated_queries = {}
        for query_id, query_text in base_queries.items():
            translated = self.translate_query_text(query_text, src, tgt)
            translated_queries[query_id] = self._apply_target_dialect_overrides(int(query_id), translated, tgt)
        return translated_queries

    def get_query(
        self,
        query_id: int,
        *,
        params: Optional[dict[str, Any]] = None,
        seed: Optional[int] = None,
        scale_factor: Optional[float] = None,
        dialect: Optional[str] = None,
        base_dialect: Optional[str] = None,
        variant: Optional[str] = None,
        **kwargs,
    ) -> str:
        self._validate_get_query_args(query_id, scale_factor, seed, id_range=(1, 99))

        if params is None:
            params = {}

        actual_seed = seed if seed is not None else params.get("seed")
        actual_scale_factor = (
            scale_factor if scale_factor is not None else params.get("scale_factor", self.data_generator.scale_factor)
        )

        actual_seed = self._resolve_seed_from_stream_or_permutation(
            actual_seed, query_id, params.get("stream_id"), params.get("permutation")
        )

        src = (base_dialect or "netezza").lower()
        tgt = (dialect or src).lower()
        query = self._generate_tpcds_query(query_id, variant, actual_seed, actual_scale_factor, src)
        translated = self.translate_query_text(query, src, tgt)
        return self._apply_target_dialect_overrides(query_id, translated, tgt, variant=variant)

    def _apply_target_dialect_overrides(
        self,
        query_id: int,
        query: str,
        target_dialect: str,
        *,
        variant: str | None = None,
    ) -> str:
        target = target_dialect.lower()

        if query_id in {36, 70, 86} and target == "postgres":
            return self._rewrite_postgres_rollup_order_aliases(query_id, query)

        if query_id == 90 and target in {"postgres", "datafusion"}:
            return self._rewrite_postgres_q90_zero_denominator(query)

        if query_id == 90 and target in {"spark", "lakesail"}:
            return self._rewrite_spark_q90_zero_denominator(query)

        if query_id == 90:
            return self._rewrite_default_q90_zero_denominator(query)

        if "clickhouse" not in target:
            return query

        if query_id in (47, 57):
            return self._rewrite_clickhouse_monthly_avg_query(query_id, query)
        if query_id == 10 and variant is None:
            return rewrite_q10_for_clickhouse(query)
        if query_id == 35 and variant is None:
            return rewrite_q35_for_clickhouse(query)
        if query_id == 66:
            return self._rewrite_clickhouse_q66(query)
        return query

    @staticmethod
    def _rewrite_spark_q90_zero_denominator(query: str) -> str:
        return re.sub(
            r"/\s+CAST\(`pmc`\s+AS\s+DECIMAL\(15,\s*4\)\)",
            "/ NULLIF(CAST(`pmc` AS DECIMAL(15, 4)), 0)",
            query,
            count=1,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _rewrite_postgres_q90_zero_denominator(query: str) -> str:
        return re.sub(
            r"/\s+CAST\(pmc\s+AS\s+DECIMAL\(15,\s*4\)\)",
            "/ NULLIF(CAST(pmc AS DECIMAL(15, 4)), 0)",
            query,
            count=1,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _rewrite_default_q90_zero_denominator(query: str) -> str:
        return re.sub(
            r'/\s*CAST\(\s*(?P<denominator>pmc|`pmc`|"pmc"|\[pmc\])\s+AS\s+'
            r"(?P<type>(?:DECIMAL|NUMERIC)\s*\(\s*15\s*,\s*4\s*\))\s*\)",
            lambda match: f"/CAST(NULLIF({match.group('denominator')}, 0) AS {match.group('type')})",
            query,
            count=1,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _rewrite_postgres_rollup_order_aliases(query_id: int, query: str) -> str:
        rollup_columns_by_query = {
            36: ("i_category", "i_class", "i_category"),
            70: ("s_state", "s_county", "s_state"),
            86: ("i_category", "i_class", "i_category"),
        }
        rollup_columns = rollup_columns_by_query.get(query_id)
        if rollup_columns is None:
            return query

        first_col, second_col, hierarchy_key = rollup_columns
        hierarchy_expr = f"GROUPING({first_col}) + GROUPING({second_col})"
        return re.sub(
            rf"ORDER BY\s+lochierarchy\s+DESC,\s+CASE\s+WHEN\s+lochierarchy\s*=\s*0\s+THEN\s+{hierarchy_key}\s+END",
            f"ORDER BY {hierarchy_expr} DESC, CASE WHEN {hierarchy_expr} = 0 THEN {hierarchy_key} END",
            query,
            count=1,
            flags=re.IGNORECASE,
        )

    @classmethod
    def _rewrite_clickhouse_monthly_avg_query(cls, query_id: int, query: str) -> str:
        if "AVG(SUM(" not in query.upper():
            return query

        prefix = "WITH v1 AS ("
        if not query.startswith(prefix):
            raise ValueError(f"Unsupported ClickHouse Q{query_id} shape: expected leading v1 CTE")

        open_index = len(prefix) - 1
        close_index = cls._find_matching_parenthesis(query, open_index)
        v1_body = query[open_index + 1 : close_index]
        suffix = cls._alias_clickhouse_rank_neighbor_projections(query[close_index + 1 :])

        pattern = re.compile(
            r"SELECT (?P<select_dims>.+), SUM\((?P<sum_expr>.+)\) AS sum_sales, "
            r"AVG\(SUM\((?P=sum_expr)\)\) OVER \(PARTITION BY (?P<avg_partition>.+)\) AS avg_monthly_sales, "
            r"RANK\(\) OVER \(PARTITION BY (?P<rank_partition>.+) ORDER BY (?P<rank_order>.+)\) AS rn "
            r"FROM (?P<from_clause>.+) GROUP BY (?P<group_by>.+)",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.fullmatch(v1_body)
        if match is None:
            raise ValueError(f"Unsupported ClickHouse Q{query_id} monthly aggregate shape")

        groups = match.groupdict()
        rewritten = (
            f"WITH v1_monthly AS (SELECT {groups['select_dims']}, SUM({groups['sum_expr']}) AS sum_sales "
            f"FROM {groups['from_clause']} GROUP BY {groups['group_by']}), "
            f"v1 AS (SELECT {groups['select_dims']}, sum_sales, "
            f"AVG(sum_sales) OVER (PARTITION BY {groups['avg_partition']}) AS avg_monthly_sales, "
            f"RANK() OVER (PARTITION BY {groups['rank_partition']} ORDER BY {groups['rank_order']}) AS rn "
            f"FROM v1_monthly){suffix}"
        )
        if "AVG(SUM(" in rewritten.upper():
            raise ValueError(f"ClickHouse Q{query_id} rewrite still contains nested aggregate-over-window pattern")
        return rewritten

    @staticmethod
    def _alias_clickhouse_rank_neighbor_projections(suffix: str) -> str:
        pattern = r"(,\s*v2\s+AS\s+\(SELECT\s+)(.*?)(\s+FROM\s+v1,\s+v1\s+AS\s+v1_lag,\s+v1\s+AS\s+v1_lead)"

        def alias_projection(match: re.Match[str]) -> str:
            aliased_items = []
            for item in match.group(2).split(","):
                stripped = item.strip()
                plain_v1_column = re.fullmatch(r"v1\.([a-zA-Z_][a-zA-Z0-9_]*)", stripped, re.IGNORECASE)
                if plain_v1_column:
                    column_name = plain_v1_column.group(1)
                    aliased_items.append(f"v1.{column_name} AS {column_name}")
                else:
                    aliased_items.append(stripped)
            return f"{match.group(1)}{', '.join(aliased_items)}{match.group(3)}"

        return re.sub(pattern, alias_projection, suffix, flags=re.IGNORECASE | re.DOTALL)

    @classmethod
    def _rewrite_clickhouse_q66(cls, query: str) -> str:
        if "SUM(jan_sales)" not in query and "SUM(jan_net)" not in query:
            return query

        from_marker = " FROM ("
        outer_from_index = query.find(from_marker)
        if outer_from_index == -1:
            raise ValueError("Unsupported ClickHouse Q66 shape: missing derived table")

        outer_select = query[len("SELECT ") : outer_from_index]
        subquery_open = query.find("(", outer_from_index + len(" FROM "))
        if subquery_open == -1:
            raise ValueError("Unsupported ClickHouse Q66 shape: missing derived table body")

        subquery_close = cls._find_matching_parenthesis(query, subquery_open)
        union_body = query[subquery_open + 1 : subquery_close]
        suffix = query[subquery_close + 1 :]

        outer_match = re.match(
            r"(?P<dimensions>.+), SUM\(jan_sales\) AS jan_sales, ",
            outer_select,
            re.IGNORECASE | re.DOTALL,
        )
        if outer_match is None:
            raise ValueError("Unsupported ClickHouse Q66 shape: missing outer dimension projection")

        suffix_match = re.fullmatch(
            r"\s+AS x GROUP BY (?P<group_by>.+) ORDER BY (?P<order_by>.+) LIMIT (?P<limit>\d+)",
            suffix,
            re.IGNORECASE | re.DOTALL,
        )
        if suffix_match is None:
            raise ValueError("Unsupported ClickHouse Q66 shape: missing outer GROUP BY / ORDER BY / LIMIT")

        branch_a, branch_b = cls._split_top_level_union_all(union_body)
        parsed_a = cls._parse_clickhouse_q66_branch(branch_a)
        parsed_b = cls._parse_clickhouse_q66_branch(branch_b)

        dimensions = outer_match.group("dimensions")
        grouped_columns = suffix_match.group("group_by")
        order_by = suffix_match.group("order_by")
        limit = suffix_match.group("limit")

        monthly_sales = ", ".join(
            f"SUM(CASE WHEN d_moy = {month_num} THEN sales_amount ELSE 0 END) AS {month_name}_sales"
            for month_name, month_num in cls._MONTH_ALIASES
        )
        monthly_sales_per_sq_foot = ", ".join(
            f"SUM(CASE WHEN d_moy = {month_num} THEN sales_amount / w_warehouse_sq_ft ELSE 0 END) "
            f"AS {month_name}_sales_per_sq_foot"
            for month_name, month_num in cls._MONTH_ALIASES
        )
        monthly_net = ", ".join(
            f"SUM(CASE WHEN d_moy = {month_num} THEN net_amount ELSE 0 END) AS {month_name}_net"
            for month_name, month_num in cls._MONTH_ALIASES
        )

        rewritten = (
            "WITH channel_sales AS ("
            f"SELECT {parsed_a['dimensions']}, {parsed_a['ship_expr']} AS ship_carriers, d_year AS year, d_moy, "
            f"{parsed_a['sales_expr']} AS sales_amount, {parsed_a['net_expr']} AS net_amount "
            f"FROM {parsed_a['from_clause']} WHERE {parsed_a['where_clause']} UNION ALL "
            f"SELECT {parsed_b['dimensions']}, {parsed_b['ship_expr']} AS ship_carriers, d_year AS year, d_moy, "
            f"{parsed_b['sales_expr']} AS sales_amount, {parsed_b['net_expr']} AS net_amount "
            f"FROM {parsed_b['from_clause']} WHERE {parsed_b['where_clause']}) "
            f"SELECT {dimensions}, {monthly_sales}, {monthly_sales_per_sq_foot}, {monthly_net} "
            f"FROM channel_sales GROUP BY {grouped_columns} ORDER BY {order_by} LIMIT {limit}"
        )
        if re.search(r"\bSUM\s*\(\s*jan_(?:sales|net)\b", rewritten, re.IGNORECASE):
            raise ValueError("ClickHouse Q66 rewrite still contains analyzer-hostile alias aggregation")
        return rewritten

    @classmethod
    def _parse_clickhouse_q66_branch(cls, branch: str) -> dict[str, str]:
        sales_patterns = []
        net_patterns = []
        for month_name, month_num in cls._MONTH_ALIASES:
            if month_num == 1:
                sales_patterns.append(
                    rf"SUM\(CASE WHEN d_moy = {month_num} THEN (?P<sales_expr>.+?) ELSE 0 END\) AS {month_name}_sales"
                )
                net_patterns.append(
                    rf"SUM\(CASE WHEN d_moy = {month_num} THEN (?P<net_expr>.+?) ELSE 0 END\) AS {month_name}_net"
                )
            else:
                sales_patterns.append(
                    rf"SUM\(CASE WHEN d_moy = {month_num} THEN (?P=sales_expr) ELSE 0 END\) AS {month_name}_sales"
                )
                net_patterns.append(
                    rf"SUM\(CASE WHEN d_moy = {month_num} THEN (?P=net_expr) ELSE 0 END\) AS {month_name}_net"
                )

        pattern = re.compile(
            rf"SELECT (?P<dimensions>.+), (?P<ship_expr>.+) AS ship_carriers, d_year AS year, "
            rf"{', '.join(sales_patterns)}, {', '.join(net_patterns)} "
            rf"FROM (?P<from_clause>.+) WHERE (?P<where_clause>.+) GROUP BY (?P<group_by>.+)",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.fullmatch(branch)
        if match is None:
            raise ValueError("Unsupported ClickHouse Q66 branch shape")
        return match.groupdict()

    @staticmethod
    def _find_matching_parenthesis(text: str, open_index: int) -> int:
        return find_matching_parenthesis(text, open_index)

    @classmethod
    def _split_top_level_union_all(cls, query: str) -> tuple[str, str]:
        marker = " UNION ALL "
        depth = 0
        in_single_quote = False
        index = 0

        while index < len(query):
            char = query[index]
            if char == "'":
                if in_single_quote and index + 1 < len(query) and query[index + 1] == "'":
                    index += 2
                    continue
                in_single_quote = not in_single_quote
            elif not in_single_quote:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                elif depth == 0 and query.startswith(marker, index):
                    return query[:index], query[index + len(marker) :]
            index += 1

        raise ValueError("Expected top-level UNION ALL in ClickHouse Q66")

    @staticmethod
    def _validate_get_query_args(
        query_id: int, scale_factor: Optional[float], seed: Optional[int], *, id_range: tuple[int, int]
    ) -> None:
        if not isinstance(query_id, int):
            raise TypeError(f"query_id must be an integer, got {type(query_id).__name__}")
        lo, hi = id_range
        if not (lo <= query_id <= hi):
            raise ValueError(f"Query ID must be {lo}-{hi}, got {query_id}")
        if scale_factor is not None:
            if not isinstance(scale_factor, (int, float)):
                raise TypeError(f"scale_factor must be a number, got {type(scale_factor).__name__}")
            if scale_factor <= 0:
                raise ValueError(f"scale_factor must be positive, got {scale_factor}")
        if seed is not None and not isinstance(seed, int):
            raise TypeError(f"seed must be an integer, got {type(seed).__name__}")

    @staticmethod
    def _resolve_seed_from_stream_or_permutation(
        actual_seed: Optional[int], query_id: int, stream_id: Optional[int], permutation: Optional[list]
    ) -> Optional[int]:
        if stream_id is not None:
            if actual_seed is None:
                return stream_id
        elif permutation is not None and actual_seed is None:
            try:
                return permutation.index(query_id)
            except ValueError:
                pass
        return actual_seed

    def _generate_tpcds_query(
        self,
        query_id: int,
        variant: Optional[str],
        actual_seed: Optional[int],
        actual_scale_factor: float,
        src: str,
    ) -> str:
        if variant is not None:
            composite_id = f"{query_id}{variant}"
            try:
                return self.query_manager.dsqgen.generate(
                    composite_id,
                    seed=actual_seed,
                    scale_factor=actual_scale_factor,
                    dialect=src,
                )
            except AttributeError:
                return self.query_manager.get_query(
                    query_id,
                    seed=actual_seed,
                    scale_factor=actual_scale_factor,
                    dialect=src,
                )
        return self.query_manager.get_query(
            query_id,
            seed=actual_seed,
            scale_factor=actual_scale_factor,
            dialect=src,
        )

    def _normalize_interval_syntax(self, query: str) -> str:
        import re

        pattern = r"([+\-])\s+(\d+)\s+days"

        replacement = r"\1 INTERVAL \2 DAY"

        return re.sub(pattern, replacement, query, flags=re.IGNORECASE)

    def _fix_query58_ambiguity(self, query: str) -> str:
        if "ss_items" not in query.lower() or "cs_items" not in query.lower() or "ws_items" not in query.lower():
            return query

        import re

        pattern = r'(ORDER\s+BY\s+)"item_id"'
        replacement = r'\1"ss_items"."item_id"'
        query = re.sub(pattern, replacement, query, flags=re.IGNORECASE)

        pattern_unquoted = r"(ORDER\s+BY\s+)item_id\b(?!\.)"
        replacement_unquoted = r"\1ss_items.item_id"
        query = re.sub(pattern_unquoted, replacement_unquoted, query, flags=re.IGNORECASE)

        return query

    def translate_query_text(self, query: str, source_dialect: str, target_dialect: str) -> str:
        from benchbox.utils.dialect_utils import fix_postgres_date_arithmetic, translate_sql_query

        src = (source_dialect or "netezza").lower()
        tgt = (target_dialect or src).lower()

        post_procs = [self._fix_query58_ambiguity]
        if tgt == "postgres":
            post_procs.append(fix_postgres_date_arithmetic)

        return translate_sql_query(
            query=query,
            target_dialect=tgt,
            source_dialect=src,
            identify=True,
            pre_processors=[self._normalize_interval_syntax],
            post_processors=post_procs,
        )

    def generate_table_data(self, table_name: str, output_dir: Optional[str] = None) -> Iterator[str]:
        if output_dir:
            return self.c_tools.generate_data_table(table_name, self.scale_factor, output_dir=output_dir)
        else:
            return self.generator.generate_table(table_name, self.scale_factor)

    def get_available_tables(self) -> list[str]:
        from benchbox.core.tpcds.constants import TPCDS_TABLE_NAMES

        return list(TPCDS_TABLE_NAMES)

    def get_table_loading_order(self, available_tables: list[str]) -> list[str]:
        from benchbox.core.tpcds.constants import TPCDS_TABLE_LOADING_ORDER

        tpcds_loading_order = TPCDS_TABLE_LOADING_ORDER

        ordered_tables = [t for t in tpcds_loading_order if t in available_tables]

        remaining_tables = [t for t in available_tables if t not in ordered_tables]
        ordered_tables.extend(remaining_tables)

        return ordered_tables

    def get_available_queries(self) -> list[int]:
        return list(range(1, 100))

    def get_schema(self) -> dict[str, dict[str, Any]]:
        schema = {}
        for table in TABLES:
            table_schema = {
                "name": table.name.lower(),
                "columns": [
                    {
                        "name": col.name,
                        "type": col.get_sql_type(),
                        "nullable": col.nullable,
                        "primary_key": col.primary_key,
                        "foreign_key": col.foreign_key,
                    }
                    for col in table.columns
                ],
            }
            schema[table.name.lower()] = table_schema
        return schema

    def get_create_tables_sql(
        self,
        dialect: str = "standard",
        tuning_config: Optional["UnifiedTuningConfiguration"] = None,
    ) -> str:
        from benchbox.core.tpcds.schema import get_create_all_tables_sql

        enable_primary_keys = tuning_config.primary_keys.enabled if tuning_config else False
        enable_foreign_keys = tuning_config.foreign_keys.enabled if tuning_config else False

        return get_create_all_tables_sql(
            enable_primary_keys=enable_primary_keys,
            enable_foreign_keys=enable_foreign_keys,
        )

    def generate_streams(
        self,
        num_streams: int = 1,
        rng_seed: Optional[int] = None,
        streams_output_dir: Optional[Union[str, Path]] = None,
    ) -> list[Path]:
        from benchbox.core.tpcds.streams import create_standard_streams

        streams_output_dir = self.output_dir / "streams" if streams_output_dir is None else Path(streams_output_dir)

        streams_output_dir.mkdir(parents=True, exist_ok=True)

        stream_manager = create_standard_streams(
            query_manager=self.query_manager,
            num_streams=num_streams,
            base_seed=rng_seed or 42,
        )

        streams = stream_manager.generate_streams()

        stream_files = []
        for stream_id, stream_queries in streams.items():
            stream_file = streams_output_dir / f"stream_{stream_id}.sql"

            with open(stream_file, "w", encoding="utf-8") as f:
                f.write(f"-- TPC-DS Stream {stream_id}\n")
                f.write(f"-- Scale Factor: {self.scale_factor}\n")
                f.write(f"-- RNG Seed: {rng_seed or 42}\n")
                f.write("-- Generated using TPC-DS query manager\n")
                f.write("-- Compliant with TPC-DS specification\n\n")

                for query in stream_queries:
                    if query.sql:
                        f.write(
                            f"-- Query {query.query_id}{query.variant or ''} (Stream {stream_id}, Position {query.position + 1})\n"
                        )
                        f.write(query.sql)
                        f.write("\n\n")

            stream_files.append(stream_file)

        if self.verbose:
            emit(f"Generated {len(stream_files)} TPC-DS streams in {streams_output_dir}")

        return stream_files

    def get_stream_info(self, stream_id: int) -> dict[str, Any]:
        from benchbox.core.tpcds.streams import create_standard_streams

        max_reasonable_streams = 100
        if stream_id < 0:
            raise ValueError(f"Invalid stream ID: {stream_id}. Stream ID must be non-negative.")
        if stream_id >= max_reasonable_streams:
            raise ValueError(
                f"Invalid stream ID: {stream_id}. "
                f"Stream ID must be less than {max_reasonable_streams}. "
                f"Use generate_streams() with num_streams parameter for custom stream counts."
            )

        stream_manager = create_standard_streams(
            query_manager=self.query_manager,
            num_streams=stream_id + 1,
            base_seed=42,
        )

        streams = stream_manager.generate_streams()

        if stream_id not in streams:
            raise ValueError(f"Stream {stream_id} does not exist")

        stream_queries = streams[stream_id]

        unique_queries = set()
        total_queries = 0
        for query in stream_queries:
            query_key = f"{query.query_id}{query.variant or ''}"
            unique_queries.add(query_key)
            total_queries += 1

        query_order = [q.query_id for q in stream_queries]

        return {
            "stream_id": stream_id,
            "scale_factor": self.scale_factor,
            "query_count": total_queries,
            "unique_query_count": len(unique_queries),
            "rng_seed": 42 + stream_id,
            "parameter_seed": 42 + stream_id + 1000,
            "query_order": query_order,
            "query_list": [f"{q.query_id}{q.variant or ''}" for q in stream_queries],
            "permutation_mode": "tpcds_standard",
        }

    def get_all_streams_info(self, num_streams: int = 2) -> list[dict[str, Any]]:
        all_info = []
        for stream_id in range(num_streams):
            try:
                stream_info = self.get_stream_info(stream_id)
                all_info.append(stream_info)
            except (ValueError, KeyError) as e:
                if self.verbose:
                    emit(f"Warning: Could not get info for stream {stream_id}: {e}")
                continue
        return all_info

    def run_streams(
        self,
        connection: Any,
        stream_files: Optional[list[Path]] = None,
        concurrent: bool = True,
        dialect: str = "standard",
    ) -> dict[str, Any]:
        raise NotImplementedError(
            "TPCDSBenchmark.run_streams does not execute SQL against "
            "`connection`. It previously faked success by counting "
            "'-- Query' comment lines in stream files without running "
            "anything. Use `benchbox run --phases throughput` "
            "(TPCDSThroughputTest) for TPC-DS throughput runs."
        )

    def _load_data(self, connection: _DatabaseConnection) -> None:
        import logging

        logger = logging.getLogger(__name__)

        if not self.tables:
            raise ValueError("No data has been generated. Call generate_data() first.")

        logger.info("Loading TPC-DS data into database...")

        schema_sql = self.get_create_tables_sql()

        logger.info("Creating TPC-DS tables...")
        try:
            if ";" in schema_sql:
                statements = [stmt.strip() for stmt in schema_sql.split(";") if stmt.strip()]
                for statement in statements:
                    connection.execute(statement)
            else:
                connection.execute(schema_sql)
            connection.commit()
            logger.info("✅ Tables created")
        except Exception as e:
            logger.error(f"Failed to create tables: {e}")
            raise

        total_rows = 0
        loaded_tables = 0

        from benchbox.core.tpcds.constants import TPCDS_TABLE_LOADING_ORDER

        table_load_order = [t for t in TPCDS_TABLE_LOADING_ORDER if t != "dbgen_version"]

        for table_name in table_load_order:
            if table_name not in self.tables:
                logger.warning(f"Data file for table {table_name} not found, skipping...")
                continue

            data_file = self.tables[table_name]

            if not data_file.exists() or data_file.stat().st_size == 0:
                logger.warning(f"Data file {data_file} is empty or missing, skipping {table_name}...")
                continue

            logger.info(f"Loading {table_name.upper()} from {data_file.name}...")

            try:
                rows_loaded = self._load_table_data(connection, table_name, data_file)

                total_rows += rows_loaded
                loaded_tables += 1
                logger.info(f"✅ Loaded {rows_loaded:,} rows into {table_name.upper()}")

            except Exception as e:
                logger.error(f"Failed to load data for {table_name}: {e}")
                raise

        try:
            connection.commit()
            logger.info(f"✅ Successfully loaded {total_rows:,} total rows across {loaded_tables} tables")
        except Exception as e:
            logger.error(f"Failed to commit data loading transaction: {e}")
            raise

    def _load_table_data(self, connection: _DatabaseConnection, table_name: str, data_file: Path) -> int:
        import csv

        normalized_table_name = table_name.lower()
        table_name_upper = table_name.upper()

        table_schema = next((table for table in TABLES if table.name.lower() == normalized_table_name), None)
        if not table_schema:
            raise ValueError(f"Unknown table: {table_name}")

        num_columns = len(table_schema.columns)

        placeholders = ", ".join(["?" for _ in range(num_columns)])
        insert_sql = f"INSERT INTO {table_name_upper} VALUES ({placeholders})"

        rows_loaded = 0

        delimiter = get_delimiter_for_file(data_file)

        with open(data_file, encoding="utf-8") as f:
            reader = csv.reader(f, delimiter=delimiter)

            for row in reader:
                if not row or (len(row) == 1 and row[0] == ""):
                    continue

                if len(row) > num_columns:
                    row = row[:num_columns]
                elif len(row) < num_columns:
                    row.extend([None] * (num_columns - len(row)))

                processed_row = []
                for _i, value in enumerate(row):
                    if value == "":
                        processed_row.append(None)
                    else:
                        processed_row.append(value)

                connection.execute(insert_sql, processed_row)
                rows_loaded += 1

        return rows_loaded

    def run_official_benchmark(
        self,
        connection: Any,
        num_streams: int = 2,
        power_test: bool = True,
        throughput_test: bool = True,
        maintenance_test: bool = True,
        _refresh_functions: Optional[list[str]] = None,
        _data_maintenance: bool = True,
        result_validation: bool = True,
        dialect: str = "standard",
        output_dir: Optional[Union[str, Path]] = None,
        *,
        adapter: Any = None,
    ) -> dict[str, Any]:
        warn_legacy_throughput_api("TPCDSBenchmark.run_official_benchmark", "tpcds")
        if throughput_test:
            require_adapter("TPCDSBenchmark.run_official_benchmark", adapter)

        logger = logging.getLogger(__name__)
        if self.verbose:
            logger.setLevel(logging.INFO)
            logger.info("Starting TPC-DS Official Benchmark")
            logger.info(f"Scale factor: {self.scale_factor}")
            logger.info(f"Number of streams: {num_streams}")
            logger.info(f"Tests: Power={power_test}, Throughput={throughput_test}, Maintenance={maintenance_test}")

        benchmark_start_time = time.time()

        result = {
            "scale_factor": self.scale_factor,
            "num_streams": num_streams,
            "start_time": datetime.now().isoformat(),
            "end_time": None,
            "total_time": 0.0,
            "power_test_result": None,
            "throughput_test_result": None,
            "maintenance_test_result": None,
            "power_at_size": 0.0,
            "throughput_at_size": 0.0,
            "success": True,
            "errors": [],
        }

        try:
            if power_test:
                self._run_power_phase(connection, dialect, logger, result)

            if throughput_test:
                self._run_throughput_phase(num_streams, logger, result, adapter, connection, dialect)

            if maintenance_test:
                boundary = check_phase_boundary(result["throughput_test_result"])
                if not boundary.proceed:
                    refusal = f"Maintenance Test refused: {boundary.reason}"
                    result["errors"].append(refusal)
                    result["maintenance_test_result"] = {
                        "success": False,
                        "status": "contained",
                        "reason": refusal,
                        "outstanding_stream_ids": list(boundary.outstanding_stream_ids),
                    }
                    result["success"] = False
                else:
                    self._run_maintenance_phase(connection, dialect, logger, result)

            self._finalize_benchmark_result(result, benchmark_start_time, logger)
            return result

        except Exception as e:
            benchmark_end_time = time.time()
            result["total_time"] = benchmark_end_time - benchmark_start_time
            result["end_time"] = datetime.now().isoformat()
            result["success"] = False
            error_msg = f"Official benchmark failed: {e}"
            result["errors"].append(error_msg)

            if self.verbose:
                logger.error(error_msg)

            return result

    def _run_power_phase(self, connection: Any, dialect: str, logger: logging.Logger, result: dict[str, Any]) -> None:
        if self.verbose:
            logger.info("Running Power Test...")
        try:
            power_result = self.run_power_test(
                connection=connection,
                dialect=dialect,
                verbose=self.verbose,
            )
            result["power_test_result"] = power_result
            if power_result.get("success", True) and power_result.get("total_time", 0) > 0:
                result["power_at_size"] = power_result.get("power_at_size", 0.0)
                if self.verbose:
                    logger.info(f"Power Test completed: Power@Size = {result['power_at_size']:.2f}")
            else:
                result["errors"].append("Power Test failed: Power@Size withheld from results.")
                result["success"] = False
        except Exception as e:
            error_msg = f"Power Test failed: {e}"
            result["errors"].append(error_msg)
            result["success"] = False
            if self.verbose:
                logger.error(error_msg)

    def _run_throughput_phase(
        self,
        num_streams: int,
        logger: logging.Logger,
        result: dict[str, Any],
        adapter: Any = None,
        connection: Any = None,
        dialect: str = "standard",
    ) -> None:
        if self.verbose:
            logger.info("Running Throughput Test...")
        try:
            throughput_result = self.run_throughput_test(
                num_streams=num_streams,
                dialect=dialect,
                adapter=adapter,
                connection=connection,
                _warn_deprecated=False,
            )
            result["throughput_test_result"] = throughput_result
            if throughput_result.throughput_at_size is None:
                result["success"] = False
                result["errors"].append("Throughput Test failed: Throughput@Size withheld because a stream failed")
            else:
                result["throughput_at_size"] = throughput_result.throughput_at_size
            if self.verbose:
                logger.info(f"Throughput Test completed: Throughput@Size = {result['throughput_at_size']:.2f}")
        except Exception as e:
            error_msg = f"Throughput Test failed: {e}"
            result["errors"].append(error_msg)
            result["success"] = False
            if self.verbose:
                logger.error(error_msg)

    def _run_maintenance_phase(
        self, connection: Any, dialect: str, logger: logging.Logger, result: dict[str, Any]
    ) -> None:
        if self.verbose:
            logger.info("Running Maintenance Test...")
        try:
            maintenance_config = MaintenanceTestConfig(scale_factor=self.scale_factor, verbose=self.verbose)
            maintenance_result = self.run_maintenance_test(
                connection=connection,
                config=maintenance_config,
                dialect=dialect,
            )
            result["maintenance_test_result"] = maintenance_result
            if self.verbose:
                logger.info(
                    f"Maintenance Test completed: {maintenance_result.successful_operations}/{maintenance_result.total_operations} operations successful"
                )
        except Exception as e:
            error_msg = f"Maintenance Test failed: {e}"
            result["errors"].append(error_msg)
            result["success"] = False
            if self.verbose:
                logger.error(error_msg)

    def _finalize_benchmark_result(
        self, result: dict[str, Any], benchmark_start_time: float, logger: logging.Logger
    ) -> None:
        benchmark_end_time = time.time()
        result["total_time"] = benchmark_end_time - benchmark_start_time
        result["end_time"] = datetime.now().isoformat()

        if self.verbose:
            logger.info("TPC-DS Official Benchmark completed!")
            logger.info(f"Total time: {result['total_time']:.3f} seconds")
            logger.info(f"Power@Size: {result['power_at_size']:.2f}")
            logger.info(f"Throughput@Size: {result['throughput_at_size']:.2f}")
            logger.info(f"Success: {result['success']}")
            if result["errors"]:
                logger.warning(f"Errors encountered: {len(result['errors'])}")

    def run_throughput_test(
        self,
        connection_factory=None,
        num_streams: int = 2,
        query_timeout: int = 300,
        stream_timeout: int = 3600,
        base_seed: int = 42,
        max_retries: int = 3,
        enable_validation: bool = True,
        output_dir: Optional[Union[str, Path]] = None,
        dialect: str = "standard",
        *,
        adapter: Any = None,
        connection: Any = None,
        _warn_deprecated: bool = True,
    ) -> ThroughputTestResult:
        if _warn_deprecated:
            warn_legacy_throughput_api("TPCDSBenchmark.run_throughput_test", "tpcds")
        if num_streams < 1:
            raise ValueError(f"num_streams must be positive, got {num_streams}")
        if query_timeout < 1:
            raise ValueError(f"query_timeout must be positive, got {query_timeout}")
        if stream_timeout < 1:
            raise ValueError(f"stream_timeout must be positive, got {stream_timeout}")
        if max_retries < 0:
            raise ValueError(f"max_retries must be non-negative, got {max_retries}")
        require_stream_minimum(num_streams, "num_streams")
        require_adapter("TPCDSBenchmark.run_throughput_test", adapter)
        if connection_factory is not None:
            warnings.warn(
                "connection_factory is ignored by TPCDSBenchmark.run_throughput_test; "
                "per-stream sessions come from adapter.",
                UserWarning,
                stacklevel=2,
            )
        if connection is None:
            raise ValueError("connection is required when adapter is given")
        ignored = [
            name
            for name, differs in (
                ("query_timeout", query_timeout != 300),
                ("max_retries", max_retries != 3),
                ("output_dir", output_dir is not None),
            )
            if differs
        ]
        if ignored:
            warnings.warn(
                f"{', '.join(ignored)} no longer has any effect on TPCDSBenchmark.run_throughput_test.",
                UserWarning,
                stacklevel=2,
            )

        config = ThroughputTestConfig(
            num_streams=num_streams,
            scale_factor=self.scale_factor,
            base_seed=base_seed,
            query_timeout=query_timeout,
            stream_timeout=stream_timeout,
            max_retries=max_retries,
            enable_validation=enable_validation,
            output_dir=Path(output_dir) if output_dir else None,
        )
        test_start_time = time.time()
        routed = adapter._run_routed_throughput(
            self,
            connection,
            {
                "num_streams": num_streams,
                "seed": base_seed,
                "stream_timeout_seconds": stream_timeout,
                "verbose": self.verbose,
                **({} if enable_validation else {"validation_mode": "disabled"}),
            },
        )
        return ThroughputTestResult(
            config=config,
            start_time=test_start_time,
            end_time=time.time(),
            total_duration=routed.total_time,
            streams_executed=routed.streams_executed,
            streams_successful=routed.streams_successful,
            stream_results=[dataclasses.asdict(stream) for stream in routed.stream_results],
            throughput_at_size=routed.throughput_at_size,
            success=routed.success,
            error="; ".join(routed.errors) or None,
            outstanding_stream_ids=list(getattr(routed, "outstanding_stream_ids", None) or []),
        )

    def run_power_test(
        self,
        connection: Any,
        seed: Optional[int] = None,
        dialect: str = "standard",
        verbose: Optional[bool] = None,
        timeout: Optional[float] = None,
        warm_up: bool = True,
        validation: bool = True,
    ) -> dict[str, Any]:
        import logging
        import time
        from datetime import datetime

        if verbose is None:
            verbose = self.verbose

        if seed is None:
            seed = 1

        logger = logging.getLogger(__name__)
        if verbose:
            logger.setLevel(logging.INFO)

        result = {
            "scale_factor": self.scale_factor,
            "total_time": 0.0,
            "power_at_size": 0.0,
            "success": False,
            "query_results": {},
            "start_time": datetime.now().isoformat(),
            "end_time": None,
            "errors": [],
            "database_info": {
                "connection_type": type(connection).__name__,
                "dialect": dialect,
            },
        }

        try:
            if verbose:
                logger.info(f"Starting TPC-DS Power Test (scale factor: {self.scale_factor})")

            if warm_up:
                self._run_power_test_warmup(connection, seed, dialect, verbose, logger)

            test_start_time = time.time()

            for query_id in range(1, 100):
                if verbose:
                    logger.info(f"Executing Query {query_id}...")
                query_result = self._execute_power_test_query(
                    connection, query_id, seed, dialect, timeout, verbose, logger
                )
                if query_result["status"] == "failed":
                    result["errors"].append(f"Query {query_id} failed: {query_result['error']}")
                result["query_results"][query_id] = query_result

            test_end_time = time.time()
            result["total_time"] = test_end_time - test_start_time
            result["end_time"] = datetime.now().isoformat()

            successful_queries = sum(1 for qr in result["query_results"].values() if qr["status"] == "success")
            result["success"] = successful_queries == len(result["query_results"])

            if result["success"] and result["total_time"] > 0:
                result["power_at_size"] = (3600.0 * self.scale_factor) / result["total_time"]

            if verbose:
                logger.info(f"Power Test completed in {result['total_time']:.3f} seconds")
                logger.info(f"Power@Size: {result['power_at_size']:.2f}")
                logger.info(f"Successful queries: {successful_queries}/99")
                if result["errors"]:
                    logger.warning(f"Errors encountered: {len(result['errors'])}")

            return result

        except Exception as e:
            error_msg = f"Power Test failed: {e}"
            result["errors"].append(error_msg)
            result["end_time"] = datetime.now().isoformat()

            if verbose:
                logger.error(error_msg)

            return result

        finally:
            if connection:
                try:
                    pass
                except Exception:
                    pass

    def _run_power_test_warmup(self, connection: Any, seed: int, dialect: str, verbose: bool, logger) -> None:
        if verbose:
            logger.info("Performing database warm-up...")
        for warm_query_id in [1, 19, 42]:
            try:
                warm_query = self.get_query(warm_query_id, seed=seed, dialect=dialect)
                connection.execute(warm_query)
                connection.fetchall()
            except Exception as e:
                if verbose:
                    logger.warning(f"Warm-up query {warm_query_id} failed: {e}")

    def _execute_power_test_query(
        self,
        connection: Any,
        query_id: int,
        seed: int,
        dialect: str,
        timeout: Optional[float],
        verbose: bool,
        logger,
    ) -> dict[str, Any]:
        import time

        query_start = time.time()
        result: dict[str, Any] = {
            "query_id": query_id,
            "execution_time_seconds": 0.0,
            "result_count": 0,
            "status": "failed",
            "error": None,
        }
        try:
            query_text = self.get_query(query_id, seed=seed, dialect=dialect)
            connection.execute(query_text)
            rows = connection.fetchall()
            query_end = time.time()
            execution_time = query_end - query_start
            if timeout and execution_time > timeout:
                raise RuntimeError(f"Query exceeded timeout ({timeout}s)")
            result.update(
                {
                    "execution_time_seconds": execution_time,
                    "result_count": len(rows) if rows else 0,
                    "status": "success",
                }
            )
            if verbose:
                logger.info(f"Query {query_id} completed in {execution_time:.3f}s ({result['result_count']} rows)")
        except Exception as e:
            query_end = time.time()
            execution_time = query_end - query_start
            error_msg = str(e)
            result.update({"execution_time_seconds": execution_time, "error": error_msg})
            if verbose:
                logger.error(f"Query {query_id} failed after {execution_time:.3f}s: {error_msg}")
        return result

    def run_maintenance_test(
        self,
        connection: Any,
        config: Optional[MaintenanceTestConfig] = None,
        stream_executor: Optional[Any] = None,
        dialect: str = "standard",
    ) -> MaintenanceTestResult:
        from benchbox.core.tpcds.maintenance_test import (
            TPCDSMaintenanceTest,
            TPCDSMaintenanceTestConfig,
        )

        if connection is None:
            raise ValueError("connection object cannot be None")

        if config is None or not hasattr(config, "verbose"):
            config = MaintenanceTestConfig(
                scale_factor=self.scale_factor,
                verbose=self.verbose,
                output_dir=self.output_dir,
            )

        connection_factory = lambda: connection

        maintenance_test = TPCDSMaintenanceTest(
            benchmark=self,
            connection_factory=connection_factory,
            scale_factor=self.scale_factor,
            output_dir=self.output_dir,
            verbose=config.verbose,
            dialect=dialect,
        )

        maintenance_operations = getattr(config, "maintenance_operations", 4)

        tpcds_config = TPCDSMaintenanceTestConfig(
            scale_factor=config.scale_factor,
            maintenance_operations=maintenance_operations,
            operation_interval=0.0,
            concurrent_with_queries=False,
            validate_integrity=True,
            verbose=config.verbose,
            output_dir=config.output_dir,
        )

        result_dict = maintenance_test.run(config=tpcds_config)

        maintenance_operations = []
        for operation in result_dict["operations"]:
            maintenance_operations.append(
                {
                    "operation": f"{operation.operation_type}_{operation.table_name}",
                    "start_time": operation.start_time,
                    "end_time": operation.end_time,
                    "duration": operation.duration,
                    "rows_affected": operation.rows_affected,
                    "success": operation.success,
                    "error": operation.error,
                }
            )

        result = MaintenanceTestResult(
            test_duration=result_dict["total_time"],
            total_operations=result_dict["total_operations"],
            successful_operations=result_dict["successful_operations"],
            failed_operations=result_dict["failed_operations"],
            overall_throughput=result_dict["overall_throughput"],
            maintenance_operations=maintenance_operations,
            error_details=result_dict["errors"],
        )

        return result

    def validate_maintenance_data_integrity(self, connection: Any, dialect: str = "standard") -> dict[str, Any]:
        if connection is None:
            raise ValueError("connection object cannot be None")

        logger = logging.getLogger(__name__)
        if self.verbose:
            logger.setLevel(logging.INFO)
            logger.info("Validating data integrity after maintenance operations")

        validation_results = {
            "validation_checks": [],
            "integrity_score": 0.0,
            "errors": [],
            "timestamp": datetime.now().isoformat(),
        }

        try:
            checks = [
                {
                    "name": "Primary key uniqueness",
                    "query": "SELECT COUNT(*) FROM (SELECT C_CUSTOMER_SK, COUNT(*) FROM CUSTOMER GROUP BY C_CUSTOMER_SK HAVING COUNT(*) > 1) duplicates",
                    "expected_result": 0,
                },
                {
                    "name": "Date dimension consistency",
                    "query": "SELECT COUNT(*) FROM DATE_DIM WHERE D_DATE IS NULL",
                    "expected_result": 0,
                },
                {
                    "name": "Store sales referential integrity",
                    "query": "SELECT COUNT(*) FROM STORE_SALES SS LEFT JOIN CUSTOMER C ON SS.SS_CUSTOMER_SK = C.C_CUSTOMER_SK WHERE C.C_CUSTOMER_SK IS NULL AND SS.SS_CUSTOMER_SK IS NOT NULL",
                    "expected_result": 0,
                },
            ]

            passed_checks = 0

            for check in checks:
                try:
                    connection.execute(check["query"])
                    result = connection.fetchone()
                    actual_result = result[0] if result else None

                    check_passed = actual_result == check["expected_result"]
                    if check_passed:
                        passed_checks += 1

                    validation_results["validation_checks"].append(
                        {
                            "name": check["name"],
                            "expected": check["expected_result"],
                            "actual": actual_result,
                            "passed": check_passed,
                        }
                    )

                    if self.verbose:
                        status = "PASSED" if check_passed else "FAILED"
                        logger.info(f"Integrity check '{check['name']}': {status}")

                except Exception as e:
                    error_msg = f"Integrity check '{check['name']}' failed: {e}"
                    validation_results["errors"].append(error_msg)

                    validation_results["validation_checks"].append(
                        {
                            "name": check["name"],
                            "expected": check["expected_result"],
                            "actual": None,
                            "passed": False,
                            "error": str(e),
                        }
                    )

                    if self.verbose:
                        logger.error(error_msg)

            total_checks = len(checks)
            validation_results["integrity_score"] = passed_checks / total_checks if total_checks > 0 else 0.0

            connection.close()

            if self.verbose:
                logger.info(f"Data integrity validation completed: {passed_checks}/{total_checks} checks passed")
                logger.info(f"Integrity score: {validation_results['integrity_score']:.1%}")

        except Exception as e:
            error_msg = f"Data integrity validation failed: {e}"
            validation_results["errors"].append(error_msg)
            if self.verbose:
                logger.error(error_msg)

        return validation_results

    def get_benchmark_info(self) -> dict[str, Any]:
        return {
            "name": "TPC-DS",
            "scale_factor": self.scale_factor,
            "available_tables": self.get_available_tables(),
            "available_queries": self.get_available_queries(),
            "c_tools_info": self.c_tools.get_tools_info(),
            "maintenance_test_supported": True,
        }

    def validate_preflight_conditions(self) -> ValidationResult:
        return self._data_validation_engine.validate_preflight_conditions(
            benchmark_type="tpcds",
            scale_factor=self.scale_factor,
            output_dir=self.output_dir,
        )

    def validate_generated_data(self) -> ValidationResult:
        manifest_path = self.output_dir / "_datagen_manifest.json"
        return self._data_validation_engine.validate_generated_data(manifest_path)

    def validate_loaded_data(self, connection: Any) -> ValidationResult:
        return self._db_validation_engine.validate_loaded_data(
            connection=connection,
            benchmark_type="tpcds",
            scale_factor=self.scale_factor,
        )

    def validate_data_integrity(self, connection: Optional[Any] = None) -> ValidationResult:
        file_result = self.validate_generated_data()

        if connection is None:
            return file_result

        db_result = self.validate_loaded_data(connection)

        all_errors = file_result.errors + db_result.errors
        all_warnings = file_result.warnings + db_result.warnings

        combined_details = {
            "file_validation": file_result.details,
            "database_validation": db_result.details,
        }

        return ValidationResult(
            is_valid=file_result.is_valid and db_result.is_valid,
            errors=all_errors,
            warnings=all_warnings,
            details=combined_details,
        )


__all__ = ["TPCDSBenchmark"]
