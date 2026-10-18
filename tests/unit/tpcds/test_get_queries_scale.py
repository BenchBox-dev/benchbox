import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
    pytest.mark.tpcds,
]

SCALE_FACTOR = 10.0
SCALE_DEPENDENT_QUERY_IDS = (9, 44)


@pytest.fixture(scope="module")
def benchmark_and_queries(tmp_path_factory):
    from benchbox.core.tpcds.benchmark import TPCDSBenchmark
    from benchbox.core.tpcds.c_tools import TPCDSError

    try:
        bench = TPCDSBenchmark(scale_factor=SCALE_FACTOR, output_dir=tmp_path_factory.mktemp("tpcds"))
        return bench, bench.get_queries()
    except (TPCDSError, FileNotFoundError, RuntimeError) as exc:
        pytest.skip(f"dsqgen binary or templates unavailable: {exc}")


@pytest.mark.parametrize("query_id", SCALE_DEPENDENT_QUERY_IDS)
def test_get_queries_matches_get_query_at_the_data_scale(benchmark_and_queries, query_id):
    bench, queries = benchmark_and_queries

    assert queries[str(query_id)] == bench.get_query(query_id)


@pytest.mark.parametrize("query_id", SCALE_DEPENDENT_QUERY_IDS)
def test_get_queries_is_not_rendered_at_scale_factor_one(benchmark_and_queries, query_id):
    bench, queries = benchmark_and_queries

    assert queries[str(query_id)] != bench.get_query(query_id, scale_factor=1.0)
