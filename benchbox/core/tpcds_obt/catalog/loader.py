from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

CATALOG_FILENAME = "queries.yaml"
CATALOG_VERSION = 1
PLACEHOLDER_PATTERN = re.compile(r"\[([A-Za-z_][A-Za-z0-9_]*)(?:\.(\d+))?\]")


class OBTCatalogError(RuntimeError):
    pass


@dataclass(frozen=True)
class OBTQuery:
    id: int
    template: str
    template_sha256: str
    parameters: dict[str, tuple[int, ...]]
    identifier_parameters: tuple[str, ...]
    identifier_map: dict[str, str]
    sql: str


@dataclass(frozen=True)
class OBTExclusion:
    id: int
    template: str
    template_sha256: str
    reason: str


def _load_payload() -> dict[str, Any]:
    try:
        with resources.files(__package__).joinpath(CATALOG_FILENAME).open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise OBTCatalogError(f"Unable to read the TPC-DS OBT query catalog: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != CATALOG_VERSION:
        raise OBTCatalogError(f"TPC-DS OBT query catalog must be a mapping with version {CATALOG_VERSION}")
    return payload


def _parse_entry(index: int, entry: object) -> OBTQuery:
    if not isinstance(entry, dict):
        raise OBTCatalogError(f"Catalog entry at index {index} must be a mapping")
    query_id = entry.get("id")
    if not isinstance(query_id, int):
        raise OBTCatalogError(f"Catalog entry at index {index} is missing an integer 'id'")
    for key in ("template", "template_sha256", "sql"):
        if not isinstance(entry.get(key), str) or not entry[key].strip():
            raise OBTCatalogError(f"Catalog entry {query_id} is missing a non-empty '{key}'")
    parameters = {name: tuple(indices) for name, indices in (entry.get("parameters") or {}).items()}
    identifier_parameters = tuple(entry.get("identifier_parameters") or ())
    identifier_map = dict(entry.get("identifier_map") or {})
    unknown = set(identifier_parameters) - set(parameters)
    if unknown:
        raise OBTCatalogError(f"Catalog entry {query_id} marks undeclared parameters as identifiers: {sorted(unknown)}")
    sql: str = entry["sql"]
    used = {(name, int(idx or 1)) for name, idx in PLACEHOLDER_PATTERN.findall(sql)}
    declared = {(name, idx) for name, indices in parameters.items() for idx in indices}
    if used != declared:
        raise OBTCatalogError(
            f"Catalog entry {query_id} placeholders differ from its declared parameters: {sorted(used ^ declared)}"
        )
    return OBTQuery(
        id=query_id,
        template=entry["template"],
        template_sha256=entry["template_sha256"],
        parameters=parameters,
        identifier_parameters=identifier_parameters,
        identifier_map=identifier_map,
        sql=sql,
    )


@lru_cache(maxsize=1)
def load_catalog() -> dict[int, OBTQuery]:
    queries: dict[int, OBTQuery] = {}
    for index, entry in enumerate(_load_payload().get("queries") or []):
        query = _parse_entry(index, entry)
        if query.id in queries:
            raise OBTCatalogError(f"Duplicate catalog entry for query {query.id}")
        queries[query.id] = query
    overlap = queries.keys() & load_exclusions().keys()
    if overlap:
        raise OBTCatalogError(f"Queries are both catalogued and excluded: {sorted(overlap)}")
    return queries


@lru_cache(maxsize=1)
def load_exclusions() -> dict[int, OBTExclusion]:
    exclusions: dict[int, OBTExclusion] = {}
    for entry in _load_payload().get("excluded") or []:
        for key in ("id", "template", "template_sha256", "reason"):
            if not entry.get(key):
                raise OBTCatalogError(f"Excluded entry {entry!r} is missing '{key}'")
        if entry["id"] in exclusions:
            raise OBTCatalogError(f"Duplicate exclusion for query {entry['id']}")
        exclusions[entry["id"]] = OBTExclusion(
            id=entry["id"],
            template=entry["template"],
            template_sha256=entry["template_sha256"],
            reason=entry["reason"],
        )
    return exclusions


__all__ = ["OBTCatalogError", "OBTExclusion", "OBTQuery", "PLACEHOLDER_PATTERN", "load_catalog", "load_exclusions"]
