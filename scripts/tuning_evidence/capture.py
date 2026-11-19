from __future__ import annotations

import argparse
import json
import os
import re
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from benchbox.platforms.clickhouse.query_rewrites import rewrite_query_for_clickhouse

TRAILING_SETTINGS = re.compile(r"\s+SETTINGS\s+[^;]*;?\s*$", re.IGNORECASE)
ALGORITHM_LINE = re.compile(r"Algorithm:\s*(\w+)")
QUERY_LOG_COLUMNS = (
    "query_id, type, exception_code, exception, memory_usage, read_rows, query_duration_ms, "
    "Settings, ProfileEvents, query"
)

CANDIDATES: dict[str, dict[str, str | int]] = {
    "default": {},
    "hash": {"join_algorithm": "hash"},
    "parallel_hash": {"join_algorithm": "parallel_hash"},
    "grace_hash_8": {"join_algorithm": "grace_hash", "grace_hash_join_initial_buckets": 8},
    "grace_hash_32": {"join_algorithm": "grace_hash", "grace_hash_join_initial_buckets": 32},
    "partial_merge": {"join_algorithm": "partial_merge"},
    "full_sorting_merge": {"join_algorithm": "full_sorting_merge"},
    "auto": {"join_algorithm": "auto"},
    "hash_threads_2": {"join_algorithm": "hash", "max_threads": 2},
    "grace_hash_32_threads_2": {
        "join_algorithm": "grace_hash",
        "grace_hash_join_initial_buckets": 32,
        "max_threads": 2,
    },
    "grace_hash_32_spill": {
        "join_algorithm": "grace_hash",
        "grace_hash_join_initial_buckets": 32,
        "max_bytes_in_join": 1073741824,
        "max_bytes_before_external_group_by": 1073741824,
        "max_bytes_before_external_sort": 1073741824,
    },
    "partial_merge_spill": {
        "join_algorithm": "partial_merge",
        "max_bytes_in_join": 1073741824,
        "max_bytes_before_external_group_by": 1073741824,
        "max_bytes_before_external_sort": 1073741824,
    },
}


def strip_settings(sql: str) -> str:
    return TRAILING_SETTINGS.sub("", sql.strip().rstrip(";")).strip()


def coerce(value: Any) -> str | int:
    text = str(value)
    return int(text) if re.fullmatch(r"-?\d+", text) else text


def join_algorithms(plan_lines: Sequence[str]) -> list[str]:
    return sorted({match.group(1) for line in plan_lines for match in [ALGORITHM_LINE.search(line)] if match})


def log_record(row: Sequence[Any]) -> dict[str, Any]:
    query_id, kind, code, exception, memory, read_rows, duration_ms, settings, events, query = row
    return {
        "query_id": query_id,
        "type": str(kind),
        "exception_code": code or None,
        "exception": (exception or "")[:2000] or None,
        "memory_usage_bytes": memory,
        "read_rows": read_rows,
        "duration_ms": duration_ms,
        "settings": dict(settings),
        "join_build_rows": events.get("JoinBuildTableRowCount", 0),
        "join_probe_rows": events.get("JoinProbeTableRowCount", 0),
        "query": query,
    }


def fetch_log(client: Any, query_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    if not query_ids:
        return {}
    client.execute("SYSTEM FLUSH LOGS")
    rows = client.execute(
        f"SELECT {QUERY_LOG_COLUMNS} FROM system.query_log WHERE type != 'QueryStart' AND query_id IN %(ids)s",
        {"ids": tuple(query_ids)},
    )
    return {record["query_id"]: record for record in map(log_record, rows)}


def explain_algorithms(client: Any, database: str, sql: str, settings: Mapping[str, Any]) -> dict[str, Any]:
    statement = rewrite_query_for_clickhouse(
        strip_settings(sql), {name: coerce(value) for name, value in settings.items()}
    )
    try:
        rows = client.execute(f"EXPLAIN PLAN actions = 1 {statement}", settings={"database": database})
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"[:500], "algorithms": []}
    lines = [str(row[0]) for row in rows]
    return {"algorithms": join_algorithms(lines)}


def failed_and_passing(payload: Mapping[str, Any], query: str) -> dict[str, list[dict[str, Any]]]:
    per_arm: dict[str, list[dict[str, Any]]] = {}
    for execution in payload["executions"]:
        if str(execution.get("query")) == query:
            per_arm.setdefault(execution["arm"], []).append(execution)
    return per_arm


def capture_arms(client: Any, payload: Mapping[str, Any], query: str) -> dict[str, Any]:
    arms: dict[str, Any] = {}
    for arm, executions in failed_and_passing(payload, query).items():
        ids = [execution["engine_query_id"] for execution in executions if execution.get("engine_query_id")]
        log = fetch_log(client, ids)
        records = []
        for execution in executions:
            entry = log.get(execution.get("engine_query_id") or "")
            records.append(
                {
                    "phase": execution["phase"],
                    "ok": execution["ok"],
                    "rows": execution.get("rows"),
                    "error": execution.get("error"),
                    "engine_query_id": execution.get("engine_query_id"),
                    "log": entry,
                }
            )
        sample = next((record["log"] for record in records if record["log"]), None)
        database = payload["arms"][arm]["database"]
        arms[arm] = {
            "database": database,
            "executions": records,
            "passed": sum(1 for record in records if record["ok"]),
            "failed": sum(1 for record in records if not record["ok"]),
            "peak_memory_bytes": max((r["log"]["memory_usage_bytes"] for r in records if r["log"]), default=None),
            "join_algorithm_setting": sample["settings"].get("join_algorithm") if sample else None,
            "plan": explain_algorithms(client, database, sample["query"], sample["settings"]) if sample else None,
        }
    return arms


def sweep(
    client: Any, database: str, sql: str, candidates: Mapping[str, Mapping[str, str | int]], tag: str
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    base = strip_settings(sql)
    for name, settings in candidates.items():
        query_id = f"cap-{tag}-{name}-{uuid.uuid4().hex[:8]}"
        statement = rewrite_query_for_clickhouse(base, dict(settings))
        record: dict[str, Any] = {"settings": dict(settings), "query_id": query_id}
        try:
            rows = client.execute(statement, query_id=query_id, settings={"database": database})
            record.update(ok=True, rows=len(rows), error=None)
        except Exception as exc:
            record.update(ok=False, rows=None, error=f"{type(exc).__name__}: {exc}"[:2000])
        results[name] = record
    log = fetch_log(client, [record["query_id"] for record in results.values()])
    for record in results.values():
        entry = log.get(record["query_id"])
        record["peak_memory_bytes"] = entry["memory_usage_bytes"] if entry else None
        record["duration_ms"] = entry["duration_ms"] if entry else None
        record["exception_code"] = entry["exception_code"] if entry else None
    return results


def render(report: Mapping[str, Any]) -> str:
    lines = [
        f"# Capture for TPC-H Q{report['query']} on ClickHouse {report['server_version']}",
        "",
        f"Commit {report['commit']}; harness output `{report['harness_json']}`.",
        "",
        "## Harness arms",
        "",
        "| Arm | Passed | Failed | Peak memory (GiB) | Join setting | Plan join algorithms | Error |",
        "|---|---|---|---|---|---|---|",
    ]
    for arm, record in report["arms"].items():
        peak = record["peak_memory_bytes"]
        error = next((e["error"] for e in record["executions"] if e["error"]), None) or ""
        plan = (record["plan"] or {}).get("algorithms") or []
        lines.append(
            f"| {arm} | {record['passed']} | {record['failed']} | "
            f"{'' if peak is None else format(peak / 2**30, '.2f')} | {record['join_algorithm_setting']} | "
            f"{', '.join(plan)} | {error.replace('|', '/')[:300]} |"
        )
    lines += [
        "",
        "## Setting sweep on the notuning arm",
        "",
        "| Candidate | Settings | Result | Rows | Peak memory (GiB) | Seconds | Error |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, record in report["sweep"].items():
        peak = record["peak_memory_bytes"]
        seconds = record["duration_ms"]
        lines.append(
            f"| {name} | {json.dumps(record['settings'])} | {'pass' if record['ok'] else 'fail'} | "
            f"{'' if record['rows'] is None else record['rows']} | "
            f"{'' if peak is None else format(peak / 2**30, '.2f')} | "
            f"{'' if seconds is None else format(seconds / 1000, '.1f')} | "
            f"{(record['error'] or '').replace('|', '/')[:200]} |"
        )
    return "\n".join(lines) + "\n"


def build_report(
    client: Any, payload: Mapping[str, Any], query: str, commit: str, harness_json: Path
) -> dict[str, Any]:
    arms = capture_arms(client, payload, query)
    sample = next(
        (record["log"] for arm in arms.values() for record in arm["executions"] if record["log"]),
        None,
    )
    reference_arm = payload["config"]["baseline"]
    results = sweep(client, arms[reference_arm]["database"], sample["query"], CANDIDATES, "q") if sample else {}
    return {
        "query": query,
        "commit": commit,
        "harness_json": str(harness_json),
        "server_version": client.execute("SELECT version()")[0][0],
        "arms": arms,
        "sweep": results,
    }


def main(argv: Sequence[str] | None = None) -> int:
    from clickhouse_driver import Client

    parser = argparse.ArgumentParser(prog="python -m scripts.tuning_evidence.capture")
    parser.add_argument("harness_json", type=Path)
    parser.add_argument("--query", default="21")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--user", default="default")
    parser.add_argument("--password-env", default="CLICKHOUSE_PASSWORD")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = json.loads(args.harness_json.read_text(encoding="utf-8"))
    client = Client(
        host=args.host,
        port=args.port,
        user=args.user,
        password=os.environ.get(args.password_env, ""),
        send_receive_timeout=600,
    )
    report = build_report(client, payload, args.query, args.commit, args.harness_json)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "capture.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    (args.out / "capture.md").write_text(render(report), encoding="utf-8")
    print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
