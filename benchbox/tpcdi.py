# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from pathlib import Path
from typing import Any, Optional, Union

from benchbox.base import BaseBenchmark
from benchbox.core.run_service import refuses_unavailable_benchmark
from benchbox.core.tpcdi.benchmark import TPCDIBenchmark


class TPCDI(BaseBenchmark):
    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Optional[Union[str, Path]] = None,
        **kwargs,
    ) -> None:
        super().__init__(scale_factor=scale_factor, output_dir=output_dir, **kwargs)

        verbose = kwargs.pop("verbose", False)
        self._impl = TPCDIBenchmark(scale_factor=scale_factor, output_dir=output_dir, verbose=verbose, **kwargs)

    @refuses_unavailable_benchmark("tpcdi")
    def generate_data(self) -> list[Union[str, Path]]:
        return self._impl.generate_data()

    @refuses_unavailable_benchmark("tpcdi")
    def get_queries(self) -> dict[str, str]:
        return self._impl.get_queries()

    @refuses_unavailable_benchmark("tpcdi")
    def get_query(self, query_id: Union[int, str], *, params: Optional[dict[str, Any]] = None) -> str:
        return self._impl.get_query(query_id, params=params)

    @refuses_unavailable_benchmark("tpcdi")
    def run_benchmark(
        self,
        connection: Any,
        query_ids: Optional[list[Union[int, str]]] = None,
        fetch_results: bool = False,
        setup_database: bool = True,
    ) -> dict[str, Any]:
        return self._impl.run_benchmark(connection, query_ids, fetch_results, setup_database)

    def get_schema(self, dialect: str = "standard") -> dict[str, dict[str, Any]]:
        return self._impl.get_schema(dialect)

    def get_create_tables_sql(self, dialect: str = "standard", tuning_config=None) -> str:
        return self._impl.get_create_tables_sql(dialect=dialect, tuning_config=tuning_config)
