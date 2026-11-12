#!/usr/bin/env bash

set -euo pipefail

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  echo "usage: sketch_storage_smoke.sh [OUTPUT_FILE]"
  echo "Measures sketch storage sizes. On DuckDB 2.x this tool opts in to the third-party Query.Farm extension"
  echo "repository (https://duckdb-extensions.query.farm) itself; BenchBox runs require"
  echo "--platform-option allow_third_party_extensions=true instead."
  exit 0
fi

OUT="${1:-/dev/stdout}"

uv run -- python - <<'PY' >"${OUT}"
from __future__ import annotations

from dataclasses import dataclass

import duckdb

from benchbox.utils.duckdb_extensions import install_datasketches


@dataclass(frozen=True)
class DuckDbProbe:
    op_id: str
    family: str
    setup_sql: tuple[str, ...]
    bytes_sql: str


def _theta_probe(op_id: str, build_expr: str, bytes_expr: str) -> DuckDbProbe:
    return DuckDbProbe(
        op_id=op_id,
        family="theta",
        setup_sql=(
            "DROP TABLE IF EXISTS sketch_ops_daily_users",
            """
            CREATE TABLE sketch_ops_daily_users (
                activity_date DATE, region VARCHAR(25), user_sketch BLOB, rev_sketch BLOB
            )
            """,
            f"""
            INSERT INTO sketch_ops_daily_users
            SELECT l_shipdate, l_returnflag,
                   {build_expr.replace("{value}", "l_orderkey")},
                   {build_expr.replace("{value}", "CAST(l_extendedprice AS INTEGER)")}
            FROM lineitem
            GROUP BY l_shipdate, l_returnflag
            """,
        ),
        bytes_sql=f"""
            SELECT octet_length({bytes_expr}) AS sketch_bytes
            FROM sketch_ops_daily_users
            """,
    )


def _kll_probe(op_id: str, k: int) -> DuckDbProbe:
    return DuckDbProbe(
        op_id=op_id,
        family="kll",
        setup_sql=(
            "DROP TABLE IF EXISTS sketch_ops_kll_partitions",
            f"""
            CREATE TABLE sketch_ops_kll_partitions AS
            SELECT
                l_shipdate, l_returnflag,
                datasketch_kll({k}, l_extendedprice::DOUBLE) AS price_sketch
            FROM lineitem
            GROUP BY l_shipdate, l_returnflag
            """,
        ),
        bytes_sql=f"""
            SELECT octet_length(datasketch_kll({k}, price_sketch::sketch_kll_double)) AS sketch_bytes
            FROM sketch_ops_kll_partitions
            """,
    )


def _topk_probe(op_id: str, lg_max_map_size: int) -> DuckDbProbe:
    return DuckDbProbe(
        op_id=op_id,
        family="frequent_items",
        setup_sql=(
            "DROP TABLE IF EXISTS sketch_ops_topk",
            """
            CREATE TABLE sketch_ops_topk (shard_id INTEGER, topk_sketch BLOB)
            """,
            f"""
            INSERT INTO sketch_ops_topk
            SELECT CAST(l_orderkey % 8 AS INTEGER),
                   datasketch_frequent_items({lg_max_map_size}, l_shipmode)
            FROM lineitem
            GROUP BY l_orderkey % 8
            """,
        ),
        bytes_sql=f"""
            SELECT octet_length(datasketch_frequent_items({lg_max_map_size}, topk_sketch)) AS sketch_bytes
            FROM sketch_ops_topk
            """,
    )


DUCKDB_FAMILY_PROBES: dict[str, str] = {
    "theta": "SELECT datasketch_theta(v) FROM (VALUES (1::INTEGER)) t(v)",
    "frequent_items": "SELECT datasketch_frequent_items(8, v) FROM (VALUES ('x')) t(v)",
    "cpc": "SELECT datasketch_cpc(11, v) FROM (VALUES (1::INTEGER)) t(v)",
    "req": "SELECT datasketch_req(12, v) FROM (VALUES (1.0::FLOAT)) t(v)",
    "kll": "SELECT datasketch_kll(200, v) FROM (VALUES (1.0::DOUBLE)) t(v)",
}


DUCKDB_PROBES: tuple[DuckDbProbe, ...] = (
    _theta_probe(
        op_id="sketch_query_theta_union_merge",
        build_expr="datasketch_theta({value})",
        bytes_expr="datasketch_theta(user_sketch)",
    ),
    _kll_probe(op_id="sketch_query_kll_quantiles_merge", k=200),
    _topk_probe(op_id="sketch_query_topk_combine", lg_max_map_size=8),
    DuckDbProbe(
        op_id="sketch_cpc_query_union_merge",
        family="cpc",
        setup_sql=(
            "DROP TABLE IF EXISTS sketch_cpc_partitions",
            """
            CREATE TABLE sketch_cpc_partitions AS
            SELECT l_shipdate, l_returnflag, datasketch_cpc(11, l_orderkey) AS user_sketch
            FROM lineitem
            GROUP BY l_shipdate, l_returnflag
            """,
        ),
        bytes_sql="""
            SELECT octet_length(datasketch_cpc_union(11, user_sketch::sketch_cpc)) AS sketch_bytes
            FROM sketch_cpc_partitions
            """,
    ),
    DuckDbProbe(
        op_id="sketch_req_query_quantile_merge",
        family="req",
        setup_sql=(
            "DROP TABLE IF EXISTS sketch_req_partitions",
            """
            CREATE TABLE sketch_req_partitions AS
            SELECT l_shipdate, l_returnflag, datasketch_req(12, l_extendedprice::FLOAT) AS price_sketch
            FROM lineitem
            GROUP BY l_shipdate, l_returnflag
            """,
        ),
        bytes_sql="""
            SELECT octet_length(datasketch_req(12, price_sketch::sketch_req_float)) AS sketch_bytes
            FROM sketch_req_partitions
            """,
    ),
    _theta_probe(
        op_id="sketch_query_theta_union_merge_lgk10",
        build_expr="datasketch_theta(10, {value})",
        bytes_expr="datasketch_theta(10, user_sketch::sketch_theta_uint64)",
    ),
    _theta_probe(
        op_id="sketch_query_theta_union_merge_lgk14",
        build_expr="datasketch_theta(14, {value})",
        bytes_expr="datasketch_theta(14, user_sketch::sketch_theta_uint64)",
    ),
    _kll_probe(op_id="sketch_query_kll_quantiles_merge_k100", k=100),
    _kll_probe(op_id="sketch_query_kll_quantiles_merge_k1000", k=1000),
    _topk_probe(op_id="sketch_query_topk_combine_lgmm8", lg_max_map_size=8),
    _topk_probe(op_id="sketch_query_topk_combine_lgmm10", lg_max_map_size=10),
)


def emit(tool: str, op_id: str, observed_bytes: int | str) -> None:
    print(f"{tool}\t{op_id}\t{observed_bytes}")


con = duckdb.connect(":memory:")
con.execute("INSTALL tpch")
con.execute("LOAD tpch")
con.execute("CALL dbgen(sf=0.01)")

try:
    install_datasketches(con)
    datasketches_ok = True
except duckdb.Error:
    datasketches_ok = False

print("tool\top_id\tobserved_bytes")

if not datasketches_ok:
    for probe in DUCKDB_PROBES:
        emit("duckdb", probe.op_id, "SKIP (datasketches extension unavailable)")
else:
    available_families: set[str] = set()
    for family, probe_sql in DUCKDB_FAMILY_PROBES.items():
        try:
            con.execute(probe_sql)
            available_families.add(family)
        except duckdb.Error:
            pass

    for probe in DUCKDB_PROBES:
        if probe.family not in available_families:
            emit("duckdb", probe.op_id, f"SKIP ({probe.family} functions unavailable)")
            continue
        for statement in probe.setup_sql:
            con.execute(statement)
        bytes_observed = con.execute(probe.bytes_sql).fetchone()[0]
        emit("duckdb", probe.op_id, bytes_observed)
PY

CLICKHOUSE_PROBE_OP_IDS=(
  "sketch_query_theta_union_merge"
  "sketch_query_kll_quantiles_merge"
  "sketch_query_topk_combine"
)

CLICKHOUSE_LOCAL_BIN="${CLICKHOUSE_LOCAL_BIN:-}"
if [[ -z "${CLICKHOUSE_LOCAL_BIN}" ]]; then
  CLICKHOUSE_LOCAL_BIN="$(command -v clickhouse-local || true)"
fi

if [[ -z "${CLICKHOUSE_LOCAL_BIN}" ]]; then
  for op_id in "${CLICKHOUSE_PROBE_OP_IDS[@]}"; do
    printf 'clickhouse-local\t%s\tSKIP (clickhouse-local not on PATH; set CLICKHOUSE_LOCAL_BIN to run ClickHouse probes)\n' "${op_id}" >>"${OUT}"
  done
else
  "${CLICKHOUSE_LOCAL_BIN}" --multiquery --query "
    DROP TABLE IF EXISTS lineitem;
    CREATE TABLE lineitem
    (
      l_orderkey UInt64,
      l_shipdate Date,
      l_returnflag String,
      l_extendedprice Float64,
      l_shipmode String
    ) ENGINE = Memory;
    INSERT INTO lineitem
    SELECT
      number + 1,
      toDate('1992-01-01') + toIntervalDay(number % 2500),
      if(number % 3 = 0, 'A', if(number % 3 = 1, 'R', 'N')),
      toFloat64(1000 + (number % 100000)) / 100,
      concat('MODE', toString(number % 7))
    FROM numbers(15000);

    DROP TABLE IF EXISTS sketch_ops_daily_users;
    CREATE TABLE sketch_ops_daily_users
    (
      activity_date Date,
      region String,
      user_sketch AggregateFunction(uniq, UInt64)
    ) ENGINE = MergeTree() ORDER BY (activity_date, region);
    INSERT INTO sketch_ops_daily_users
    SELECT l_shipdate, l_returnflag, uniqState(l_orderkey)
    FROM lineitem GROUP BY l_shipdate, l_returnflag;
    SELECT 'clickhouse-local', 'sketch_query_theta_union_merge', length(toString(uniqMergeState(user_sketch)))
    FROM sketch_ops_daily_users;

    DROP TABLE IF EXISTS sketch_ops_kll_partitions;
    CREATE TABLE sketch_ops_kll_partitions
    (
      activity_date Date,
      region String,
      price_sketch AggregateFunction(quantileTDigest(0.5), Float64)
    ) ENGINE = MergeTree() ORDER BY (activity_date, region);
    INSERT INTO sketch_ops_kll_partitions
    SELECT l_shipdate, l_returnflag, quantileTDigestState(0.5)(l_extendedprice)
    FROM lineitem GROUP BY l_shipdate, l_returnflag;
    SELECT 'clickhouse-local', 'sketch_query_kll_quantiles_merge', length(toString(quantileTDigestMergeState(0.5)(price_sketch)))
    FROM sketch_ops_kll_partitions;

    DROP TABLE IF EXISTS sketch_ops_topk;
    CREATE TABLE sketch_ops_topk
    (
      shard_id Int32,
      topk_sketch AggregateFunction(topK(8), String)
    ) ENGINE = MergeTree() ORDER BY shard_id;
    INSERT INTO sketch_ops_topk
    SELECT toInt32(l_orderkey % 8), topKState(8)(l_shipmode)
    FROM lineitem GROUP BY l_orderkey % 8;
    SELECT 'clickhouse-local', 'sketch_query_topk_combine', length(toString(topKMergeState(8)(topk_sketch)))
    FROM sketch_ops_topk;
  " >>"${OUT}"
fi
