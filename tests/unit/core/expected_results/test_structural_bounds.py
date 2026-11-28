from __future__ import annotations

import re

import pytest

from benchbox.core.expected_results.query_generation import current_query_generation_hash
from benchbox.core.expected_results.row_count_references import QUERY_IDS
from benchbox.core.expected_results.structural_bounds import (
    StructuralBound,
    derive_structural_bound,
    structural_bound,
    structural_bounds_identity,
    validate_structural_table,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]

_QUERY_ID = re.compile(r"(\d+)([ab]?)")
SEEDS = (1001, 20261125)
SCALE_FACTOR = 0.1


def _tpch_sql(instance, query_id: str, seed: int) -> str:
    return instance.get_query(int(query_id), seed=seed, scale_factor=SCALE_FACTOR, dialect="duckdb")


def _tpcds_sql(instance, query_id: str, seed: int) -> str:
    match = _QUERY_ID.fullmatch(query_id)
    kwargs = {"variant": match.group(2)} if match.group(2) else {}
    return instance.get_query(int(match.group(1)), seed=seed, scale_factor=SCALE_FACTOR, dialect="duckdb", **kwargs)


@pytest.fixture(scope="module")
def instances(tmp_path_factory):
    from benchbox.core.tpcds.benchmark.runner import TPCDSBenchmark
    from benchbox.core.tpch.benchmark import TPCHBenchmark

    output = str(tmp_path_factory.mktemp("structural"))
    return {
        "tpch": (TPCHBenchmark(scale_factor=SCALE_FACTOR, output_dir=output), _tpch_sql),
        "tpcds": (TPCDSBenchmark(scale_factor=SCALE_FACTOR, output_dir=output), _tpcds_sql),
    }


@pytest.mark.parametrize("benchmark_id", ["tpch", "tpcds"])
def test_the_table_matches_the_generated_sql_of_every_template(instances, benchmark_id: str) -> None:
    instance, generate = instances[benchmark_id]
    mismatches = []
    for query_id in QUERY_IDS[benchmark_id]:
        recorded = structural_bound(benchmark_id, query_id)
        for seed in SEEDS:
            derived = derive_structural_bound(generate(instance, query_id, seed))
            expected = StructuralBound(limit=None if recorded.one_row else recorded.limit, one_row=recorded.one_row)
            if derived.one_row != expected.one_row or (not derived.one_row and derived.limit != expected.limit):
                mismatches.append((query_id, seed, recorded, derived))
    assert not mismatches


def test_the_table_lists_the_measured_statement_counts() -> None:
    tpch_limited = {q for q in QUERY_IDS["tpch"] if structural_bound("tpch", q).limit is not None}
    tpch_one_row = {q for q in QUERY_IDS["tpch"] if structural_bound("tpch", q).one_row}
    tpcds_one_row = {q for q in QUERY_IDS["tpcds"] if structural_bound("tpcds", q).one_row}

    assert tpch_limited == {"2", "3", "10", "18", "21"}
    assert tpch_one_row == {"6", "14", "17", "19"}
    assert tpcds_one_row == {"13", "16", "23a", "32", "38", "48", "87", "92", "94", "95", "96", "97"}


@pytest.mark.parametrize("benchmark_id", ["tpch", "tpcds"])
def test_the_table_is_bound_to_the_current_query_generation(benchmark_id: str) -> None:
    assert structural_bounds_identity(benchmark_id) == current_query_generation_hash(benchmark_id)


def test_derivation_reads_limits_and_aggregate_only_selects() -> None:
    assert derive_structural_bound("SELECT a FROM t ORDER BY a LIMIT 10") == StructuralBound(limit=10)
    assert derive_structural_bound("SELECT COUNT(*) FROM t") == StructuralBound(one_row=True)
    assert derive_structural_bound("SELECT SUM(a) FROM t HAVING SUM(a) > 3") == StructuralBound()
    assert derive_structural_bound("SELECT a, SUM(b) FROM t GROUP BY a") == StructuralBound()
    assert (
        derive_structural_bound("SELECT CASE WHEN (SELECT COUNT(*) FROM u) > 1 THEN 1 END FROM r") == StructuralBound()
    )


def test_a_malformed_table_is_refused() -> None:
    with pytest.raises(ValueError):
        validate_structural_table({"schema_version": 1})
