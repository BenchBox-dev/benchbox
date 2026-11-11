from typing import Any

import pytest

from benchbox.core.tpcds.benchmark import TPCDSBenchmark
from benchbox.core.tpch.benchmark import TPCHBenchmark
from benchbox.platforms.base import PlatformAdapter

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


class _MinimalBenchmark:
    def __init__(self) -> None:
        self._name = "TPC-DS Benchmark"
        self.captured = {}

    def get_queries(self, *, dialect: str | None = None, base_dialect: str | None = None) -> dict[str, str]:
        self.captured["dialect"] = dialect
        self.captured["base_dialect"] = base_dialect
        return {"Q1": "SELECT 1"}


class _DummyAdapter(PlatformAdapter):
    @staticmethod
    def add_cli_arguments(parser) -> None:
        pass

    @classmethod
    def from_config(cls, config):
        return cls(**config)

    @property
    def platform_name(self) -> str:
        return "DummyDB"

    def get_target_dialect(self) -> str:
        return "duckdb"

    def create_connection(self, **connection_config):
        return None

    def create_schema(self, benchmark, connection: Any) -> float:
        return 0.0

    def load_data(self, benchmark, connection: Any, data_dir) -> tuple[dict[str, int], float]:
        return {}, 0.0

    def configure_for_benchmark(self, connection: Any, benchmark_type: str) -> None:
        return None

    def execute_query(
        self,
        connection: Any,
        query: str,
        query_id: str,
        benchmark_type: str | None = None,
        scale_factor: float | None = None,
        validate_row_count: bool = True,
    ) -> dict[str, Any]:
        return {
            "query_id": query_id,
            "status": "SUCCESS",
            "execution_time": 0.0,
            "rows_returned": 1,
        }

    def apply_platform_optimizations(self, platform_config, connection: Any) -> None:
        return None

    def apply_constraint_configuration(self, primary_key_config, foreign_key_config, connection: Any) -> None:
        return None


def test_adapter_passes_dialects_to_benchmark():
    adapter = _DummyAdapter()
    bench = _MinimalBenchmark()
    results = adapter._execute_all_queries(bench, None, run_config={})

    assert bench.captured["base_dialect"] == "netezza"
    assert bench.captured["dialect"] == "duckdb"
    assert results and results[0]["status"] == "SUCCESS"


def test_tpch_translate_always_uses_sqlglot():
    b = TPCHBenchmark(scale_factor=0.01, output_dir=None)
    sql = b.translate_query_text("SELECT 1", "ansi", "duckdb")
    assert "select" in sql.lower()


def test_tpcds_translate_always_uses_sqlglot():
    b = TPCDSBenchmark(scale_factor=1.0, output_dir=None)
    sql = b.translate_query_text("SELECT 1", "ansi", "duckdb")
    assert "select" in sql.lower()


