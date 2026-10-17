from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from benchbox.core.tpcds.c_tools import DSQGenBinary
from benchbox.core.tpcds_obt.manual_queries import MANUAL_QUERY_IDS, get_manual_query, render_manual_query
from benchbox.core.tpcds_obt.query_conversion import QueryConverter


def _load_query_specs() -> dict[str, Any]:
    with (Path(__file__).with_name("query_specs.yaml")).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


CONVERTIBLE_QUERY_IDS = tuple(_load_query_specs()["convertible_query_ids"])


class TPCDSOBTQueryManager:
    def __init__(self, converter: QueryConverter | None = None, dsqgen: DSQGenBinary | None = None) -> None:
        self.converter = converter or QueryConverter()
        self._converted = self._load_queries()
        self._manual_queries = self._load_manual_queries()
        self._dsqgen = dsqgen
        self._drawn: dict[tuple[int, int | None, float, int], dict[str, str]] = {}

    def _load_queries(self) -> dict[int, Any]:
        queries: dict[int, Any] = {}
        for qid in CONVERTIBLE_QUERY_IDS:
            if qid in MANUAL_QUERY_IDS:
                continue
            queries[qid] = self.converter.convert(qid)
        return queries

    def _load_manual_queries(self) -> dict[int, Any]:
        manual: dict[int, Any] = {}
        for qid in CONVERTIBLE_QUERY_IDS:
            if qid in MANUAL_QUERY_IDS:
                manual[qid] = get_manual_query(qid)
        return manual

    def get_query(
        self,
        query_id: int | str,
        parameters: dict[str, Any] | None = None,
        *,
        seed: int | None = None,
        scale_factor: float = 1.0,
        stream_id: int = 0,
    ) -> str:
        qid_int = self._normalize_id(query_id)
        converted = None if qid_int in self._manual_queries else self._get_converted(query_id)
        values = {**self.drawn_parameters(qid_int, seed=seed, scale_factor=scale_factor, stream_id=stream_id)}
        for name, value in (parameters or {}).items():
            base, _, index = name.partition(".")
            if index:
                values[f"{base}.{int(index):02d}"] = str(value)
            else:
                values.update({key: str(value) for key in values if key.split(".")[0] == base})
                values[f"{base}.01"] = str(value)
        if converted is None:
            names = self._manual_queries[qid_int].parameters
            return render_manual_query(qid_int, {name: values[f"{name}.01"] for name in names})
        return self.converter.render(converted, values)

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
        qid_int = self._normalize_id(query_id)
        if qid_int in self._manual_queries:
            return self._manual_queries[qid_int].template_sql
        return self._get_converted(query_id).template_sql

    def get_queries(
        self, parameters: dict[str, Any] | None = None, *, seed: int | None = None, scale_factor: float = 1.0
    ) -> dict[int, str]:
        return {
            qid: self.get_query(qid, parameters, seed=seed, scale_factor=scale_factor) for qid in self.list_query_ids()
        }

    def list_query_ids(self) -> list[int]:
        all_ids = set(self._converted.keys()) | set(self._manual_queries.keys())
        return sorted(all_ids)

    def _get_converted(self, query_id: int | str) -> Any:
        qid_int = self._normalize_id(query_id)
        if qid_int in self._manual_queries:
            raise ValueError(f"Query {query_id} is a manual query, use get_query() instead")
        if qid_int not in self._converted:
            raise ValueError(f"Unknown query id: {query_id}")
        return self._converted[qid_int]

    def _normalize_id(self, query_id: int | str) -> int:
        if isinstance(query_id, int):
            return query_id
        try:
            return int(query_id)
        except ValueError as exc:
            raise ValueError(f"Unknown query id: {query_id}") from exc


__all__ = ["CONVERTIBLE_QUERY_IDS", "TPCDSOBTQueryManager"]
