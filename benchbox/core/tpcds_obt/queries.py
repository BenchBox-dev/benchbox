from __future__ import annotations

import re
from typing import Any

from benchbox.core.query_catalog_base import QuerySkippedError
from benchbox.core.tpcds.c_tools import DSQGenBinary
from benchbox.core.tpcds_obt.catalog.loader import PLACEHOLDER_PATTERN, OBTQuery, load_catalog, load_exclusions


def __getattr__(name: str) -> Any:
    if name == "CONVERTIBLE_QUERY_IDS":
        return tuple(sorted(load_catalog()))
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class TPCDSOBTQueryManager:
    def __init__(self, dsqgen: DSQGenBinary | None = None) -> None:
        self._dsqgen = dsqgen
        self._drawn: dict[tuple[int, int | None, float, int], dict[str, str]] = {}

    def get_query(
        self,
        query_id: int | str,
        parameters: dict[str, Any] | None = None,
        *,
        seed: int | None = None,
        scale_factor: float = 1.0,
        stream_id: int = 0,
    ) -> str:
        query = self._get_query(query_id)
        values = {**self.drawn_parameters(query.id, seed=seed, scale_factor=scale_factor, stream_id=stream_id)}
        for name, value in (parameters or {}).items():
            base, _, index = name.partition(".")
            if index:
                values[f"{base}.{int(index):02d}"] = str(value)
            else:
                values.update({key: str(value) for key in values if key.split(".")[0] == base})
                values[f"{base}.01"] = str(value)
        return self._render(query, values)

    @staticmethod
    def _render(query: OBTQuery, values: dict[str, str]) -> str:
        def substitute(match: re.Match[str]) -> str:
            name = match.group(1)
            key = f"{name}.{int(match.group(2) or 1):02d}"
            if name not in query.parameters:
                return match.group(0)
            if key not in values:
                raise ValueError(f"Query {query.id} has no value for parameter {key}")
            value = str(values[key])
            if name in query.identifier_parameters:
                return ", ".join(
                    query.identifier_map.get(item.strip().lower(), item.strip()) for item in value.split(",")
                )
            return value

        return PLACEHOLDER_PATTERN.sub(substitute, query.sql)

    def drawn_parameters(
        self, query_id: int, *, seed: int | None = None, scale_factor: float = 1.0, stream_id: int = 0
    ) -> dict[str, str]:
        key = (query_id, seed, float(scale_factor), stream_id)
        if key not in self._drawn:
            if self._dsqgen is None:
                self._dsqgen = DSQGenBinary()
            logged = self._dsqgen.generate_parameter_log(
                query_id, seed=seed, scale_factor=scale_factor, stream_id=stream_id
            )
            self._drawn[key] = dict(logged.substitutions)
        return self._drawn[key]

    def get_template(self, query_id: int | str) -> str:
        return self._get_query(query_id).sql

    def get_queries(
        self, parameters: dict[str, Any] | None = None, *, seed: int | None = None, scale_factor: float = 1.0
    ) -> dict[int, str]:
        return {
            qid: self.get_query(qid, parameters, seed=seed, scale_factor=scale_factor) for qid in self.list_query_ids()
        }

    def list_query_ids(self) -> list[int]:
        return sorted(load_catalog())

    def _get_query(self, query_id: int | str) -> OBTQuery:
        qid_int = self._normalize_id(query_id)
        query = load_catalog().get(qid_int)
        if query is None:
            exclusion = load_exclusions().get(qid_int)
            if exclusion is not None:
                raise QuerySkippedError(f"TPC-DS OBT does not provide query {qid_int}: {exclusion.reason}")
            raise ValueError(f"Unknown query id: {query_id}")
        return query

    def _normalize_id(self, query_id: int | str) -> int:
        if isinstance(query_id, int):
            return query_id
        try:
            return int(query_id)
        except ValueError as exc:
            raise ValueError(f"Unknown query id: {query_id}") from exc


__all__ = ["TPCDSOBTQueryManager"]
