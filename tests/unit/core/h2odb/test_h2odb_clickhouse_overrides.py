from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_h2odb_q9_clickhouse_uses_quantile_not_percentile_cont():

    from benchbox.core.h2odb.benchmark import H2OBenchmark

    bench = H2OBenchmark()
    queries = bench.get_queries(dialect="clickhouse")

    q9 = queries["Q9"]
    assert "quantile(" in q9.lower(), f"Q9 must use quantile() for ClickHouse, got:\n{q9}"
    assert "PERCENTILE_CONT" not in q9.upper(), f"Q9 must not use PERCENTILE_CONT for ClickHouse, got:\n{q9}"


def test_h2odb_q9_base_dialect_retains_percentile_cont():

    from benchbox.core.h2odb.benchmark import H2OBenchmark

    bench = H2OBenchmark()
    queries = bench.get_queries()

    q9 = queries["Q9"]
    assert "PERCENTILE_CONT" in q9.upper(), f"Base Q9 must use PERCENTILE_CONT, got:\n{q9}"


def test_h2odb_q9_sqlite_uses_registered_two_argument_percentile():

    from benchbox.core.h2odb.benchmark import H2OBenchmark

    q9 = H2OBenchmark().get_queries(dialect="sqlite")["Q9"]

    assert 'PERCENTILE_CONT(0.5, "fare_amount")' in q9
    assert "WITHIN GROUP" not in q9.upper()


def test_h2odb_q9_clickhouse_preserves_column_names():

    from benchbox.core.h2odb.benchmark import H2OBenchmark

    bench = H2OBenchmark()
    q9 = bench.get_queries(dialect="clickhouse")["Q9"]

    assert "median_fare_amount" in q9, f"Q9 must alias median column, got:\n{q9}"
    assert "p90_fare_amount" in q9, f"Q9 must alias p90 column, got:\n{q9}"


def test_h2odb_other_queries_unaffected_by_clickhouse_dialect():

    from benchbox.core.h2odb.benchmark import H2OBenchmark

    bench = H2OBenchmark()
    clickhouse = bench.get_queries(dialect="clickhouse")

    for qid in [f"Q{i}" for i in range(1, 11) if i != 9]:
        assert qid in clickhouse, f"{qid} missing from ClickHouse queries"
        assert clickhouse[qid].strip(), f"{qid} is empty in ClickHouse queries"


