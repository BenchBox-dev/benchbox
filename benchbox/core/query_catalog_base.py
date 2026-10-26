# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

from __future__ import annotations

from typing import Protocol, runtime_checkable


class QuerySkippedError(ValueError):
    pass


@runtime_checkable
class CatalogEntry(Protocol):
    @property
    def sql(self) -> str: ...

    @property
    def variants(self) -> dict[str, str] | None: ...

    @property
    def skip_on(self) -> list[str] | None: ...


class BaseQueryCatalogMixin:
    _entries: dict[str, CatalogEntry]
    _queries: dict[str, str]

    def get_query(self, query_id: str, dialect: str | None = None) -> str:
        try:
            entry = self._entries[query_id]
        except KeyError as exc:
            available = ", ".join(sorted(self._queries.keys()))
            raise ValueError(f"Invalid query ID: {query_id}. Available: {available}") from exc

        if dialect and entry.skip_on:
            normalized_dialect = dialect.lower().strip()
            if normalized_dialect in entry.skip_on:
                raise QuerySkippedError(
                    f"Query '{query_id}' is not supported on dialect '{dialect}' (marked as skip_on: {entry.skip_on})"
                )

        if dialect and entry.variants:
            normalized_dialect = dialect.lower().strip()
            if normalized_dialect in entry.variants:
                return entry.variants[normalized_dialect]

        return self._queries[query_id]

    def has_variant(self, query_id: str, dialect: str) -> bool:
        entry = self._entries.get(query_id)
        if entry is None or not entry.variants:
            return False
        return dialect.lower().strip() in entry.variants


def is_native_dialect(source_dialect: str, target_dialect: str) -> bool:
    from benchbox.utils.dialect_utils import resolve_sql_dialect

    return resolve_sql_dialect(source_dialect) == resolve_sql_dialect(target_dialect)


class TranslatableQueryMixin:
    _source_dialect: str = "netezza"
    _native_source_passthrough: bool = False

    def translate_for_dialect(self, query_text: str, dialect: str | None) -> str:
        if not dialect:
            return query_text
        return self.translate_query_text(query_text, dialect)

    def translate_query_text(self, query_text: str, target_dialect: str) -> str:
        from benchbox.utils.dialect_utils import translate_sql_query

        if self._native_source_passthrough and is_native_dialect(self._source_dialect, target_dialect):
            return query_text
        return translate_sql_query(
            query=query_text,
            target_dialect=target_dialect,
            source_dialect=self._source_dialect,
        )


__all__ = [
    "BaseQueryCatalogMixin",
    "CatalogEntry",
    "QuerySkippedError",
    "TranslatableQueryMixin",
    "is_native_dialect",
]
