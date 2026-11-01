# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from typing import Any

import yaml

from benchbox.core.primitives.catalog.loader import (
    _parse_expected_value_bounds as _shared_parse_expected_value_bounds,
    _parse_validation_platform_overrides as _shared_parse_validation_platform_overrides,
    _parse_validation_queries as shared_parse_validation_queries,
)

CATALOG_FILENAME = "operations.yaml"


class WritePrimitivesCatalogError(RuntimeError):
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
class AggregateStateSpec:
    sketch_type: str
    source_table: str
    target_subdir: str
    group_cols: list[str] = field(default_factory=list)
    value_col: str = ""
    sketch_alias: str = "sketch"
    supported_platforms: list[str] = field(default_factory=list)


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
    cleanup_platform_overrides: dict[str, str | None] = field(default_factory=dict)
    requires_setup: bool = True
    aggregate_state: AggregateStateSpec | None = None


@dataclass(frozen=True)
class WriteOperationsCatalog:
    version: int
    operations: dict[str, WriteOperation]


def _load_catalog_payload() -> dict:
    try:
        catalog_file = resources.files(__package__).joinpath(CATALOG_FILENAME)
    except (AttributeError, FileNotFoundError) as exc:
        raise WritePrimitivesCatalogError("Write Primitives operation catalog resource not found") from exc

    try:
        with catalog_file.open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
    except OSError as exc:
        raise WritePrimitivesCatalogError("Unable to read write primitives operation catalog") from exc
    except yaml.YAMLError as exc:
        raise WritePrimitivesCatalogError("Invalid YAML in write primitives operation catalog") from exc

    if not isinstance(payload, dict):
        raise WritePrimitivesCatalogError("Write Primitives operation catalog must be a mapping")
    return payload


def _parse_validation_queries(operation_id: str, raw_validations: object) -> list[ValidationQuery]:
    return shared_parse_validation_queries(
        {"validation_queries": raw_validations},
        operation_id,
        WritePrimitivesCatalogError,
        ValidationQuery,
    )


def _parse_validation_platform_overrides(
    operation_id: str,
    val_id: str,
    val_entry: dict,
) -> dict[str, str | None]:
    return _shared_parse_validation_platform_overrides(operation_id, val_id, val_entry, WritePrimitivesCatalogError)


def _parse_expected_value_bounds(
    operation_id: str,
    val_id: str,
    val_entry: dict,
) -> tuple[float | None, float | None]:
    return _shared_parse_expected_value_bounds(operation_id, val_id, val_entry, WritePrimitivesCatalogError)


def _parse_optional_scalars(operation_id: str, entry: dict) -> tuple[str | None, int | None]:
    cleanup_sql = entry.get("cleanup_sql")
    if cleanup_sql is not None and not isinstance(cleanup_sql, str):
        raise WritePrimitivesCatalogError(f"Catalog entry '{operation_id}' cleanup_sql must be a string")

    expected_rows_affected = entry.get("expected_rows_affected")
    if expected_rows_affected is not None:
        try:
            expected_rows_affected = int(expected_rows_affected)
        except (TypeError, ValueError):
            raise WritePrimitivesCatalogError(
                f"Catalog entry '{operation_id}' expected_rows_affected must be an integer"
            ) from None
    return cleanup_sql, expected_rows_affected


def _parse_aggregate_state(operation_id: str, raw: object) -> AggregateStateSpec | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise WritePrimitivesCatalogError(f"Catalog entry '{operation_id}' aggregate_state must be a mapping")
    sketch_type = raw.get("sketch_type")
    if sketch_type not in ("hll", "topk"):
        raise WritePrimitivesCatalogError(
            f"Catalog entry '{operation_id}' aggregate_state.sketch_type must be 'hll' or 'topk'"
        )
    source_table = raw.get("source_table")
    if not isinstance(source_table, str) or not source_table.strip():
        raise WritePrimitivesCatalogError(
            f"Catalog entry '{operation_id}' aggregate_state.source_table must be a non-empty string"
        )
    target_subdir = raw.get("target_subdir")
    if not isinstance(target_subdir, str) or not target_subdir.strip():
        raise WritePrimitivesCatalogError(
            f"Catalog entry '{operation_id}' aggregate_state.target_subdir must be a non-empty string"
        )
    raw_group_cols = raw.get("group_cols", [])
    if not isinstance(raw_group_cols, list) or not all(isinstance(col, str) and col.strip() for col in raw_group_cols):
        raise WritePrimitivesCatalogError(
            f"Catalog entry '{operation_id}' aggregate_state.group_cols must be a list of non-empty strings"
        )
    value_col = raw.get("value_col")
    if not isinstance(value_col, str) or not value_col.strip():
        raise WritePrimitivesCatalogError(
            f"Catalog entry '{operation_id}' aggregate_state.value_col must be a non-empty string"
        )
    sketch_alias = raw.get("sketch_alias", "sketch")
    if not isinstance(sketch_alias, str) or not sketch_alias.strip():
        raise WritePrimitivesCatalogError(
            f"Catalog entry '{operation_id}' aggregate_state.sketch_alias must be a non-empty string"
        )
    raw_supported = raw.get("supported_platforms", [])
    if not isinstance(raw_supported, list) or not all(isinstance(p, str) and p.strip() for p in raw_supported):
        raise WritePrimitivesCatalogError(
            f"Catalog entry '{operation_id}' aggregate_state.supported_platforms must be a list of non-empty strings"
        )
    return AggregateStateSpec(
        sketch_type=sketch_type,
        source_table=source_table.strip(),
        target_subdir=target_subdir.strip(),
        group_cols=[col.strip() for col in raw_group_cols],
        value_col=value_col.strip(),
        sketch_alias=sketch_alias.strip(),
        supported_platforms=[p.strip() for p in raw_supported],
    )


def _parse_operation_entry(index: int, entry: object, existing_ids: set[str]) -> WriteOperation:
    if not isinstance(entry, dict):
        raise WritePrimitivesCatalogError(f"Catalog entry at index {index} must be a mapping")

    operation_id = entry.get("id")
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise WritePrimitivesCatalogError(f"Catalog entry at index {index} is missing a valid 'id'")
    operation_id = operation_id.strip()

    if operation_id in existing_ids:
        raise WritePrimitivesCatalogError(f"Duplicate operation id detected in catalog: {operation_id}")

    category = entry.get("category")
    if not isinstance(category, str) or not category.strip():
        category = operation_id.split("_")[0]
    category = category.strip().lower()

    description = entry.get("description")
    if not isinstance(description, str) or not description.strip():
        raise WritePrimitivesCatalogError(f"Catalog entry '{operation_id}' must include a description")
    description = description.strip()

    aggregate_state = _parse_aggregate_state(operation_id, entry.get("aggregate_state"))

    write_sql = entry.get("write_sql")
    if aggregate_state is None:
        if not isinstance(write_sql, str) or not write_sql.strip():
            raise WritePrimitivesCatalogError(f"Catalog entry '{operation_id}' must include non-empty write_sql")
    else:
        if write_sql is None:
            write_sql = ""
        if not isinstance(write_sql, str):
            raise WritePrimitivesCatalogError(
                f"Catalog entry '{operation_id}' write_sql must be a string when aggregate_state is set"
            )

    cleanup_sql, expected_rows_affected = _parse_optional_scalars(operation_id, entry)

    file_dependencies = entry.get("file_dependencies", [])
    if not isinstance(file_dependencies, list):
        raise WritePrimitivesCatalogError(f"Catalog entry '{operation_id}' file_dependencies must be a list")

    platform_overrides = entry.get("platform_overrides", {})
    if not isinstance(platform_overrides, dict):
        raise WritePrimitivesCatalogError(f"Catalog entry '{operation_id}' platform_overrides must be a mapping")

    cleanup_platform_overrides = entry.get("cleanup_platform_overrides", {})
    if not isinstance(cleanup_platform_overrides, dict):
        raise WritePrimitivesCatalogError(
            f"Catalog entry '{operation_id}' cleanup_platform_overrides must be a mapping"
        )

    requires_setup = entry.get("requires_setup", True)
    if not isinstance(requires_setup, bool):
        raise WritePrimitivesCatalogError(f"Catalog entry '{operation_id}' requires_setup must be a boolean")

    return WriteOperation(
        id=operation_id,
        category=category,
        description=description,
        write_sql=write_sql,
        validation_queries=_parse_validation_queries(operation_id, entry.get("validation_queries", [])),
        cleanup_sql=cleanup_sql,
        expected_rows_affected=expected_rows_affected,
        file_dependencies=list(file_dependencies),
        platform_overrides=dict(platform_overrides),
        cleanup_platform_overrides=dict(cleanup_platform_overrides),
        requires_setup=requires_setup,
        aggregate_state=aggregate_state,
    )


def load_write_primitives_catalog() -> WriteOperationsCatalog:
    payload = _load_catalog_payload()

    raw_version = payload.get("version", 1)
    try:
        version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise WritePrimitivesCatalogError("Write Primitives operation catalog version must be an integer") from exc

    raw_entries = payload.get("operations")
    if not isinstance(raw_entries, list):
        raise WritePrimitivesCatalogError("Write Primitives operation catalog must define an 'operations' list")

    operations: dict[str, WriteOperation] = {}
    for index, entry in enumerate(raw_entries):
        op = _parse_operation_entry(index, entry, set(operations.keys()))
        operations[op.id] = op

    return WriteOperationsCatalog(version=version, operations=operations)


__all__ = [
    "WriteOperationsCatalog",
    "WriteOperation",
    "ValidationQuery",
    "AggregateStateSpec",
    "WritePrimitivesCatalogError",
    "load_write_primitives_catalog",
]
