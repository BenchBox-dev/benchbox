from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import multiprocessing
import os
import platform
import sqlite3
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

SCHEMA = "sql-transpiler-comparison-v1"
ENGINES = ("wrapper", "wrapper-date-columns", "sqlglot", "sqlglot-compiled", "polyglot")
BASELINE = "sqlglot"
NO_IDENTIFY_DIALECTS = frozenset(
    {"clickhouse", "postgres", "snowflake", "exasol", "questdb", "netezza", "greenplum", "vertica", "datafusion"}
)
TARGETS = ("sqlite", "duckdb")
WARM_ITERATIONS = 300
WARM_DISCARD = 30
COLD_RUNS = 7
COLD_PROBE_SQL = "SELECT s, SUM(v) AS tot FROM t WHERE d >= DATE '2000-01-01' GROUP BY s ORDER BY s"

SETUP_ROWS = [
    (1, "2000-02-29", 10, "a"),
    (2, "1900-03-01", 20, "b"),
    (3, "2024-12-31", 30, "a"),
    (4, "2024-12-31", 40, None),
    (5, None, 50, "b"),
]
SETUP_U = [(1, "x"), (2, "y"), (3, "z")]

DATES = [(2000,), (1900,), (2024,), (2024,), (None,)]

CASES: list[dict[str, Any]] = [
    {
        "id": "good-group-order",
        "kind": "known-good",
        "source": "postgres",
        "sql": "SELECT s, SUM(v) AS tot FROM t WHERE s IS NOT NULL GROUP BY s ORDER BY s",
        "expected": [("a", 40), ("b", 70)],
        "ordered": True,
    },
    {
        "id": "good-join",
        "kind": "known-good",
        "source": "postgres",
        "sql": "SELECT t.id, u.w FROM t JOIN u ON t.id = u.id ORDER BY t.id",
        "expected": [(1, "x"), (2, "y"), (3, "z")],
        "ordered": True,
    },
    {
        "id": "good-case",
        "kind": "known-good",
        "source": "duckdb",
        "sql": "SELECT id, CASE WHEN v > 25 THEN 'hi' ELSE 'lo' END FROM t ORDER BY id",
        "expected": [(1, "lo"), (2, "lo"), (3, "hi"), (4, "hi"), (5, "hi")],
        "ordered": True,
    },
    {
        "id": "good-cte-window",
        "kind": "known-good",
        "source": "duckdb",
        "sql": "WITH x AS (SELECT id, v FROM t) SELECT id, SUM(v) OVER (ORDER BY id) FROM x ORDER BY id",
        "expected": [(1, 10), (2, 30), (3, 60), (4, 100), (5, 150)],
        "ordered": True,
    },
    {
        "id": "good-cast-concat",
        "kind": "known-good",
        "source": "postgres",
        "sql": "SELECT CAST(v AS VARCHAR) || '-' || COALESCE(s, 'n') FROM t ORDER BY id",
        "expected": [("10-a",), ("20-b",), ("30-a",), ("40-n",), ("50-b",)],
        "ordered": True,
    },
    {
        "id": "good-limit-offset",
        "kind": "known-good",
        "source": "postgres",
        "sql": "SELECT id FROM t ORDER BY id LIMIT 2 OFFSET 1",
        "expected": [(2,), (3,)],
        "ordered": True,
    },
    {
        "id": "good-date-literal-compare",
        "kind": "known-good",
        "source": "postgres",
        "sql": "SELECT COUNT(*) FROM t WHERE d >= DATE '2000-01-01'",
        "expected": [(3,)],
        "ordered": True,
    },
    {
        "id": "problem-date-plus-interval-pg",
        "kind": "known-problem",
        "source": "postgres",
        "sql": "SELECT COUNT(*) FROM t WHERE d <= DATE '2000-01-01' + INTERVAL '90 days'",
        "expected": [(2,)],
        "ordered": True,
    },
    {
        "id": "problem-date-minus-interval-tpch",
        "kind": "known-problem",
        "source": "netezza",
        "sql": "SELECT COUNT(*) FROM t WHERE d <= DATE '2000-06-01' - INTERVAL '90' DAY",
        "expected": [(2,)],
        "ordered": True,
    },
    {
        "id": "problem-extract-year-pg",
        "kind": "known-problem",
        "source": "postgres",
        "sql": "SELECT EXTRACT(YEAR FROM d) FROM t ORDER BY id",
        "expected": DATES,
        "ordered": True,
    },
    {
        "id": "problem-extract-month-duckdb",
        "kind": "known-problem",
        "source": "duckdb",
        "sql": "SELECT EXTRACT(MONTH FROM d) FROM t ORDER BY id",
        "expected": [(2,), (3,), (12,), (12,), (None,)],
        "ordered": True,
    },
    {
        "id": "problem-date-part-duckdb",
        "kind": "known-problem",
        "source": "duckdb",
        "sql": "SELECT DATE_PART('year', d) FROM t ORDER BY id",
        "expected": DATES,
        "ordered": True,
    },
    {
        "id": "problem-order-by-all",
        "kind": "known-problem",
        "source": "duckdb",
        "sql": "SELECT s, v FROM t WHERE s IS NOT NULL ORDER BY ALL",
        "expected": [("a", 10), ("a", 30), ("b", 20), ("b", 50)],
        "ordered": True,
    },
    {
        "id": "problem-group-by-all",
        "kind": "known-problem",
        "source": "duckdb",
        "sql": "SELECT s, SUM(v) AS tot FROM t WHERE s IS NOT NULL GROUP BY ALL ORDER BY s",
        "expected": [("a", 40), ("b", 70)],
        "ordered": True,
    },
    {
        "id": "problem-ilike",
        "kind": "known-problem",
        "source": "postgres",
        "sql": "SELECT id FROM t WHERE s ILIKE 'A' ORDER BY id",
        "expected": [(1,), (3,)],
        "ordered": True,
    },
    {
        "id": "problem-pg-cast-operator",
        "kind": "known-problem",
        "source": "postgres",
        "sql": "SELECT v::text FROM t ORDER BY id",
        "expected": [("10",), ("20",), ("30",), ("40",), ("50",)],
        "ordered": True,
    },
    {
        "id": "problem-pg-integer-division",
        "kind": "known-problem",
        "source": "postgres",
        "sql": "SELECT 7 / 2",
        "expected": [(3,)],
        "ordered": True,
    },
    {
        "id": "problem-string-agg",
        "kind": "known-problem",
        "source": "postgres",
        "sql": "SELECT STRING_AGG(w, ',' ORDER BY w) FROM u",
        "expected": [("x,y,z",)],
        "ordered": True,
    },
]

WARM_QUERIES = [case for case in CASES if case["kind"] == "known-good"]


def _identify(target: str) -> bool:
    return target not in NO_IDENTIFY_DIALECTS


def _load_engine(name: str) -> tuple[Callable[[str, str, str], str], dict[str, Any]]:
    info: dict[str, Any] = {"engine": name, "python": platform.python_version(), "platform": platform.platform()}
    if name in ("wrapper", "wrapper-date-columns"):
        from benchbox.utils.dialect_utils import translate_sql_query

        manifest_path = Path(__file__).resolve().parents[3] / "benchbox" / "_vendor" / "sqlglot.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        info.update(
            version=manifest["version"],
            manifest_version=manifest["version"],
            fork_sha=manifest["fork_sha"],
            upstream_sha=manifest["upstream_sha"],
            wheel_sha256=manifest["sha256"],
            module_path=getattr(sys.modules.get(manifest["namespace"]), "__file__", None),
            compiled_modules=_compiled_modules(manifest["namespace"]),
            options={
                "entry_point": "benchbox.utils.dialect_utils.translate_sql_query",
                "strict": True,
                "identify": True,
                "date_columns": ["d"] if name == "wrapper-date-columns" else None,
            },
        )
        date_columns = ["d"] if name == "wrapper-date-columns" else None

        def translate(sql: str, source: str, target: str) -> str:
            return translate_sql_query(
                sql,
                target,
                source_dialect=source,
                strict=True,
                date_columns=date_columns if target == "sqlite" else None,
            )

        return translate, info
    if name in ("sqlglot", "sqlglot-compiled"):
        import sqlglot

        compiled = _compiled_modules("sqlglot")
        info.update(
            version=sqlglot.__version__,
            module_path=sqlglot.__file__,
            compiled_modules=compiled,
            distributions=_distributions("sqlglot"),
            options={"read": "source dialect", "identify": "False for NO_IDENTIFY_DIALECTS targets, else True"},
        )
        if name == "sqlglot-compiled" and not compiled:
            raise RuntimeError("sqlglot-compiled requested but no compiled sqlglot modules were imported")
        if name == "sqlglot" and compiled:
            raise RuntimeError("sqlglot (pure Python) requested but compiled sqlglot modules were imported")

        def translate(sql: str, source: str, target: str) -> str:
            return ";\n".join(sqlglot.transpile(sql, read=source, write=target, identify=_identify(target)))

        return translate, info
    if name == "polyglot":
        import polyglot_sql

        info.update(
            version=importlib.metadata.version("polyglot-sql"),
            module_path=polyglot_sql.__file__,
            distributions=_distributions("polyglot"),
            options={"read": "source dialect", "identify": "not offered by transpile()", "pretty": False},
        )

        def translate(sql: str, source: str, target: str) -> str:
            return ";\n".join(polyglot_sql.transpile(sql, read=source, write=target))

        return translate, info
    raise ValueError(f"unknown engine {name}")


def _compiled_modules(prefix: str) -> list[str]:
    result = []
    for module_name, module in sorted(sys.modules.items()):
        if module_name != prefix and not module_name.startswith(prefix + "."):
            continue
        path = getattr(module, "__file__", None) or ""
        if path.endswith((".so", ".pyd")):
            result.append(module_name)
    return result


def _distributions(fragment: str) -> list[str]:
    return sorted(
        f"{dist.metadata['Name']} {dist.version}"
        for dist in importlib.metadata.distributions()
        if fragment in dist.metadata["Name"].lower()
    )


def _fixture_sqlite() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        "CREATE TABLE t(id INTEGER, d TEXT, v INTEGER, s TEXT); CREATE TABLE u(id INTEGER, w TEXT);"
    )
    connection.executemany("INSERT INTO t VALUES (?, ?, ?, ?)", SETUP_ROWS)
    connection.executemany("INSERT INTO u VALUES (?, ?)", SETUP_U)
    return connection


def _fixture_duckdb() -> Any:
    import duckdb

    connection = duckdb.connect(":memory:")
    connection.execute(
        "CREATE TABLE t(id INTEGER, d DATE, v INTEGER, s VARCHAR); CREATE TABLE u(id INTEGER, w VARCHAR)"
    )
    connection.executemany("INSERT INTO t VALUES (?, CAST(? AS DATE), ?, ?)", SETUP_ROWS)
    connection.executemany("INSERT INTO u VALUES (?, ?)", SETUP_U)
    return connection


def _normalize(rows: list[tuple[Any, ...]], ordered: bool) -> list[tuple[Any, ...]]:
    normalized = [tuple(float(cell) if hasattr(cell, "as_tuple") else cell for cell in row) for row in rows]
    return normalized if ordered else sorted(normalized, key=repr)


def _run_case(
    translate: Callable[[str, str, str], str], case: dict[str, Any], target: str, connection: Any
) -> dict[str, Any]:
    record: dict[str, Any] = {"case": case["id"], "kind": case["kind"], "source": case["source"], "target": target}
    try:
        translated = translate(case["sql"], case["source"], target)
    except Exception as exc:
        record.update(outcome="translate_error", detail=f"{type(exc).__name__}: {str(exc)[:200]}")
        return record
    record["translated"] = translated
    try:
        statements = [part for part in translated.split(";\n") if part.strip()]
        rows: list[tuple[Any, ...]] = []
        for statement in statements:
            cursor = connection.execute(statement)
            rows = [tuple(row) for row in cursor.fetchall()]
    except Exception as exc:
        record.update(outcome="exec_error", detail=f"{type(exc).__name__}: {str(exc)[:200]}")
        return record
    actual = _normalize(rows, case["ordered"])
    expected = _normalize([tuple(row) for row in case["expected"]], case["ordered"])
    record["actual"] = [list(row) for row in actual]
    record["outcome"] = "match" if actual == expected else "wrong_result"
    return record


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def _warm(translate: Callable[[str, str, str], str]) -> dict[str, Any]:
    workload = [(case["sql"], case["source"], target) for case in WARM_QUERIES for target in TARGETS]
    samples: list[float] = []
    for iteration in range(WARM_ITERATIONS):
        for sql, source, target in workload:
            start = time.perf_counter_ns()
            translate(sql, source, target)
            elapsed = time.perf_counter_ns() - start
            if iteration >= WARM_DISCARD:
                samples.append(elapsed / 1000.0)
    return {
        "calls": len(samples),
        "iterations": WARM_ITERATIONS,
        "discarded_iterations": WARM_DISCARD,
        "median_us": round(statistics.median(samples), 1),
        "p95_us": round(_percentile(samples, 0.95), 1),
    }


def _cold_child(engine: str, queue: Any) -> None:
    start = time.perf_counter()
    translate, _ = _load_engine(engine)
    loaded = time.perf_counter()
    translate(COLD_PROBE_SQL, "postgres", "sqlite")
    done = time.perf_counter()
    queue.put({"import_ms": (loaded - start) * 1000, "first_translate_ms": (done - loaded) * 1000})


def _cold(engine: str) -> dict[str, Any]:
    context = multiprocessing.get_context("spawn")
    runs = []
    for _ in range(COLD_RUNS):
        queue = context.Queue()
        start = time.perf_counter()
        child = context.Process(target=_cold_child, args=(engine, queue))
        child.start()
        probe = queue.get(timeout=120)
        child.join()
        wall = (time.perf_counter() - start) * 1000
        runs.append({"process_wall_ms": wall, **probe})
    return {
        "runs": COLD_RUNS,
        "process_wall_median_ms": round(statistics.median(run["process_wall_ms"] for run in runs), 1),
        "process_wall_max_ms": round(max(run["process_wall_ms"] for run in runs), 1),
        "import_median_ms": round(statistics.median(run["import_ms"] for run in runs), 1),
        "first_translate_median_ms": round(statistics.median(run["first_translate_ms"] for run in runs), 1),
    }


def _git_revision() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            cwd=Path(__file__).resolve().parent,
        ).stdout.strip()
    except Exception:
        return "unknown"


def run_engine(engine: str, timing: bool) -> dict[str, Any]:
    translate, info = _load_engine(engine)
    import duckdb

    info["sqlite_version"] = sqlite3.sqlite_version
    info["duckdb_version"] = duckdb.__version__
    info["benchbox_revision"] = _git_revision()
    info["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    info["command"] = " ".join(sys.argv)
    info["load_average_before"] = os.getloadavg()
    connections = {"sqlite": _fixture_sqlite(), "duckdb": _fixture_duckdb()}
    results = [_run_case(translate, case, target, connections[target]) for case in CASES for target in TARGETS]
    counts: dict[str, int] = {}
    for record in results:
        counts[record["outcome"]] = counts.get(record["outcome"], 0) + 1
    report: dict[str, Any] = {"schema": SCHEMA, "info": info, "counts": counts, "results": results}
    if timing:
        report["load_average_before_timing"] = os.getloadavg()
        report["warm"] = _warm(translate)
        report["cold"] = _cold(engine)
    return report


def summarize(paths: list[str]) -> str:
    reports = [json.loads(Path(path).read_text(encoding="utf-8")) for path in paths]
    by_engine = {report["info"]["engine"]: report for report in reports}
    lines = [
        "engine | version | match | translate_error | exec_error | wrong_result | warm median/p95 us | cold wall ms"
    ]
    for engine in ENGINES:
        report = by_engine.get(engine)
        if report is None:
            continue
        counts = report["counts"]
        warm = report.get("warm")
        cold = report.get("cold")
        lines.append(
            " | ".join(
                [
                    engine,
                    str(report["info"].get("version")),
                    *(str(counts.get(name, 0)) for name in ("match", "translate_error", "exec_error", "wrong_result")),
                    f"{warm['median_us']}/{warm['p95_us']}" if warm else "n/a",
                    str(cold["process_wall_median_ms"]) if cold else "n/a",
                ]
            )
        )
    baseline = by_engine.get(BASELINE)
    if baseline is not None:
        base = {(r["case"], r["target"]): r["outcome"] for r in baseline["results"]}
        for engine in ENGINES:
            report = by_engine.get(engine)
            if report is None or engine == BASELINE:
                continue
            repairs = []
            regressions = []
            for record in report["results"]:
                key = (record["case"], record["target"])
                before, after = base[key], record["outcome"]
                if before != "match" and after == "match":
                    repairs.append(f"{key[0]}->{key[1]} ({before})")
                if before == "match" and after != "match":
                    regressions.append(f"{key[0]}->{key[1]} ({after})")
            lines.append(f"{engine} vs {BASELINE}: repairs={len(repairs)} regressions={len(regressions)}")
            lines.extend(f"  repair: {item}" for item in repairs)
            lines.extend(f"  regression: {item}" for item in regressions)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay translation fixtures through one transpiler engine.")
    parser.add_argument("--engine", choices=ENGINES)
    parser.add_argument("--no-timing", action="store_true")
    parser.add_argument("--out")
    parser.add_argument("--summarize", nargs="+")
    args = parser.parse_args()
    if args.summarize:
        print(summarize(args.summarize))
        return 0
    if args.engine is None:
        parser.error("--engine is required")
    report = run_engine(args.engine, timing=not args.no_timing)
    text = json.dumps(report, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Comparison infrastructure failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
