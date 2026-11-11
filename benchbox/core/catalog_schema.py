# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from importlib import resources
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidationError, field_validator

from benchbox.utils.printing import emit

SupportStatus = Literal["stable", "beta", "experimental", "repo_only", "deprecated", "document_only"]
Surface = Literal["public", "internal"]
_STRICT_MODEL_CONFIG = ConfigDict(extra="forbid", strict=True)


class CatalogSchemaError(ValueError):
    pass


class PresortColumn(BaseModel):
    model_config = _STRICT_MODEL_CONFIG

    name: str
    order: Literal["asc", "desc"] = "asc"


class BenchmarkMeta(BaseModel):
    model_config = _STRICT_MODEL_CONFIG

    display_name: str
    description: str
    category: str
    support_status: SupportStatus
    num_queries: int
    query_description: str
    supports_streams: bool
    default_scale: float
    scale_options: list[float]
    complexity: str
    estimated_time_range: list[float] = Field(min_length=2, max_length=2)
    base_memory_gb: float = Field(gt=0)
    data_source: str | None
    supports_dataframe: bool
    min_scale: float | None = None
    scale_policy: Literal["fixed"] | None = None
    surface: Surface | None = None
    data_manifest: str | None = None
    supports_statistics_phase: bool = False
    presort_table_configs: dict[str, list[PresortColumn]] | None = None

    @field_validator("estimated_time_range")
    @classmethod
    def _validate_estimated_time_range(cls, value: list[float]) -> list[float]:
        if any(item < 0 for item in value) or value[0] > value[1]:
            raise ValueError("estimated_time_range must contain non-negative ordered bounds")
        return value


class BenchmarkRegistryCatalog(BaseModel):
    model_config = _STRICT_MODEL_CONFIG

    category_order: list[str]
    benchmark_order: dict[str, list[str]]
    benchmark_class_names: dict[str, str]
    core_class_name_overrides: dict[str, str]
    data_source_probe_ids: list[str]
    tpc_official_scale_options: list[float]
    benchmark_metadata: dict[str, BenchmarkMeta]


class BenchmarkSpecEntry(BaseModel):
    model_config = _STRICT_MODEL_CONFIG

    benchmark_id: str
    unique_query_ids: list[str]
    min_unique_queries: int = 0
    min_success_rate: float = 1.0
    high_failure_expected: bool = False
    requires_tables_object: bool = True
    sf1_row_counts: dict[str, int] | None = None
    sf1_power_at_size_range: list[float] | None = Field(default=None, min_length=2, max_length=2)


class BenchmarkSpecsCatalog(BaseModel):
    model_config = _STRICT_MODEL_CONFIG

    legacy_aliases: dict[str, str]
    tpch_sf1_row_counts: dict[str, int]
    benchmark_specs: dict[str, BenchmarkSpecEntry]


class StaticQueryEntry(BaseModel):
    model_config = _STRICT_MODEL_CONFIG

    id: str
    name: str
    description: str
    category: str
    sql: str
    params: dict[str, object] | None = None


class StaticQueryCatalog(RootModel[dict[str, dict[str, StaticQueryEntry]]]):
    model_config = ConfigDict(strict=True)


CATALOG_SCHEMAS: dict[tuple[str, str], type[BaseModel]] = {
    ("benchbox.core", "benchmark_registry.yaml"): BenchmarkRegistryCatalog,
    ("benchbox.core.results", "benchmark_specs.yaml"): BenchmarkSpecsCatalog,
    ("benchbox.core.flightdata", "query_catalog.yaml"): StaticQueryCatalog,
    ("benchbox.core.nyctaxi", "query_catalog.yaml"): StaticQueryCatalog,
    ("benchbox.core.tsbs_devops", "query_catalog.yaml"): StaticQueryCatalog,
}


def validate_catalog(package: str, filename: str, model: type[BaseModel]) -> BaseModel:
    text = resources.files(package).joinpath(filename).read_text(encoding="utf-8")
    payload = yaml.safe_load(text)
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise CatalogSchemaError(f"{package}/{filename} failed schema validation:\n{exc}") from exc


def validate_all_catalogs() -> int:
    for (package, filename), model in CATALOG_SCHEMAS.items():
        validate_catalog(package, filename, model)
    return len(CATALOG_SCHEMAS)


def main() -> int:
    try:
        count = validate_all_catalogs()
    except CatalogSchemaError as exc:
        emit(f"catalog-schema-check FAILED:\n{exc}")
        return 1
    emit(f"catalog-schema-check OK: {count} migrated catalogs valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
