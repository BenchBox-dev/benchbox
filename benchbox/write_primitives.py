# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from pathlib import Path
from typing import Any, Optional, Union

from benchbox.base import BaseBenchmark
from benchbox.core.benchmark_mixins import OperationCategoryFacadeMixin, QueryCategoryFacadeMixin, QueryFacadeMixin
from benchbox.core.operations import OperationExecutor
from benchbox.core.write_primitives.benchmark import WritePrimitivesBenchmark


class WritePrimitives(
    OperationCategoryFacadeMixin,
    QueryCategoryFacadeMixin,
    QueryFacadeMixin,
    BaseBenchmark,
    OperationExecutor,
):
    DATAFRAME_WORKLOAD_USES_PLATFORM_ENGINE = WritePrimitivesBenchmark.DATAFRAME_WORKLOAD_USES_PLATFORM_ENGINE

    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Optional[Union[str, Path]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(scale_factor=scale_factor, output_dir=output_dir, **kwargs)

        self._impl = WritePrimitivesBenchmark(scale_factor=scale_factor, output_dir=output_dir, **kwargs)

        self.output_dir = self._impl.output_dir

    def get_data_source_benchmark(self) -> Optional[str]:
        return self._impl.get_data_source_benchmark()

    @property
    def tables(self) -> dict[str, Path]:
        return getattr(self._impl, "tables", {})

    def generate_data(self, tables: Optional[list[str]] = None) -> list[Union[str, Path]]:
        return self._impl.generate_data(tables)

    def get_operation(self, operation_id: str) -> Any:
        return self._impl.get_operation(operation_id)

    def get_all_operations(self) -> dict[str, Any]:
        return self._impl.get_all_operations()

    def get_schema(self, dialect: str = "standard") -> dict[str, dict]:
        return self._impl.get_schema(dialect=dialect)

    def get_create_tables_sql(self, dialect: str = "standard", tuning_config=None) -> str:
        return self._impl.get_create_tables_sql(dialect=dialect, tuning_config=tuning_config)

    def get_benchmark_info(self) -> dict[str, Any]:
        return self._impl.get_benchmark_info()

    def setup(self, connection: Any, force: bool = False) -> dict[str, Any]:
        return self._impl.setup(connection, force)

    def load_data(self, connection: Any, **kwargs) -> dict[str, Any]:
        return self._impl.load_data(connection, **kwargs)

    def teardown(self, connection: Any) -> None:
        return self._impl.teardown(connection)

    def reset(self, connection: Any) -> None:
        return self._impl.reset(connection)

    def is_setup(self, connection: Any) -> bool:
        return self._impl.is_setup(connection)

    def execute_operation(self, operation_id: str, connection: Any, **kwargs: Any) -> Any:
        return self._impl.execute_operation(operation_id, connection, **kwargs)

    def run_benchmark(
        self,
        connection: Any,
        operation_ids: Optional[list[str]] = None,
        categories: Optional[list[str]] = None,
    ) -> list[Any]:
        return self._impl.run_benchmark(connection, operation_ids, categories)


__all__ = ["WritePrimitives", "WritePrimitivesBenchmark"]
