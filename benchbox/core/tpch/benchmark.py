# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-H specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

if TYPE_CHECKING:
    from benchbox.core.tuning.interface import UnifiedTuningConfiguration

from benchbox.base import BaseBenchmark, GeneratorOutputDirMixin
from benchbox.core.expected_results.loader import TPCH_SUBSCALE_ROW_COUNT_SCALE_FACTORS
from benchbox.core.tpch.compliance import validate_tpch_scale
from benchbox.core.tpch.generator import TPCHDataGenerator
from benchbox.core.tpch.maintenance_test import TPCHMaintenanceTest
from benchbox.core.tpch.queries import TPCHQueries
from benchbox.core.tpch.schema import TABLES
from benchbox.core.tpch.streams import TPCHStreams

TPCH_ANSWER_SET_SCALE_FACTOR = 1.0


def has_answer_set(scale_factor: float) -> bool:
    return float(scale_factor) == TPCH_ANSWER_SET_SCALE_FACTOR


def has_row_count_reference(scale_factor: float) -> bool:
    return has_answer_set(scale_factor) or float(scale_factor) in TPCH_SUBSCALE_ROW_COUNT_SCALE_FACTORS


def binds_answer_set_parameters(seed: int | None) -> bool:
    return seed is None


def power_stream_seed(seed: int | None, stream_id: int) -> int | None:
    if seed is None:
        return None
    return seed + stream_id * 1000


def describe_query_parameters(
    seed: int | None, *, execution_type: str = "power", requested_phases: set[str] | None = None
) -> str:
    if execution_type == "standard":
        seed = None
    elif execution_type == "throughput":
        from benchbox.core.tpch.throughput_test import TPCHThroughputTestConfig

        base_seed = TPCHThroughputTestConfig().base_seed if seed is None else seed
        return f"qgen -r ({base_seed} + 1001 * stream_id + query_position)"
    elif execution_type == "maintenance":
        return "not applicable (TPC-H refresh functions)"
    elif execution_type == "combined":
        phases = requested_phases or {"power", "throughput", "maintenance"}
        return "; ".join(
            f"{phase}: {describe_query_parameters(seed, execution_type=phase)}"
            for phase in ("power", "throughput", "maintenance")
            if phase in phases
        )
    if seed is None:
        return "qgen -d (TPC-H default substitution parameters)"
    return f"qgen -r ({seed} + 1000 * stream_id)"


@dataclass
class TPCHThroughputTestConfig:
    num_streams: int = 2
    scale_factor: float = 1.0
    base_seed: int = 42
    query_timeout: int = 300
    stream_timeout: int = 3600
    max_retries: int = 3
    enable_validation: bool = True
    output_dir: Path | None = None


@dataclass
class TPCHThroughputTestResult:
    config: TPCHThroughputTestConfig
    start_time: float
    end_time: float
    total_duration: float
    streams_executed: int
    streams_successful: int
    stream_results: list[dict[str, Any]]
    throughput_at_size: float
    success: bool
    error: str | None = None


@dataclass
class TPCHMaintenanceTestConfig:
    scale_factor: float = 1.0
    num_concurrent_streams: int = 2
    maintenance_interval: float = 30.0
    enable_rf1: bool = True
    enable_rf2: bool = True
    verbose: bool = False
    output_dir: Path | None = None


def _validate_tpch_get_query_args(query_id: int, scale_factor: float | None, seed: int | None) -> None:
    if not isinstance(query_id, int):
        raise TypeError(f"query_id must be an integer, got {type(query_id).__name__}")
    if not (1 <= query_id <= 22):
        raise ValueError(f"Query ID must be 1-22, got {query_id}")
    if scale_factor is not None:
        if not isinstance(scale_factor, (int, float)):
            raise TypeError(f"scale_factor must be a number, got {type(scale_factor).__name__}")
        if scale_factor <= 0:
            raise ValueError(f"scale_factor must be positive, got {scale_factor}")
    if seed is not None and not isinstance(seed, int):
        raise TypeError(f"seed must be an integer, got {type(seed).__name__}")


def _resolve_tpch_seed(
    actual_seed: int | None, query_id: int, stream_id: int | None, permutation: list | None
) -> int | None:
    if stream_id is not None:
        if not (0 <= stream_id < len(TPCHStreams.PERMUTATION_MATRIX)):
            raise ValueError(f"stream_id must be 0-{len(TPCHStreams.PERMUTATION_MATRIX) - 1}")

        stream_permutation = TPCHStreams.PERMUTATION_MATRIX[stream_id]
        try:
            position = stream_permutation.index(query_id)
            if actual_seed is None:
                return stream_id * 1000 + position
        except ValueError:
            raise ValueError(f"Query {query_id} not found in stream {stream_id} permutation") from None

    elif permutation is not None:
        if query_id not in permutation:
            raise ValueError(f"Query {query_id} not found in provided permutation")
        if actual_seed is None:
            try:
                return permutation.index(query_id)
            except ValueError:
                pass

    return actual_seed


NAMED_COLUMN_ALIAS_UNSUPPORTED_DIALECTS = frozenset({"sqlite", "mysql", "singlestore", "bigquery", "doris"})


def _expand_named_column_aliases(query: str) -> str:
    import re

    alias_pattern = re.compile(r"(\bAS\s+c_orders)\s*\(\s*c_custkey\s*,\s*c_count\s*\)", re.IGNORECASE)
    if alias_pattern.search(query):
        sub_pattern = re.compile(r"(\bcount\s*\(\s*o_orderkey\s*\))(\s+FROM\b)", re.IGNORECASE)
        query = sub_pattern.sub(r"\1 AS c_count\2", query, count=1)
        query = alias_pattern.sub(r"\1", query, count=1)
    elif ") as c_orders (c_custkey, c_count)" in query:
        query = query.replace(
            "count(o_orderkey)\nfrom",
            "count(o_orderkey) as c_count\nfrom",
            1,
        ).replace(
            ") as c_orders (c_custkey, c_count)",
            ") as c_orders",
            1,
        )
    return query.replace(
        "with revenue (supplier_no, total_revenue) as (\nselect\nl_suppkey,\nsum(l_extendedprice * (1-l_discount))",
        "with revenue as (\nselect\nl_suppkey as supplier_no,\nsum(l_extendedprice * (1-l_discount)) as total_revenue",
        1,
    )


class TPCHBenchmark(GeneratorOutputDirMixin, BaseBenchmark):
    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Union[str, Path] | None = None,
        verbose: int | bool = 0,
        parallel: int = 1,
        force_regenerate: bool = False,
        official: bool = False,
        tpch_refresh_functions: bool = False,
        tpch_refresh_first_set: int = 1,
        **kwargs: Any,
    ) -> None:
        if not isinstance(scale_factor, (int, float)):
            raise TypeError(f"scale_factor must be a number, got {type(scale_factor).__name__}")
        if scale_factor <= 0:
            raise ValueError(f"scale_factor must be positive, got {scale_factor}")

        if type(self) is TPCHBenchmark:
            self.compliance_class = validate_tpch_scale(scale_factor, official=official)
        else:
            self.compliance_class = None

        if not isinstance(parallel, int):
            raise TypeError(f"parallel must be an integer, got {type(parallel).__name__}")
        if parallel < 1:
            raise ValueError(f"parallel must be positive, got {parallel}")
        if type(tpch_refresh_functions) is not bool:
            raise TypeError("tpch_refresh_functions must be a boolean")
        if isinstance(tpch_refresh_first_set, bool) or not isinstance(tpch_refresh_first_set, int):
            raise TypeError("tpch_refresh_first_set must be an integer")
        if tpch_refresh_first_set < 1:
            raise ValueError(f"tpch_refresh_first_set must be positive, got {tpch_refresh_first_set}")

        kwargs = dict(kwargs)
        quiet = kwargs.pop("quiet", False)

        super().__init__(scale_factor=scale_factor, output_dir=output_dir, verbose=verbose, quiet=quiet, **kwargs)
        self._name = "TPC-H Benchmark"
        self.parallel = parallel
        self.tpch_refresh_functions = tpch_refresh_functions
        self.tpch_refresh_first_set = tpch_refresh_first_set
        self.query_manager = TPCHQueries()
        self.data_generator = TPCHDataGenerator(
            scale_factor=scale_factor,
            parallel=parallel,
            output_dir=self.output_dir,
            verbose=verbose,
            quiet=quiet,
            force_regenerate=force_regenerate,
            **kwargs,
        )
        self.tables: dict[str, Path | list[Path]] = {}

        self.streams_manager: TPCHStreams | None = None

        self.maintenance_test: TPCHMaintenanceTest | None = None

    def generate_data(self) -> list[Union[str, Path]]:
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.log_verbose(f"Generating TPC-H data at scale factor {self.scale_factor}...")
        self.log_verbose(f"Output directory: {self.output_dir}")

        self.tables = self.data_generator.generate()

        if self.verbose_enabled:
            self.logger.info(f"Generated {len(self.tables)} TPC-H tables:")
            for table_name, file_path in self.tables.items():
                self.logger.info(f"  - {table_name}: {file_path}")

        return list(self.tables.values())

    def get_queries(self, dialect: str | None = None, base_dialect: str | None = None) -> dict[str, str]:
        src = (base_dialect or "netezza").lower()
        tgt = (dialect or src).lower()
        int_queries = self.query_manager.get_all_queries(scale_factor=self.scale_factor)
        base_queries = {str(k): v for k, v in int_queries.items()}
        translated_queries = {}
        for query_id, query in base_queries.items():
            translated_queries[query_id] = self.translate_query_text(query, src, tgt)
        return translated_queries

    def translate_query_text(self, query: str, source_dialect: str, target_dialect: str) -> str:
        from benchbox.utils.dialect_utils import translate_sql_query

        src = (source_dialect or "netezza").lower()
        tgt = (target_dialect or src).lower()
        if tgt in NAMED_COLUMN_ALIAS_UNSUPPORTED_DIALECTS:
            query = _expand_named_column_aliases(query)

        return translate_sql_query(
            query=query,
            target_dialect=tgt,
            source_dialect=src,
            identify=True,
        )

    def get_query(
        self,
        query_id: int,
        *,
        params: dict[str, Any] | None = None,
        seed: int | None = None,
        scale_factor: float | None = None,
        dialect: str | None = None,
        base_dialect: str | None = None,
        **kwargs,
    ) -> str:
        _validate_tpch_get_query_args(query_id, scale_factor, seed)

        if params is None:
            params = {}

        actual_seed = seed if seed is not None else params.get("seed")
        actual_scale_factor = (
            scale_factor if scale_factor is not None else params.get("scale_factor", self.scale_factor)
        )

        actual_seed = _resolve_tpch_seed(actual_seed, query_id, params.get("stream_id"), params.get("permutation"))

        src = (base_dialect or "netezza").lower()
        tgt = (dialect or src).lower()
        query = self.query_manager.get_query(query_id, seed=actual_seed, scale_factor=actual_scale_factor)
        return self.translate_query_text(query, src, tgt)

    def get_schema(self) -> dict[str, dict[str, Any]]:
        schema = {}
        for table in TABLES:
            table_schema = {
                "name": table.name,
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

    def get_table_loading_order(self, available_tables: list[str]) -> list[str]:
        from benchbox.core.tpch.schema import get_table_loading_order as _schema_table_loading_order

        full_order = _schema_table_loading_order()
        available_set = set(available_tables)

        ordered_tables = [t for t in full_order if t in available_set]
        remaining_tables = [t for t in available_tables if t not in ordered_tables]
        ordered_tables.extend(remaining_tables)

        return ordered_tables

    def get_create_tables_sql(
        self,
        dialect: str = "standard",
        tuning_config: UnifiedTuningConfiguration | None = None,
    ) -> str:
        from benchbox.core.tpch.schema import get_create_all_tables_sql

        self.log_very_verbose(
            f"TPC-H get_create_tables_sql called: dialect={dialect}, tuning_config={tuning_config is not None}"
        )

        enable_primary_keys = False
        enable_foreign_keys = False

        if tuning_config:
            try:
                enable_primary_keys = tuning_config.primary_keys.enabled
                enable_foreign_keys = tuning_config.foreign_keys.enabled
                self.log_very_verbose(
                    f"Extracted constraints from tuning_config: primary_keys={enable_primary_keys}, "
                    f"foreign_keys={enable_foreign_keys}"
                )
            except AttributeError as e:
                self.logger.error(
                    f"Failed to extract constraint settings from tuning_config: {e}. "
                    f"tuning_config type: {type(tuning_config)}"
                )
                raise RuntimeError(
                    f"Invalid tuning_config object (missing primary_keys or foreign_keys attributes): {e}"
                ) from e

        result = get_create_all_tables_sql(
            enable_primary_keys=enable_primary_keys,
            enable_foreign_keys=enable_foreign_keys,
        )
        self.log_very_verbose(f"Generated SQL: {len(result)} characters")
        return result

    @property
    def generator(self) -> TPCHDataGenerator:
        return self.data_generator


from benchbox.core.hooks.benchmark_hooks import (
    BenchmarkHookRegistry,
    BenchmarkOptionSpec,
    parse_bool,
    parse_int,
)

BenchmarkHookRegistry.register_option_specs(
    "tpch",
    BenchmarkOptionSpec(
        name="tpch_refresh_functions",
        parser=parse_bool,
        default=False,
        help="Run TPC-H DBGen refresh functions in power and throughput phases",
        aliases=("tpch-refresh-functions",),
    ),
    BenchmarkOptionSpec(
        name="tpch_refresh_first_set",
        parser=parse_int,
        default=1,
        help="First DBGen refresh set number for this run",
        aliases=("tpch-refresh-first-set",),
    ),
    benchmark_class=TPCHBenchmark,
)
