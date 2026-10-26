# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import zlib
from datetime import datetime, timedelta
from typing import Any, Optional

import numpy as np

from benchbox.core.static_query_catalog import load_static_query_catalog

_CATALOG = load_static_query_catalog(__package__)
QUERIES: dict[str, dict[str, Any]] = _CATALOG["QUERIES"]
GREEN_QUERIES: dict[str, dict[str, Any]] = _CATALOG["GREEN_QUERIES"]
HVFHV_QUERIES: dict[str, dict[str, Any]] = _CATALOG["HVFHV_QUERIES"]
FHV_QUERIES: dict[str, dict[str, Any]] = _CATALOG["FHV_QUERIES"]
CROSS_TYPE_QUERIES: dict[str, dict[str, Any]] = _CATALOG["CROSS_TYPE_QUERIES"]


class NYCTaxiQueryManager:
    def __init__(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        seed: Optional[int] = None,
        include_green_queries: bool = False,
        include_hvfhv_queries: bool = False,
        include_fhv_queries: bool = False,
        include_cross_type_queries: bool = False,
    ) -> None:
        self.start_date = start_date or datetime(2019, 1, 1)
        self.end_date = end_date or datetime(2019, 12, 31)
        self.seed = int(seed) if seed is not None else int(np.random.SeedSequence().generate_state(1)[0])

        self.popular_zones = [132, 138, 161, 162, 163, 164, 186, 230, 234, 236, 237, 239, 261, 262, 263]

        self._active_queries: dict[str, Any] = dict(QUERIES)
        if include_green_queries:
            self._active_queries.update(GREEN_QUERIES)
        if include_hvfhv_queries:
            self._active_queries.update(HVFHV_QUERIES)
        if include_fhv_queries:
            self._active_queries.update(FHV_QUERIES)
        if include_cross_type_queries:
            self._active_queries.update(CROSS_TYPE_QUERIES)

    def get_query(
        self,
        query_id: str,
        params: Optional[dict[str, Any]] = None,
    ) -> str:
        if query_id not in self._active_queries:
            raise ValueError(f"Unknown query: {query_id}. Available: {list(self._active_queries.keys())}")

        query_def = self._active_queries[query_id]
        sql = query_def["sql"].strip()

        query_params = self._generate_params(query_def, params, query_id)

        return sql.format(**query_params)

    def _generate_params(
        self,
        query_def: dict[str, Any],
        overrides: Optional[dict[str, Any]] = None,
        query_id: Optional[str] = None,
    ) -> dict[str, Any]:
        params = {}
        rng = np.random.default_rng([self.seed, zlib.crc32((query_id or "").encode("utf-8"))])
        query_params = query_def.get("params", {})
        overrides = overrides or {}

        duration_days = query_params.get("duration_days", 30)

        dataset_days = (self.end_date - self.start_date).days
        max_offset = max(1, dataset_days - duration_days)
        offset_days = int(rng.integers(0, max_offset))

        start = self.start_date + timedelta(days=offset_days)
        end = start + timedelta(days=duration_days)

        params["start_date"] = start.strftime("%Y-%m-%d")
        params["end_date"] = end.strftime("%Y-%m-%d")

        params["zone_id"] = overrides.get(
            "zone_id",
            self.popular_zones[int(rng.integers(0, len(self.popular_zones)))],
        )

        params.update(overrides)

        return params

    def get_queries(self) -> dict[str, str]:
        return {qid: self.get_query(qid) for qid in self._active_queries}

    def get_query_info(self, query_id: str) -> dict[str, Any]:
        if query_id not in self._active_queries:
            raise ValueError(f"Unknown query: {query_id}")
        return self._active_queries[query_id]

    def get_queries_by_category(self, category: str) -> list[str]:
        return [qid for qid, qdef in self._active_queries.items() if qdef.get("category") == category]

    def get_categories(self) -> list[str]:
        return list({str(qdef["category"]) for qdef in self._active_queries.values()})

    def get_query_count(self) -> int:
        return len(self._active_queries)
