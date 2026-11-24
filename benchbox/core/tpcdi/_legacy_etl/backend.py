from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class TPCDIETLBackend(Protocol):
    def create_schema(self) -> None: ...

    def load_dataframes(self, staged_data: dict[str, pd.DataFrame], batch_type: str) -> dict[str, Any]: ...

    def validate_results(self) -> dict[str, Any]: ...

    def execute_scd2_expire(self, table_name: str, condition: str | Any, updates: dict[str, Any]) -> dict[str, Any]: ...

    def execute_scd2_insert(self, table_name: str, dataframe: pd.DataFrame) -> dict[str, Any]: ...

    def read_current_dimension(self, table_name: str) -> pd.DataFrame | None: ...
