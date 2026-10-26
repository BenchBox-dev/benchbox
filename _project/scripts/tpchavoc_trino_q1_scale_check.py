from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
import urllib.request
from typing import Any

from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark

LINEITEM_COLUMNS = (
    "orderkey",
    "partkey",
    "suppkey",
    "linenumber",
    "quantity",
    "extendedprice",
    "discount",
    "tax",
    "returnflag",
    "linestatus",
    "shipdate",
)
RELATIVE_TOLERANCE = 1e-8
SCALE_SCHEMAS = {
    0.01: "sf0.01",
    0.1: "sf0.1",
    1.0: "sf1",
    2.0: "sf2",
    3.0: "sf3",
    5.0: "sf5",
    10.0: "sf10",
    100.0: "sf100",
}


class TrinoClient:
    def __init__(self, server: str, user: str, catalog: str) -> None:
        self.server = server.rstrip("/")
        self.user = user
        self.catalog = catalog

    def _request(self, url: str, data: bytes | None, schema: str | None) -> dict[str, Any]:
        headers = {"X-Trino-User": self.user, "X-Trino-Catalog": self.catalog}
        if schema:
            headers["X-Trino-Schema"] = schema
        request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
        with urllib.request.urlopen(request, timeout=3600) as response:
            return json.loads(response.read())

    def info(self) -> dict[str, Any]:
        return self._request(f"{self.server}/v1/info", None, None)

    def peak_memory_bytes(self, query_id: str) -> int:
        info = self._request(f"{self.server}/v1/query/{query_id}", None, None)
        return int(str(info["queryStats"]["peakUserMemoryReservation"]).removesuffix("B"))

    def execute(self, sql: str, schema: str | None = None) -> dict[str, Any]:
        started = time.monotonic()
        payload = self._request(f"{self.server}/v1/statement", sql.encode("utf-8"), schema)
        rows: list[list[Any]] = []
        columns: list[str] = []
        while True:
            if "error" in payload:
                error = payload["error"]
                return {
                    "ok": False,
                    "error_name": error.get("errorName"),
                    "error_message": error.get("message"),
                    "query_id": payload.get("id"),
                    "stats": payload.get("stats", {}),
                    "peak_memory_bytes": self.peak_memory_bytes(payload["id"]),
                    "wall_seconds": time.monotonic() - started,
                }
            if "columns" in payload and not columns:
                columns = [column["name"] for column in payload["columns"]]
            rows.extend(payload.get("data", []))
            next_uri = payload.get("nextUri")
            if not next_uri:
                break
            payload = self._request(next_uri, None, schema)
        stats = payload.get("stats", {})
        return {
            "ok": True,
            "query_id": payload.get("id"),
            "columns": columns,
            "rows": rows,
            "stats": stats,
            "peak_memory_bytes": self.peak_memory_bytes(payload["id"]),
            "wall_seconds": time.monotonic() - started,
        }


def create_views(client: TrinoClient, scale: float, schema: str) -> str:
    target = f"q1_{schema.replace('.', '_')}"
    client.execute(f"CREATE SCHEMA IF NOT EXISTS memory.{target}")
    projection = ", ".join(f"{column} AS l_{column}" for column in LINEITEM_COLUMNS)
    result = client.execute(
        f'CREATE OR REPLACE VIEW memory.{target}.lineitem AS SELECT {projection} FROM tpch."{schema}".lineitem'
    )
    if not result["ok"]:
        raise RuntimeError(f"view creation failed: {result['error_message']}")
    return target


def max_relative_difference(canonical: list[list[Any]], variant: list[list[Any]]) -> float:
    if len(canonical) != len(variant):
        return math.inf
    worst = 0.0
    for left_row, right_row in zip(canonical, variant):
        if len(left_row) != len(right_row):
            return math.inf
        for left, right in zip(left_row, right_row):
            if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                scale = max(abs(left), abs(right), 1e-300)
                worst = max(worst, abs(left - right) / scale)
            elif left != right:
                return math.inf
    return worst


def measure(client: TrinoClient, sql: str, schema: str, repeats: int) -> dict[str, Any]:
    runs = [client.execute(sql, schema) for _ in range(repeats)]
    failures = [run for run in runs if not run["ok"]]
    if failures:
        failure = failures[0]
        return {
            "status": "FAIL",
            "error_name": failure["error_name"],
            "error_message": failure["error_message"],
            "peak_memory_bytes": failure["peak_memory_bytes"],
        }
    last = runs[-1]
    return {
        "status": "PASS",
        "rows": last["rows"],
        "row_count": len(last["rows"]),
        "peak_memory_bytes": max(run["peak_memory_bytes"] for run in runs),
        "peak_memory_bytes_runs": [run["peak_memory_bytes"] for run in runs],
        "wall_seconds": [round(run["wall_seconds"], 3) for run in runs],
        "cpu_millis": [run["stats"].get("cpuTimeMillis") for run in runs],
        "processed_rows": last["stats"].get("processedRows"),
    }


def repository_revision() -> str:
    completed = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    return completed.stdout.strip() or "unknown"


def repository_dirty() -> bool:
    completed = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True, check=False
    )
    return bool(completed.stdout.strip())


def run_scale(client: TrinoClient, benchmark: TPCHavocBenchmark, scale: float, repeats: int) -> dict[str, Any]:
    schema = SCALE_SCHEMAS[scale]
    target = create_views(client, scale, schema)
    count = client.execute(f'SELECT count(*) FROM tpch."{schema}".lineitem')["rows"][0][0]
    canonical_sql = benchmark.get_query(1, dialect="trino")
    variant_sql = benchmark.get_query("1_v7", dialect="trino")
    canonical = measure(client, canonical_sql, target, repeats)
    variant = measure(client, variant_sql, target, repeats)
    entry: dict[str, Any] = {
        "scale_factor": scale,
        "lineitem_rows": count,
        "canonical": {key: value for key, value in canonical.items() if key != "rows"},
        "variant_1_v7": {key: value for key, value in variant.items() if key != "rows"},
    }
    if canonical["status"] == "PASS" and variant["status"] == "PASS":
        difference = max_relative_difference(canonical["rows"], variant["rows"])
        entry["max_relative_difference"] = difference
        entry["equivalent"] = difference <= RELATIVE_TOLERANCE
        entry["result_groups"] = canonical["row_count"]
    else:
        entry["equivalent"] = False
    entry["status"] = "PASS" if entry["equivalent"] else "FAIL"
    return entry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the TPC-Havoc 1_v7 ARRAY_AGG rewrite at scale on Trino.")
    parser.add_argument("--server", default="http://localhost:8080")
    parser.add_argument("--user", default="benchbox")
    parser.add_argument("--catalog", default="memory")
    parser.add_argument("--scales", type=float, nargs="+", default=[1.0])
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)

    unknown = [scale for scale in args.scales if scale not in SCALE_SCHEMAS]
    if unknown:
        parser.error(f"unsupported scale factors {unknown}; choose from {sorted(SCALE_SCHEMAS)}")

    client = TrinoClient(args.server, args.user, args.catalog)
    info = client.info()
    benchmark = TPCHavocBenchmark(scale_factor=1.0)
    report = {
        "trino_version": info["nodeVersion"]["version"],
        "benchbox_revision": repository_revision(),
        "benchbox_tree_dirty": repository_dirty(),
        "relative_tolerance": RELATIVE_TOLERANCE,
        "repeats": args.repeats,
        "results": [run_scale(client, benchmark, scale, args.repeats) for scale in args.scales],
    }
    report["status"] = "PASS" if all(entry["status"] == "PASS" for entry in report["results"]) else "FAIL"
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    print(rendered)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
