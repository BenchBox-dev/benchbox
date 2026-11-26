from __future__ import annotations

from benchbox.core.query_catalog_base import BaseQueryCatalogMixin
from benchbox.core.read_primitives.catalog import PrimitiveQuery, load_primitives_catalog


class ReadPrimitivesQueryManager(BaseQueryCatalogMixin):
    def __init__(self) -> None:
        catalog = load_primitives_catalog()
        self._catalog_version = catalog.version
        self._entries = catalog.queries
        self._queries: dict[str, str] = {query_id: entry.sql for query_id, entry in catalog.queries.items()}
        self._category_index = self._build_category_index(catalog.queries)

    @staticmethod
    def _build_category_index(queries: dict[str, PrimitiveQuery]) -> dict[str, list[str]]:
        category_index: dict[str, list[str]] = {}
        for query_id, entry in queries.items():
            category = entry.category.lower()
            category_index.setdefault(category, []).append(query_id)
        return category_index

    @property
    def catalog_version(self) -> int:

        return self._catalog_version

    def get_all_queries(self) -> dict[str, str]:

        return self._queries.copy()

    def get_queries_by_category(self, category: str) -> dict[str, str]:

        normalized = category.lower()
        query_ids = self._category_index.get(normalized, [])
        return {query_id: self._queries[query_id] for query_id in query_ids}

    def get_query_categories(self) -> list[str]:

        return sorted(self._category_index.keys())

    def get_query_category(self, query_id: str) -> str:
        try:
            entry = self._entries[query_id]
            return entry.category.lower()
        except KeyError as exc:
            raise ValueError(f"Invalid query ID: {query_id}") from exc


__all__ = ["ReadPrimitivesQueryManager"]
