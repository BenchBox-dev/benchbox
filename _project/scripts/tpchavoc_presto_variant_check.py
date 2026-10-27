from __future__ import annotations

import argparse
import datetime
import decimal
import json
import math
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

from _project.scripts.tpchavoc_trino_q1_scale_check import RELATIVE_TOLERANCE, cell_difference
from benchbox.core.tpchavoc.benchmark import TPCHavocBenchmark
from benchbox.core.tpchavoc.variant_sets import VARIANT_REGISTRY
from benchbox.utils.clock import elapsed_seconds, mono_time

TPCH_TABLES = ("region", "nation", "supplier", "customer", "part", "partsupp", "orders", "lineitem")
DUCKDB_TYPES = {
    "bigint": "BIGINT",
    "integer": "INTEGER",
    "smallint": "SMALLINT",
    "double": "DOUBLE",
    "real": "DOUBLE",
    "date": "DATE",
}
SCALE_SCHEMAS = {0.1: "sf0.1", 1.0: "sf1", 2.0: "sf2", 3.0: "sf3", 5.0: "sf5"}
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class PrestoClient:
    def __init__(
        self, server: str, user: str, catalog: str, schema: str, session: dict[str, str] | None = None
    ) -> None:
        self.server = server.rstrip("/")
        self.user = user
        self.catalog = catalog
        self.schema = schema
        self.session = dict(session or {})

    def _request(self, url: str, data: bytes | None, schema: str | None = None) -> dict[str, Any]:
        headers = {
            "X-Presto-User": self.user,
            "X-Presto-Catalog": self.catalog,
            "X-Presto-Schema": schema or self.schema,
        }
        if self.session:
            headers["X-Presto-Session"] = ",".join(f"{name}={value}" for name, value in self.session.items())
        method = "POST" if data is not None else "GET"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=3600) as response:
            return json.loads(response.read())

    def info(self) -> dict[str, Any]:
        return self._request(f"{self.server}/v1/info", None)

    def query_info(self, query_id: str) -> dict[str, Any]:
        return self._request(f"{self.server}/v1/query/{query_id}", None)

    def peak_memory_bytes(self, query_id: str) -> dict[str, int | None]:
        stats = self.query_info(query_id)["queryStats"]
        return {
            name: _parse_bytes(stats.get(name)) for name in ("peakUserMemoryReservation", "peakTotalMemoryReservation")
        }

    def execute(self, sql: str, schema: str | None = None) -> dict[str, Any]:
        started = mono_time()
        payload = self._request(f"{self.server}/v1/statement", sql.encode("utf-8"), schema)
        rows: list[list[Any]] = []
        columns: list[dict[str, Any]] = []
        while True:
            if "error" in payload:
                error = payload["error"]
                return {
                    "ok": False,
                    "error_name": error.get("errorName"),
                    "error_message": error.get("message"),
                    "query_id": payload.get("id"),
                    "peak_memory": self.peak_memory_bytes(payload["id"]),
                    "wall_seconds": elapsed_seconds(started),
                }
            if "columns" in payload and not columns:
                columns = payload["columns"]
            rows.extend(payload.get("data", []))
            next_uri = payload.get("nextUri")
            if not next_uri:
                break
            payload = self._request(next_uri, None, schema)
        return {
            "ok": True,
            "query_id": payload.get("id"),
            "columns": columns,
            "rows": rows,
            "peak_memory": self.peak_memory_bytes(payload["id"]),
            "wall_seconds": elapsed_seconds(started),
        }


def _parse_bytes(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value)
    units = {"TB": 1 << 40, "GB": 1 << 30, "MB": 1 << 20, "kB": 1 << 10, "B": 1}
    for suffix, factor in units.items():
        if text.endswith(suffix):
            return int(float(text.removesuffix(suffix)) * factor)
    return int(float(text))


def git_output(*args: str) -> str | None:
    try:
        completed = subprocess.run(["git", *args], capture_output=True, text=True, check=False, cwd=REPOSITORY_ROOT)
    except OSError:
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def repository_dirty() -> bool | None:
    status = git_output("status", "--porcelain", "--untracked-files=no")
    return None if status is None else bool(status)


def variant_ids() -> list[str]:
    return [f"{query}_v{variant}" for query in sorted(VARIANT_REGISTRY) for variant in sorted(VARIANT_REGISTRY[query])]


def has_top_level_order_by(sql: str) -> bool:
    depth = 0
    quote: str | None = None
    lowered = sql.lower()
    index = 0
    while index < len(lowered):
        char = lowered[index]
        if quote:
            if char == quote:
                quote = None
        elif char in ("'", '"'):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0 and lowered.startswith("order", index) and (index == 0 or not lowered[index - 1].isalnum()):
            rest = lowered[index + 5 :].lstrip()
            if rest.startswith("by") and not rest[2:3].isalnum() and rest[2:3] != "_":
                return True
        index += 1
    return False


def normalize_cell(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return value


def sort_key(row: list[Any]) -> tuple:
    key = []
    for cell in row:
        if cell is None:
            key.append((0, ""))
        elif isinstance(cell, bool):
            key.append((1, str(cell)))
        elif isinstance(cell, (int, float)):
            key.append((2, f"{float(cell):+.5e}" if math.isfinite(float(cell)) else str(cell)))
        else:
            key.append((3, str(cell)))
    return tuple(key)


def cells_equal(left: Any, right: Any) -> float:
    if left is None or right is None:
        return 0.0 if left is right else math.inf
    if isinstance(left, str) and isinstance(right, (int, float)) and not isinstance(right, bool):
        try:
            left = float(left) if "." in left or "e" in left.lower() else int(left)
        except ValueError:
            return math.inf
    return cell_difference(left, right)


def row_difference(left_row: list[Any], right_row: list[Any]) -> float:
    return max((cells_equal(a, b) for a, b in zip(left_row, right_row)), default=0.0)


def worst_difference(
    left_rows: list[list[Any]], right_rows: list[list[Any]], tolerance: float
) -> tuple[float, int | None]:
    largest = 0.0
    first: int | None = None
    for position, (left_row, right_row) in enumerate(zip(left_rows, right_rows)):
        difference = row_difference(left_row, right_row)
        if difference > tolerance and first is None:
            first = position
        largest = max(largest, difference)
    return largest, first


def sorted_worst_difference(
    left_rows: list[list[Any]], right_rows: list[list[Any]], tolerance: float
) -> tuple[float, int | None]:
    largest, first = worst_difference(sorted(left_rows, key=sort_key), sorted(right_rows, key=sort_key), tolerance)
    if first is None:
        return largest, None
    left_groups: dict[tuple, list[list[Any]]] = {}
    right_groups: dict[tuple, list[list[Any]]] = {}
    for rows, groups in ((left_rows, left_groups), (right_rows, right_groups)):
        for row in rows:
            groups.setdefault(sort_key(row), []).append(row)
    left_sizes = {key: len(rows) for key, rows in left_groups.items()}
    right_sizes = {key: len(rows) for key, rows in right_groups.items()}
    if left_sizes != right_sizes:
        return largest, first
    matched = 0.0
    for key, group in left_groups.items():
        remaining = list(right_groups[key])
        for row in group:
            index, difference = min(
                ((position, row_difference(row, other)) for position, other in enumerate(remaining)),
                key=lambda pair: pair[1],
            )
            if difference > tolerance:
                return largest, first
            matched = max(matched, difference)
            remaining.pop(index)
    return matched, None


def compare_results(
    reference: list[list[Any]], candidate: list[list[Any]], ordered: bool, tolerance: float = RELATIVE_TOLERANCE
) -> dict[str, Any]:
    reference = [[normalize_cell(cell) for cell in row] for row in reference]
    candidate = [[normalize_cell(cell) for cell in row] for row in candidate]
    outcome: dict[str, Any] = {"reference_rows": len(reference), "candidate_rows": len(candidate)}
    if len(reference) != len(candidate):
        return {**outcome, "status": "MISMATCH", "reason": "row count differs", "max_relative_difference": math.inf}
    reference_columns = {len(row) for row in reference}
    candidate_columns = {len(row) for row in candidate}
    if reference_columns != candidate_columns:
        return {**outcome, "status": "MISMATCH", "reason": "column count differs", "max_relative_difference": math.inf}

    if ordered:
        largest, first = worst_difference(reference, candidate, tolerance)
        if first is None:
            return {**outcome, "status": "PASS", "compare": "ordered", "max_relative_difference": largest}
        sorted_largest, sorted_first = sorted_worst_difference(reference, candidate, tolerance)
        if sorted_first is None:
            return {
                **outcome,
                "status": "ORDER_DIFFERS",
                "compare": "ordered",
                "reason": "same rows, different order",
                "first_difference_row": first,
                "max_relative_difference": sorted_largest,
            }
        return {
            **outcome,
            "status": "MISMATCH",
            "compare": "ordered",
            "reason": "cell values differ",
            "first_difference_row": first,
            "max_relative_difference": largest,
        }
    largest, first = sorted_worst_difference(reference, candidate, tolerance)
    if first is None:
        return {**outcome, "status": "PASS", "compare": "unordered", "max_relative_difference": largest}
    return {
        **outcome,
        "status": "MISMATCH",
        "compare": "unordered",
        "reason": "cell values differ",
        "first_difference_row": first,
        "max_relative_difference": largest,
    }


def duckdb_type(presto_type: str) -> str:
    lowered = presto_type.lower()
    if lowered.startswith(("varchar", "char")):
        return "VARCHAR"
    if lowered not in DUCKDB_TYPES:
        raise RuntimeError(f"unmapped Presto type {presto_type}")
    return DUCKDB_TYPES[lowered]


def load_duckdb(client: PrestoClient, schema: str):
    import duckdb

    connection = duckdb.connect()
    counts = {}
    for table in TPCH_TABLES:
        described = client.execute(f'DESCRIBE tpch."{schema}".{table}')
        names = [row[0] for row in described["rows"]]
        types = [duckdb_type(row[1]) for row in described["rows"]]
        columns = ", ".join(f'"{name}" {kind}' for name, kind in zip(names, types))
        connection.execute(f"CREATE TABLE {table} ({columns})")
        data = client.execute(f'SELECT * FROM tpch."{schema}".{table}')
        placeholders = ", ".join("?" for _ in names)
        connection.executemany(f"INSERT INTO {table} VALUES ({placeholders})", data["rows"])
        counts[table] = len(data["rows"])
    return connection, counts


def rerun_statuses(client: PrestoClient, sql: str, reference: list[list[Any]], ordered: bool, reruns: int) -> list[str]:
    statuses = []
    for _ in range(reruns):
        execution = client.execute(sql.strip().rstrip(";"))
        statuses.append(
            compare_results(reference, execution["rows"], ordered)["status"] if execution["ok"] else "ERROR"
        )
    return statuses


def run_variants(
    client: PrestoClient, schema: str, scale_factor: float, only: list[str] | None, reruns: int
) -> dict[str, Any]:
    connection, counts = load_duckdb(client, schema)
    benchmark = TPCHavocBenchmark(scale_factor=scale_factor)
    queries: list[tuple[str, str]] = [(f"{number}", "canonical") for number in range(1, 23)]
    queries += [(identifier, "variant") for identifier in variant_ids()]
    if only:
        queries = [item for item in queries if item[0] in only]
    results = []
    for identifier, kind in queries:
        key: Any = int(identifier) if kind == "canonical" else identifier
        entry: dict[str, Any] = {"id": identifier, "kind": kind}
        duckdb_sql = benchmark.get_query(key)
        presto_sql = benchmark.get_query(key, dialect="presto")
        entry["presto_differs_from_duckdb_text"] = " ".join(duckdb_sql.split()) != " ".join(presto_sql.split())
        entry["presto_sql"] = presto_sql
        try:
            reference = connection.execute(duckdb_sql.strip().rstrip(";")).fetchall()
        except Exception as error:
            entry.update(status="REFERENCE_ERROR", error=f"{type(error).__name__}: {error}")
            results.append(entry)
            continue
        execution = client.execute(presto_sql.strip().rstrip(";"))
        if not execution["ok"]:
            entry.update(status="ERROR", error_name=execution["error_name"], error=execution["error_message"])
            results.append(entry)
            continue
        ordered = has_top_level_order_by(duckdb_sql)
        outcome = compare_results([list(row) for row in reference], execution["rows"], ordered)
        entry.update(outcome)
        entry["ordered_by_query"] = ordered
        entry["wall_seconds"] = round(execution["wall_seconds"], 3)
        if entry["status"] != "PASS":
            entry["reference_head"] = [list(map(str, row)) for row in reference[:3]]
            entry["presto_head"] = [list(map(str, row)) for row in execution["rows"][:3]]
            entry["rerun_statuses"] = rerun_statuses(
                client, presto_sql, [list(row) for row in reference], ordered, reruns
            )
        results.append(entry)
    return {"table_row_counts": counts, "results": results}


def parse_session(items: list[str] | None) -> dict[str, str]:
    session = {}
    for item in items or []:
        name, separator, value = item.partition("=")
        if not separator or not name:
            raise ValueError(f"session property must be NAME=VALUE: {item!r}")
        session[name] = value
    return session


def run_statements(client: PrestoClient, statements: list[str], schema: str | None = None) -> list[dict[str, Any]]:
    outcomes = []
    for statement in statements:
        execution = client.execute(statement.strip().rstrip(";"), schema)
        outcome: dict[str, Any] = {"sql": statement, "ok": execution["ok"]}
        if execution["ok"]:
            outcome["row_count"] = len(execution["rows"])
            outcome["rows_head"] = execution["rows"][:5]
        else:
            outcome["error_name"] = execution["error_name"]
            outcome["error_message"] = execution["error_message"]
        outcome["wall_seconds"] = round(execution["wall_seconds"], 3)
        outcomes.append(outcome)
    return outcomes


def run_scale(client: PrestoClient, benchmark: TPCHavocBenchmark, scale: float, repeats: int) -> dict[str, Any]:
    schema = SCALE_SCHEMAS[scale]
    count = client.execute(f'SELECT count(*) FROM tpch."{schema}".lineitem', schema)["rows"][0][0]
    outputs = {}
    for name, sql in (
        ("canonical", benchmark.get_query(1, dialect="presto")),
        ("variant_1_v7", benchmark.get_query("1_v7", dialect="presto")),
    ):
        runs = [client.execute(sql, schema) for _ in range(repeats)]
        failure = next((run for run in runs if not run["ok"]), None)
        if failure:
            outputs[name] = {
                "status": "FAIL",
                "error_name": failure["error_name"],
                "error_message": failure["error_message"],
                "peak_memory": failure["peak_memory"],
            }
            continue
        outputs[name] = {
            "status": "PASS",
            "rows": runs[-1]["rows"],
            "row_count": len(runs[-1]["rows"]),
            "peak_user_memory_bytes_runs": [run["peak_memory"]["peakUserMemoryReservation"] for run in runs],
            "peak_total_memory_bytes_runs": [run["peak_memory"]["peakTotalMemoryReservation"] for run in runs],
            "wall_seconds": [round(run["wall_seconds"], 3) for run in runs],
        }
    entry: dict[str, Any] = {
        "scale_factor": scale,
        "lineitem_rows": count,
        "canonical": {key: value for key, value in outputs["canonical"].items() if key != "rows"},
        "variant_1_v7": {key: value for key, value in outputs["variant_1_v7"].items() if key != "rows"},
    }
    if outputs["canonical"]["status"] == "PASS" and outputs["variant_1_v7"]["status"] == "PASS":
        comparison = compare_results(outputs["canonical"]["rows"], outputs["variant_1_v7"]["rows"], True)
        entry["comparison"] = comparison
        entry["status"] = comparison["status"]
    else:
        entry["status"] = "FAIL"
    return entry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run TPC-Havoc variants on PrestoDB and compare with DuckDB.")
    parser.add_argument("mode", choices=("variants", "scale", "sql"))
    parser.add_argument("--server", default="http://localhost:18081")
    parser.add_argument("--user", default="benchbox")
    parser.add_argument("--schema", default="tiny")
    parser.add_argument("--scale-factor", type=float, default=0.01)
    parser.add_argument("--scales", type=float, nargs="+", default=[1.0])
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--reruns", type=int, default=4)
    parser.add_argument("--session", action="append", default=[])
    parser.add_argument("--statement", action="append", default=[])
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)

    client = PrestoClient(args.server, args.user, "tpch", args.schema, parse_session(args.session))
    info = client.info()
    report: dict[str, Any] = {
        "presto_version": info["nodeVersion"]["version"],
        "benchbox_revision": git_output("rev-parse", "HEAD") or "unknown",
        "benchbox_tree_dirty": repository_dirty(),
        "relative_tolerance": RELATIVE_TOLERANCE,
        "session": client.session,
    }
    if args.mode == "variants":
        report.update(run_variants(client, args.schema, args.scale_factor, args.only, args.reruns))
        counts: dict[str, int] = {}
        for entry in report["results"]:
            counts[entry["status"]] = counts.get(entry["status"], 0) + 1
        report["status_counts"] = counts
        success = set(counts) == {"PASS"}
    elif args.mode == "sql":
        report["results"] = run_statements(client, args.statement, args.schema)
        success = all(entry["ok"] for entry in report["results"])
    else:
        benchmark = TPCHavocBenchmark(scale_factor=1.0)
        report["repeats"] = args.repeats
        report["results"] = [run_scale(client, benchmark, scale, args.repeats) for scale in args.scales]
        success = all(entry["status"] == "PASS" for entry in report["results"])
    rendered = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2, default=str))
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
