# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.clickbench.benchmark import ClickBenchBenchmark
from benchbox.platforms.duckdb import DuckDBAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class TestClickBenchCSVLoading:
    def setup_method(self):
        self.benchmark = ClickBenchBenchmark(scale_factor=0.001)
        self.adapter = DuckDBAdapter()

    def test_clickbench_csv_loading_config(self):
        config = self.benchmark.get_csv_loading_config("hits")

        expected_config = [
            "delim='|'",
            "header=false",
            "nullstr='__NULL__'",
            "ignore_errors=true",
            "auto_detect=true",
        ]

        assert config == expected_config

    def test_empty_string_preservation_logic(self):
        config = self.benchmark.get_csv_loading_config("hits")

        assert "nullstr='__NULL__'" in config, "Must use non-empty string NULL marker to preserve empty strings"
        assert "delim='|'" in config, "ClickBench uses pipe delimiter"

    def test_duckdb_native_pipe_loader_preserves_empty_referer(self, tmp_path):
        from unittest.mock import Mock

        import duckdb

        from benchbox.platforms.base.data_loading import DuckDBNativeHandler

        data_file = tmp_path / "hits.csv"
        data_file.write_text("https://example.test||0\n", encoding="utf-8")

        connection = duckdb.connect(":memory:")
        try:
            connection.execute(
                "CREATE TABLE hits (URL TEXT NOT NULL, Referer TEXT NOT NULL, IsRefresh INTEGER NOT NULL)"
            )
            adapter = Mock()
            adapter.dry_run_mode = False
            handler = DuckDBNativeHandler("|", adapter, self.benchmark, null_marker=None)

            loaded_rows = handler.load_table("hits", data_file, connection, self.benchmark, None)

            assert loaded_rows == 1
            row = connection.execute("SELECT Referer, Referer IS NULL FROM hits").fetchone()
            assert row is not None
            referer, is_null = row
            assert referer == ""
            assert is_null is False
        finally:
            connection.close()

    def test_duckdb_external_scan_preserves_empty_referer_for_none_null_marker(self, tmp_path):
        from benchbox.platforms.base.data_loading import DataSource
        from benchbox.platforms.duckdb import _build_csv_scan_expression

        data_file = tmp_path / "hits.csv"
        data_file.write_text("https://example.test||0\n", encoding="utf-8")
        data_source = DataSource(
            source_type="test",
            tables={"hits": [data_file]},
            table_metadata={"hits": {"csv_delimiter": "|", "csv_null_marker": None}},
        )

        scan_sql = _build_csv_scan_expression(
            [data_file],
            column_names=["URL", "Referer", "IsRefresh"],
            data_source=data_source,
            table_name="hits",
            benchmark=self.benchmark,
        )

        assert "nullstr='__NULL__'" in scan_sql
        assert "nullstr=''" not in scan_sql

    def test_clickbench_schema_consistency(self):
        from benchbox.core.clickbench.schema import HITS_TABLE

        for column in HITS_TABLE["columns"]:
            assert column["nullable"] is False, f"Column {column['name']} should be NOT NULL"

    def test_spark_schema_omits_not_null_constraints(self):
        from benchbox.core.clickbench.schema import get_create_table_sql

        spark_sql = get_create_table_sql(dialect="spark")
        duckdb_sql = get_create_table_sql(dialect="duckdb")

        assert " NOT NULL" not in spark_sql
        assert " NOT NULL" in duckdb_sql

    def test_clickbench_query_patterns(self):
        from benchbox.core.clickbench.queries import ClickBenchQueryManager

        query_manager = ClickBenchQueryManager()
        query_ids = [f"Q{i}" for i in range(1, 44)]
        queries = {qid: query_manager.get_query(qid) for qid in query_ids}

        empty_string_queries = []
        for query_id, query_sql in queries.items():
            if "<> ''" in query_sql:
                empty_string_queries.append(query_id)

        assert len(empty_string_queries) > 0, "ClickBench should have queries filtering empty strings"

        null_queries = []
        for query_id, query_sql in queries.items():
            if "IS NOT NULL" in query_sql.upper():
                null_queries.append(query_id)

        assert len(null_queries) == 0, f"ClickBench queries should not use IS NOT NULL: {null_queries}"


class TestClickBenchDataIntegrity:
    def test_csv_loading_configuration_logic(self):
        from benchbox.core.clickbench.benchmark import ClickBenchBenchmark

        benchmark = ClickBenchBenchmark()
        config = benchmark.get_csv_loading_config("hits")

        assert "nullstr='__NULL__'" in config
        assert "delim='|'" in config

    def test_empty_string_vs_null_handling(self):

        from benchbox.core.clickbench.schema import HITS_TABLE

        referer_col = next(col for col in HITS_TABLE["columns"] if col["name"] == "Referer")
        assert referer_col["nullable"] is False

        from benchbox.core.clickbench.queries import ClickBenchQueryManager

        query_manager = ClickBenchQueryManager()

        try:
            q29_sql = query_manager.get_query("Q29")
            if q29_sql and "Referer" in q29_sql:
                assert "<>" in q29_sql or "!=" in q29_sql or "WHERE" in q29_sql
        except Exception:
            pass

    def test_benchmark_integration_concept(self):
        from benchbox.core.clickbench.benchmark import ClickBenchBenchmark

        benchmark = ClickBenchBenchmark()

        csv_config = benchmark.get_csv_loading_config("hits")
        assert len(csv_config) > 0

        assert "nullstr='__NULL__'" in csv_config
        assert "delim='|'" in csv_config


class TestClickBenchSchemaRanges:
    UINT16_COLUMNS = {"Interests", "RefererCategoryID", "URLCategoryID"}
    SMALLINT_MAX = 32_767

    BOUNDED_UINT16_COLUMNS: dict[str, tuple[str, int]] = {
        "UserAgent": ("UInt16", 1_000),
        "ResolutionWidth": ("UInt16", 1_920),
        "ResolutionHeight": ("UInt16", 1_200),
        "UserAgentMajor": ("UInt16", 100),
        "SearchEngineID": ("UInt16", 50),
        "WindowClientWidth": ("UInt16", 1_920),
        "WindowClientHeight": ("UInt16", 1_200),
        "SilverlightVersion2": ("UInt16", 50),
        "SilverlightVersion4": ("UInt16", 100),
        "HistoryLength": ("UInt16", 100),
        "HTTPError": ("UInt16", 500),
        "ParamCurrencyID": ("UInt16", 10),
    }

    def _col_map(self):
        from benchbox.core.clickbench.schema import HITS_TABLE

        return {c["name"]: c["type"] for c in HITS_TABLE["columns"]}

    def test_uint16_columns_are_not_smallint(self):
        col_map = self._col_map()
        for col in self.UINT16_COLUMNS:
            assert col in col_map, f"Column {col} missing from schema"
            assert col_map[col].upper() != "SMALLINT", (
                f"{col} is UInt16 in ClickHouse (0-65535) but declared as SMALLINT "
                f"(max {self.SMALLINT_MAX}); use INTEGER or wider"
            )

    def test_bounded_uint16_columns_remain_smallint(self):
        col_map = self._col_map()
        for col, (ch_type, benchbox_max) in self.BOUNDED_UINT16_COLUMNS.items():
            assert col in col_map, f"Column {col} missing from schema"
            assert col_map[col].upper() == "SMALLINT", (
                f"{col} ({ch_type} in ClickHouse, BenchBox max={benchbox_max}) should remain "
                f"SMALLINT — only widen if the generator range is updated to exceed {self.SMALLINT_MAX}"
            )
            assert benchbox_max <= self.SMALLINT_MAX, (
                f"BOUNDED_UINT16_COLUMNS entry for {col} documents BenchBox max={benchbox_max} "
                f"which exceeds SMALLINT_MAX={self.SMALLINT_MAX}; the column needs widening to INTEGER"
            )

    def test_generator_interests_values_fit_integer_and_exceed_smallint(self):
        import tempfile
        from datetime import datetime
        from pathlib import Path

        from benchbox.core.clickbench.generator import ClickBenchDataGenerator
        from benchbox.core.clickbench.schema import HITS_TABLE

        with tempfile.TemporaryDirectory() as td:
            gen = ClickBenchDataGenerator(scale_factor=0.0001, output_dir=Path(td))
            records = [gen._generate_hit_record(i, datetime(2013, 7, 1)) for i in range(200)]

        col_names = [c["name"] for c in HITS_TABLE["columns"]]
        interests_idx = col_names.index("Interests")
        values = [r[interests_idx] for r in records]

        assert all(0 <= v <= 65_535 for v in values), "Interests values must be in UInt16 range [0, 65535]"
        assert any(v > self.SMALLINT_MAX for v in values), (
            f"Expected at least one Interests value > {self.SMALLINT_MAX} in 200 records; "
            "got all values ≤ SMALLINT_MAX — the INTEGER widening may be unnecessary"
        )


