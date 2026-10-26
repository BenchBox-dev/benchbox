# Copyright 2026 Joe Harris / BenchBox Project

# This implementation is derived from TPC Benchmark™ H (TPC-H) - Copyright © Transaction Processing Performance Council

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import Any, Union

from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.core.tpchavoc.dialect_compat import (
    LIST_AGGREGATE_VARIANT_IDS,
    rewrite_dialect_variant,
    translate_list_aggregate_variant,
)
from benchbox.core.tpchavoc.queries import TPCHavocQueryManager
from benchbox.core.tpchavoc.validation import ResultValidator, ValidationReport
from benchbox.sql_compat.rules.execution_filter.clickhouse_tpchavoc import CLICKHOUSE_TPCHAVOC_SKIPS
from benchbox.sql_compat.rules.execution_filter.cloud_tpchavoc import CLOUD_TPCHAVOC_SKIPS
from benchbox.sql_compat.rules.execution_filter.datafusion_tpchavoc import DATAFUSION_TPCHAVOC_SKIPS
from benchbox.sql_compat.rules.execution_filter.lakesail_tpchavoc import LAKESAIL_TPCHAVOC_SKIPS
from benchbox.sql_compat.rules.execution_filter.list_aggregate_tpchavoc import (
    LIST_AGGREGATE_TPCHAVOC_QUERY_ID,
    LIST_AGGREGATE_TPCHAVOC_SKIPS,
)
from benchbox.sql_compat.rules.execution_filter.postgres_tpchavoc import (
    POSTGRES_DUCKDB_BACKED_TPCHAVOC_SKIPS,
    POSTGRES_TPCHAVOC_SKIPS,
)


class TPCHavocBenchmark(TPCHBenchmark):
    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Union[str, Path] | None = None,
        verbose: int | bool = 0,
        parallel: int = 1,
        validation_tolerance: float = 1e-10,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            scale_factor=scale_factor,
            output_dir=output_dir,
            verbose=verbose,
            parallel=parallel,
            **kwargs,
        )

        self._name = "TPC-Havoc Benchmark"

        self.query_manager = TPCHavocQueryManager()

        self.validator = ResultValidator(tolerance=validation_tolerance)
        self.validation_report = ValidationReport()

    def get_platform_skip_queries(self, platform_name: str) -> list[str]:
        platform = re.sub(r"[^a-z0-9]+", "-", platform_name.lower()).strip("-")
        if platform == "lakesail":
            return list(LAKESAIL_TPCHAVOC_SKIPS)
        if platform == "datafusion":
            return list(DATAFUSION_TPCHAVOC_SKIPS)
        if platform in {"clickhouse-local", "clickhouse-server", "clickhouse-cloud"}:
            return list(CLICKHOUSE_TPCHAVOC_SKIPS)
        if platform in {"pg-duckdb", "pg-mooncake"}:
            return list({**POSTGRES_TPCHAVOC_SKIPS, **POSTGRES_DUCKDB_BACKED_TPCHAVOC_SKIPS})
        if platform == "timescaledb":
            return list(POSTGRES_TPCHAVOC_SKIPS)
        if platform in CLOUD_TPCHAVOC_SKIPS:
            return list(CLOUD_TPCHAVOC_SKIPS[platform])
        if platform in LIST_AGGREGATE_TPCHAVOC_SKIPS:
            return [LIST_AGGREGATE_TPCHAVOC_QUERY_ID]
        return []

    def get_queries(self, dialect: str | None = None, base_dialect: str | None = None) -> dict[str, str]:
        queries = super().get_queries(dialect=dialect, base_dialect=base_dialect)
        target = (dialect or "").lower()
        queries = {query_id: rewrite_dialect_variant(query_id, query, target) for query_id, query in queries.items()}
        raw_queries = self.query_manager.get_all_queries(scale_factor=self.scale_factor)
        for query_id in LIST_AGGREGATE_VARIANT_IDS:
            if query_id in queries and (
                rewritten := translate_list_aggregate_variant(query_id, raw_queries[query_id], target)
            ):
                queries[query_id] = rewritten
        return queries

    def get_query(
        self,
        query_id,
        *,
        seed: int | None = None,
        scale_factor: float | None = None,
        dialect: str | None = None,
        base_dialect: str | None = None,
        **kwargs,
    ) -> str:
        query = self.query_manager.get_query(
            query_id,
            seed=seed,
            scale_factor=scale_factor or self.scale_factor,
            **kwargs,
        )
        if dialect is None:
            return query
        target = dialect.lower()
        if rewritten := translate_list_aggregate_variant(str(query_id), query, target):
            return rewritten
        translated = self.translate_query_text(query, (base_dialect or "netezza").lower(), target)
        return rewrite_dialect_variant(str(query_id), translated, target)

    def get_query_variant(self, query_id: int, variant_id: int, params: dict[str, Any] | None = None) -> str:
        return self.query_manager.get_query_variant(query_id, variant_id, params, scale_factor=self.scale_factor)

    def get_all_variants(self, query_id: int) -> dict[int, str]:
        return self.query_manager.get_all_variants(query_id, scale_factor=self.scale_factor)

    def get_variant_description(self, query_id: int, variant_id: int) -> str:
        return self.query_manager.get_variant_description(query_id, variant_id)

    def get_implemented_queries(self) -> list[int]:
        return self.query_manager.get_implemented_queries()

    def supports_dataframe_mode(self) -> bool:
        return True

    def get_dataframe_queries(self):
        from benchbox.core.tpchavoc.dataframe_queries import get_dataframe_queries

        return get_dataframe_queries()

    def get_all_variants_info(self, query_id: int) -> dict[int, dict[str, str]]:
        return self.query_manager.get_all_variants_info(query_id)

    def validate_variant_equivalence(
        self,
        query_id: int,
        variant_id: int,
        original_results: list[tuple[Any, ...]],
        variant_results: list[tuple[Any, ...]],
        use_checksum: bool = False,
    ) -> bool:
        if use_checksum:
            return self.validator.validate_results_checksum(original_results, variant_results, query_id, variant_id)
        elif query_id == 1:
            return self.validator.validate_query1_results(original_results, variant_results, variant_id)
        else:
            return self.validator.validate_results_exact(original_results, variant_results, query_id, variant_id)

    def get_benchmark_info(self) -> dict[str, Any]:
        implemented_queries = self.get_implemented_queries()

        variants_info = {}
        for query_id in implemented_queries:
            variants_info[query_id] = self.get_all_variants_info(query_id)

        return {
            "benchmark_name": "TPC-Havoc",
            "base_benchmark": "TPC-H",
            "scale_factor": self.scale_factor,
            "implemented_queries": implemented_queries,
            "total_queries_with_variants": len(implemented_queries),
            "variants_per_query": 10,
            "total_query_variants": len(implemented_queries) * 10,
            "variants_info": variants_info,
            "validation_tolerance": self.validator.tolerance,
            "description": (
                "TPC-Havoc generates 10 structural variants of each TPC-H query "
                "to stress different aspects of query optimizers while maintaining "
                "result equivalence."
            ),
        }

    def export_variant_queries(
        self, output_dir: Union[str, Path] | None = None, format: str = "sql"
    ) -> dict[str, Path]:
        if format not in ["sql", "json"]:
            raise ValueError(f"Unsupported export format: {format}")

        output_dir = self.output_dir / "queries" if output_dir is None else Path(output_dir)

        output_dir.mkdir(parents=True, exist_ok=True)

        exported_files = {}

        for query_id in self.get_implemented_queries():
            variants = self.get_all_variants(query_id)

            for variant_id, query_text in variants.items():
                if format == "sql":
                    filename = f"q{query_id}_variant_{variant_id}.sql"
                    filepath = output_dir / filename

                    description = self.get_variant_description(query_id, variant_id)
                    content = f"-- TPC-Havoc Query {query_id} Variant {variant_id}\n"
                    content += f"-- {description}\n\n"
                    content += query_text

                    with filepath.open("w") as f:
                        f.write(content)

                    exported_files[f"Q{query_id}.{variant_id}"] = filepath

        self.log_verbose(f"Exported {len(exported_files)} variant queries to {output_dir}")

        return exported_files

    def _check_compatible_tpch_database(self, connection) -> bool:
        try:
            from benchbox.core.connection import DatabaseConnection

            if not hasattr(connection, "execute") or not hasattr(connection, "commit"):
                try:
                    connection = DatabaseConnection(connection)
                except Exception:
                    pass
        except ImportError:
            pass

        try:
            required_tables = [
                "region",
                "nation",
                "customer",
                "supplier",
                "part",
                "partsupp",
                "orders",
                "lineitem",
            ]

            for table_name in required_tables:
                try:
                    result = connection.execute(f"SELECT COUNT(*) FROM {table_name} LIMIT 1")
                    if not result:
                        return False
                except Exception:
                    return False

            try:
                result = connection.execute("SELECT COUNT(*) FROM lineitem")
                lineitem_count = result[0][0] if result else 0

                expected_min = int(6000000 * self.scale_factor * 0.8)
                expected_max = int(6000000 * self.scale_factor * 1.2)

                if not (expected_min <= lineitem_count <= expected_max):
                    return False

                self.log_verbose(
                    f"Found compatible TPC-H database with {lineitem_count:,} lineitem rows (scale factor {self.scale_factor})"
                )
                return True

            except Exception:
                return False

        except Exception:
            return False

    def _load_data(self, connection) -> None:
        import logging

        logger = logging.getLogger(__name__)

        try:
            from benchbox.core.connection import DatabaseConnection

            if not hasattr(connection, "execute") or not hasattr(connection, "commit"):
                with contextlib.suppress(Exception):
                    connection = DatabaseConnection(connection)
        except ImportError:
            pass

        if self._check_compatible_tpch_database(connection):
            logger.info("Reusing existing compatible TPC-H database for TPC-Havoc benchmark")
            return

        logger.info("Loading TPC-H data for TPC-Havoc benchmark...")
        super()._load_data(connection)

    def _validate_database_configuration_compatibility(self, other_config: dict) -> bool:
        benchmark_type = other_config.get("benchmark_type", "").lower()
        if benchmark_type not in ["tpch", "tpc-h", "tpchavoc", "tpc-havoc"]:
            return False

        other_scale = other_config.get("scale_factor")
        if other_scale != self.scale_factor:
            return False

        return True
