from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from benchbox.core.dataframe.query_resolution import get_tpch_dataframe_queries
from benchbox.core.tpch.dataframe_queries import parameter_scope
from benchbox.core.tpch.dataframe_refresh import DataFrameRefresher, check_refresh_function, execute_refresh_function
from benchbox.core.tpch.parameter_extractor import get_tpch_query_extracted_parameters
from benchbox.core.tpch.refresh import TPCHRefreshUpdateSet, TPCHRF2Baseline
from benchbox.core.tpch.throughput_test import TPCHThroughputTest


@dataclass(frozen=True)
class DataFrameStreamQuery:
    query_id: int
    query: Any
    parameters: dict[str, Any]
    scale_factor: float


@dataclass(frozen=True)
class DataFrameStreamCursor:
    platform_result: dict[str, Any]


class DataFrameStreamConnection:
    def __init__(self, ctx: Any, execute_query: Callable[[Any, Any], dict[str, Any]]) -> None:
        self._ctx = ctx
        self._execute_query = execute_query

    def execute(self, stream_query: DataFrameStreamQuery) -> DataFrameStreamCursor:
        snapshot = self._ctx.snapshot()
        overrides = {stream_query.query_id: stream_query.parameters}
        with parameter_scope(overrides, stream_query.scale_factor):
            return DataFrameStreamCursor(dict(self._execute_query(snapshot, stream_query.query)))

    def close(self) -> None:
        return None


class DataFrameRefreshSession:
    def __init__(self, refresher: DataFrameRefresher) -> None:
        self.refresher = refresher

    def close(self) -> None:
        return None


class DataFrameTPCHThroughputTest(TPCHThroughputTest):
    def __init__(
        self,
        ctx: Any,
        refresher: DataFrameRefresher | None,
        execute_query: Callable[[Any, Any], dict[str, Any]],
        *,
        scale_factor: float,
        num_streams: int,
        verbose: bool = False,
        refresh_first_set: int = 1,
    ) -> None:
        super().__init__(
            benchmark=None,
            connection_factory=lambda: DataFrameStreamConnection(ctx, execute_query),
            scale_factor=scale_factor,
            num_streams=num_streams,
            verbose=verbose,
            refresh_enabled=refresher is not None,
            refresh_first_set=refresh_first_set,
            refresh_connection_factory=(lambda: DataFrameRefreshSession(refresher)) if refresher is not None else None,
        )
        self._ctx = ctx
        self._queries_by_stream: dict[int, dict[str, Any]] = {}

    def _get_stream_query(self, query_id: int, stream_seed: int, stream_id: int, scale_factor: float) -> Any:
        queries = self._queries_by_stream.get(stream_id)
        if queries is None:
            queries = {str(query.query_id).upper(): query for query in get_tpch_dataframe_queries(stream_id)}
            self._queries_by_stream[stream_id] = queries
        query = queries.get(f"Q{query_id}")
        if query is None:
            raise ValueError(f"TPC-H query Q{query_id} has no DataFrame implementation")
        parameters = get_tpch_query_extracted_parameters(query_id, stream_seed, scale_factor)
        return DataFrameStreamQuery(query_id, query, parameters, scale_factor)

    def _execute_refresh_function(
        self, function: str, update_set: TPCHRefreshUpdateSet, connection: DataFrameRefreshSession
    ) -> dict[str, Any]:
        return execute_refresh_function(connection.refresher, function, update_set, check=False)

    def _capture_rf2_baselines(self, update_sets: dict[int, TPCHRefreshUpdateSet]) -> dict[int, TPCHRF2Baseline]:
        return {}

    def _check_refresh_function(
        self,
        connection: DataFrameRefreshSession,
        function_result: dict[str, Any],
        update_set: TPCHRefreshUpdateSet,
        baseline: TPCHRF2Baseline | None,
    ) -> None:
        check_refresh_function(connection.refresher, function_result["function"], update_set, function_result)

    def _verify_refresh_totals(
        self,
        connection: DataFrameRefreshSession,
        baselines: dict[int, TPCHRF2Baseline],
        update_sets: dict[int, TPCHRefreshUpdateSet],
        committed: list[tuple[dict[str, Any], dict[str, Any]]],
    ) -> None:
        return None
