from __future__ import annotations

import copy
from importlib import resources

import pytest
import yaml
from pydantic import ValidationError

from benchbox.core.catalog_schema import (
    CATALOG_SCHEMAS,
    BenchmarkRegistryCatalog,
    BenchmarkSpecsCatalog,
    StaticQueryCatalog,
    validate_all_catalogs,
    validate_catalog,
)

pytestmark = [pytest.mark.unit, pytest.mark.fast]


def _load(package: str, filename: str) -> dict:
    return yaml.safe_load(resources.files(package).joinpath(filename).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("package", "filename", "model"),
    [(package, filename, model) for (package, filename), model in CATALOG_SCHEMAS.items()],
    ids=[f"{package}:{filename}" for package, filename in CATALOG_SCHEMAS],
)
def test_real_catalog_validates(package: str, filename: str, model: type) -> None:
    validate_catalog(package, filename, model)


def test_validate_all_catalogs_passes() -> None:
    assert validate_all_catalogs() == len(CATALOG_SCHEMAS)


def test_registry_missing_required_field_rejected() -> None:
    bad = copy.deepcopy(_load("benchbox.core", "benchmark_registry.yaml"))
    first = next(iter(bad["benchmark_metadata"]))
    del bad["benchmark_metadata"][first]["category"]
    with pytest.raises(ValidationError):
        BenchmarkRegistryCatalog.model_validate(bad)


def test_registry_wrong_type_rejected() -> None:
    bad = copy.deepcopy(_load("benchbox.core", "benchmark_registry.yaml"))
    first = next(iter(bad["benchmark_metadata"]))
    bad["benchmark_metadata"][first]["num_queries"] = "23"
    with pytest.raises(ValidationError):
        BenchmarkRegistryCatalog.model_validate(bad)


def test_registry_scalar_coercion_rejected() -> None:
    bad = copy.deepcopy(_load("benchbox.core", "benchmark_registry.yaml"))
    first = next(iter(bad["benchmark_metadata"]))
    bad["benchmark_metadata"][first]["supports_streams"] = "false"
    with pytest.raises(ValidationError):
        BenchmarkRegistryCatalog.model_validate(bad)


def test_registry_bad_support_status_rejected() -> None:
    bad = copy.deepcopy(_load("benchbox.core", "benchmark_registry.yaml"))
    first = next(iter(bad["benchmark_metadata"]))
    bad["benchmark_metadata"][first]["support_status"] = "totally-bogus"
    with pytest.raises(ValidationError):
        BenchmarkRegistryCatalog.model_validate(bad)


@pytest.mark.parametrize("value", [[10, 2], [-1, 2]])
def test_registry_bad_estimated_time_range_rejected(value: list[float]) -> None:
    bad = copy.deepcopy(_load("benchbox.core", "benchmark_registry.yaml"))
    first = next(iter(bad["benchmark_metadata"]))
    bad["benchmark_metadata"][first]["estimated_time_range"] = value
    with pytest.raises(ValidationError):
        BenchmarkRegistryCatalog.model_validate(bad)


@pytest.mark.parametrize("value", [0, -1])
def test_registry_non_positive_base_memory_rejected(value: float) -> None:
    bad = copy.deepcopy(_load("benchbox.core", "benchmark_registry.yaml"))
    first = next(iter(bad["benchmark_metadata"]))
    bad["benchmark_metadata"][first]["base_memory_gb"] = value
    with pytest.raises(ValidationError):
        BenchmarkRegistryCatalog.model_validate(bad)


def test_registry_unknown_field_rejected() -> None:

    bad = copy.deepcopy(_load("benchbox.core", "benchmark_registry.yaml"))
    first = next(iter(bad["benchmark_metadata"]))
    bad["benchmark_metadata"][first]["unexpected_extra_key"] = bad["benchmark_metadata"][first]["category"]
    with pytest.raises(ValidationError):
        BenchmarkRegistryCatalog.model_validate(bad)


def test_query_catalog_missing_sql_rejected() -> None:
    bad = {"QUERIES": {"q1": {"id": "1", "name": "n", "description": "d", "category": "c"}}}
    with pytest.raises(ValidationError):
        StaticQueryCatalog.model_validate(bad)


def test_query_catalog_typoed_field_rejected() -> None:
    bad = {
        "QUERIES": {"q1": {"id": "1", "name": "n", "description": "d", "category": "c", "sql": "SELECT 1", "sqll": "x"}}
    }
    with pytest.raises(ValidationError):
        StaticQueryCatalog.model_validate(bad)


def test_benchmark_specs_catalog_is_registered() -> None:
    assert ("benchbox.core.results", "benchmark_specs.yaml") in CATALOG_SCHEMAS


def test_benchmark_specs_wrong_type_rejected() -> None:
    bad = copy.deepcopy(_load("benchbox.core.results", "benchmark_specs.yaml"))
    first = next(iter(bad["benchmark_specs"]))
    bad["benchmark_specs"][first]["min_success_rate"] = "1.0"
    with pytest.raises(ValidationError):
        BenchmarkSpecsCatalog.model_validate(bad)


