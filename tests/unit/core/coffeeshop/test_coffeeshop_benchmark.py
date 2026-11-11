# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from pathlib import Path

import pytest

from benchbox.core.coffeeshop.benchmark import CoffeeShopBenchmark

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


@pytest.fixture
def coffeeshop(tmp_path):
    return CoffeeShopBenchmark(scale_factor=0.000001, output_dir=tmp_path)


class TestCoffeeShopBenchmarkInit:
    def test_default_scale_factor(self, tmp_path):
        b = CoffeeShopBenchmark(output_dir=tmp_path)
        assert b.scale_factor == 1.0

    def test_custom_scale_factor(self, tmp_path):
        b = CoffeeShopBenchmark(scale_factor=0.5, output_dir=tmp_path)
        assert b.scale_factor == 0.5

    def test_name_and_version(self, coffeeshop):
        assert coffeeshop._name == "CoffeeShop Benchmark"
        assert coffeeshop._version == "2.0"

    def test_query_manager_set(self, coffeeshop):
        from benchbox.core.coffeeshop.queries import CoffeeShopQueryManager

        assert isinstance(coffeeshop.query_manager, CoffeeShopQueryManager)

    def test_data_generator_scale_propagated(self, tmp_path):
        b = CoffeeShopBenchmark(scale_factor=0.5, output_dir=tmp_path)
        assert b.data_generator.scale_factor == 0.5


class TestCoffeeShopBenchmarkGenerateData:
    def test_all_three_tables_generated(self, coffeeshop):
        result = coffeeshop.generate_data()
        assert set(result.keys()) == {"dim_locations", "dim_products", "order_lines"}

    def test_generated_files_exist(self, coffeeshop):
        result = coffeeshop.generate_data()
        for path_str in result.values():
            assert Path(path_str).exists()

    def test_subset_of_tables(self, coffeeshop):
        result = coffeeshop.generate_data(tables=["dim_locations"])
        assert set(result.keys()) == {"dim_locations"}

    def test_tables_stored_after_generation(self, coffeeshop):
        coffeeshop.generate_data()
        assert len(coffeeshop.tables) == 3

    def test_unsupported_format_raises(self, coffeeshop):
        with pytest.raises(ValueError, match="format"):
            coffeeshop.generate_data(output_format="parquet")

    def test_unknown_table_raises(self, coffeeshop):
        with pytest.raises(ValueError):
            coffeeshop.generate_data(tables=["no_such_table"])

    def test_spark_tm1_query_casts_string_order_time(self, coffeeshop):
        query = coffeeshop.get_queries(dialect="spark")["TM1"]
        assert "TO_TIMESTAMP(CONCAT('1970-01-01 ', ol.order_time))" in query
        assert "EXTRACT(HOUR FROM `ol`.`order_time`)" not in query

    def test_spark_sa4_query_avoids_nested_decimal_window(self, coffeeshop):
        query = coffeeshop.get_queries(dialect="spark")["SA4"]
        assert "CROSS JOIN" in query
        assert "CAST(SUM(ol.total_price) AS DOUBLE)" in query
        assert "SUM(SUM" not in query


class TestCoffeeShopBenchmarkLoadAndRun:
    @pytest.fixture
    def loaded_conn(self, coffeeshop):
        import duckdb

        coffeeshop.generate_data()
        conn = duckdb.connect(":memory:")
        coffeeshop.load_data_to_database(conn)
        return conn

    def test_load_before_generate_raises(self, coffeeshop):
        import duckdb

        conn = duckdb.connect(":memory:")
        with pytest.raises(ValueError, match="No data generated"):
            coffeeshop.load_data_to_database(conn)

    def test_dim_locations_loaded(self, loaded_conn):
        count = loaded_conn.execute("SELECT COUNT(*) FROM dim_locations").fetchone()[0]
        assert count > 0

    def test_order_lines_loaded(self, loaded_conn):
        count = loaded_conn.execute("SELECT COUNT(*) FROM order_lines").fetchone()[0]
        assert count > 0

    def test_run_benchmark_returns_query_results(self, coffeeshop, loaded_conn):
        result = coffeeshop.run_benchmark(loaded_conn, queries=["SA1"])
        assert "query_results" in result
        assert len(result["query_results"]) == 1

    def test_run_benchmark_query_id_correct(self, coffeeshop, loaded_conn):
        result = coffeeshop.run_benchmark(loaded_conn, queries=["SA1"])
        assert result["query_results"][0]["query_id"] == "SA1"

    def test_run_benchmark_success_flag(self, coffeeshop, loaded_conn):
        result = coffeeshop.run_benchmark(loaded_conn, queries=["SA1"])
        assert result["query_results"][0]["success"] is True

    def test_run_benchmark_avg_time_positive(self, coffeeshop, loaded_conn):
        result = coffeeshop.run_benchmark(loaded_conn, queries=["SA1"])
        assert result["query_results"][0]["avg_time"] > 0


class TestCoffeeShopBenchmarkTableLoadingOrder:
    def test_orders_a_shuffled_available_subset_fk_safely(self, coffeeshop):
        available = ["order_lines", "dim_products", "dim_locations"]
        order = coffeeshop.get_table_loading_order(available)

        assert set(order) == set(available)
        position = {name: i for i, name in enumerate(order)}
        assert position["dim_locations"] < position["order_lines"]
        assert position["dim_products"] < position["order_lines"]

    def test_unknown_table_is_preserved_not_dropped(self, coffeeshop):
        order = coffeeshop.get_table_loading_order(["dim_locations", "some_extra_table"])
        assert "some_extra_table" in order


