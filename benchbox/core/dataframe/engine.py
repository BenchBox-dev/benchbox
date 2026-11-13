from __future__ import annotations

from enum import Enum


class DataFrameEngine(str, Enum):
    POLARS = "Polars"
    PANDAS = "Pandas"
    CUDF = "cuDF"
    DASK = "Dask"
    DATAFUSION = "DataFusion"
    PYSPARK = "PySpark"
    LAKESAIL = "LakeSail"

    @classmethod
    def from_platform_name(cls, name: str) -> DataFrameEngine:
        try:
            return cls(name)
        except ValueError:
            known = ", ".join(member.value for member in cls)
            raise ValueError(f"Unknown DataFrame engine {name!r}; expected one of: {known}") from None


SPARK_ENGINES: frozenset[DataFrameEngine] = frozenset({DataFrameEngine.PYSPARK, DataFrameEngine.LAKESAIL})
