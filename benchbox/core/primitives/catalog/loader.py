# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from inspect import Parameter, signature
from typing import Any

import yaml


@dataclass(frozen=True)
class ResultColumnContract:
    name: str
    type_class: str = "scalar"
    order_sensitive: bool = False


@dataclass(frozen=True)
class ResultContract:
    columns: tuple[ResultColumnContract, ...]
    row_identity: tuple[str, ...] = ()
    capability: str | None = None


_RESULT_CONTRACT_KEYS = {"columns", "row_identity", "capability"}
_RESULT_COLUMN_CONTRACT_KEYS = {"name", "type_class", "order_sensitive"}
_RESULT_TYPE_CLASSES = {"scalar", "json", "array", "struct", "map"}


def load_operations_catalog(
    *,
    package: str,
    catalog_filename: str,
    error_class: type[RuntimeError],
    label: str,
    build_validation_query: Any,
    build_operation: Any,
    build_catalog: Any,
) -> Any:
    payload = _load_yaml(package, catalog_filename, error_class, label, "operation")

    version = _parse_version(payload, error_class, label, "operation")

    raw_entries = payload.get("operations")
    if not isinstance(raw_entries, list):
        raise error_class(f"{label} operation catalog must define an 'operations' list")

    operations: dict[str, Any] = {}

    for index, entry in enumerate(raw_entries):
        if not isinstance(entry, dict):
            raise error_class(f"Catalog entry at index {index} must be a mapping")

        operation_id = _parse_required_string(entry, "id", index, None, error_class)

        if operation_id in operations:
            raise error_class(f"Duplicate operation id detected in catalog: {operation_id}")

        operations[operation_id] = _parse_operation_entry(
            entry, index, operation_id, error_class, build_validation_query, build_operation
        )

    return build_catalog(version=version, operations=operations)


def _parse_operation_entry(
    entry: dict[str, Any],
    index: int,
    operation_id: str,
    error_class: type[RuntimeError],
    build_validation_query: Any,
    build_operation: Any,
) -> Any:
    category = entry.get("category")
    if not isinstance(category, str) or not category.strip():
        category = operation_id.split("_")[0]
    category = category.strip().lower()

    description = entry.get("description")
    if not isinstance(description, str) or not description.strip():
        raise error_class(f"Catalog entry '{operation_id}' must include a description")
    description = description.strip()

    write_sql = entry.get("write_sql")
    if not isinstance(write_sql, str) or not write_sql.strip():
        raise error_class(f"Catalog entry '{operation_id}' must include non-empty write_sql")

    validation_queries = _parse_validation_queries(entry, operation_id, error_class, build_validation_query)

    cleanup_sql = entry.get("cleanup_sql")
    if cleanup_sql is not None and not isinstance(cleanup_sql, str):
        raise error_class(f"Catalog entry '{operation_id}' cleanup_sql must be a string")

    expected_rows_affected = entry.get("expected_rows_affected")
    if expected_rows_affected is not None:
        try:
            expected_rows_affected = int(expected_rows_affected)
        except (TypeError, ValueError) as exc:
            raise error_class(f"Catalog entry '{operation_id}' expected_rows_affected must be an integer") from exc

    file_dependencies = entry.get("file_dependencies", [])
    if not isinstance(file_dependencies, list):
        raise error_class(f"Catalog entry '{operation_id}' file_dependencies must be a list")

    platform_overrides = entry.get("platform_overrides", {})
    if not isinstance(platform_overrides, dict):
        raise error_class(f"Catalog entry '{operation_id}' platform_overrides must be a mapping")

    requires_setup = entry.get("requires_setup", True)
    if not isinstance(requires_setup, bool):
        raise error_class(f"Catalog entry '{operation_id}' requires_setup must be a boolean")

    return build_operation(
        id=operation_id,
        category=category,
        description=description,
        write_sql=write_sql,
        validation_queries=validation_queries,
        cleanup_sql=cleanup_sql,
        expected_rows_affected=expected_rows_affected,
        file_dependencies=file_dependencies,
        platform_overrides=platform_overrides,
        requires_setup=requires_setup,
    )


def load_query_catalog(
    *,
    package: str,
    catalog_filename: str,
    error_class: type[RuntimeError],
    label: str,
    build_query: Any,
    build_catalog: Any,
) -> Any:
    payload = _load_yaml(package, catalog_filename, error_class, label, "query")

    version = _parse_version(payload, error_class, label, "query")

    raw_entries = payload.get("queries")
    if not isinstance(raw_entries, list):
        raise error_class(f"{label} query catalog must define a 'queries' list")

    queries: dict[str, Any] = {}
    build_query_accepts_result_contract = _callable_accepts_keyword(build_query, "result_contract")

    for index, entry in enumerate(raw_entries):
        if not isinstance(entry, dict):
            raise error_class(f"Catalog entry at index {index} must be a mapping")

        query_id = _parse_required_string(entry, "id", index, None, error_class)

        if query_id in queries:
            raise error_class(f"Duplicate query id detected in catalog: {query_id}")

        raw_sql = entry.get("sql")
        if not isinstance(raw_sql, str) or not raw_sql.strip():
            raise error_class(f"Catalog entry '{query_id}' must include non-empty SQL text")

        category = entry.get("category")
        if category is None:
            category = query_id.split("_", 1)[0]
        if not isinstance(category, str) or not category.strip():
            raise error_class(f"Catalog entry '{query_id}' must define a non-empty category")
        category = category.strip().lower()

        description = entry.get("description")
        if description is not None:
            if not isinstance(description, str) or not description.strip():
                raise error_class(
                    f"Catalog entry '{query_id}' has invalid 'description'; expected non-empty string",
                )
            description = description.strip()

        variants = _parse_variants(entry, query_id, error_class)
        skip_on = _parse_skip_on(entry, query_id, error_class)
        result_contract = _parse_result_contract(entry, query_id, error_class)

        query_kwargs = {
            "id": query_id,
            "category": category,
            "sql": raw_sql,
            "description": description,
            "variants": variants,
            "skip_on": skip_on,
        }
        if build_query_accepts_result_contract:
            query_kwargs["result_contract"] = result_contract
        elif result_contract is not None:
            raise error_class(
                f"Catalog entry '{query_id}' declares result_contract, but this query model does not support it"
            )

        queries[query_id] = build_query(
            **query_kwargs,
        )

    return build_catalog(version=version, queries=queries)


def _callable_accepts_keyword(callable_obj: Any, keyword: str) -> bool:
    try:
        parameters = signature(callable_obj).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(parameter.kind == Parameter.VAR_KEYWORD or parameter.name == keyword for parameter in parameters)


def _parse_result_contract(
    entry: dict[str, Any],
    query_id: str,
    error_class: type[RuntimeError],
) -> ResultContract | None:
    raw_contract = entry.get("result_contract")
    if raw_contract is None:
        return None

    if not isinstance(raw_contract, dict):
        raise error_class(f"Catalog entry '{query_id}' has invalid 'result_contract'; expected mapping")

    unknown_keys = set(raw_contract) - _RESULT_CONTRACT_KEYS
    if unknown_keys:
        unknown = ", ".join(sorted(unknown_keys))
        raise error_class(f"Catalog entry '{query_id}' result_contract has unknown field(s): {unknown}")

    raw_columns = raw_contract.get("columns")
    if not isinstance(raw_columns, list) or not raw_columns:
        raise error_class(f"Catalog entry '{query_id}' result_contract.columns must be a non-empty list")

    columns = tuple(
        _parse_result_column_contract(column, query_id, index, error_class) for index, column in enumerate(raw_columns)
    )
    row_identity = _parse_result_contract_row_identity(
        raw_contract,
        query_id,
        {column.name for column in columns},
        error_class,
    )
    capability = _parse_optional_contract_string(raw_contract, "capability", query_id, error_class)

    return ResultContract(columns=columns, row_identity=row_identity, capability=capability)


def _parse_result_column_contract(
    raw_column: Any,
    query_id: str,
    index: int,
    error_class: type[RuntimeError],
) -> ResultColumnContract:
    if isinstance(raw_column, str):
        name = raw_column.strip()
        if not name:
            raise error_class(f"Catalog entry '{query_id}' result_contract column {index} must be non-empty")
        return ResultColumnContract(name=name)

    if not isinstance(raw_column, dict):
        raise error_class(f"Catalog entry '{query_id}' result_contract column {index} must be a string or mapping")

    unknown_keys = set(raw_column) - _RESULT_COLUMN_CONTRACT_KEYS
    if unknown_keys:
        unknown = ", ".join(sorted(unknown_keys))
        raise error_class(f"Catalog entry '{query_id}' result_contract column {index} has unknown field(s): {unknown}")

    name = _parse_contract_string_field(raw_column, "name", query_id, index, error_class)
    type_class = raw_column.get("type_class", "scalar")
    if not isinstance(type_class, str) or not type_class.strip():
        raise error_class(
            f"Catalog entry '{query_id}' result_contract column '{name}' type_class must be a non-empty string"
        )
    type_class = type_class.lower().strip()
    if type_class not in _RESULT_TYPE_CLASSES:
        allowed = ", ".join(sorted(_RESULT_TYPE_CLASSES))
        raise error_class(
            f"Catalog entry '{query_id}' result_contract column '{name}' type_class must be one of: {allowed}"
        )

    order_sensitive = raw_column.get("order_sensitive", False)
    if not isinstance(order_sensitive, bool):
        raise error_class(
            f"Catalog entry '{query_id}' result_contract column '{name}' order_sensitive must be a boolean"
        )

    return ResultColumnContract(name=name, type_class=type_class, order_sensitive=order_sensitive)


def _parse_result_contract_row_identity(
    raw_contract: dict[str, Any],
    query_id: str,
    column_names: set[str],
    error_class: type[RuntimeError],
) -> tuple[str, ...]:
    raw_row_identity = raw_contract.get("row_identity", [])
    if raw_row_identity is None:
        return ()
    if not isinstance(raw_row_identity, list):
        raise error_class(f"Catalog entry '{query_id}' result_contract.row_identity must be a list")

    row_identity = []
    for index, column_name in enumerate(raw_row_identity):
        if not isinstance(column_name, str) or not column_name.strip():
            raise error_class(
                f"Catalog entry '{query_id}' result_contract.row_identity entry {index} must be a non-empty string"
            )
        normalized = column_name.strip()
        if normalized not in column_names:
            raise error_class(
                f"Catalog entry '{query_id}' result_contract.row_identity references unknown column '{normalized}'"
            )
        row_identity.append(normalized)
    return tuple(row_identity)


def _parse_contract_string_field(
    payload: dict[str, Any],
    field: str,
    query_id: str,
    index: int,
    error_class: type[RuntimeError],
) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise error_class(f"Catalog entry '{query_id}' result_contract column {index} must include non-empty '{field}'")
    return value.strip()


def _parse_optional_contract_string(
    payload: dict[str, Any],
    field: str,
    query_id: str,
    error_class: type[RuntimeError],
) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise error_class(f"Catalog entry '{query_id}' result_contract.{field} must be a non-empty string")
    return value.strip()


def _load_yaml(
    package: str,
    filename: str,
    error_class: type[RuntimeError],
    label: str,
    kind: str,
) -> dict[str, Any]:
    try:
        catalog_file = resources.files(package).joinpath(filename)
    except (AttributeError, FileNotFoundError) as exc:
        raise error_class(f"{label} {kind} catalog resource not found") from exc

    try:
        with catalog_file.open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
    except OSError as exc:
        raise error_class(f"Unable to read {label.lower()} {kind} catalog") from exc
    except yaml.YAMLError as exc:
        raise error_class(f"Invalid YAML in {label.lower()} {kind} catalog") from exc

    if not isinstance(payload, dict):
        raise error_class(f"{label} {kind} catalog must be a mapping")

    return payload


def _parse_version(
    payload: dict[str, Any],
    error_class: type[RuntimeError],
    label: str,
    kind: str,
) -> int:
    raw_version = payload.get("version", 1)
    try:
        return int(raw_version)
    except (TypeError, ValueError) as exc:
        raise error_class(f"{label} {kind} catalog version must be an integer") from exc


def _parse_required_string(
    entry: dict[str, Any],
    field: str,
    index: int,
    entry_id: str | None,
    error_class: type[RuntimeError],
) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        context = f"'{entry_id}'" if entry_id else f"at index {index}"
        raise error_class(f"Catalog entry {context} is missing a valid '{field}'")
    return value.strip()


def _parse_validation_queries(
    entry: dict[str, Any],
    operation_id: str,
    error_class: type[RuntimeError],
    build_validation_query: Any,
) -> list[Any]:
    raw_validations = entry.get("validation_queries", [])
    if not isinstance(raw_validations, list):
        raise error_class(f"Catalog entry '{operation_id}' validation_queries must be a list")

    validation_queries = []
    for val_idx, val_entry in enumerate(raw_validations):
        if not isinstance(val_entry, dict):
            raise error_class(f"Validation query {val_idx} in operation '{operation_id}' must be a mapping")

        val_id = val_entry.get("id")
        if not isinstance(val_id, str) or not val_id.strip():
            raise error_class(f"Validation query {val_idx} in operation '{operation_id}' missing 'id'")

        val_sql = val_entry.get("sql")
        if not isinstance(val_sql, str) or not val_sql.strip():
            raise error_class(f"Validation query '{val_id}' in operation '{operation_id}' missing 'sql'")

        expected_value_min, expected_value_max = _parse_expected_value_bounds(
            operation_id, val_id, val_entry, error_class
        )
        platform_overrides = _parse_validation_platform_overrides(operation_id, val_id, val_entry, error_class)

        all_kwargs = {
            "id": val_id.strip(),
            "sql": val_sql,
            "expected_rows": val_entry.get("expected_rows"),
            "expected_rows_min": val_entry.get("expected_rows_min"),
            "expected_rows_max": val_entry.get("expected_rows_max"),
            "expected_values": val_entry.get("expected_values"),
            "check_expression": val_entry.get("check_expression"),
            "expected_value_min": expected_value_min,
            "expected_value_max": expected_value_max,
            "bounds_scale_with_sf": val_entry.get("bounds_scale_with_sf", False),
            "platform_overrides": platform_overrides,
        }
        validation_queries.append(
            build_validation_query(**_filter_supported_kwargs(build_validation_query, all_kwargs))
        )

    return validation_queries


def _parse_validation_platform_overrides(
    operation_id: str,
    val_id: str,
    val_entry: dict[str, Any],
    error_class: type[RuntimeError],
) -> dict[str, str | None]:
    raw = val_entry.get("platform_overrides")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise error_class(
            f"Validation query '{val_id}' in operation '{operation_id}' platform_overrides must be a mapping"
        )
    overrides: dict[str, str | None] = {}
    for platform_key, override in raw.items():
        if not isinstance(platform_key, str) or not platform_key.strip():
            raise error_class(
                f"Validation query '{val_id}' in operation '{operation_id}' "
                "platform_overrides keys must be non-empty platform names"
            )
        if override is None:
            overrides[platform_key.strip()] = None
            continue
        if not isinstance(override, str) or not override.strip():
            raise error_class(
                f"Validation query '{val_id}' in operation '{operation_id}' "
                f"platform_overrides['{platform_key}'] must be a non-empty string or null"
            )
        overrides[platform_key.strip()] = override
    return overrides


def _parse_expected_value_bounds(
    operation_id: str,
    val_id: str,
    val_entry: dict[str, Any],
    error_class: type[RuntimeError],
) -> tuple[float | None, float | None]:
    raw_min = val_entry.get("expected_value_min")
    raw_max = val_entry.get("expected_value_max")
    if raw_min is None and raw_max is None:
        return None, None
    if raw_min is None or raw_max is None:
        raise error_class(
            f"Validation query '{val_id}' in operation '{operation_id}' must set both "
            "expected_value_min and expected_value_max together"
        )
    try:
        bound_min = float(raw_min)
        bound_max = float(raw_max)
    except (TypeError, ValueError) as exc:
        raise error_class(
            f"Validation query '{val_id}' in operation '{operation_id}' expected_value_min/max must be numeric"
        ) from exc
    if bound_min > bound_max:
        raise error_class(
            f"Validation query '{val_id}' in operation '{operation_id}' expected_value_min "
            f"({bound_min}) must be <= expected_value_max ({bound_max})"
        )
    if any(val_entry.get(k) is not None for k in ("expected_rows", "expected_rows_min", "expected_rows_max")):
        raise error_class(
            f"Validation query '{val_id}' in operation '{operation_id}' cannot combine "
            "expected_value_min/max with expected_rows fields — they describe different "
            "validation kinds"
        )
    if val_entry.get("expected_values") is not None:
        raise error_class(
            f"Validation query '{val_id}' in operation '{operation_id}' cannot combine "
            "expected_value_min/max with expected_values"
        )
    return bound_min, bound_max


def _filter_supported_kwargs(callable_: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        params = signature(callable_).parameters
    except (TypeError, ValueError):
        return kwargs
    if any(p.kind == Parameter.VAR_KEYWORD for p in params.values()):
        return kwargs
    return {k: v for k, v in kwargs.items() if k in params}


def _parse_variants(
    entry: dict[str, Any],
    query_id: str,
    error_class: type[RuntimeError],
) -> dict[str, str] | None:
    raw_variants = entry.get("variants")
    if raw_variants is None:
        return None

    if not isinstance(raw_variants, dict):
        raise error_class(f"Catalog entry '{query_id}' has invalid 'variants'; expected mapping of dialect -> SQL")

    variants = {}
    for dialect, variant_sql in raw_variants.items():
        if not isinstance(dialect, str) or not dialect.strip():
            raise error_class(f"Catalog entry '{query_id}' variant dialect must be a non-empty string")
        if not isinstance(variant_sql, str) or not variant_sql.strip():
            raise error_class(f"Catalog entry '{query_id}' variant SQL for dialect '{dialect}' must be non-empty")
        variants[dialect.lower().strip()] = variant_sql

    return variants


def _parse_skip_on(
    entry: dict[str, Any],
    query_id: str,
    error_class: type[RuntimeError],
) -> list[str] | None:
    raw_skip_on = entry.get("skip_on")
    if raw_skip_on is None:
        return None

    if not isinstance(raw_skip_on, list):
        raise error_class(f"Catalog entry '{query_id}' has invalid 'skip_on'; expected list of dialects")

    skip_on = []
    for dialect in raw_skip_on:
        if not isinstance(dialect, str) or not dialect.strip():
            raise error_class(f"Catalog entry '{query_id}' skip_on dialect must be a non-empty string")
        skip_on.append(dialect.lower().strip())

    return skip_on


__all__ = [
    "ResultColumnContract",
    "ResultContract",
    "load_operations_catalog",
    "load_query_catalog",
]
