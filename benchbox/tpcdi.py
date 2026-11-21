# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DI (TPC-DI) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DI specification.

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

    def get_schema(self, dialect: str = "standard") -> dict[str, dict[str, Any]]:
        return self._impl.get_schema(dialect)

    def get_create_tables_sql(self, dialect: str = "standard", tuning_config=None) -> str:
        return self._impl.get_create_tables_sql(dialect=dialect, tuning_config=tuning_config)

    @refuses_unavailable_benchmark("tpcdi")
    def generate_source_data(
        self,
        formats: Optional[list[str]] = None,
        batch_types: Optional[list[str]] = None,
    ) -> dict[str, list[str]]:
        return self._impl.generate_source_data(formats, batch_types)

    @refuses_unavailable_benchmark("tpcdi")
    def run_etl_pipeline(
        self,
        connection: Any,
        batch_type: str = "historical",
        validate_data: bool = True,
    ) -> dict[str, Any]:
        return self._impl.run_etl_pipeline(
            connection=connection,
            batch_type=batch_type,
            validate_data=validate_data,
        )

    @refuses_unavailable_benchmark("tpcdi")
    def validate_etl_results(self, connection: Any) -> dict[str, Any]:
        return self._impl.validate_etl_results(connection)

    def get_etl_status(self) -> dict[str, Any]:
        return self._impl.get_etl_status()

    @property
    def etl_mode(self) -> bool:
        return True

    @refuses_unavailable_benchmark("tpcdi")
    def load_data_to_database(self, connection: Any, tables: Optional[list[str]] = None) -> None:
        return self._impl.load_data_to_database(connection, tables)

    @refuses_unavailable_benchmark("tpcdi")
    def create_schema(self, connection: Any, dialect: str = "duckdb") -> None:
        return self._impl.create_schema(connection, dialect)

    @refuses_unavailable_benchmark("tpcdi")
    def run_full_benchmark(self, connection: Any, dialect: str = "duckdb") -> dict[str, Any]:
        return self._impl.run_full_benchmark(connection, dialect)

    @refuses_unavailable_benchmark("tpcdi")
    def run_etl_benchmark(self, connection: Any, dialect: str = "duckdb") -> Any:
        return self._impl.run_etl_benchmark(connection, dialect)

    @refuses_unavailable_benchmark("tpcdi")
    def run_data_validation(self, connection: Any) -> Any:
        return self._impl.run_data_validation(connection)

    @refuses_unavailable_benchmark("tpcdi")
    def calculate_official_metrics(self, etl_result: Any, validation_result: Any) -> Any:
        return self._impl.calculate_official_metrics(etl_result, validation_result)

    @refuses_unavailable_benchmark("tpcdi")
    def optimize_database(self, connection: Any) -> dict[str, Any]:
        return self._impl.optimize_database(connection)

    @property
    def validator(self) -> Any:
        return self._impl.validator

    @property
    def schema_manager(self) -> Any:
        return self._impl.schema_manager

    @property
    def metrics_calculator(self) -> Any:
        return self._impl.metrics_calculator
