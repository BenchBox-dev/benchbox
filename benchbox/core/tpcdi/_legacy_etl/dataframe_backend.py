from __future__ import annotations

import logging
import math
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from benchbox.core.dataframe.maintenance_interface import DataFrameMaintenanceOperations

logger = logging.getLogger(__name__)

_UNSAFE_CONDITION_TOKENS = (";", "--", "/*", "*/")
_FORBIDDEN_CONDITION_KEYWORDS = frozenset(
    {
        "DROP",
        "DELETE",
        "INSERT",
        "UPDATE",
        "ALTER",
        "CREATE",
        "ATTACH",
        "DETACH",
        "PRAGMA",
    }
)
_COLUMN_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _validate_condition_column(column: str) -> str:
    if not _COLUMN_NAME_PATTERN.fullmatch(column):
        raise ValueError(f"Unsafe column name in SCD2 expire condition: {column!r}")
    return column


def _check_condition_text(normalized: str) -> str:
    if any(token in normalized for token in _UNSAFE_CONDITION_TOKENS):
        raise ValueError("Unsafe SQL tokens are not allowed in SCD2 expire condition")
    if any(re.search(rf"\b{keyword}\b", normalized.upper()) for keyword in _FORBIDDEN_CONDITION_KEYWORDS):
        raise ValueError("DML/DDL keywords are not allowed in SCD2 expire condition")
    return normalized


def _render_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    try:
        import numpy as np

        if isinstance(value, np.generic):
            value = value.item()
    except ImportError:
        pass
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"Non-finite float cannot be rendered as a SQL literal: {value!r}")
        return repr(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return f"'{value.isoformat(sep=' ')}'"
    if isinstance(value, date):
        return f"'{value.strftime('%Y-%m-%d')}'"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    raise TypeError(f"Unsupported literal value for SCD2 operation: {value!r}")


def _condition_to_sql(condition: str | Any) -> str:
    if isinstance(condition, str):
        if not condition.strip():
            raise ValueError("SCD2 expire condition cannot be empty")
        return _check_condition_text(condition.strip())
    if isinstance(condition, dict):
        if not condition:
            raise ValueError("SCD2 expire condition dictionary cannot be empty")
        clauses: list[str] = []
        for raw_column, value in condition.items():
            column = _validate_condition_column(str(raw_column))
            if value is None:
                clauses.append(f'"{column}" IS NULL')
            else:
                clauses.append(f'"{column}" = {_render_literal(value)}')
        return " AND ".join(clauses)
    raise TypeError("SCD2 expire condition must be a SQL expression string or dict[str, Any]")


class DataFrameETLBackend:
    def __init__(
        self,
        *,
        maintenance_ops: DataFrameMaintenanceOperations,
        platform_name: str,
        table_root: Path | str | None = None,
    ) -> None:
        self.maintenance_ops = maintenance_ops
        self.platform_name = platform_name
        self.table_root = Path(table_root) if table_root is not None else None

    def _resolve_table_path(self, table_name: str) -> Path | str:
        if self.table_root is None:
            return table_name
        return self.table_root / table_name

    def create_schema(self) -> None:
        return

    def load_dataframes(self, staged_data: dict[str, pd.DataFrame], batch_type: str) -> dict[str, Any]:
        _ = batch_type
        load_results: dict[str, Any] = {"records_loaded": 0, "tables_updated": []}
        for table_name, dataframe in staged_data.items():
            if dataframe is None or dataframe.empty:
                continue
            result = self.maintenance_ops.insert_rows(self._resolve_table_path(table_name), dataframe, mode="append")
            if not result.success:
                raise RuntimeError(result.error_message or f"Failed to insert rows for table {table_name}")
            load_results["records_loaded"] += int(result.rows_affected)
            if int(result.rows_affected) > 0 and table_name not in load_results["tables_updated"]:
                load_results["tables_updated"].append(table_name)
        return load_results

    def validate_results(self) -> dict[str, Any]:
        return {
            "validation_queries": {},
            "data_quality_issues": [],
            "data_quality_score": 0.0,
            "completeness_checks": {},
            "consistency_checks": {},
            "accuracy_checks": {},
            "validation_level": "not_executed",
            "notes": f"Validation executed in DataFrame mode for platform '{self.platform_name}'",
        }

    def _adapter_accepts_sql_predicates(self) -> bool:
        get_capabilities = getattr(self.maintenance_ops, "get_capabilities", None)
        if get_capabilities is None:
            return True
        try:
            return bool(get_capabilities().accepts_sql_predicates)
        except AttributeError:
            logger.warning("Maintenance adapter capabilities lack accepts_sql_predicates; assuming SQL predicates")
            return True

    def execute_scd2_expire(self, table_name: str, condition: str | Any, updates: dict[str, Any]) -> dict[str, Any]:
        if not updates:
            return {"success": True, "rows_affected": 0}
        if not self._adapter_accepts_sql_predicates():
            if not isinstance(condition, dict):
                raise TypeError(
                    "This maintenance adapter does not accept SQL predicate text; "
                    "pass the SCD2 expire condition as dict[str, Any]"
                )
            for raw_column in condition:
                _validate_condition_column(str(raw_column))
            predicate: str | Any = condition
            rendered_updates = {str(column): value for column, value in updates.items()}
        else:
            predicate = _condition_to_sql(condition)
            rendered_updates = {str(column): _render_literal(value) for column, value in updates.items()}
        result = self.maintenance_ops.update_rows(self._resolve_table_path(table_name), predicate, rendered_updates)
        if not result.success:
            raise RuntimeError(result.error_message or f"Failed to expire rows for table {table_name}")
        return {"success": True, "rows_affected": int(result.rows_affected)}

    def execute_scd2_insert(self, table_name: str, dataframe: pd.DataFrame) -> dict[str, Any]:
        result = self.maintenance_ops.insert_rows(self._resolve_table_path(table_name), dataframe, mode="append")
        if not result.success:
            raise RuntimeError(result.error_message or f"Failed to insert SCD2 rows for table {table_name}")
        return {"success": True, "rows_affected": int(result.rows_affected)}

    def read_current_dimension(self, table_name: str) -> pd.DataFrame | None:
        _ = table_name
        return None
