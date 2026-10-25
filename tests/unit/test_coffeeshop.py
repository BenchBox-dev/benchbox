from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
import tempfile
from importlib import resources
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchbox.core.coffeeshop.benchmark import CoffeeShopBenchmark
from benchbox.core.coffeeshop.generator import CoffeeShopDataGenerator
from benchbox.core.coffeeshop.queries import CoffeeShopQueryManager
from benchbox.core.coffeeshop.schema import (
    TABLES,
    get_all_create_table_sql,
    get_create_table_sql,
)
from benchbox.core.runner.runner import _validate_manifest_if_present
from benchbox.utils.coffeeshop_seed_loader import load_location_seeds, load_product_seeds
from benchbox.utils.path_utils import get_benchmark_runs_datagen_path
from benchbox.utils.scale_factor import format_scale_factor

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
]


skip_windows_file_io = pytest.mark.skipif(
    sys.platform == "win32",
    reason="CoffeeShop file tests have cross-platform newline handling issues on Windows",
)

SEED_CHECKSUMS = {
    "dim_locations.csv": "649a37932a6939bf81b363b8dbb2e0258c27a470cabe8aff3de27e6bfc8db915",
    "dim_products.csv": "663af10fcd72d4c29ede2da457a5baa4a03656aa5ace7171ce5e4cb2c314cf26",
}


class TestCoffeeShopSchema:
    def test_tables_defined(self):
        assert set(TABLES.keys()) == {"dim_locations", "dim_products", "order_lines"}

    def test_dim_locations_structure(self):
        dim_locations = TABLES["dim_locations"]
        column_names = [column["name"] for column in dim_locations["columns"]]
        assert column_names == [
            "record_id",
            "location_id",
            "city",
            "state",
            "country",
            "region",
        ]

    def test_order_lines_structure(self):
        order_lines = TABLES["order_lines"]
        column_names = [column["name"] for column in order_lines["columns"]]
        assert column_names == [
            "order_id",
            "line_number",
            "location_record_id",
            "location_id",
            "product_record_id",
            "product_id",
            "order_date",
            "order_time",
            "quantity",
            "unit_price",
            "total_price",
            "region",
        ]

    def test_create_table_sql_generation(self):
        sql = get_create_table_sql("dim_products")
        assert "CREATE TABLE dim_products" in sql
        assert "record_id INTEGER" in sql
        assert sql.strip().endswith(";")

    def test_get_all_create_table_sql(self):
        sql = get_all_create_table_sql()
        assert sql.count("CREATE TABLE") == 3
        assert sql.index("CREATE TABLE dim_locations") < sql.index("CREATE TABLE order_lines")


class TestCoffeeShopGenerator:
    def test_seed_resources_packaged(self):
        package_root = resources.files("benchbox.data.coffeeshop")

        dim_locations = package_root.joinpath("dim_locations.csv")
        dim_products = package_root.joinpath("dim_products.csv")

        assert dim_locations.is_file()
        assert dim_products.is_file()

        locations = load_location_seeds()
        products = load_product_seeds()
        assert locations
        assert products

        for filename, expected_hash in SEED_CHECKSUMS.items():
            with resources.as_file(package_root.joinpath(filename)) as seed_path:
                assert _hash_file(seed_path) == expected_hash

    def test_calculate_order_count(self):
        assert CoffeeShopDataGenerator.calculate_order_count(0.001) == 8_500
        assert CoffeeShopDataGenerator.calculate_order_count(0.01) == 85_000
        assert CoffeeShopDataGenerator.calculate_order_count(1.0) == 8_500_000

    def test_daily_weighting_bias(self):
        generator = CoffeeShopDataGenerator(scale_factor=0.001, output_dir=Path.cwd(), compress_data=False)
        distribution = generator._distribute_daily_orders(100_000)
        december = sum(count for day, count in distribution.items() if day.month == 12)
        july = sum(count for day, count in distribution.items() if day.month == 7)
        assert december > july

    @skip_windows_file_io
    def test_generate_data_row_counts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            generator = CoffeeShopDataGenerator(scale_factor=0.0001, output_dir=Path(temp_dir), compress_data=False)
            tables = generator.generate_data()

            assert set(tables.keys()) == {"dim_locations", "dim_products", "order_lines"}

            location_rows = _count_pipe_rows(Path(tables["dim_locations"]))
            product_rows = _count_pipe_rows(Path(tables["dim_products"]))
            order_line_rows = _count_pipe_rows(Path(tables["order_lines"]))

            assert location_rows == len(load_location_seeds())
            assert product_rows == len(load_product_seeds())

            order_count = CoffeeShopDataGenerator.calculate_order_count(0.0001)
            sequence = generator._order_line_sequence
            expected_lines = sum(sequence[i % len(sequence)] for i in range(order_count))
            assert order_line_rows == expected_lines

    @skip_windows_file_io
    def test_region_weighting_bias(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            generator = CoffeeShopDataGenerator(scale_factor=0.0002, output_dir=Path(temp_dir), compress_data=False)
            tables = generator.generate_data()

            south_lines = 0
            west_lines = 0
            with open(tables["order_lines"], encoding="utf-8") as handle:
                reader = csv.reader(handle)
                for row in reader:
                    region = row[-1]
                    if region == "South":
                        south_lines += 1
                    elif region == "West":
                        west_lines += 1

            assert south_lines > west_lines

    def test_default_output_directory_uses_datagen_path(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)

        generator = CoffeeShopDataGenerator(scale_factor=0.001)
        tables = generator.generate_data()

        expected_root = get_benchmark_runs_datagen_path("coffeeshop", 0.001)
        assert Path(tables["order_lines"]).parent == expected_root
        assert (expected_root / "_datagen_manifest.json").exists()

    def test_generation_is_deterministic_across_runs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            generator = CoffeeShopDataGenerator(scale_factor=0.0001, output_dir=output, compress_data=False)

            first_paths = generator.generate_data()
            first_hash = _hash_file(first_paths["order_lines"])

            second_paths = generator.generate_data()
            second_hash = _hash_file(second_paths["order_lines"])

            assert first_hash == second_hash

    @skip_windows_file_io
    def test_manifest_contains_relative_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            generator = CoffeeShopDataGenerator(scale_factor=0.0001, output_dir=output, compress_data=False)
            tables = generator.generate_data()

            manifest_path = output / "_datagen_manifest.json"
            assert manifest_path.exists()

            manifest = json.loads(manifest_path.read_text())
            assert manifest["benchmark"] == "coffeeshop"
            assert set(manifest["tables"].keys()) == {"dim_locations", "dim_products", "order_lines"}

            from benchbox.utils.datagen_manifest import get_table_files

            for table_name in manifest["tables"].keys():
                entries = get_table_files(manifest, table_name)
                for entry in entries:
                    assert not os.path.isabs(entry["path"])

            row_count = _count_pipe_rows(Path(tables["order_lines"]))
            order_lines_entries = get_table_files(manifest, "order_lines")
            manifest_row_count = order_lines_entries[0]["row_count"]
            assert manifest_row_count == row_count

    @skip_windows_file_io
    def test_dimension_tables_match_seed_rows(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        generator = CoffeeShopDataGenerator(scale_factor=0.0001, compress_data=False)
        tables = generator.generate_data(tables=["dim_locations", "dim_products"])

        package_root = resources.files("benchbox.data.coffeeshop")
        for table in ("dim_locations", "dim_products"):
            generated_rows = _read_delimited_rows(Path(tables[table]), ",")
            with resources.as_file(package_root.joinpath(f"{table}.csv")) as seed_path:
                seed_rows = _read_delimited_rows(seed_path, ",", skip_header=True)
            if table == "dim_products":
                seed_rows = [
                    tuple(list(row[:5]) + [f"{float(row[5]):.2f}", f"{float(row[6]):.2f}"] + list(row[7:]))
                    for row in seed_rows
                ]

            def sort_key(row):
                return int(row[0])

            assert sorted(generated_rows, key=sort_key) == sorted(seed_rows, key=sort_key)


class TestCoffeeShopSeedErrors:
    def test_missing_seed_file_raises(self, monkeypatch, tmp_path):
        load_location_seeds.cache_clear()
        load_product_seeds.cache_clear()

        ghost_package = tmp_path / "coffeeshop"
        ghost_package.mkdir()

        monkeypatch.setattr(
            "benchbox.utils.coffeeshop_seed_loader.resources.files",
            lambda package: ghost_package,
        )

        with pytest.raises(FileNotFoundError):
            load_location_seeds()

        load_location_seeds.cache_clear()
        load_product_seeds.cache_clear()


class TestCoffeeShopQueries:
    def test_query_manager(self):
        manager = CoffeeShopQueryManager()
        queries = manager.get_all_queries()

        assert len(queries) == 11
        sample_query = manager.get_query("SA1")
        assert "order_lines" in sample_query
        assert "dim_locations" in sample_query

    def test_invalid_query_id(self):
        manager = CoffeeShopQueryManager()
        with pytest.raises(ValueError, match="Query 'INVALID' not found"):
            manager.get_query("INVALID")


class TestCoffeeShopBenchmark:
    def test_generate_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            benchmark = CoffeeShopBenchmark(scale_factor=0.0001, output_dir=Path(temp_dir))
            tables = benchmark.generate_data()
            assert set(tables.keys()) == {"dim_locations", "dim_products", "order_lines"}
            assert all(Path(path).exists() for path in tables.values())

    def test_get_schema(self):
        benchmark = CoffeeShopBenchmark(scale_factor=0.001)
        schema = benchmark.get_schema()
        assert set(schema.keys()) == {"dim_locations", "dim_products", "order_lines"}

    def test_get_queries(self):
        benchmark = CoffeeShopBenchmark(scale_factor=0.001)
        queries = benchmark.get_queries()
        assert len(queries) == 11
        assert "order_lines" in queries["SA2"]

    def test_execute_query_mock(self):
        benchmark = CoffeeShopBenchmark(scale_factor=0.001)
        mock_connection = _MockConnection()
        result = benchmark.execute_query("SA1", mock_connection)
        assert result == [("ok",)]

    def test_run_benchmark_mock(self):
        benchmark = CoffeeShopBenchmark(scale_factor=0.001)
        mock_connection = _MockConnection()
        result = benchmark.run_benchmark(mock_connection, queries=["SA1", "PR1"], iterations=1)
        assert result["benchmark"] == "CoffeeShop Benchmark"
        assert len(result["query_results"]) == 2
        assert all(entry["success"] for entry in result["query_results"])

    def test_default_output_dir_matches_basebenchmark(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("BENCHBOX_OUTPUT_DIR", raising=False)
        benchmark = CoffeeShopBenchmark(scale_factor=0.001)
        tables = benchmark.generate_data()

        sf_fragment = format_scale_factor(0.001)
        expected_root = tmp_path / "benchmark_runs" / "datagen" / f"coffeeshop_{sf_fragment}"
        assert benchmark.output_dir == expected_root
        assert Path(tables["order_lines"]).parent == expected_root

    def test_manifest_reuse_validation(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        scale = 0.0001
        benchmark = CoffeeShopBenchmark(scale_factor=scale)
        benchmark.generate_data()

        config = SimpleNamespace(name="CoffeeShop", scale_factor=scale)
        valid, manifest, found = _validate_manifest_if_present(benchmark, config)
        assert found is True
        assert valid is True
        assert manifest["benchmark"] == "coffeeshop"


class TestCoffeeShopErrors:
    def test_invalid_table_name(self):
        benchmark = CoffeeShopBenchmark(scale_factor=0.001)
        with pytest.raises(ValueError, match="Invalid table names"):
            benchmark.generate_data(tables=["missing_table"])

    def test_unsupported_output_format(self):
        benchmark = CoffeeShopBenchmark(scale_factor=0.001)
        with pytest.raises(ValueError, match="Unsupported output format"):
            benchmark.generate_data(output_format="parquet")

    def test_load_data_without_generation(self):
        benchmark = CoffeeShopBenchmark(scale_factor=0.001)
        with pytest.raises(ValueError, match="No data generated"):
            benchmark.load_data_to_database(_MockConnection())


def _count_pipe_rows(path: Path) -> int:
    with open(path, encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_delimited_rows(path: Path, delimiter: str, *, skip_header: bool = False) -> list[tuple[str, ...]]:
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        rows = [tuple(row) for row in reader]
    if skip_header and rows:
        return rows[1:]
    return rows


class _MockCursor:
    def __init__(self):
        self.last_query: str | None = None

    def execute(self, sql: str, params: list[str] | None = None):
        self.last_query = sql
        return [("ok",)]

    def fetchall(self):
        return [("ok",)]


class _MockConnection:
    def __init__(self):
        self.cursor_obj = _MockCursor()

    def execute(self, sql: str):
        return self.cursor_obj

    def cursor(self):
        return self.cursor_obj

    def executescript(self, sql: str):
        self.cursor_obj.last_query = sql

    def commit(self):
        pass
