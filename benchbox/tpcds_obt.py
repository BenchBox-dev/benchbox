from pathlib import Path
from typing import Any, Optional, Union

from benchbox.base import BaseBenchmark
from benchbox.core.tpcds_obt.benchmark import TPCDSOBTBenchmark


class TPCDSOBT(BaseBenchmark):
    GENERATES_OWN_OUTPUT = TPCDSOBTBenchmark.GENERATES_OWN_OUTPUT
    REQUIRED_LOADED_TABLES = TPCDSOBTBenchmark.REQUIRED_LOADED_TABLES

    def __init__(
        self,
        scale_factor: float = 1.0,
        output_dir: Optional[Union[str, Path]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(scale_factor=scale_factor, output_dir=output_dir, **kwargs)
        self._initialize_benchmark_implementation(
            TPCDSOBTBenchmark,
            scale_factor,
            output_dir,
            **kwargs,
        )

    def generate_data(self) -> dict[str, Any]:
        return self._impl.generate_data()

    def get_queries(self, dialect: Optional[str] = None, base_dialect: Optional[str] = None) -> dict[str, str]:
        return self._impl.get_queries(dialect=dialect)

    def get_query(self, query_id: Union[int, str], **kwargs: Any) -> str:
        return self._impl.get_query(query_id, **kwargs)

    def get_schema(self) -> dict[str, Any]:
        return self._impl.get_schema()

    def get_create_tables_sql(self, dialect: Optional[str] = None) -> str:
        return self._impl.get_create_tables_sql(dialect=dialect or "duckdb")
