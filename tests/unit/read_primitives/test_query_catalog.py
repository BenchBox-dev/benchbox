from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from benchbox.core.read_primitives.catalog import loader
from benchbox.core.read_primitives.catalog.loader import (
    PrimitiveCatalog,
    PrimitiveQuery,
    PrimitivesCatalogError,
    load_primitives_catalog,
)
from benchbox.core.read_primitives.queries import ReadPrimitivesQueryManager

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]


def test_load_primitives_catalog_success() -> None:

    catalog = load_primitives_catalog()

    assert isinstance(catalog, PrimitiveCatalog)
    assert catalog.version >= 1
    assert "aggregation_distinct" in catalog.queries
    entry = catalog.queries["aggregation_distinct"]
    assert isinstance(entry, PrimitiveQuery)
    assert entry.category == "aggregation"
    assert entry.sql.strip().startswith("SELECT COUNT(DISTINCT o_custkey)")


def test_query_manager_category_index() -> None:

    manager = ReadPrimitivesQueryManager()

    aggregation = manager.get_queries_by_category("aggregation")
    window = manager.get_queries_by_category("WINDOW")

    assert "aggregation_distinct" in aggregation
    assert aggregation["aggregation_distinct"].startswith("\nSELECT COUNT(DISTINCT o_custkey)")
    assert "window_growing_frame" in window
    assert manager.get_query_categories()[0] == "aggregation"


def test_load_primitives_catalog_duplicate_id(monkeypatch: pytest.MonkeyPatch) -> None:

    duplicate_yaml = """
version: 1
queries:
  - id: duplicate
    category: aggregation
    sql: "SELECT 1;"
  - id: duplicate
    category: aggregation
    sql: "SELECT 2;"
"""

    class DummyResource:
        def open(self, mode: str = "r", encoding: str | None = None):
            return io.StringIO(duplicate_yaml)

    monkeypatch.setattr(
        loader.resources,
        "files",
        lambda package: SimpleNamespace(joinpath=lambda filename: DummyResource()),
    )

    with pytest.raises(PrimitivesCatalogError, match="Duplicate query id"):
        load_primitives_catalog()


