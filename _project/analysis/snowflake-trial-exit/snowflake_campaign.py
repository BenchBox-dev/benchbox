#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from typing import Any

import snowflake.connector

from benchbox.security.credentials import CredentialManager

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CONTEXT_DATABASE = "TPCH_SF1_NOTUNING_NOCONSTRAINTS"


def _identifier(value: str) -> str:
    if not IDENTIFIER.match(value):
        raise SystemExit(f"refusing non-simple identifier: {value!r}")
    return value.upper()


def _connect() -> tuple[Any, str]:
    credentials = CredentialManager().get_platform_credentials("snowflake") or {}
    warehouse = _identifier(str(credentials.get("warehouse")))
    params = {
        "account": credentials.get("account"),
        "user": credentials.get("username"),
        "password": credentials.get("password"),
        "warehouse": warehouse,
        "application": "BenchBox",
        "login_timeout": 30,
        "network_timeout": 300,
    }
    if credentials.get("role"):
        params["role"] = credentials["role"]
    connection = snowflake.connector.connect(**params)
    cursor = connection.cursor()
    cursor.execute("ALTER SESSION SET USE_CACHED_RESULT = FALSE")
    cursor.execute("ALTER SESSION SET QUERY_TAG = 'BenchBox_campaign_helper'")
    return connection, warehouse


def _rows(cursor: Any) -> list[dict[str, Any]]:
    names = [column[0].lower() for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _suspend(cursor: Any, warehouse: str) -> bool:
    try:
        cursor.execute(f"ALTER WAREHOUSE {warehouse} SUSPEND")
        return True
    except Exception:
        return False


def _schema_tables(cursor: Any, database: str) -> list[dict[str, Any]]:
    cursor.execute(f"SHOW TABLES IN SCHEMA {database}.PUBLIC")
    return _rows(cursor)


def _clustered_tables(cursor: Any, database: str) -> list[dict[str, Any]]:
    return [row for row in _schema_tables(cursor, database) if str(row.get("cluster_by") or "").strip()]


def clustering_state(cursor: Any, database: str) -> dict[str, Any]:
    schema_tables = _schema_tables(cursor, database)
    tables = []
    for row in (row for row in schema_tables if str(row.get("cluster_by") or "").strip()):
        name = _identifier(str(row["name"]))
        cursor.execute(f"SELECT SYSTEM$CLUSTERING_INFORMATION('{database}.PUBLIC.{name}')")
        info = json.loads(cursor.fetchone()[0])
        tables.append(
            {
                "table": name,
                "cluster_by": row.get("cluster_by"),
                "automatic_clustering": row.get("automatic_clustering"),
                "rows": row.get("rows"),
                "bytes": row.get("bytes"),
                "total_partition_count": info.get("total_partition_count"),
                "average_overlaps": info.get("average_overlaps"),
                "average_depth": info.get("average_depth"),
            }
        )
    cursor.execute(
        f"SELECT table_name, clustering_key FROM {database}.INFORMATION_SCHEMA.TABLES "
        "WHERE table_schema = 'PUBLIC' AND clustering_key IS NOT NULL ORDER BY table_name"
    )
    raw_keys = {str(row["table_name"]): row["clustering_key"] for row in _rows(cursor)}
    return {
        "database": database,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "tables": tables,
        "table_count": len(schema_tables),
        "tuning_metadata_table_present": any(
            str(row.get("name", "")).upper() == "BENCHBOX_TUNING_METADATA" for row in schema_tables
        ),
        "information_schema_clustering_key": raw_keys,
    }


def _settled(history: list[dict[str, Any]], polls: int, tolerance: float) -> bool:
    if len(history) < polls:
        return False
    window = history[-polls:]
    names = {table["table"] for table in window[-1]["tables"]}
    if not names:
        return False
    for name in names:
        depths = [
            next((table["average_depth"] for table in state["tables"] if table["table"] == name), None)
            for state in window
        ]
        if any(depth is None for depth in depths):
            return False
        low, high = min(depths), max(depths)
        if high > 0 and (high - low) / high > tolerance:
            return False
    return True


def budget(cursor: Any, warehouse: str, since: str, baseline: float, margin: float) -> dict[str, Any]:
    cursor.execute(
        f"SELECT COALESCE(SUM(credits_used), 0) FROM TABLE({CONTEXT_DATABASE}.INFORMATION_SCHEMA."
        f"WAREHOUSE_METERING_HISTORY(DATE_RANGE_START => '{since}'::TIMESTAMP_TZ, WAREHOUSE_NAME => '{warehouse}'))"
    )
    warehouse_credits = float(cursor.fetchone()[0])
    cursor.execute(
        f"SELECT COALESCE(SUM(credits_used), 0) FROM TABLE({CONTEXT_DATABASE}.INFORMATION_SCHEMA."
        f"AUTOMATIC_CLUSTERING_HISTORY(DATE_RANGE_START => '{since}'::TIMESTAMP_TZ))"
    )
    clustering_credits = float(cursor.fetchone()[0])
    estimate = baseline + warehouse_credits + clustering_credits + margin
    return {
        "since": since,
        "baseline_credits": baseline,
        "warehouse_credits_since": round(warehouse_credits, 3),
        "automatic_clustering_credits_since": round(clustering_credits, 3),
        "safety_margin": margin,
        "cumulative_estimate": round(estimate, 3),
    }


def statement_history(cursor: Any, database: str, since: str) -> dict[str, Any]:
    cursor.execute(
        f"SELECT query_text, start_time, execution_status FROM TABLE({CONTEXT_DATABASE}.INFORMATION_SCHEMA."
        f"QUERY_HISTORY(END_TIME_RANGE_START => '{since}'::TIMESTAMP_TZ, RESULT_LIMIT => 10000)) "
        "ORDER BY start_time"
    )
    counts: dict[str, int] = {}
    drops: list[dict[str, Any]] = []
    for row in _rows(cursor):
        text = " ".join(str(row["query_text"]).split()).upper()
        if database not in text and not text.startswith(("TRUNCATE TABLE", "COPY INTO", "CREATE OR REPLACE TABLE")):
            continue
        for verb in ("DROP DATABASE", "DROP TABLE", "TRUNCATE TABLE", "COPY INTO", "CREATE OR REPLACE TABLE"):
            if text.startswith(verb):
                counts[verb] = counts.get(verb, 0) + 1
                if verb.startswith("DROP"):
                    drops.append({"start_time": str(row["start_time"]), "text": text[:200]})
    return {"database": database, "since": since, "statement_counts": counts, "drops": drops}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("suspend")
    for name in ("clustering-state", "suspend-recluster", "drop-tuned-database"):
        command = sub.add_parser(name)
        command.add_argument("--database", required=True)
    settle = sub.add_parser("settle")
    settle.add_argument("--database", required=True)
    settle.add_argument("--interval", type=int, default=600)
    settle.add_argument("--max-wait", type=int, default=10800)
    settle.add_argument("--polls", type=int, default=3)
    settle.add_argument("--tolerance", type=float, default=0.05)
    spend = sub.add_parser("budget")
    spend.add_argument("--since", required=True)
    spend.add_argument("--baseline", type=float, required=True)
    spend.add_argument("--margin", type=float, default=0.5)
    history = sub.add_parser("statement-history")
    history.add_argument("--database", required=True)
    history.add_argument("--since", required=True)
    args = parser.parse_args()

    connection, warehouse = _connect()
    cursor = connection.cursor()
    try:
        if args.command == "suspend":
            result: Any = {"suspended": _suspend(cursor, warehouse)}
        elif args.command == "clustering-state":
            result = clustering_state(cursor, _identifier(args.database))
        elif args.command == "suspend-recluster":
            database = _identifier(args.database)
            names = [_identifier(str(row["name"])) for row in _clustered_tables(cursor, database)]
            for name in names:
                cursor.execute(f"ALTER TABLE {database}.PUBLIC.{name} SUSPEND RECLUSTER")
            result = {"database": database, "suspended_recluster": names}
        elif args.command == "drop-tuned-database":
            database = _identifier(args.database)
            if "_CUSTOM_" not in database:
                raise SystemExit(f"refusing to drop a database that is not a tuned campaign database: {database}")
            cursor.execute(f"DROP DATABASE IF EXISTS {database}")
            result = {"dropped": database}
        elif args.command == "settle":
            database = _identifier(args.database)
            started = time.monotonic()
            states: list[dict[str, Any]] = []
            settled = False
            while True:
                state = clustering_state(cursor, database)
                states.append(state)
                print(json.dumps({"poll": len(states), "state": state}, default=str), flush=True)
                if _settled(states, args.polls, args.tolerance):
                    settled = True
                    break
                if time.monotonic() - started + args.interval > args.max_wait:
                    break
                _suspend(cursor, warehouse)
                time.sleep(args.interval)
            result = {
                "database": database,
                "settled": settled,
                "polls": len(states),
                "waited_seconds": round(time.monotonic() - started),
                "final_state": states[-1],
            }
        elif args.command == "budget":
            result = budget(cursor, warehouse, args.since, args.baseline, args.margin)
        else:
            result = statement_history(cursor, _identifier(args.database), args.since)
    finally:
        if args.command != "suspend":
            _suspend(cursor, warehouse)
        connection.close()
    print(json.dumps({"result": result}, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
