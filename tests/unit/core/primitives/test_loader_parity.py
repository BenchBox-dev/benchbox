from __future__ import annotations

import inspect

import pytest

from benchbox.core.primitives.catalog.loader import _parse_validation_queries as shared_parser
from benchbox.core.transaction_primitives.catalog.loader import (
    ValidationQuery as TransactionValidationQuery,
)
from benchbox.core.write_primitives.catalog.loader import ValidationQuery as WriteValidationQuery

pytestmark = [pytest.mark.fast, pytest.mark.unit]


def _validation_query_fields(dc) -> set[str]:
    return set(dc.__dataclass_fields__.keys())


def test_validation_query_dataclasses_share_field_set() -> None:
    write_fields = _validation_query_fields(WriteValidationQuery)
    transaction_fields = _validation_query_fields(TransactionValidationQuery)
    assert write_fields == transaction_fields, (
        "ValidationQuery dataclass field divergence -- the two loaders' contracts "
        "must stay in lockstep. Diff: "
        f"only-in-write={sorted(write_fields - transaction_fields)} "
        f"only-in-transaction={sorted(transaction_fields - write_fields)}"
    )


def test_shared_parser_forwards_all_validation_query_fields() -> None:

    class _CatalogError(RuntimeError):
        pass

    entry = {
        "validation_queries": [
            {
                "id": "v1",
                "sql": "SELECT 1",
                "expected_value_min": 0.0,
                "expected_value_max": 1.0,
                "platform_overrides": {"duckdb": None, "postgres": "SELECT 2"},
            }
        ]
    }
    queries = shared_parser(entry, "op1", _CatalogError, WriteValidationQuery)
    assert len(queries) == 1
    vq = queries[0]
    assert vq.id == "v1"
    assert vq.expected_value_min == 0.0
    assert vq.expected_value_max == 1.0
    assert vq.platform_overrides == {"duckdb": None, "postgres": "SELECT 2"}


def test_shared_parser_rejects_half_set_value_bounds() -> None:
    class _CatalogError(RuntimeError):
        pass

    entry = {
        "validation_queries": [
            {"id": "v1", "sql": "SELECT 1", "expected_value_min": 0.0},
        ]
    }
    with pytest.raises(_CatalogError, match="must set both"):
        shared_parser(entry, "op1", _CatalogError, WriteValidationQuery)


def test_shared_parser_rejects_min_above_max() -> None:
    class _CatalogError(RuntimeError):
        pass

    entry = {
        "validation_queries": [
            {"id": "v1", "sql": "SELECT 1", "expected_value_min": 5.0, "expected_value_max": 1.0},
        ]
    }
    with pytest.raises(_CatalogError, match="must be <="):
        shared_parser(entry, "op1", _CatalogError, WriteValidationQuery)


def test_shared_parser_rejects_value_bounds_with_row_bounds() -> None:
    class _CatalogError(RuntimeError):
        pass

    entry = {
        "validation_queries": [
            {
                "id": "v1",
                "sql": "SELECT 1",
                "expected_rows": 1,
                "expected_value_min": 0.0,
                "expected_value_max": 1.0,
            },
        ]
    }
    with pytest.raises(_CatalogError, match="cannot combine"):
        shared_parser(entry, "op1", _CatalogError, WriteValidationQuery)


def test_shared_parser_rejects_empty_string_override() -> None:
    class _CatalogError(RuntimeError):
        pass

    entry = {
        "validation_queries": [
            {"id": "v1", "sql": "SELECT 1", "platform_overrides": {"duckdb": ""}},
        ]
    }
    with pytest.raises(_CatalogError, match="must be a non-empty string or null"):
        shared_parser(entry, "op1", _CatalogError, WriteValidationQuery)


def test_write_primitives_loader_delegates_to_shared_parser() -> None:
    from benchbox.core.write_primitives.catalog import loader as write_loader

    source = inspect.getsource(write_loader._parse_validation_queries)
    assert "shared_parse_validation_queries" in source, (
        "write_primitives loader must delegate to the shared parser; "
        "if a divergent helper reappears here the parity contract is broken"
    )


def test_write_primitives_backcompat_helper_shims_stay_callable() -> None:
    from benchbox.core.write_primitives.catalog import loader as write_loader

    assert write_loader._parse_expected_value_bounds(
        "op1", "v1", {"expected_value_min": "1", "expected_value_max": "2"}
    ) == (1.0, 2.0)
    assert write_loader._parse_validation_platform_overrides(
        "op1", "v1", {"platform_overrides": {" duckdb ": "SELECT 1", "redshift": None}}
    ) == {"duckdb": "SELECT 1", "redshift": None}


