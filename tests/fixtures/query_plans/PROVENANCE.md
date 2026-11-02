# Query Plan Fixture Provenance

Every sample under this directory that was synthesized from documented
EXPLAIN output (rather than captured from a running engine) carries a
sibling `<fixture>.meta.json` sidecar:

```json
{"provenance": "synthetic", "models": "<what shape it models>", "validated_against_live": false}
```

When a fixture is diffed against real engine output, keep the synthetic
file for fast no-credential coverage, add the captured file alongside it,
and flip `validated_against_live` to `true` with the engine/version noted
in `models`.

## Parser validation status

Captured fixtures (`<engine>_captured_<shape>.<ext>`, shapes `scan_filter`,
`join`, `aggregate`, `union`) come from the platform adapter's own
`get_query_plan` against a real engine, over two small tables
(`orders(o_id, o_cust, o_total)`, `customers(c_id, c_name, c_region)`).
Their semantic assertions live in
`tests/unit/query_plans/test_captured_plan_semantics.py`. Captured text is
unmodified except that Spark's temporary warehouse path in `Location:` is
replaced with `/tmp/spark-warehouse` and QuestDB tables carry an `lpv_` prefix.

| Parser | Fixture(s) | Live-validated |
|---|---|---|
| Presto/Trino (`presto_trino.py`) | synthetic: `presto_explain_sample.json`, `trino_explain_sample.json`, `trino_distributed_explain_sample.json`, `athena_explain_sample.json`; captured: `trino_captured_*.json`, `presto_captured_*.json` | Trino 484 (`EXPLAIN (FORMAT JSON)`, memory catalog): Yes. Presto 0.299 (`prestodb/presto`, `EXPLAIN (FORMAT JSON)`, memory catalog): Yes (`presto_captured_*.json`). Athena: No (cloud service, not reachable without credentials) |
| ClickHouse (`clickhouse.py`) | synthetic: `clickhouse_explain_plan_sample.txt`, `clickhouse_explain_pipeline_sample.txt`; captured: `clickhouse_captured_*.txt`, `clickhouse_pipeline_captured_*.txt`, `clickhouse_chdb_captured_*.txt` | ClickHouse 25.8.33.6 server (`EXPLAIN PLAN`): Yes. `EXPLAIN PIPELINE` on the same server: Yes. chdb 3.7.2 (ClickHouse 25.8.2.1) local mode `EXPLAIN PLAN`: Yes. The repository-locked chdb 4.1.6 still fails to load on this macOS host (`dlopen ... mis-aligned LINKEDIT string pool`), so the local capture used a throwaway `uv run --with chdb==3.7.2` environment |
| Spark (`spark.py`) | synthetic: `spark_explain_sample.txt`; captured: `spark_captured_*.txt` | Spark 4.2.0 `local[1]` (`EXPLAIN EXTENDED`): Yes |
| LakeSail (`spark.py` via the `lakesail` registration) | captured: `lakesail_captured_*.txt` | LakeSail Sail through `pysail` 0.7.3 over Spark Connect, DataFrame `explain(extended=True)` captured with the DataFrame adapter's own `capture_query_plan`: Yes. Physical plans are DataFusion-style (`DataSourceExec`, `HashJoinExec`), so scans are named from the logical-plan `TableScan` names when present (catalog tables, directory-per-table) and from the file stem for `read.parquet` temp views over flat `<table>.parquet` files (`lakesail_flat_captured_*.txt`) |
| Databend (`databend.py`) | synthetic: `databend_explain_sample.txt`; captured: `databend_captured_*.txt` | Databend Query v1.2.881 (`EXPLAIN`): Yes |
| QuestDB (`questdb.py`) | synthetic: `questdb_explain_sample.txt`; captured: `questdb_captured_*.txt` | QuestDB 9.3.5 (`EXPLAIN`): Yes |
| Doris (`doris.py`) | synthetic: `doris_shape_plan_sample.txt`; captured: `doris_captured_*.txt`, `doris_verbose_captured_*.txt` | Apache Doris 4.0.3 (`EXPLAIN SHAPE PLAN`): Yes. `EXPLAIN VERBOSE` (`doris_verbose_captured_*.txt`, including semi/anti/left join, top-n, window, intersect and except samples): Yes |
| SingleStore (`singlestore.py`) | synthetic: `singlestore_explain_sample.txt`; captured: `singlestore_captured_*.txt` | SingleStore `singlestoredb-dev` image (memsql_version 10.8.1, linux/amd64): Yes |

Live hooks: `tests/integration/test_plan_capture_synthetic_parsers_live.py`
(Presto/Trino, ClickHouse, Spark) and
`tests/integration/test_misc_plan_capture_live.py`
(Databend, QuestDB, Doris, SingleStore). Each module skips unless its
engine env var is set. Each hook creates two small tables, then runs the four
shapes (scan with filter, two-table join, group-by aggregate, `UNION ALL`)
through the adapter and asserts the parsed operator types, scanned tables and
fingerprint stability. Presto/Trino hooks need a writable catalog
(`PRESTO_CATALOG` / `TRINO_CATALOG`, default `memory`); the ClickHouse hook
uses server mode on the native port (`CLICKHOUSE_PORT`, default 9000).

Live hooks added for the remaining routes: Presto (`PRESTO_HOST`), ClickHouse
`EXPLAIN PIPELINE` (`CLICKHOUSE_HOST`), chdb local mode (`CHDB_LIVE=1`, needs a
loadable `chdb`), Doris `EXPLAIN VERBOSE` (`DORIS_HOST`) and LakeSail DataFrame
capture (`LAKESAIL_ENDPOINT`, a `sc://` Spark Connect URL served by `pysail`).

Not validated: Athena is a cloud-only service and was not exercised.
`EXPLAIN PIPELINE` output carries no table names or predicates, so its
semantic assertions cover processor names, read steps and join/aggregate/union
structure only. The `chdb`, `pysail` and `grpcio` packages were used only
through throwaway `uv run --with` environments and are not project dependencies.
