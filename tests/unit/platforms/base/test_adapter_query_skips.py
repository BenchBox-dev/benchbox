from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from benchbox.core.read_primitives.benchmark import ReadPrimitivesBenchmark
from benchbox.platforms.base.adapter import PlatformAdapter
from benchbox.platforms.dataframe.benchmark_mixin import BenchmarkExecutionMixin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class MinimalAdapter(PlatformAdapter):
    def __init__(self, platform_name: str) -> None:
        self._platform_name = platform_name
        super().__init__()

    @property
    def platform_name(self) -> str:
        return self._platform_name

    def get_target_dialect(self) -> str | None:
        return self._platform_name.lower()

    @staticmethod
    def add_cli_arguments(parser: Any) -> None:
        pass

    @classmethod
    def from_config(cls, config: Any) -> MinimalAdapter:
        return cls("test")

    def create_connection(self, **kwargs: Any) -> Any:
        return None

    def create_schema(self, benchmark: Any, connection: Any) -> float:
        return 0.0

    def load_data(self, benchmark: Any, connection: Any, data_dir: str) -> tuple[dict, float, Any]:
        return {}, 0.0, None

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        pass

    def apply_platform_optimizations(self, platform_config: Any, connection: Any) -> None:
        pass

    def apply_constraint_configuration(
        self,
        primary_key_config: Any,
        foreign_key_config: Any,
        connection: Any,
    ) -> None:
        pass

    def execute_query(
        self,
        connection: Any,
        query: str,
        query_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return {"query_id": query_id, "status": "SUCCESS", "execution_time_seconds": 0.0, "rows_returned": 0}


class MinimalDFAdapter(BenchmarkExecutionMixin):
    def __init__(self, platform_name: str, family: str = "expression") -> None:
        self.platform_name = platform_name
        self.family = family

    def create_context(self) -> Any:
        return SimpleNamespace()

    def load_table(self, ctx: Any, table_name: str, file_paths: list, **kwargs: Any) -> int:
        return 0

    def execute_query(self, ctx: Any, query: Any, query_id: str | None = None) -> dict[str, Any]:
        return {"query_id": query_id, "status": "SUCCESS"}

    def get_platform_info(self) -> dict[str, Any]:
        return {"platform": self.platform_name}


def test_datafusion_sql_keeps_variants():

    adapter = MinimalAdapter("DataFusion")
    benchmark = ReadPrimitivesBenchmark()

    target_ids = ["list_filter", "list_transform", "list_reduce", "array_distinct"]

    queries = {qid: f"SELECT * FROM {qid}" for qid in target_ids}
    queries["q1"] = "SELECT 1"

    filtered = adapter._filter_queries(queries, benchmark, "read_primitives", {})

    for qid in target_ids:
        assert qid in filtered, f"{qid} should NOT be skipped in DataFusion SQL mode"
    assert "q1" in filtered


def test_operation_skip_logging_uses_skip_reason_when_error_is_none(monkeypatch):

    adapter = MinimalAdapter("pg-duckdb")
    printed: list[str] = []

    monkeypatch.setattr(
        "benchbox.platforms.base.execution.quiet_console",
        SimpleNamespace(print=lambda message: printed.append(str(message))),
    )

    adapter._log_query_result(
        {
            "query_id": "bulk_load_csv_small_uncompressed",
            "status": "SKIPPED",
            "error": None,
            "skip_reason": "server-side file COPY is unsupported",
        },
        1,
        1,
        "bulk_load_csv_small_uncompressed",
    )

    assert "SKIPPED" in printed[0]
    assert "server-side file COPY" in printed[0]


def test_lakesail_sql_skip_remains_active():

    adapter = MinimalAdapter("LakeSail")
    benchmark = ReadPrimitivesBenchmark()

    queries = {"empty_build_join": "SELECT *", "q1": "SELECT 1"}
    filtered = adapter._filter_queries(queries, benchmark, "read_primitives", {})

    assert "empty_build_join" not in filtered, "LakeSail must still skip empty_build_join in SQL mode"
    assert "q1" in filtered


def test_datafusion_df_skips_continue_to_work():

    adapter = MinimalDFAdapter("DataFusion")
    benchmark = ReadPrimitivesBenchmark()

    target_ids = ["list_filter", "list_transform", "list_reduce", "array_distinct"]

    skip_ids = adapter._collect_skip_query_ids(benchmark)

    upper_targets = {qid.upper() for qid in target_ids}

    for qid in upper_targets:
        assert qid in skip_ids, f"{qid} should be skipped in DataFusion DataFrame mode"


def test_polars_df_skips_maps():

    adapter = MinimalDFAdapter("Polars")
    benchmark = ReadPrimitivesBenchmark()

    target_ids = ["map_construction", "map_access", "map_keys_values"]
    skip_ids = adapter._collect_skip_query_ids(benchmark)

    for qid in target_ids:
        assert qid.upper() in skip_ids, f"{qid} should be skipped in Polars DataFrame mode"


def test_pyspark_df_skips_list_hofs():

    adapter = MinimalDFAdapter("PySpark")
    benchmark = ReadPrimitivesBenchmark()

    target_ids = ["list_filter", "list_transform"]
    skip_ids = adapter._collect_skip_query_ids(benchmark)

    for qid in target_ids:
        assert qid.upper() in skip_ids, f"{qid} should be skipped in PySpark DataFrame mode"

    assert "MAP_CONSTRUCTION" not in skip_ids, "PySpark should support map_construction"


def test_datafusion_df_does_not_skip_maps():

    adapter = MinimalDFAdapter("DataFusion")
    benchmark = ReadPrimitivesBenchmark()

    skip_ids = adapter._collect_skip_query_ids(benchmark)
    assert "MAP_CONSTRUCTION" not in skip_ids, "DataFusion should support map_construction"


def test_multi_statement_queries_run_as_lettered_statements():
    adapter = MinimalAdapter("DataFusion")
    queries = {"1": "SELECT 1;", "14": "SELECT 1; SELECT 2;", "15": "SELECT 3"}

    filtered = adapter._filter_queries(queries, ReadPrimitivesBenchmark(), "tpcds", {})

    assert filtered == {"1": "SELECT 1;", "14a": "SELECT 1", "14b": "SELECT 2", "15": "SELECT 3"}


def test_query_subset_of_a_split_query_selects_every_statement():
    adapter = MinimalAdapter("DataFusion")
    queries = {"1": "SELECT 1", "14": "SELECT 1; SELECT 2", "15": "SELECT 3"}

    filtered = adapter._filter_queries(queries, ReadPrimitivesBenchmark(), "tpcds", {"query_subset": ["Q14", "1"]})

    assert list(filtered) == ["14a", "14b", "1"]
    assert list(adapter._filter_queries(queries, ReadPrimitivesBenchmark(), "tpcds", {"query_subset": ["14b"]})) == [
        "14b"
    ]
