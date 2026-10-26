from __future__ import annotations

import pytest

from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark
from benchbox.sql_compat.rules.execution_filter.postgres_tpchavoc import POSTGRES_TPCHAVOC_SKIPS

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


def test_benchmark_exposes_variant_helpers(tmp_path):
    benchmark = TPCHavocBenchmark(output_dir=tmp_path)

    variant_sql = benchmark.get_query_variant(2, 3)
    assert "select" in variant_sql.lower()

    descriptions = benchmark.get_all_variants_info(2)
    assert len(descriptions) == 10
    assert descriptions[3]["description"]

    all_variants = benchmark.get_all_variants(2)
    assert len(all_variants) == 10

    assert benchmark.get_variant_description(2, 1)


def test_postgres_family_platform_skips_unsupported_variants(tmp_path):
    benchmark = TPCHavocBenchmark(output_dir=tmp_path)

    duckdb_backed = set(POSTGRES_TPCHAVOC_SKIPS) | {"1_v7"}
    assert set(benchmark.get_platform_skip_queries("pg-duckdb")) == duckdb_backed
    assert set(benchmark.get_platform_skip_queries("pg_duckdb")) == duckdb_backed
    assert set(benchmark.get_platform_skip_queries("pg-mooncake")) == duckdb_backed
    assert set(benchmark.get_platform_skip_queries("pg_mooncake")) == duckdb_backed
    assert set(benchmark.get_platform_skip_queries("timescaledb")) == set(POSTGRES_TPCHAVOC_SKIPS)
    assert "1_v7" not in benchmark.get_platform_skip_queries("postgresql")
    assert benchmark.get_platform_skip_queries("duckdb") == []
