from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from rich.console import Console

from benchbox.base import BaseBenchmark
from benchbox.core.results.builder import BenchmarkInfoInput, ResultBuilder, RunConfigInput
from benchbox.core.results.exporter import ResultExporter
from benchbox.core.results.platform_info import PlatformInfoInput
from benchbox.core.results.query_normalizer import normalize_query_result
from benchbox.core.results.schema import build_result_payload
from benchbox.core.schemas import BenchmarkConfig
from benchbox.platforms.base.adapter import PlatformAdapter
from benchbox.platforms.dataframe.benchmark_mixin import (
    BenchmarkExecutionMixin,
    DataFramePhases,
    DataFrameRunOptions,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def _build_result(mode: str):
    builder = ResultBuilder(
        benchmark=BenchmarkInfoInput(
            name="TPC-H",
            scale_factor=1.0,
            test_type="power",
            benchmark_id="tpch",
            display_name="TPC-H",
        ),
        platform=PlatformInfoInput(
            name="DataFusion",
            platform_version="1.0.0",
            client_library_version="1.0.0",
            execution_mode=mode,
        ),
    )
    builder.set_run_config(
        RunConfigInput(
            compression_type="zstd",
            compression_level=3,
            seed=1,
            phases=["power"],
            query_subset=["1", "2"],
        )
    )

    warmup = normalize_query_result(
        {
            "query_id": "Q1",
            "execution_time_seconds": 1.0,
            "rows_returned": 10,
            "status": "SUCCESS",
            "iteration": 0,
            "stream_id": 0,
            "run_type": "warmup",
        }
    )
    measurement = normalize_query_result(
        {
            "query_id": "Q1",
            "execution_time_seconds": 0.9,
            "rows_returned": 10,
            "status": "SUCCESS",
            "iteration": 1,
            "stream_id": 1,
            "run_type": "measurement",
        }
    )
    builder.add_query_result(warmup)
    builder.add_query_result(measurement)
    return builder.build()


class ExportParitySqlBenchmark(BaseBenchmark):
    def __init__(self, output_dir: Path):
        self._name = "TPC-H"
        super().__init__(scale_factor=0.01, output_dir=output_dir)
        self.tables = {}

        self.compliance_class = "unofficial_subscale"

    def generate_data(self) -> list[Path]:
        return []

    def get_queries(self) -> dict[str, str]:
        return {"Q1": "SELECT 1"}

    def get_query(self, query_id: int | str, *, params: dict[str, Any] | None = None) -> str:
        _ = params
        normalized = str(query_id).upper()
        if normalized == "1":
            normalized = "Q1"
        return self.get_queries()[normalized]

    def get_platform_skip_queries(self, platform_name: str) -> list[str]:
        _ = platform_name
        return []


class ExportParitySqlAdapter(PlatformAdapter):
    @staticmethod
    def add_cli_arguments(parser) -> None:
        _ = parser

    @classmethod
    def from_config(cls, config: dict[str, Any]):
        return cls(**config)

    @property
    def platform_name(self) -> str:
        return "DuckDB"

    def get_target_dialect(self) -> str | None:
        return None

    def get_platform_info(self, connection=None) -> dict[str, Any]:
        _ = connection
        return {
            "platform_type": "duckdb",
            "platform_name": self.platform_name,
            "platform_version": "test-sql",
            "connection_mode": "test",
            "host": None,
            "port": None,
            "configuration": {},
            "client_library_version": "test-sql-client",
            "embedded_library_version": None,
        }

    def create_connection(self, **connection_config):
        _ = connection_config

        class _Cursor:
            def execute(self, query: str) -> None:
                _ = query

            def fetchall(self) -> list[tuple[str]]:
                return [("lineitem",)]

            def close(self) -> None:
                return None

        return SimpleNamespace(close=lambda: None, cursor=lambda: _Cursor())

    def create_schema(self, benchmark, connection) -> float:
        _ = (benchmark, connection)
        return 0.001

    def load_data(self, benchmark, connection, data_dir) -> tuple[dict[str, int], float, dict[str, Any] | None]:
        _ = (benchmark, connection, data_dir)
        return {"lineitem": 1}, 0.001, None

    def configure_for_benchmark(self, connection, benchmark_type: str) -> None:
        _ = (connection, benchmark_type)

    def execute_query(
        self,
        connection,
        query: str,
        query_id: str,
        benchmark_type: str | None = None,
        scale_factor: float | None = None,
        validate_row_count: bool = True,
        stream_id: int | None = None,
    ) -> dict[str, Any]:
        _ = (connection, query, benchmark_type, scale_factor, validate_row_count, stream_id)
        return {
            "query_id": query_id,
            "status": "SUCCESS",
            "execution_time_seconds": 0.010,
            "rows_returned": 4,
        }

    def apply_table_tunings(self, table_tuning, connection) -> None:
        _ = (table_tuning, connection)

    def generate_tuning_clause(self, table_tuning) -> str:
        _ = table_tuning
        return ""

    def apply_unified_tuning(self, unified_config, connection) -> None:
        _ = (unified_config, connection)

    def apply_platform_optimizations(self, platform_config, connection) -> None:
        _ = (platform_config, connection)

    def apply_constraint_configuration(self, primary_key_config, foreign_key_config, connection) -> None:
        _ = (primary_key_config, foreign_key_config, connection)


class ExportParityDataFrameAdapter(BenchmarkExecutionMixin):
    platform_name = "Polars"
    family = "expression"

    def create_context(self) -> SimpleNamespace:
        return SimpleNamespace()

    def load_table(
        self,
        ctx: Any,
        table_name: str,
        file_paths: list[Any],
        column_names: list[str] | None = None,
        delimiter: str | None = None,
        benchmark: Any | None = None,
        **kwargs: Any,
    ) -> int:
        _ = (ctx, table_name, file_paths, column_names, delimiter, benchmark)
        return 1

    def execute_query(
        self,
        ctx: Any,
        query: Any,
        query_id: str | None = None,
    ) -> dict[str, Any]:
        _ = (ctx, query_id)
        return {
            "query_id": query.query_id,
            "status": "SUCCESS",
            "execution_time_seconds": 0.012,
            "rows_returned": 4,
        }

    def get_platform_info(self) -> dict[str, Any]:
        return {
            "platform": self.platform_name,
            "family": self.family,
            "version": "test-df",
            "client_library_version": "test-df-client",
        }


class ExportParityBenchmark:
    name = "tpch"
    display_name = "TPC-H"
    scale_factor = 0.01

    def skip_dataframe_data_loading(self) -> bool:
        return True


def _export_payload(tmp_path, result, filename: str) -> dict[str, Any]:
    result.output_filename = filename
    exporter = ResultExporter(
        output_dir=tmp_path,
        anonymize=False,
        console=Console(file=io.StringIO()),
    )
    exported = exporter.export_result(result, formats=["json"])

    assert "json" in exported
    return json.loads(exported["json"].read_text(encoding="utf-8"))


def _build_sql_export_parity_result(tmp_path):
    data_dir = tmp_path / "sql-data"
    data_dir.mkdir()
    (data_dir / "lineitem.tbl").write_text("1|", encoding="utf-8")

    result = ExportParitySqlAdapter().run_benchmark(
        ExportParitySqlBenchmark(data_dir),
        benchmark_name="tpch",
        query_subset=["Q1"],
        compression_type="zstd",
        compression_level=3,
        seed=7,
        phases=["load", "power"],
    )
    result.execution_id = "sql-export-parity"
    return result


def _build_dataframe_export_parity_result():
    adapter = ExportParityDataFrameAdapter()
    benchmark = ExportParityBenchmark()
    config = BenchmarkConfig(
        name="tpch",
        display_name="TPC-H",
        scale_factor=0.01,
        queries=["Q1"],
        options={
            "compression_type": "zstd",
            "compression_level": 3,
            "seed": 7,
            "phases": ["load", "power"],
            "power_warmup_iterations": 0,
            "power_iterations": 1,
        },
    )

    result = adapter.run_benchmark(
        benchmark,
        benchmark_config=config,
        phases=DataFramePhases(load=True, execute=True),
        options=DataFrameRunOptions(ignore_memory_warnings=True, prefer_parquet=False),
    )
    result.execution_id = "df-export-parity"
    return result


def test_output_keys_match():
    sql_payload = build_result_payload(_build_result("sql"))
    df_payload = build_result_payload(_build_result("dataframe"))
    assert sql_payload.keys() == df_payload.keys()


def test_query_keys_match():
    sql_payload = build_result_payload(_build_result("sql"))
    df_payload = build_result_payload(_build_result("dataframe"))
    assert sql_payload["queries"][0].keys() == df_payload["queries"][0].keys()


def test_warmup_results_present():
    payload = build_result_payload(_build_result("sql"))
    warmup = [q for q in payload["queries"] if q.get("run_type") == "warmup"]
    assert len(warmup) > 0


def test_config_block_present():
    payload = build_result_payload(_build_result("sql"))
    assert "config" in payload
    assert payload["config"].get("compression") is not None


def test_platform_versions_present():
    payload = build_result_payload(_build_result("sql"))
    assert payload["platform"].get("version") is not None
    assert payload["platform"].get("client_version") is not None


def test_mode_is_execution_type():
    payload = build_result_payload(_build_result("dataframe"))
    assert payload.get("execution", {}).get("mode") in ("sql", "dataframe")


def test_sql_translation_metadata_is_allowed_conditional_execution_metadata():
    sql_result = _build_result("sql")
    sql_result.execution_metadata = {
        **(sql_result.execution_metadata or {}),
        "translation": {
            "status": "success",
            "strict_mode": True,
            "attempt_count": 1,
            "fallback_count": 0,
            "failed_count": 0,
        },
    }

    sql_payload = build_result_payload(sql_result)
    df_payload = build_result_payload(_build_result("dataframe"))

    assert set(sql_payload["execution"]) - set(df_payload["execution"]) == {"translation"}
    assert set(df_payload["execution"]) - set(sql_payload["execution"]) == set()
    assert sql_payload["execution"]["translation"]["status"] == "success"


def test_phases_block_complete():
    payload = build_result_payload(_build_result("sql"))
    for phase in (
        "data_generation",
        "schema_creation",
        "data_loading",
        "validation",
        "power_test",
        "throughput_test",
    ):
        assert phase in payload["phases"]


def test_exported_sql_and_dataframe_bundles_share_cross_mode_contract(tmp_path):
    sql_payload = _export_payload(tmp_path, _build_sql_export_parity_result(tmp_path), "sql-export-parity")
    df_payload = _export_payload(tmp_path, _build_dataframe_export_parity_result(), "df-export-parity")

    required_top_level = {
        "result_schema_version",
        "run",
        "benchmark",
        "platform",
        "config",
        "summary",
        "phases",
        "queries",
        "execution",
        "export",
    }
    assert required_top_level.issubset(sql_payload)
    assert required_top_level.issubset(df_payload)
    assert set(sql_payload) - set(df_payload) <= {"tables"}
    assert set(df_payload) - set(sql_payload) <= {"tables"}
    assert sql_payload["result_schema_version"] == df_payload["result_schema_version"]
    assert sql_payload["version"] == "2.3"
    assert df_payload["version"] == "2.3"
    assert sql_payload["benchmark"] == df_payload["benchmark"]

    assert sql_payload["config"]["compression"] == df_payload["config"]["compression"]
    assert sql_payload["config"]["seed"] == df_payload["config"]["seed"] == 7
    assert sql_payload["config"]["phases"] == df_payload["config"]["phases"] == ["load", "power"]
    assert sql_payload["config"]["query_subset"] == df_payload["config"]["query_subset"] == ["Q1"]

    expected_phases = {
        "data_generation",
        "schema_creation",
        "data_loading",
        "validation",
        "power_test",
        "throughput_test",
    }
    assert expected_phases.issubset(sql_payload["phases"])
    assert expected_phases.issubset(df_payload["phases"])
    assert sql_payload["phases"]["data_loading"]["status"] in {"COMPLETED", "SUCCESS"}
    assert df_payload["phases"]["data_loading"]["status"] == "SKIPPED"
    assert sql_payload["phases"]["power_test"]["status"] == "COMPLETED"
    assert df_payload["phases"]["power_test"]["status"] == "COMPLETED"

    assert [query["id"] for query in sql_payload["queries"]] == [query["id"] for query in df_payload["queries"]]
    assert sql_payload["queries"][0]["rows"] == df_payload["queries"][0]["rows"] == 4
    assert sql_payload["summary"]["validation"] == "passed"
    assert df_payload["summary"]["validation"] == "passed"
    assert df_payload["queries"][0]["row_count_validation"]["status"] == "PASSED"
    assert sql_payload.get("errors", []) == df_payload.get("errors", []) == []

    assert set(sql_payload["execution"]) == set(df_payload["execution"]) == {"mode"}
    assert sql_payload["execution"]["mode"] == "sql"
    assert df_payload["execution"]["mode"] == "dataframe"

    for payload in (sql_payload, df_payload):
        assert isinstance(payload["run"]["total_duration_ms"], (int, float))
        assert isinstance(payload["run"]["query_time_ms"], (int, float))
        assert isinstance(payload["queries"][0]["ms"], (int, float))
