from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class FakeConn:
    def __init__(self, rows=None, fail_on: str | None = None):
        self._rows = rows or [("ok",)]
        self._fail_on = fail_on
        self.committed = False

    def execute(self, sql, params=None):
        if self._fail_on and self._fail_on in sql:
            raise RuntimeError(f"fake error on {self._fail_on}")
        return SimpleNamespace(fetchall=lambda: self._rows)

    def executemany(self, sql, rows):
        pass

    def executescript(self, sql):
        pass

    def commit(self):
        self.committed = True


class CursorConn:
    def __init__(self, rows=None):
        self._rows = rows or [("ok",)]

    def cursor(self):
        return SimpleNamespace(
            execute=lambda sql, params=None: None,
            fetchall=lambda: self._rows,
        )


class UnsupportedConn:
    pass


class TestAMPLabBenchmark:
    def test_init_and_metadata(self, tmp_path: Path):
        from benchbox.core.amplab.benchmark import AMPLabBenchmark

        b = AMPLabBenchmark(scale_factor=0.01, output_dir=tmp_path)
        assert b._name == "AMPLab Big Data Benchmark"
        assert b.scale_factor == 0.01

    def test_generate_data_unsupported_format(self, tmp_path: Path):
        from benchbox.core.amplab.benchmark import AMPLabBenchmark

        b = AMPLabBenchmark(scale_factor=0.01, output_dir=tmp_path)
        with pytest.raises(ValueError, match="Unsupported output format"):
            b.generate_data(output_format="parquet")

    def test_generate_data_invalid_table(self, tmp_path: Path):
        from benchbox.core.amplab.benchmark import AMPLabBenchmark

        b = AMPLabBenchmark(scale_factor=0.01, output_dir=tmp_path)
        with pytest.raises(ValueError, match="Invalid table names"):
            b.generate_data(tables=["nonexistent"])

    def test_get_query_and_queries(self, tmp_path: Path):
        from benchbox.core.amplab.benchmark import AMPLabBenchmark

        b = AMPLabBenchmark(scale_factor=0.01, output_dir=tmp_path)
        queries = b.get_queries()
        assert len(queries) > 0

        queries_translated = b.get_queries(dialect="duckdb")
        assert len(queries_translated) > 0

        first_qid = next(iter(queries))
        q = b.get_query(first_qid)
        assert "SELECT" in q.upper() or "select" in q.lower()

    def test_execute_query_direct(self, tmp_path: Path):
        from benchbox.core.amplab.benchmark import AMPLabBenchmark

        b = AMPLabBenchmark(scale_factor=0.01, output_dir=tmp_path)
        conn = FakeConn(rows=[(1, 2, 3)])
        qid = next(iter(b.get_all_queries()))
        result = b.execute_query(qid, conn)
        assert result == [(1, 2, 3)]

    def test_execute_query_cursor(self, tmp_path: Path):
        from benchbox.core.amplab.benchmark import AMPLabBenchmark

        b = AMPLabBenchmark(scale_factor=0.01, output_dir=tmp_path)
        conn = CursorConn(rows=[(4,)])
        qid = next(iter(b.get_all_queries()))
        result = b.execute_query(qid, conn)
        assert result == [(4,)]

    def test_execute_query_unsupported(self, tmp_path: Path):
        from benchbox.core.amplab.benchmark import AMPLabBenchmark

        b = AMPLabBenchmark(scale_factor=0.01, output_dir=tmp_path)
        qid = next(iter(b.get_all_queries()))
        with pytest.raises(ValueError, match="Unsupported connection"):
            b.execute_query(qid, UnsupportedConn())

    def test_get_schema_and_create_tables(self, tmp_path: Path):
        from benchbox.core.amplab.benchmark import AMPLabBenchmark

        b = AMPLabBenchmark(scale_factor=0.01, output_dir=tmp_path)
        schema = b.get_schema()
        assert "rankings" in schema
        sql = b.get_create_tables_sql()
        assert "CREATE TABLE" in sql.upper()

    def test_load_data_no_data(self, tmp_path: Path):
        from benchbox.core.amplab.benchmark import AMPLabBenchmark

        b = AMPLabBenchmark(scale_factor=0.01, output_dir=tmp_path)
        with pytest.raises(ValueError, match="No data generated"):
            b.load_data_to_database(FakeConn())

    def test_run_benchmark_success(self, tmp_path: Path):
        from benchbox.core.amplab.benchmark import AMPLabBenchmark

        b = AMPLabBenchmark(scale_factor=0.01, output_dir=tmp_path)
        conn = FakeConn(rows=[(1,)])
        queries = list(b.get_all_queries().keys())[:2]
        result = b.run_benchmark(conn, queries=queries, iterations=1)
        assert result["benchmark"] == "AMPLab"
        assert len(result["queries"]) == 2

    def test_run_benchmark_with_error(self, tmp_path: Path):
        from benchbox.core.amplab.benchmark import AMPLabBenchmark

        b = AMPLabBenchmark(scale_factor=0.01, output_dir=tmp_path)
        conn = FakeConn(fail_on="SELECT")
        queries = list(b.get_all_queries().keys())[:1]
        result = b.run_benchmark(conn, queries=queries, iterations=1)
        assert result["queries"][queries[0]]["iterations"][0]["success"] is False

    def test_get_csv_loading_config(self, tmp_path: Path):
        from benchbox.core.amplab.benchmark import AMPLabBenchmark

        b = AMPLabBenchmark(scale_factor=0.01, output_dir=tmp_path)
        config = b.get_csv_loading_config("rankings")
        assert any("delim" in c for c in config)

    def test_translate_query_text(self, tmp_path: Path):
        from benchbox.core.amplab.benchmark import AMPLabBenchmark

        b = AMPLabBenchmark(scale_factor=0.01, output_dir=tmp_path)
        q = "SELECT 1"
        translated = b.translate_query_text(q, "duckdb")
        assert "1" in translated


class TestAMPLabGenerator:
    def test_generate_data_small(self, tmp_path: Path):
        from benchbox.core.amplab.generator import AMPLabDataGenerator

        gen = AMPLabDataGenerator(scale_factor=0.001, output_dir=tmp_path)
        result = gen.generate_data(tables=["rankings", "documents"])
        assert "rankings" in result
        assert "documents" in result
        assert Path(result["rankings"]).exists()

    def test_url_ip_content_generation(self, tmp_path: Path):
        from benchbox.core.amplab.generator import AMPLabDataGenerator

        gen = AMPLabDataGenerator(scale_factor=0.001, output_dir=tmp_path)
        url = gen._generate_url(42)
        assert url.startswith("http://")
        ip = gen._generate_ip_address()
        assert ip.count(".") == 3
        content = gen._generate_content(5, 10)
        assert len(content.split()) >= 5


class TestClickBenchBenchmark:
    def test_init_and_metadata(self, tmp_path: Path):
        from benchbox.core.clickbench.benchmark import ClickBenchBenchmark

        b = ClickBenchBenchmark(scale_factor=0.01, output_dir=tmp_path)
        assert b._name == "ClickBench"

    def test_generate_data_unsupported_format(self, tmp_path: Path):
        from benchbox.core.clickbench.benchmark import ClickBenchBenchmark

        b = ClickBenchBenchmark(scale_factor=0.01, output_dir=tmp_path)
        with pytest.raises(ValueError, match="Unsupported output format"):
            b.generate_data(output_format="json")

    def test_get_query_rejects_params(self, tmp_path: Path):
        from benchbox.core.clickbench.benchmark import ClickBenchBenchmark

        b = ClickBenchBenchmark(scale_factor=0.01, output_dir=tmp_path)
        with pytest.raises(ValueError, match="static"):
            b.get_query("Q1", params={"x": 1})

    def test_get_queries_and_categories(self, tmp_path: Path):
        from benchbox.core.clickbench.benchmark import ClickBenchBenchmark

        b = ClickBenchBenchmark(scale_factor=0.01, output_dir=tmp_path)
        queries = b.get_queries()
        assert len(queries) >= 10
        categories = b.get_query_categories()
        assert isinstance(categories, dict)

    def test_translate_query_text(self, tmp_path: Path):
        from benchbox.core.clickbench.benchmark import ClickBenchBenchmark

        b = ClickBenchBenchmark(scale_factor=0.01, output_dir=tmp_path)
        translated = b.translate_query_text("SELECT 1", "duckdb")
        assert "1" in translated

    def test_execute_query_direct_and_cursor(self, tmp_path: Path):
        from benchbox.core.clickbench.benchmark import ClickBenchBenchmark

        b = ClickBenchBenchmark(scale_factor=0.01, output_dir=tmp_path)
        qid = next(iter(b.get_all_queries()))
        result = b.execute_query(qid, FakeConn(rows=[(1,)]))
        assert result == [(1,)]
        result2 = b.execute_query(qid, CursorConn(rows=[(2,)]))
        assert result2 == [(2,)]
        with pytest.raises(ValueError, match="Unsupported"):
            b.execute_query(qid, UnsupportedConn())

    def test_run_benchmark(self, tmp_path: Path):
        from benchbox.core.clickbench.benchmark import ClickBenchBenchmark

        b = ClickBenchBenchmark(scale_factor=0.01, output_dir=tmp_path)
        conn = FakeConn(rows=[(1,)])
        queries = list(b.get_all_queries().keys())[:2]
        result = b.run_benchmark(conn, queries=queries, iterations=1)
        assert result["benchmark"] == "ClickBench"
        assert len(result["queries"]) == 2

    def test_get_schema_and_csv_config(self, tmp_path: Path):
        from benchbox.core.clickbench.benchmark import ClickBenchBenchmark

        b = ClickBenchBenchmark(scale_factor=0.01, output_dir=tmp_path)
        schema = b.get_schema()
        assert "hits" in schema
        config = b.get_csv_loading_config("hits")
        assert any("nullstr" in c for c in config)

    def test_load_data_no_data(self, tmp_path: Path):
        from benchbox.core.clickbench.benchmark import ClickBenchBenchmark

        b = ClickBenchBenchmark(scale_factor=0.01, output_dir=tmp_path)
        with pytest.raises(ValueError, match="No data generated"):
            b.load_data_to_database(FakeConn())


class TestH2OBenchmark:
    def test_init_and_metadata(self, tmp_path: Path):
        from benchbox.core.h2odb.benchmark import H2OBenchmark

        b = H2OBenchmark(scale_factor=0.01, output_dir=tmp_path)
        assert b._name == "H2O Database Benchmark"

    def test_generate_data_validation(self, tmp_path: Path):
        from benchbox.core.h2odb.benchmark import H2OBenchmark

        b = H2OBenchmark(scale_factor=0.01, output_dir=tmp_path)
        with pytest.raises(ValueError, match="Unsupported"):
            b.generate_data(output_format="xml")
        with pytest.raises(ValueError, match="Invalid table names"):
            b.generate_data(tables=["nonexistent"])

    def test_get_query_rejects_params(self, tmp_path: Path):
        from benchbox.core.h2odb.benchmark import H2OBenchmark

        b = H2OBenchmark(scale_factor=0.01, output_dir=tmp_path)
        with pytest.raises(ValueError, match="static"):
            b.get_query("Q1", params={"x": 1})

    def test_get_queries_and_translate(self, tmp_path: Path):
        from benchbox.core.h2odb.benchmark import H2OBenchmark

        b = H2OBenchmark(scale_factor=0.01, output_dir=tmp_path)
        queries = b.get_queries()
        assert len(queries) > 0
        translated = b.get_queries(dialect="duckdb")
        assert len(translated) == len(queries)

    def test_execute_and_run(self, tmp_path: Path):
        from benchbox.core.h2odb.benchmark import H2OBenchmark

        b = H2OBenchmark(scale_factor=0.01, output_dir=tmp_path)
        conn = FakeConn(rows=[(42,)])
        qid = next(iter(b.get_all_queries()))
        assert b.execute_query(qid, conn) == [(42,)]
        result = b.run_benchmark(conn, queries=[qid], iterations=1)
        assert result["benchmark"] == "H2O Database Benchmark"

    def test_schema_and_create_tables(self, tmp_path: Path):
        from benchbox.core.h2odb.benchmark import H2OBenchmark

        b = H2OBenchmark(scale_factor=0.01, output_dir=tmp_path)
        schema = b.get_schema()
        assert len(schema) > 0
        sql = b.get_create_tables_sql()
        assert "CREATE TABLE" in sql.upper()


class TestSSBBenchmark:
    def test_init_and_metadata(self, tmp_path: Path):
        from benchbox.core.ssb.benchmark import SSBBenchmark

        b = SSBBenchmark(scale_factor=0.01, output_dir=tmp_path)
        assert b._name == "Star Schema Benchmark"

    def test_generate_data_validation(self, tmp_path: Path):
        from benchbox.core.ssb.benchmark import SSBBenchmark

        b = SSBBenchmark(scale_factor=0.01, output_dir=tmp_path)
        with pytest.raises(ValueError, match="Unsupported"):
            b.generate_data(output_format="avro")
        with pytest.raises(ValueError, match="Invalid"):
            b.generate_data(tables=["bad_table"])

    def test_get_query_and_queries(self, tmp_path: Path):
        from benchbox.core.ssb.benchmark import SSBBenchmark

        b = SSBBenchmark(scale_factor=0.01, output_dir=tmp_path)
        queries = b.get_queries()
        assert len(queries) > 0
        translated = b.get_queries(dialect="duckdb")
        assert len(translated) > 0
        first_qid = next(iter(queries))
        q = b.get_query(first_qid)
        assert isinstance(q, str)

    def test_execute_query_paths(self, tmp_path: Path):
        from benchbox.core.ssb.benchmark import SSBBenchmark

        b = SSBBenchmark(scale_factor=0.01, output_dir=tmp_path)
        qid = next(iter(b.get_all_queries()))
        assert b.execute_query(qid, FakeConn(rows=[(1,)])) == [(1,)]
        assert b.execute_query(qid, CursorConn(rows=[(2,)])) == [(2,)]
        with pytest.raises(ValueError, match="Unsupported"):
            b.execute_query(qid, UnsupportedConn())

    def test_run_benchmark(self, tmp_path: Path):
        from benchbox.core.ssb.benchmark import SSBBenchmark

        b = SSBBenchmark(scale_factor=0.01, output_dir=tmp_path)
        conn = FakeConn(rows=[(99,)])
        queries = list(b.get_all_queries().keys())[:2]
        result = b.run_benchmark(conn, queries=queries, iterations=2)
        assert result["benchmark"] == "Star Schema Benchmark"
        assert len(result["query_results"]) == 2
        assert result["query_results"][0]["success"] is True

    def test_run_benchmark_error_path(self, tmp_path: Path):
        from benchbox.core.ssb.benchmark import SSBBenchmark

        b = SSBBenchmark(scale_factor=0.01, output_dir=tmp_path)
        conn = FakeConn(fail_on="SELECT")
        queries = list(b.get_all_queries().keys())[:1]
        result = b.run_benchmark(conn, queries=queries, iterations=1)
        assert result["query_results"][0]["success"] is False
        assert "error" in result["query_results"][0]

    def test_schema_csv_config(self, tmp_path: Path):
        from benchbox.core.ssb.benchmark import SSBBenchmark

        b = SSBBenchmark(scale_factor=0.01, output_dir=tmp_path)
        schema = b.get_schema()
        assert "lineorder" in schema
        config = b.get_csv_loading_config("lineorder")
        assert any("delim" in c for c in config)
        sql = b.get_create_tables_sql()
        assert "CREATE TABLE" in sql.upper()

    def test_load_data_to_database_executescript_and_commit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import benchbox.core.ssb.benchmark as ssb_mod
        from benchbox.core.ssb.benchmark import SSBBenchmark

        monkeypatch.setattr(
            ssb_mod,
            "TABLES",
            {
                "date": {
                    "columns": [
                        {"name": "d_year"},
                        {"name": "d_weeknuminyear"},
                    ]
                }
            },
        )

        b = SSBBenchmark(scale_factor=0.01, output_dir=tmp_path)
        b.get_create_tables_sql = lambda *a, **k: "CREATE TABLE date(d_year INT, d_weeknuminyear INT);"

        data_file = tmp_path / "date.csv"
        data_file.write_text("1992|1\n", encoding="utf-8")
        b.tables = {"date": data_file}

        class ScriptConn:
            def __init__(self):
                self.script_called = False
                self.executemany_called = False
                self.committed = False

            def executescript(self, _sql):
                self.script_called = True

            def executemany(self, _sql, _rows):
                self.executemany_called = True

            def commit(self):
                self.committed = True

        conn = ScriptConn()
        b.load_data_to_database(conn)
        assert conn.script_called is True
        assert conn.executemany_called is True
        assert conn.committed is True

    def test_private_load_data_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import benchbox.core.ssb.benchmark as ssb_mod
        from benchbox.core.ssb.benchmark import SSBBenchmark

        monkeypatch.setattr(
            ssb_mod,
            "TABLES",
            {
                "date": {"columns": [{"name": "d_year"}, {"name": "d_weeknuminyear"}]},
                "customer": {"columns": [{"name": "c_id"}]},
                "supplier": {"columns": [{"name": "s_id"}]},
                "part": {"columns": [{"name": "p_id"}]},
                "lineorder": {"columns": [{"name": "lo_id"}]},
            },
        )

        b = SSBBenchmark(scale_factor=0.01, output_dir=tmp_path)
        b.get_create_tables_sql = lambda *a, **k: "CREATE TABLE date(d_year INT, d_weeknuminyear INT);"

        date_file = tmp_path / "date.tbl"
        date_file.write_text("1992|1\nbadrow\n", encoding="utf-8")
        b.tables = {"date": date_file, "lineorder": tmp_path / "missing.tbl"}

        class ExecConn:
            def __init__(self):
                self.calls = []
                self.commits = 0

            def execute(self, sql, params=None):
                self.calls.append((str(sql), params))
                return SimpleNamespace(fetchall=list)

            def commit(self):
                self.commits += 1

        conn = ExecConn()
        b._load_data(conn)
        assert any("CREATE TABLE" in sql for sql, _ in conn.calls)
        assert any("INSERT INTO date" in sql for sql, _ in conn.calls)
        assert conn.commits >= 2


class TestSSBGenerator:
    def test_generate_small_dataset(self, tmp_path: Path):
        from benchbox.core.ssb.generator import SSBDataGenerator

        gen = SSBDataGenerator(scale_factor=0.001, output_dir=tmp_path)
        result = gen.generate_data(["date", "customer"])
        assert "date" in result
        assert "customer" in result
        for path in result.values():
            assert Path(path).exists()


class TestNYCTaxiBenchmark:
    def test_init_validation(self, tmp_path: Path):
        from benchbox.core.nyctaxi.benchmark import NYCTaxiBenchmark

        with pytest.raises(ValueError, match="[Ss]cale.factor must be positive"):
            NYCTaxiBenchmark(scale_factor=-1, output_dir=tmp_path)
        with pytest.raises(ValueError, match="year must be"):
            NYCTaxiBenchmark(scale_factor=0.01, output_dir=tmp_path, year=1999)

    def test_init_and_info(self, tmp_path: Path):
        from benchbox.core.nyctaxi.benchmark import NYCTaxiBenchmark

        b = NYCTaxiBenchmark(scale_factor=0.01, output_dir=tmp_path, months=[1, 2])
        info = b.get_benchmark_info()
        assert info["name"] == "NYC Taxi OLAP"
        assert info["year"] == 2019
        assert info["months"] == [1, 2]

    def test_get_queries(self, tmp_path: Path):
        from benchbox.core.nyctaxi.benchmark import NYCTaxiBenchmark

        b = NYCTaxiBenchmark(scale_factor=0.01, output_dir=tmp_path)
        queries = b.get_queries()
        assert len(queries) > 0
        qid = next(iter(queries))
        q = b.get_query(qid)
        assert isinstance(q, str)

    def test_get_schema_and_create_tables(self, tmp_path: Path):
        from benchbox.core.nyctaxi.benchmark import NYCTaxiBenchmark

        b = NYCTaxiBenchmark(scale_factor=0.01, output_dir=tmp_path)
        schema = b.get_schema()
        assert "trips" in schema
        sql = b.get_create_tables_sql()
        assert "CREATE TABLE" in sql.upper()

    def test_get_queries_by_category(self, tmp_path: Path):
        from benchbox.core.nyctaxi.benchmark import NYCTaxiBenchmark

        b = NYCTaxiBenchmark(scale_factor=0.01, output_dir=tmp_path)
        temporal = b.get_queries_by_category("temporal")
        assert isinstance(temporal, list)

    def test_get_query_info(self, tmp_path: Path):
        from benchbox.core.nyctaxi.benchmark import NYCTaxiBenchmark

        b = NYCTaxiBenchmark(scale_factor=0.01, output_dir=tmp_path)
        qid = next(iter(b.get_queries()))
        info = b.get_query_info(qid)
        assert isinstance(info, dict)

    def test_download_stats(self, tmp_path: Path):
        from benchbox.core.nyctaxi.benchmark import NYCTaxiBenchmark

        b = NYCTaxiBenchmark(scale_factor=0.01, output_dir=tmp_path)
        stats = b.get_download_stats()
        assert stats["scale_factor"] == 0.01

    def test_private_load_data_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import benchbox.core.nyctaxi.benchmark as nyc_mod
        from benchbox.core.nyctaxi.benchmark import NYCTaxiBenchmark

        monkeypatch.setattr(
            nyc_mod,
            "NYC_TAXI_SCHEMA",
            {
                "taxi_zones": {"columns": {"location_id": "INTEGER", "borough": "VARCHAR"}},
                "trips": {"columns": {"trip_id": "INTEGER", "vendor_id": "INTEGER"}},
            },
        )

        b = NYCTaxiBenchmark(scale_factor=0.01, output_dir=tmp_path)
        b.get_create_tables_sql = lambda *a, **k: "CREATE TABLE taxi_zones(location_id INT, borough TEXT);"

        zones = tmp_path / "taxi_zones.csv"
        zones.write_text("location_id,borough\n1,Manhattan\nbad\n", encoding="utf-8")
        trips = tmp_path / "trips.csv"
        trips.write_text("trip_id,vendor_id\n100,1\n", encoding="utf-8")
        b.tables = {"taxi_zones": zones, "trips": trips}

        class ExecConn:
            def __init__(self):
                self.calls = []
                self.commits = 0

            def execute(self, sql, params=None):
                self.calls.append((str(sql), params))
                return SimpleNamespace(fetchall=list)

            def commit(self):
                self.commits += 1

        conn = ExecConn()
        b._load_data(conn)
        assert any("CREATE TABLE" in sql for sql, _ in conn.calls)
        assert any("INSERT INTO taxi_zones" in sql for sql, _ in conn.calls)
        assert any("INSERT INTO trips" in sql for sql, _ in conn.calls)
        assert conn.commits >= 2


class TestNYCTaxiDownloader:
    def test_init_defaults(self, tmp_path: Path):
        from benchbox.core.nyctaxi.downloader import NYCTaxiDataDownloader

        d = NYCTaxiDataDownloader(scale_factor=0.5, output_dir=tmp_path)
        assert d.sample_rate == 0.05
        assert d.year == 2019
        assert d.months == list(range(1, 13))

    def test_generate_taxi_zones(self, tmp_path: Path):
        from benchbox.core.nyctaxi.downloader import NYCTaxiDataDownloader

        d = NYCTaxiDataDownloader(scale_factor=0.01, output_dir=tmp_path)
        zones_file = d._generate_taxi_zones()
        assert zones_file.exists()
        with open(zones_file, encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            assert "location_id" in header
            rows = list(reader)
            assert len(rows) == 265

    def test_generate_taxi_zones_cache(self, tmp_path: Path):
        from benchbox.core.nyctaxi.downloader import NYCTaxiDataDownloader

        d = NYCTaxiDataDownloader(scale_factor=0.01, output_dir=tmp_path)
        first = d._generate_taxi_zones()
        second = d._generate_taxi_zones()
        assert first == second

    def test_generate_synthetic_month(self, tmp_path: Path):
        from benchbox.core.nyctaxi.downloader import NYCTaxiDataDownloader

        d = NYCTaxiDataDownloader(scale_factor=0.01, output_dir=tmp_path, months=[1])
        out = tmp_path / "synthetic.csv"
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            count = d._generate_synthetic_month(writer, start_trip_id=0)
        assert count > 0

    def test_get_download_stats(self, tmp_path: Path):
        from benchbox.core.nyctaxi.downloader import NYCTaxiDataDownloader

        d = NYCTaxiDataDownloader(scale_factor=1.0, output_dir=tmp_path, seed=42)
        stats = d.get_download_stats()
        assert stats["sample_rate"] == 0.1
        assert stats["seed"] == 42

    def test_download_uses_synthetic_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from benchbox.core.nyctaxi.downloader import NYCTaxiDataDownloader

        d = NYCTaxiDataDownloader(scale_factor=0.01, output_dir=tmp_path, months=[1])

        def fake_process(url, writer, start_id):
            return d._generate_synthetic_month(writer, start_id)

        monkeypatch.setattr(d, "_process_parquet_file", fake_process)
        result = d.download()
        assert "taxi_zones" in result
        assert "trips" in result
        assert result["trips"].exists()


class TestWritePrimitivesBenchmark:
    def test_init_and_metadata(self):
        from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark

        b = WritePrimitivesBenchmark(scale_factor=0.01)
        assert b._name == "Write Primitives Benchmark"
        assert b.get_data_source_benchmark() == "tpch"
        assert b.supports_dataframe_mode() is True

    def test_get_operations(self):
        from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark

        b = WritePrimitivesBenchmark(scale_factor=0.01)
        ops = b.get_all_operations()
        assert len(ops) > 0
        first_op_id = next(iter(ops))
        single = b.get_operation(first_op_id)
        assert single is not None
        assert hasattr(single, "id")

    def test_output_dir_property(self, tmp_path: Path):
        from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark

        b = WritePrimitivesBenchmark(scale_factor=0.01)
        b.output_dir = tmp_path / "new_out"
        assert str(b.output_dir).endswith("new_out")


class TestTransactionPrimitivesBenchmark:
    def test_init_and_metadata(self):
        from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark

        b = TransactionPrimitivesBenchmark(scale_factor=0.01)
        assert b._name == "Transaction Primitives Benchmark"
        assert b.get_data_source_benchmark() == "tpch"

        assert b.supports_dataframe_mode("delta-lake") is True or b.supports_dataframe_mode("duckdb") is False

    def test_get_operations(self):
        from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark

        b = TransactionPrimitivesBenchmark(scale_factor=0.01)
        ops = b.get_all_operations()
        assert len(ops) > 0
        first_op_id = next(iter(ops))
        single = b.get_operation(first_op_id)
        assert single is not None

    def test_output_dir_property(self, tmp_path: Path):
        from benchbox.core.transaction_primitives.benchmark import TransactionPrimitivesBenchmark

        b = TransactionPrimitivesBenchmark(scale_factor=0.01)
        b.output_dir = tmp_path / "txn_out"
        assert str(b.output_dir).endswith("txn_out")


class TestMetadataPrimitivesBenchmark:
    def test_init_and_metadata(self, tmp_path: Path):
        from benchbox.core.metadata_primitives.benchmark import MetadataPrimitivesBenchmark

        b = MetadataPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        assert b._name == "Metadata Primitives Benchmark"
        assert b.supports_dataframe_mode() is True

    def test_generate_data_returns_empty(self, tmp_path: Path):
        from benchbox.core.metadata_primitives.benchmark import MetadataPrimitivesBenchmark

        b = MetadataPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        result = b.generate_data()

        assert isinstance(result, (dict, list))

    def test_get_queries_and_schema(self, tmp_path: Path):
        from benchbox.core.metadata_primitives.benchmark import MetadataPrimitivesBenchmark

        b = MetadataPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        queries = b.get_queries()
        assert len(queries) > 0
        schema = b.get_schema()
        assert isinstance(schema, dict)
        sql = b.get_create_tables_sql()
        assert "CREATE TABLE" in sql.upper()

    def test_get_queries_by_category(self, tmp_path: Path):
        from benchbox.core.metadata_primitives.benchmark import MetadataPrimitivesBenchmark

        b = MetadataPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        cats = b.get_queries_by_category("schema")
        assert isinstance(cats, (dict, list))

    def test_execute_query(self, tmp_path: Path):
        from benchbox.core.metadata_primitives.benchmark import MetadataPrimitivesBenchmark

        b = MetadataPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        conn = FakeConn(rows=[("tab1", "col1", "INTEGER")])
        qid = next(iter(b.get_queries()))
        result = b.execute_query(qid, conn)
        assert result.success is True
        assert result.row_count >= 0

    def test_run_benchmark(self, tmp_path: Path):
        from benchbox.core.metadata_primitives.benchmark import MetadataPrimitivesBenchmark

        b = MetadataPrimitivesBenchmark(scale_factor=0.01, output_dir=tmp_path)
        conn = FakeConn(rows=[("data",)])
        query_ids = list(b.get_queries().keys())[:3]
        result = b.run_benchmark(conn, query_ids=query_ids)
        assert result.total_queries == len(query_ids)

    def test_acl_benchmark_result_summary_and_execute_helpers(self):
        from benchbox.core.metadata_primitives.benchmark import (
            AclBenchmarkResult,
            AclMutationResult,
            MetadataPrimitivesBenchmark,
        )

        results = [
            AclMutationResult(
                operation="GRANT",
                target_type="table",
                target_name="t1",
                grantee="r1",
                execution_time_ms=10.0,
                success=True,
            ),
            AclMutationResult(
                operation="REVOKE",
                target_type="table",
                target_name="t1",
                grantee="r1",
                execution_time_ms=20.0,
                success=False,
            ),
        ]
        summary = AclBenchmarkResult(mutation_results=results).summary
        assert summary["total_operations"] == 2
        assert summary["successful_operations"] == 1

        b = MetadataPrimitivesBenchmark(scale_factor=0.01)
        cursor_conn = SimpleNamespace(
            cursor=lambda: SimpleNamespace(execute=lambda _sql: None, fetchall=lambda: [("ok",)])
        )
        assert b._execute(cursor_conn, "SELECT 1") is not None
        with pytest.raises(TypeError):
            b._execute(object(), "SELECT 1")

    def test_acl_paths_and_summary(self, monkeypatch: pytest.MonkeyPatch):
        import benchbox.core.metadata_primitives.benchmark as mp_mod
        from benchbox.core.metadata_primitives.benchmark import MetadataPrimitivesBenchmark
        from benchbox.core.metadata_primitives.complexity import MetadataComplexityConfig

        b = MetadataPrimitivesBenchmark(scale_factor=0.01)
        conn = SimpleNamespace(execute=lambda *_a, **_k: SimpleNamespace(fetchall=list))
        cfg = MetadataComplexityConfig(acl_role_count=0)

        monkeypatch.setattr(mp_mod, "supports_acl", lambda _dialect: False)
        unsupported = b.run_acl_benchmark(conn, "duckdb", cfg)
        assert "error" in unsupported.summary

        monkeypatch.setattr(mp_mod, "supports_acl", lambda _dialect: True)
        empty_acl = b.run_acl_benchmark(conn, "duckdb", cfg)
        assert "error" in empty_acl.summary

        monkeypatch.setattr(b, "_measure_create_role", lambda *_a, **_k: SimpleNamespace(success=True))
        monkeypatch.setattr(
            b,
            "_measure_drop_role",
            lambda *_a, **_k: SimpleNamespace(operation="DROP_ROLE", success=True, execution_time_ms=1.0),
        )
        monkeypatch.setattr(
            b,
            "_measure_grant",
            lambda *_a, **_k: SimpleNamespace(operation="GRANT", success=True, execution_time_ms=1.0),
        )
        monkeypatch.setattr(
            b,
            "_measure_revoke",
            lambda *_a, **_k: SimpleNamespace(operation="REVOKE", success=True, execution_time_ms=1.0),
        )
        monkeypatch.setattr(
            b,
            "run_benchmark",
            lambda *_a, **_k: SimpleNamespace(total_queries=1, successful_queries=1, failed_queries=0),
        )
        monkeypatch.setattr(b, "_execute", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("cannot create table")))

        early = b._run_default_acl_mutations(conn, "duckdb")
        assert len(early) >= 3

        summary = b._build_acl_summary(
            [
                SimpleNamespace(operation="GRANT", success=True, execution_time_ms=3.0),
                SimpleNamespace(operation="GRANT", success=False, execution_time_ms=1.0),
                SimpleNamespace(operation="REVOKE", success=True, execution_time_ms=2.0),
            ]
        )
        assert summary["total_operations"] == 3
        assert summary["by_operation"]["GRANT"]["count"] == 2


class TestMetadataGenerator:
    def test_setup_and_teardown(self, tmp_path: Path):
        from benchbox.core.metadata_primitives.complexity import MetadataComplexityConfig
        from benchbox.core.metadata_primitives.generator import MetadataGenerator

        gen = MetadataGenerator()
        config = MetadataComplexityConfig(
            width_factor=5,
            view_depth=1,
            catalog_size=2,
        )

        exec_log = []

        class TrackingConn:
            def execute(self, sql):
                exec_log.append(sql)
                return SimpleNamespace(fetchall=list, fetchone=lambda: None)

            def commit(self):
                pass

        conn = TrackingConn()
        generated = gen.setup(conn, "duckdb", config)
        assert generated is not None
        assert len(exec_log) > 0

        gen.teardown(conn, "duckdb", generated)


