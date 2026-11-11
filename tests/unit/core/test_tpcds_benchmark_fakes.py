from collections.abc import Iterator
from pathlib import Path

import pytest

from benchbox.core.tpcds.benchmark import TPCDSBenchmark

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


RENDERED_SCALE_FACTORS: list[float] = []


@pytest.fixture
def fake_tpcds_components(monkeypatch, tmp_path):

    RENDERED_SCALE_FACTORS.clear()

    class FakeTPCDSDataGenerator:
        def __init__(self, scale_factor, parallel, output_dir, **_):
            self.scale_factor = scale_factor
            self.parallel = parallel
            self.output_dir = Path(output_dir)

        def generate(self):
            tables = {
                "store_sales": self.output_dir / "store_sales.dat",
                "customer": self.output_dir / "customer.dat",
            }
            for path in tables.values():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("row\n")
            return tables

        def generate_table(self, table_name, scale_factor) -> Iterator[str]:
            return iter([f"{table_name}|{scale_factor}"])

    class FakeDSQGen:
        def generate(self, query_id, **_):
            return f"SELECT * FROM variant_{query_id}"

    class FakeTPCDSQueryManager:
        def __init__(self):
            self.dsqgen = FakeDSQGen()

        def get_all_queries(self, dialect="netezza", scale_factor=1.0):
            RENDERED_SCALE_FACTORS.append(scale_factor)
            return {1: "SELECT 1", 2: "SELECT 2"}

        def get_query(self, query_id, **_):
            return f"SELECT {query_id}"

    class FakeTPCDSCTools:
        def generate_data_table(self, table_name, scale_factor, output_dir):
            output = Path(output_dir) / f"{table_name}.dat"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(f"{table_name}|{scale_factor}\n")
            return [str(output)]

    class FakeValidationEngine:
        def validate_preflight_conditions(self, *_, **__):
            return {"passed": True}

        def validate_generated_data(self, *_):
            return {"passed": True}

    monkeypatch.setattr("benchbox.core.tpcds.benchmark.runner.TPCDSDataGenerator", FakeTPCDSDataGenerator)
    monkeypatch.setattr("benchbox.core.tpcds.benchmark.runner.TPCDSQueryManager", lambda: FakeTPCDSQueryManager())
    monkeypatch.setattr("benchbox.core.tpcds.benchmark.runner.TPCDSCTools", FakeTPCDSCTools)
    monkeypatch.setattr("benchbox.core.tpcds.benchmark.runner.DataValidationEngine", lambda: FakeValidationEngine())
    monkeypatch.setattr("benchbox.core.tpcds.benchmark.runner.DatabaseValidationEngine", lambda: FakeValidationEngine())

    return tmp_path


def test_tpcds_benchmark_uses_fakes(fake_tpcds_components):
    bench = TPCDSBenchmark(scale_factor=1.0, output_dir=fake_tpcds_components)

    generated = bench.generate_data()
    assert len(generated) == 2
    assert all(path.exists() for path in generated)

    queries = bench.get_queries(dialect="duckdb")
    assert queries["1"].lower().startswith("select")

    query = bench.get_query(1, dialect="duckdb", variant="a", params={"seed": 7})
    assert "variant_1" in query or "select" in query.lower()

    streamed = list(bench.generate_table_data("store_sales"))
    assert streamed[0].startswith("store_sales")

    ordered = bench.get_table_loading_order(["store_sales", "customer", "date_dim"])
    assert ordered[0] == "date_dim"

    schema = bench.get_schema()
    assert any(table["name"] == "customer" for table in schema.values())


@pytest.mark.parametrize("scale_factor", [0.01, 1.0, 10.0])
def test_get_queries_renders_at_the_data_scale_factor(fake_tpcds_components, scale_factor):

    bench = TPCDSBenchmark(scale_factor=scale_factor, output_dir=fake_tpcds_components)

    bench.get_queries(dialect="duckdb")

    assert [scale_factor] == RENDERED_SCALE_FACTORS


