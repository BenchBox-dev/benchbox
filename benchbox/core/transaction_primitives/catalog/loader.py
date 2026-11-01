# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from benchbox.core.primitives.catalog.loader import load_operations_catalog

CATALOG_FILENAME = "operations.yaml"


class TransactionPrimitivesCatalogError(RuntimeError):
    pass


@dataclass(frozen=True)
class ValidationQuery:
    id: str
    sql: str
    expected_rows: int | None = None
    expected_rows_min: int | None = None
    expected_rows_max: int | None = None
    expected_values: dict[str, Any] | None = None
    check_expression: str | None = None
    expected_value_min: float | None = None
    expected_value_max: float | None = None
    bounds_scale_with_sf: bool = False
    platform_overrides: dict[str, str | None] = field(default_factory=dict)


@dataclass(frozen=True)
class WriteOperation:
    id: str
    category: str
    description: str
    write_sql: str
    validation_queries: list[ValidationQuery] = field(default_factory=list)
    cleanup_sql: str | None = None
    expected_rows_affected: int | None = None
    file_dependencies: list[str] = field(default_factory=list)
    platform_overrides: dict[str, str] = field(default_factory=dict)
    requires_setup: bool = True


@dataclass(frozen=True)
class TransactionOperationsCatalog:
    version: int
    operations: dict[str, WriteOperation]


def load_transaction_primitives_catalog() -> TransactionOperationsCatalog:
    return load_operations_catalog(
        package=__package__,
        catalog_filename=CATALOG_FILENAME,
        error_class=TransactionPrimitivesCatalogError,
        label="Transaction Primitives",
        build_validation_query=ValidationQuery,
        build_operation=WriteOperation,
        build_catalog=TransactionOperationsCatalog,
    )


__all__ = [
    "TransactionOperationsCatalog",
    "WriteOperation",
    "ValidationQuery",
    "TransactionPrimitivesCatalogError",
    "load_transaction_primitives_catalog",
]
