from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol


class CapabilityContractError(TypeError):
    pass


class SQLBenchmarkExecutor(Protocol):
    def run_benchmark(self, benchmark: Any, **run_config: Any) -> Any: ...


class DataFrameBenchmarkExecutor(Protocol):
    def run_benchmark(
        self,
        benchmark: Any,
        *,
        benchmark_config: Any = None,
        system_profile: Any = None,
        data_dir: Path | None = None,
        phases: Any = None,
        options: Any = None,
        monitor: Any = None,
        **run_config: Any,
    ) -> Any: ...


class ConnectionLifecycle(Protocol):
    def create_connection(self, **connection_config: Any) -> Any: ...

    def close_connection(self, connection: Any) -> None: ...


class ConnectionFactory(Protocol):
    def create_connection(self, **connection_config: Any) -> Any: ...


class NativeTableLoader(Protocol):
    def create_schema(self, benchmark: Any, connection: Any) -> float: ...

    def load_data(
        self,
        benchmark: Any,
        connection: Any,
        data_dir: Any,
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]: ...


class ExternalTableLoader(Protocol):
    supports_external_tables: bool

    def create_external_tables(
        self,
        benchmark: Any,
        connection: Any,
        data_dir: Any,
    ) -> tuple[dict[str, int], float, dict[str, Any] | None]: ...


class TPCHRefreshIngester(Protocol):
    supports_native_refresh_ingest: bool

    def ingest_refresh_file(
        self,
        connection: Any,
        table_name: str,
        columns: Sequence[tuple[str, str]],
        path: Path,
        null_marker: str | None = None,
    ) -> int: ...


class EnforcedPrimaryKeyProbe(Protocol):
    def enforced_primary_key_tables(self, connection: Any, tables: Sequence[str]) -> list[str]: ...


def resolve_primary_key_probe(adapter: Any) -> EnforcedPrimaryKeyProbe | None:
    if adapter is None or not callable(getattr(adapter, "enforced_primary_key_tables", None)):
        return None
    return adapter


def resolve_refresh_ingester(adapter: Any) -> TPCHRefreshIngester | None:
    if adapter is None:
        return None
    declared = vars(type(adapter)).get("supports_native_refresh_ingest")
    if declared is not True:
        return None
    if not callable(getattr(adapter, "ingest_refresh_file", None)):
        return None
    return adapter


class DataFrameQueryRuntime(Protocol):
    platform_name: str

    def create_context(self) -> Any: ...

    def load_table(
        self,
        ctx: Any,
        table_name: str,
        file_paths: list[Path],
        column_names: list[str] | None = None,
    ) -> int: ...

    def execute_query(self, ctx: Any, query: Any, query_id: str | None = None) -> dict[str, Any]: ...


class StatisticsPhaseRunner(Protocol):
    def run_statistics_phase(self, benchmark: Any, connection: Any, **options: Any) -> Any: ...


class TuningLedgerWriter(Protocol):
    def _write_applied_tuning_ledger(self, builder: Any) -> None: ...


class PlanCaptureRuntime(Protocol):
    def capture_query_plan(self, connection: Any, query: str, query_id: str) -> tuple[Any, float]: ...


class CategorizedQueryBenchmark(Protocol):
    def run_benchmark(
        self,
        connection: Any,
        queries: list[str] | None = None,
        iterations: int = 1,
        categories: list[str] | None = None,
    ) -> dict[str, Any]: ...
