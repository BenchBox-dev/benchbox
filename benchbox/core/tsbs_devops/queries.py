# Copyright 2026 Joe Harris / BenchBox Project

# Licensed under the MIT License. See LICENSE file in the project root for details.

import zlib
from datetime import datetime, timedelta
from typing import Any, Optional

import numpy as np

from benchbox.core.static_query_catalog import load_static_query_catalog

QUERIES: dict[str, dict[str, Any]] = load_static_query_catalog(__package__)["QUERIES"]


class TSBSDevOpsQueryManager:
    def __init__(
        self,
        num_hosts: int = 100,
        start_time: Optional[datetime] = None,
        duration_days: int = 1,
        seed: Optional[int] = None,
    ) -> None:
        self.num_hosts = num_hosts
        self.start_time = start_time or datetime(2024, 1, 1)
        self.duration_days = duration_days
        self.seed = int(seed) if seed is not None else int(np.random.SeedSequence().generate_state(1)[0])

        self.hostnames = [f"host_{i}" for i in range(num_hosts)]
        self.regions = ["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1"]

    def get_query(
        self,
        query_id: str,
        params: Optional[dict[str, Any]] = None,
    ) -> str:
        if query_id not in QUERIES:
            raise ValueError(f"Unknown query: {query_id}. Available: {list(QUERIES.keys())}")

        query_def = QUERIES[query_id]
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

        duration_hours = query_params.get("duration_hours", 1)
        duration_minutes = query_params.get("duration_minutes", duration_hours * 60)

        total_dataset_hours = self.duration_days * 24
        max_offset_hours = max(1, total_dataset_hours - duration_hours)
        offset_hours = int(rng.integers(0, max_offset_hours))

        start = self.start_time + timedelta(hours=offset_hours)
        end = start + timedelta(minutes=duration_minutes)

        params["start_time"] = start.strftime("%Y-%m-%d %H:%M:%S")
        params["end_time"] = end.strftime("%Y-%m-%d %H:%M:%S")

        params["hostname"] = overrides.get(
            "hostname",
            self.hostnames[int(rng.integers(0, len(self.hostnames)))],
        )

        params["region"] = overrides.get(
            "region",
            self.regions[int(rng.integers(0, len(self.regions)))],
        )

        params.update(overrides)

        return params

    def get_queries(self) -> dict[str, str]:
        return {qid: self.get_query(qid) for qid in QUERIES}

    def get_query_info(self, query_id: str) -> dict[str, Any]:
        if query_id not in QUERIES:
            raise ValueError(f"Unknown query: {query_id}")
        return QUERIES[query_id]

    def get_queries_by_category(self, category: str) -> list[str]:
        return [qid for qid, qdef in QUERIES.items() if qdef.get("category") == category]

    @staticmethod
    def get_categories() -> list[str]:
        return list({str(qdef["category"]) for qdef in QUERIES.values()})

    @staticmethod
    def get_query_count() -> int:
        return len(QUERIES)
