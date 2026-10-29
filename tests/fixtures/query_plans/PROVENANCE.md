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
| Presto/Trino (`presto_trino.py`) | synthetic: `presto_explain_sample.json`, `trino_explain_sample.json`, `trino_distributed_explain_sample.json`, `athena_explain_sample.json`; captured: `trino_captured_*.json` | Trino 484 (`EXPLAIN (FORMAT JSON)`, memory catalog): Yes. Presto and Athena: No (no Presto or Athena server available locally; Presto-native image not exercised) |
| ClickHouse (`clickhouse.py`) | synthetic: `clickhouse_explain_plan_sample.txt`, `clickhouse_explain_pipeline_sample.txt`; captured: `clickhouse_captured_*.txt` | ClickHouse 25.8.33.6 server (`EXPLAIN PLAN`): Yes. `EXPLAIN PIPELINE` shape: No (not captured). chdb local mode: not runnable here (the installed `chdb` native library fails to load on this macOS host) |
| Spark (`spark.py`) | synthetic: `spark_explain_sample.txt`; captured: `spark_captured_*.txt` | Spark 4.2.0 `local[1]` (`EXPLAIN EXTENDED`): Yes |
| Databend (`databend.py`) | synthetic: `databend_explain_sample.txt`; captured: `databend_captured_*.txt` | Databend Query v1.2.881 (`EXPLAIN`): Yes |
| QuestDB (`questdb.py`) | synthetic: `questdb_explain_sample.txt`; captured: `questdb_captured_*.txt` | QuestDB 9.3.5 (`EXPLAIN`): Yes |
| Doris (`doris.py`) | synthetic: `doris_shape_plan_sample.txt`; captured: `doris_captured_*.txt` | Apache Doris 4.0.3 (`EXPLAIN SHAPE PLAN`): Yes. `EXPLAIN VERBOSE` shape: No |
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
