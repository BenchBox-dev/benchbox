from pathlib import Path

import pytest

from benchbox.core.tpch.benchmark import TPCHBenchmark

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


@pytest.fixture
def fake_tpch_components(monkeypatch, tmp_path):

    class FakeTPCHDataGenerator:
        def __init__(self, scale_factor, parallel, output_dir, **_):
            self.scale_factor = scale_factor
            self.parallel = parallel
            self.output_dir = Path(output_dir)
            self.verbose = False

        def generate(self):
            tables = {
                "customer": self.output_dir / "customer.tbl",
                "orders": self.output_dir / "orders.tbl",
            }
            for path in tables.values():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("row\n")
            return tables

    class FakeTPCHQueryManager:
        def get_all_queries(self, **_):
            return {1: "SELECT 1", 2: "SELECT 2"}

        def get_query(self, query_id, **_):
            return f"SELECT {query_id}"

    monkeypatch.setattr("benchbox.core.tpch.benchmark.TPCHDataGenerator", FakeTPCHDataGenerator)
    monkeypatch.setattr("benchbox.core.tpch.benchmark.TPCHQueries", lambda: FakeTPCHQueryManager())

    monkeypatch.setattr(
        "benchbox.core.tpch.streams.TPCHStreams.PERMUTATION_MATRIX",
        [[1, 2], [2, 1]],
        raising=False,
    )

    return tmp_path


def test_tpch_benchmark_generates_data_with_fakes(fake_tpch_components):
    bench = TPCHBenchmark(scale_factor=0.1, output_dir=fake_tpch_components)

    generated = bench.generate_data()

    assert len(generated) == 2
    assert all(path.exists() for path in generated)

    queries = bench.get_queries(dialect="duckdb")
    assert queries["1"].lower().startswith("select")

    query = bench.get_query(1, seed=5, scale_factor=0.5)
    assert "select" in query.lower()

    stream_query = bench.get_query(2, params={"stream_id": 0})
    assert "2" in stream_query

    schema = bench.get_schema()
    assert any(table["name"].lower() == "customer" for table in schema.values())

    ddl = bench.get_create_tables_sql()
    assert "create table" in ddl.lower()


def test_get_table_loading_order_is_fk_safe_for_available_subset(fake_tpch_components):
    bench = TPCHBenchmark(scale_factor=0.1, output_dir=fake_tpch_components)

    available = ["supplier", "lineitem", "customer", "orders", "region", "nation", "part", "partsupp"]
    order = bench.get_table_loading_order(available)

    assert set(order) == set(available)
    position = {name: i for i, name in enumerate(order)}
    assert position["region"] < position["nation"]
    assert position["nation"] < position["customer"]
    assert position["nation"] < position["supplier"]
    assert position["customer"] < position["orders"]
    assert position["orders"] < position["lineitem"]
    assert position["part"] < position["lineitem"]
    assert position["supplier"] < position["lineitem"]
    assert position["part"] < position["partsupp"]
    assert position["supplier"] < position["partsupp"]

    order_with_extra = bench.get_table_loading_order([*available, "some_extra_table"])
    assert "some_extra_table" in order_with_extra


def test_expand_named_column_aliases():
    from benchbox.core.tpch.benchmark import _expand_named_column_aliases

    raw_query = """
    select c_count, count(*) as custdist
    from (
        select c_custkey, count(o_orderkey)
        from customer left outer join orders on c_custkey = o_custkey
        group by c_custkey
    ) as c_orders (c_custkey, c_count)
    group by c_count
    """
    transformed = _expand_named_column_aliases(raw_query)
    assert "(c_custkey, c_count)" not in transformed
    assert "AS c_count" in transformed
    assert ") as c_orders" in transformed.lower()


def test_tpch_benchmark_translates_bigquery_q13(fake_tpch_components):
    bench = TPCHBenchmark(scale_factor=0.1, output_dir=fake_tpch_components)
    raw_q13 = """
    select c_count, count(*) as custdist
    from (
        select c_custkey, count(o_orderkey)
        from customer left outer join orders on c_custkey = o_custkey
        group by c_custkey
    ) as c_orders (c_custkey, c_count)
    group by c_count
    """
    translated = bench.translate_query_text(raw_q13, "netezza", "bigquery")
    assert "(c_custkey, c_count)" not in translated


