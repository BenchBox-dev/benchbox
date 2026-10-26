---
develop_sha: 7b8484a4c7c9d14062c38552511ba1f38789eaf9
checked_sha: 7323edca548705e0dfebc36bda136e7724043bc8
measured_at_sha: 7323edca548705e0dfebc36bda136e7724043bc8
---

# Presto C++ reduce cap and TPC-Havoc 1_v7

## Findings

Presto native 0.299 registered active and executed ordered array aggregation.
The non-rewritten CASE reduce passed at 99,999 elements and failed at exactly
100,000 elements with NOT_SUPPORTED. The error says "more than 100000" but
its observed boundary is inclusive. Raising the property to 3,000,000 allowed
this CASE reduce to sum all 1,500,000 SF1 order keys.

The requested simple `(s, x) -> s + x` reduce did **not** reject 100,000 or
1,500,000 elements. This contradicts the expectation for that exact SQL.
Its success is consistent with Velox's simple-reduce-to-array-sum rewrite;
it is not evidence that the general lambda cap is absent. The CASE probe
retains the size check and the generated 1_v7 lambda also retains it.

TPC-Havoc 1_v7 returned four rows on tiny matching DuckDB under the existing
harness's ordered comparison and floating-point tolerance (not bitwise equality).
Default SF0.1 and SF1 failed with the reduce cap. With the raised property,
SF0.1 returned four rows matching the canonical Presto Q1. SF1 instead failed
with GENERIC_INTERNAL_ERROR after the native stuck-operator watchdog reported
40 minutes in FilterProject. This was neither a memory-limit error nor a
reduce-cap error. No query returned a memory-limit failure.

## Measurement scope

All result JSON reproduced below was collected from a clean committed tree at
`7323edca548705e0dfebc36bda136e7724043bc8`. The harness has no direct vendored-SQLGlot import.
One coordinator and one worker ran sequential queries, with no other PrestoDB
started. Existing unrelated services were not stopped or modified.

No further queries were run after the instruction to stop benchmark activity.
Specifically, raised-property tiny 1_v7 was **not** run, nor was a literal
`SET SESSION native_expression_max_array_size_in_reduce = 3000000` statement.
The raised runs used its REST equivalent, the `X-Presto-Session` request header,
explicitly on every request. The harness does not persist SET SESSION responses.
Athena, BigQuery, Presto C++ 0.298.x, and multi-worker execution were not run.
Large-scale comparisons were against canonical Presto Q1, not DuckDB.

## Binaries and provisioning

- Apple container 1.1.0; Linux arm64 worker, four CPUs, 4 GiB VM memory.
- Image `docker.io/prestodb/presto-native:0.299`.
- Index digest: `sha256:a4bc7ffc03cbaf689a120cbec91d4e644529bf6c47b1cb15dac6729e2e178249`.
- Arm64 manifest digest: `sha256:aed8a18a04edb97af7397c1eaad582a513ff2432d9a6be25272efc71db730ad0`.
- Arm64 image size reported by `container image inspect`: 201,359,889 bytes.
  Pull output reported 643.6 MB unpacked. Wire-transfer bytes were not measured.
- Coordinator archive `presto-server-0.299.tar.gz`: 745,980,728 bytes.
- Archive SHA256: `2e2cc76252d66a76c6de1d3110559f6378d48ba260e2d035f48af4533dfc1dee`.
- Archive SHA1: `4d9a9cd7ccbd28d9d9d4eb9180e50cab9afe8228`, matching Maven Central's SHA1.
- Combined image-reported size and archive size: 947,340,617 bytes.
- Maven Central's repo1 and repo.maven.apache.org archive URLs returned HTTP 429
  with a 591-byte operational-dependency rate-limit response, not an archive.
  The archive was downloaded from the Google Maven Central mirror:
  <https://maven-central.storage-download.googleapis.com/maven2/com/facebook/presto/presto-server/0.299/presto-server-0.299.tar.gz>.
- Coordinator used `/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home`.

Provisioning commands (launcher options must precede the command; the recipe's
post-command options were rejected by this launcher):

```sh
container image pull --arch arm64 docker.io/prestodb/presto-native:0.299
JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home \
  /tmp/pcpp/presto-server-0.299/bin/launcher \
  --etc-dir=/tmp/pcpp/coord/etc --data-dir=/tmp/pcpp/coord/data start
container run -d --name pcpp-native-1 --arch arm64 -c 4 -m 4g \
  -v /tmp/pcpp/worker/etc:/opt/presto-server/etc \
  docker.io/prestodb/presto-native:0.299
```

## Networking and configuration

No bind-address or node.internal-address adaptation was necessary. The worker
was assigned 192.168.64.41/24, reached coordinator discovery through
192.168.64.1:18081 and announced active. Queries completed through the native
worker with `node-scheduler.include-coordinator=false`. The host did not need
port publishing or host networking; worker HTTP used 7777. Both nodes used
`presto.version=0.299-pcpp`.

The TPCH catalog uses STANDARD names, so the probes use `l_orderkey` and
`o_orderkey`, not the shorthand `orderkey`. An initial shorthand probe returned
SYNTAX_ERROR (`Column 'orderkey' cannot be resolved`). An initial unquoted
`tpch.sf0.1.lineitem` count returned SYNTAX_ERROR. The harness was corrected to
quote the schema; final SF0.1 measurements used that committed correction.

Exact config files:

### coord/etc/catalog/tpch.properties

```text
connector.name=tpch
tpch.column-naming=STANDARD
```

### coord/etc/config.properties

```text
coordinator=true
node-scheduler.include-coordinator=false
http-server.http.port=18081
discovery-server.enabled=true
discovery.uri=http://localhost:18081
presto.version=0.299-pcpp
native-execution-enabled=true
optimizer.optimize-hash-generation=false
regex-library=RE2J
use-alternative-function-signatures=true
```

### coord/etc/jvm.config

```text
-server
-Xmx1G
-XX:+UseG1GC
-XX:G1HeapRegionSize=32M
-XX:+UseGCOverheadLimit
-XX:+ExplicitGCInvokesConcurrent
-XX:+HeapDumpOnOutOfMemoryError
-XX:+ExitOnOutOfMemoryError
-Djdk.attach.allowAttachSelf=true
--add-opens=java.base/java.io=ALL-UNNAMED
--add-opens=java.base/java.lang=ALL-UNNAMED
--add-opens=java.base/java.lang.ref=ALL-UNNAMED
--add-opens=java.base/java.lang.reflect=ALL-UNNAMED
--add-opens=java.base/java.net=ALL-UNNAMED
--add-opens=java.base/java.nio=ALL-UNNAMED
--add-opens=java.base/java.security=ALL-UNNAMED
--add-opens=java.base/javax.security.auth=ALL-UNNAMED
--add-opens=java.base/javax.security.auth.login=ALL-UNNAMED
--add-opens=java.base/java.text=ALL-UNNAMED
--add-opens=java.base/java.util=ALL-UNNAMED
--add-opens=java.base/java.util.concurrent=ALL-UNNAMED
--add-opens=java.base/java.util.concurrent.atomic=ALL-UNNAMED
--add-opens=java.base/java.util.regex=ALL-UNNAMED
--add-opens=java.base/jdk.internal.loader=ALL-UNNAMED
--add-opens=java.base/sun.security.action=ALL-UNNAMED
--add-opens=java.security.jgss/sun.security.krb5=ALL-UNNAMED
```

### coord/etc/node.properties

```text
node.environment=pcpp
node.id=pcpp-coord
node.data-dir=/tmp/pcpp/coord/data
```

### worker/etc/catalog/tpch.properties

```text
connector.name=tpch
tpch.column-naming=STANDARD
```

### worker/etc/config.properties

```text
discovery.uri=http://192.168.64.1:18081
presto.version=0.299-pcpp
http-server.http.port=7777
shutdown-onset-sec=1
system-memory-gb=3
query-memory-gb=2
query.max-memory-per-node=2GB
```

### worker/etc/node.properties

```text
node.environment=pcpp
node.id=pcpp-native-1
node.location=local
```

## Harness commands

Commands ran with `uv run -- python -m _project.scripts.tpchavoc_presto_variant_check`
from the worktree. `sql --statement SQL` was repeated for the cap statements
below; `--session native_expression_max_array_size_in_reduce=VALUE` selected the
raised property. Each execution wrote its JSON with `--output`.

```sh
uv run -- python -m _project.scripts.tpchavoc_presto_variant_check variants \
  --schema tiny --scale-factor 0.01 --only 1_v7 1
uv run -- python -m _project.scripts.tpchavoc_presto_variant_check scale \
  --scales 0.1 --repeats 1
uv run -- python -m _project.scripts.tpchavoc_presto_variant_check scale \
  --scales 1 --repeats 1
```

The last two commands were repeated with
`--session native_expression_max_array_size_in_reduce=3000000`.

## Node registration

```json
{
  "benchbox_revision": "7323edca548705e0dfebc36bda136e7724043bc8",
  "benchbox_tree_dirty": false,
  "presto_version": "0.299-pcpp",
  "relative_tolerance": 1e-08,
  "results": [
    {
      "ok": true,
      "row_count": 2,
      "rows_head": [
        [
          "pcpp-coord",
          "0.299-pcpp",
          true,
          "active"
        ],
        [
          "pcpp-native-1",
          "0.299-pcpp",
          false,
          "active"
        ]
      ],
      "sql": "SELECT node_id, node_version, coordinator, state FROM system.runtime.nodes ORDER BY coordinator DESC",
      "wall_seconds": 0.196
    }
  ],
  "session": {}
}
```

## Cap probes: exact SQL and outcomes

Every successful scalar probe returned one result row. `n` is measured array
cardinality. LIMIT does not impose an order, so sums over LIMIT-selected rows
can differ across runs; the boundary depends on cardinality, not the sum.
The first family is the originally requested simple lambda. The second family
adds a null-handling CASE to prevent the simple-addition rewrite. The raised
and 100001-property runs use the same CASE family. Leading whitespace in engine
error messages is retained in the JSON.

### cap_plus_default

```json
{
  "benchbox_revision": "7323edca548705e0dfebc36bda136e7724043bc8",
  "benchbox_tree_dirty": false,
  "presto_version": "0.299-pcpp",
  "relative_tolerance": 1e-08,
  "results": [
    {
      "ok": true,
      "row_count": 1,
      "rows_head": [
        [
          60175,
          1802759573
        ]
      ],
      "sql": "SELECT cardinality(array_agg(l_orderkey)) AS n, reduce(array_agg(l_orderkey), BIGINT '0', (s, x) -> s + x, s -> s) AS total FROM tpch.tiny.lineitem",
      "wall_seconds": 0.138
    },
    {
      "ok": true,
      "row_count": 1,
      "rows_head": [
        [
          99999,
          372429118848
        ]
      ],
      "sql": "SELECT cardinality(array_agg(o_orderkey)) AS n, reduce(array_agg(o_orderkey), BIGINT '0', (s, x) -> s + x, s -> s) AS total FROM (SELECT o_orderkey FROM tpch.sf1.orders LIMIT 99999)",
      "wall_seconds": 0.259
    },
    {
      "ok": true,
      "row_count": 1,
      "rows_head": [
        [
          100000,
          371353390048
        ]
      ],
      "sql": "SELECT cardinality(array_agg(o_orderkey)) AS n, reduce(array_agg(o_orderkey), BIGINT '0', (s, x) -> s + x, s -> s) AS total FROM (SELECT o_orderkey FROM tpch.sf1.orders LIMIT 100000)",
      "wall_seconds": 0.232
    },
    {
      "ok": true,
      "row_count": 1,
      "rows_head": [
        [
          1500000,
          4499987250000
        ]
      ],
      "sql": "SELECT cardinality(array_agg(o_orderkey)) AS n, reduce(array_agg(o_orderkey), BIGINT '0', (s, x) -> s + x, s -> s) AS total FROM tpch.sf1.orders",
      "wall_seconds": 0.998
    }
  ],
  "session": {}
}
```

### cap_case_default

```json
{
  "benchbox_revision": "7323edca548705e0dfebc36bda136e7724043bc8",
  "benchbox_tree_dirty": false,
  "presto_version": "0.299-pcpp",
  "relative_tolerance": 1e-08,
  "results": [
    {
      "ok": true,
      "row_count": 1,
      "rows_head": [
        [
          60175,
          1802759573
        ]
      ],
      "sql": "SELECT cardinality(array_agg(l_orderkey)) AS n, reduce(array_agg(l_orderkey), BIGINT '0', (s, x) -> CASE WHEN x IS NULL THEN s ELSE s + x END, s -> s) AS total FROM tpch.tiny.lineitem",
      "wall_seconds": 0.343
    },
    {
      "ok": true,
      "row_count": 1,
      "rows_head": [
        [
          99999,
          364063049248
        ]
      ],
      "sql": "SELECT cardinality(array_agg(o_orderkey)) AS n, reduce(array_agg(o_orderkey), BIGINT '0', (s, x) -> CASE WHEN x IS NULL THEN s ELSE s + x END, s -> s) AS total FROM (SELECT o_orderkey FROM tpch.sf1.orders LIMIT 99999)",
      "wall_seconds": 1.096
    },
    {
      "error_message": " Reduce lambda function doesn't support arrays with more than 100000 elements Top-level Expression: presto.default.reduce(array_agg_8, 0:BIGINT, (expr, expr_0) -> switch(is_null(expr_0), expr, presto.default.plus(expr, expr_0)), (expr_1) -> expr_1)",
      "error_name": "NOT_SUPPORTED",
      "ok": false,
      "sql": "SELECT cardinality(array_agg(o_orderkey)) AS n, reduce(array_agg(o_orderkey), BIGINT '0', (s, x) -> CASE WHEN x IS NULL THEN s ELSE s + x END, s -> s) AS total FROM (SELECT o_orderkey FROM tpch.sf1.orders LIMIT 100000)",
      "wall_seconds": 0.156
    },
    {
      "error_message": " Reduce lambda function doesn't support arrays with more than 100000 elements Top-level Expression: presto.default.reduce(array_agg_4, 0:BIGINT, (expr, expr_0) -> switch(is_null(expr_0), expr, presto.default.plus(expr, expr_0)), (expr_1) -> expr_1)",
      "error_name": "NOT_SUPPORTED",
      "ok": false,
      "sql": "SELECT cardinality(array_agg(o_orderkey)) AS n, reduce(array_agg(o_orderkey), BIGINT '0', (s, x) -> CASE WHEN x IS NULL THEN s ELSE s + x END, s -> s) AS total FROM tpch.sf1.orders",
      "wall_seconds": 0.82
    }
  ],
  "session": {}
}
```

### cap_case_raised

```json
{
  "benchbox_revision": "7323edca548705e0dfebc36bda136e7724043bc8",
  "benchbox_tree_dirty": false,
  "presto_version": "0.299-pcpp",
  "relative_tolerance": 1e-08,
  "results": [
    {
      "ok": true,
      "row_count": 1,
      "rows_head": [
        [
          60175,
          1802759573
        ]
      ],
      "sql": "SELECT cardinality(array_agg(l_orderkey)) AS n, reduce(array_agg(l_orderkey), BIGINT '0', (s, x) -> CASE WHEN x IS NULL THEN s ELSE s + x END, s -> s) AS total FROM tpch.tiny.lineitem",
      "wall_seconds": 0.267
    },
    {
      "ok": true,
      "row_count": 1,
      "rows_head": [
        [
          99999,
          364063049248
        ]
      ],
      "sql": "SELECT cardinality(array_agg(o_orderkey)) AS n, reduce(array_agg(o_orderkey), BIGINT '0', (s, x) -> CASE WHEN x IS NULL THEN s ELSE s + x END, s -> s) AS total FROM (SELECT o_orderkey FROM tpch.sf1.orders LIMIT 99999)",
      "wall_seconds": 0.752
    },
    {
      "ok": true,
      "row_count": 1,
      "rows_head": [
        [
          100000,
          366913390048
        ]
      ],
      "sql": "SELECT cardinality(array_agg(o_orderkey)) AS n, reduce(array_agg(o_orderkey), BIGINT '0', (s, x) -> CASE WHEN x IS NULL THEN s ELSE s + x END, s -> s) AS total FROM (SELECT o_orderkey FROM tpch.sf1.orders LIMIT 100000)",
      "wall_seconds": 0.805
    },
    {
      "ok": true,
      "row_count": 1,
      "rows_head": [
        [
          1500000,
          4499987250000
        ]
      ],
      "sql": "SELECT cardinality(array_agg(o_orderkey)) AS n, reduce(array_agg(o_orderkey), BIGINT '0', (s, x) -> CASE WHEN x IS NULL THEN s ELSE s + x END, s -> s) AS total FROM tpch.sf1.orders",
      "wall_seconds": 105.447
    }
  ],
  "session": {
    "native_expression_max_array_size_in_reduce": "3000000"
  }
}
```

### cap_case_100001

```json
{
  "benchbox_revision": "7323edca548705e0dfebc36bda136e7724043bc8",
  "benchbox_tree_dirty": false,
  "presto_version": "0.299-pcpp",
  "relative_tolerance": 1e-08,
  "results": [
    {
      "ok": true,
      "row_count": 1,
      "rows_head": [
        [
          60175,
          1802759573
        ]
      ],
      "sql": "SELECT cardinality(array_agg(l_orderkey)) AS n, reduce(array_agg(l_orderkey), BIGINT '0', (s, x) -> CASE WHEN x IS NULL THEN s ELSE s + x END, s -> s) AS total FROM tpch.tiny.lineitem",
      "wall_seconds": 0.426
    },
    {
      "ok": true,
      "row_count": 1,
      "rows_head": [
        [
          99999,
          372674828448
        ]
      ],
      "sql": "SELECT cardinality(array_agg(o_orderkey)) AS n, reduce(array_agg(o_orderkey), BIGINT '0', (s, x) -> CASE WHEN x IS NULL THEN s ELSE s + x END, s -> s) AS total FROM (SELECT o_orderkey FROM tpch.sf1.orders LIMIT 99999)",
      "wall_seconds": 1.133
    },
    {
      "ok": true,
      "row_count": 1,
      "rows_head": [
        [
          100000,
          367333390048
        ]
      ],
      "sql": "SELECT cardinality(array_agg(o_orderkey)) AS n, reduce(array_agg(o_orderkey), BIGINT '0', (s, x) -> CASE WHEN x IS NULL THEN s ELSE s + x END, s -> s) AS total FROM (SELECT o_orderkey FROM tpch.sf1.orders LIMIT 100000)",
      "wall_seconds": 1.085
    },
    {
      "error_message": " Reduce lambda function doesn't support arrays with more than 100001 elements Top-level Expression: presto.default.reduce(array_agg_4, 0:BIGINT, (expr, expr_0) -> switch(is_null(expr_0), expr, presto.default.plus(expr, expr_0)), (expr_1) -> expr_1)",
      "error_name": "NOT_SUPPORTED",
      "ok": false,
      "sql": "SELECT cardinality(array_agg(o_orderkey)) AS n, reduce(array_agg(o_orderkey), BIGINT '0', (s, x) -> CASE WHEN x IS NULL THEN s ELSE s + x END, s -> s) AS total FROM tpch.sf1.orders",
      "wall_seconds": 1.224
    }
  ],
  "session": {
    "native_expression_max_array_size_in_reduce": "100001"
  }
}
```

## Rendered 1_v7 SQL

This is the exact `TPCHavocBenchmark.get_query("1_v7", dialect="presto")`
text in the tiny report. The same query was executed at larger scales using
the schema header. `scale` mode also executed canonical Q1 as a control.

```sql
WITH aggregated AS (SELECT l_returnflag, l_linestatus, ARRAY_AGG(l_quantity ORDER BY l_orderkey, l_linenumber) AS quantities, ARRAY_AGG(l_extendedprice ORDER BY l_orderkey, l_linenumber) AS prices, ARRAY_AGG(l_discount ORDER BY l_orderkey, l_linenumber) AS discounts, ARRAY_AGG(l_tax ORDER BY l_orderkey, l_linenumber) AS taxes FROM lineitem WHERE l_shipdate <= CAST('1998-12-01' AS DATE) - INTERVAL '90' DAY GROUP BY l_returnflag, l_linestatus) SELECT l_returnflag, l_linestatus, REDUCE(quantities, CAST(NULL AS DOUBLE), (_acc, _v) -> CASE WHEN _v IS NULL THEN _acc WHEN _acc IS NULL THEN CAST(_v AS DOUBLE) ELSE _acc + CAST(_v AS DOUBLE) END, _acc -> _acc) AS sum_qty, REDUCE(prices, CAST(NULL AS DOUBLE), (_acc, _v) -> CASE WHEN _v IS NULL THEN _acc WHEN _acc IS NULL THEN CAST(_v AS DOUBLE) ELSE _acc + CAST(_v AS DOUBLE) END, _acc -> _acc) AS sum_base_price, REDUCE(ZIP_WITH(prices, discounts, (_v0, _v1) -> _v0 * (1 - _v1)), CAST(NULL AS DOUBLE), (_acc, _v) -> CASE WHEN _v IS NULL THEN _acc WHEN _acc IS NULL THEN CAST(_v AS DOUBLE) ELSE _acc + CAST(_v AS DOUBLE) END, _acc -> _acc) AS sum_disc_price, REDUCE(TRANSFORM(ZIP(prices, discounts, taxes), _r -> _r[1] * (1 - _r[2]) * (1 + _r[3])), CAST(NULL AS DOUBLE), (_acc, _v) -> CASE WHEN _v IS NULL THEN _acc WHEN _acc IS NULL THEN CAST(_v AS DOUBLE) ELSE _acc + CAST(_v AS DOUBLE) END, _acc -> _acc) AS sum_charge, REDUCE(quantities, ROW(CAST(0 AS DOUBLE), CAST(0 AS BIGINT)), (_state, _v) -> IF(_v IS NULL, _state, ROW(_state[1] + CAST(_v AS DOUBLE), _state[2] + 1)), _state -> CASE WHEN _state[2] = 0 THEN NULL ELSE _state[1] / _state[2] END) AS avg_qty, REDUCE(prices, ROW(CAST(0 AS DOUBLE), CAST(0 AS BIGINT)), (_state, _v) -> IF(_v IS NULL, _state, ROW(_state[1] + CAST(_v AS DOUBLE), _state[2] + 1)), _state -> CASE WHEN _state[2] = 0 THEN NULL ELSE _state[1] / _state[2] END) AS avg_price, REDUCE(discounts, ROW(CAST(0 AS DOUBLE), CAST(0 AS BIGINT)), (_state, _v) -> IF(_v IS NULL, _state, ROW(_state[1] + CAST(_v AS DOUBLE), _state[2] + 1)), _state -> CASE WHEN _state[2] = 0 THEN NULL ELSE _state[1] / _state[2] END) AS avg_disc, CARDINALITY(quantities) AS count_order FROM aggregated ORDER BY l_returnflag, l_linestatus
```

Canonical control SQL:

```sql
SELECT "l_returnflag", "l_linestatus", SUM("l_quantity") AS "sum_qty", SUM("l_extendedprice") AS "sum_base_price", SUM("l_extendedprice" * (1 - "l_discount")) AS "sum_disc_price", SUM("l_extendedprice" * (1 - "l_discount") * (1 + "l_tax")) AS "sum_charge", AVG("l_quantity") AS "avg_qty", AVG("l_extendedprice") AS "avg_price", AVG("l_discount") AS "avg_disc", COUNT(*) AS "count_order" FROM "lineitem" WHERE "l_shipdate" <= CAST('1998-12-01' AS DATE) - INTERVAL '90' DAY GROUP BY "l_returnflag", "l_linestatus" ORDER BY "l_returnflag", "l_linestatus"
```

## 1_v7 results

| Schema | Lineitem rows | Default | Raised to 3,000,000 |
|---|---:|---|---|
| tiny | 60,175 | PASS, 4 rows; DuckDB ordered comparator | Not run |
| sf0.1 | 600,572 | NOT_SUPPORTED, no result row count | PASS, 4 rows; canonical Presto comparator |
| sf1 | 6,001,215 | NOT_SUPPORTED, no result row count | GENERIC_INTERNAL_ERROR, no result row count |

The successful tiny and SF0.1 executions demonstrate that ordered ARRAY_AGG
runs on this native worker. Default SF1 reached the reduce cap rather than an
ordered-aggregation support error. Raising the cap did not establish SF1
correctness: its watchdog failure occurred at 732,954,624 reported peak user
and total memory bytes, with no memory-limit error. SF0.1 raised peak memory
was 103,389,593 bytes. Runtime figures below are observations, not benchmark
performance guarantees.

### v7_tiny_duckdb

```json
{
  "benchbox_revision": "7323edca548705e0dfebc36bda136e7724043bc8",
  "benchbox_tree_dirty": false,
  "presto_version": "0.299-pcpp",
  "relative_tolerance": 1e-08,
  "results": [
    {
      "candidate_rows": 4,
      "compare": "ordered",
      "id": "1",
      "kind": "canonical",
      "max_relative_difference": 3.085118980137921e-13,
      "ordered_by_query": true,
      "presto_differs_from_duckdb_text": true,
      "reference_rows": 4,
      "status": "PASS",
      "wall_seconds": 0.131
    },
    {
      "candidate_rows": 4,
      "compare": "ordered",
      "id": "1_v7",
      "kind": "variant",
      "max_relative_difference": 6.803141485409153e-15,
      "ordered_by_query": true,
      "presto_differs_from_duckdb_text": true,
      "reference_rows": 4,
      "status": "PASS",
      "wall_seconds": 1.075
    }
  ],
  "session": {},
  "status_counts": {
    "PASS": 2
  },
  "table_row_counts": {
    "customer": 1500,
    "lineitem": 60175,
    "nation": 25,
    "orders": 15000,
    "part": 2000,
    "partsupp": 8000,
    "region": 5,
    "supplier": 100
  }
}
```

### v7_sf0.1_default

```json
{
  "benchbox_revision": "7323edca548705e0dfebc36bda136e7724043bc8",
  "benchbox_tree_dirty": false,
  "presto_version": "0.299-pcpp",
  "relative_tolerance": 1e-08,
  "repeats": 1,
  "results": [
    {
      "canonical": {
        "peak_total_memory_bytes_runs": [
          1635778
        ],
        "peak_user_memory_bytes_runs": [
          1635778
        ],
        "row_count": 4,
        "status": "PASS",
        "wall_seconds": [
          0.179
        ]
      },
      "lineitem_rows": 600572,
      "scale_factor": 0.1,
      "status": "FAIL",
      "variant_1_v7": {
        "error_message": " Reduce lambda function doesn't support arrays with more than 100000 elements Top-level Expression: presto.default.reduce(array_agg, null:DOUBLE, (expr, expr_0) -> switch(is_null(expr_0), expr, is_null(expr), expr_0, presto.default.plus(expr, expr_0)), (expr_1) -> expr_1)",
        "error_name": "NOT_SUPPORTED",
        "peak_memory": {
          "peakTotalMemoryReservation": 66804776,
          "peakUserMemoryReservation": 66804776
        },
        "status": "FAIL"
      }
    }
  ],
  "session": {}
}
```

### v7_sf1_default

```json
{
  "benchbox_revision": "7323edca548705e0dfebc36bda136e7724043bc8",
  "benchbox_tree_dirty": false,
  "presto_version": "0.299-pcpp",
  "relative_tolerance": 1e-08,
  "repeats": 1,
  "results": [
    {
      "canonical": {
        "peak_total_memory_bytes_runs": [
          2202009
        ],
        "peak_user_memory_bytes_runs": [
          2202009
        ],
        "row_count": 4,
        "status": "PASS",
        "wall_seconds": [
          1.591
        ]
      },
      "lineitem_rows": 6001215,
      "scale_factor": 1.0,
      "status": "FAIL",
      "variant_1_v7": {
        "error_message": " Reduce lambda function doesn't support arrays with more than 100000 elements Top-level Expression: presto.default.reduce(array_agg, null:DOUBLE, (expr, expr_0) -> switch(is_null(expr_0), expr, is_null(expr), expr_0, presto.default.plus(expr, expr_0)), (expr_1) -> expr_1)",
        "error_name": "NOT_SUPPORTED",
        "peak_memory": {
          "peakTotalMemoryReservation": 416368558,
          "peakUserMemoryReservation": 416368558
        },
        "status": "FAIL"
      }
    }
  ],
  "session": {}
}
```

### v7_sf0.1_raised

```json
{
  "benchbox_revision": "7323edca548705e0dfebc36bda136e7724043bc8",
  "benchbox_tree_dirty": false,
  "presto_version": "0.299-pcpp",
  "relative_tolerance": 1e-08,
  "repeats": 1,
  "results": [
    {
      "canonical": {
        "peak_total_memory_bytes_runs": [
          819200
        ],
        "peak_user_memory_bytes_runs": [
          819200
        ],
        "row_count": 4,
        "status": "PASS",
        "wall_seconds": [
          0.233
        ]
      },
      "comparison": {
        "candidate_rows": 4,
        "compare": "ordered",
        "max_relative_difference": 1.4963456630759836e-12,
        "reference_rows": 4,
        "status": "PASS"
      },
      "lineitem_rows": 600572,
      "scale_factor": 0.1,
      "status": "PASS",
      "variant_1_v7": {
        "peak_total_memory_bytes_runs": [
          103389593
        ],
        "peak_user_memory_bytes_runs": [
          103389593
        ],
        "row_count": 4,
        "status": "PASS",
        "wall_seconds": [
          39.251
        ]
      }
    }
  ],
  "session": {
    "native_expression_max_array_size_in_reduce": "3000000"
  }
}
```

### v7_sf1_raised

```json
{
  "benchbox_revision": "7323edca548705e0dfebc36bda136e7724043bc8",
  "benchbox_tree_dirty": false,
  "presto_version": "0.299-pcpp",
  "relative_tolerance": 1e-08,
  "repeats": 1,
  "results": [
    {
      "canonical": {
        "peak_total_memory_bytes_runs": [
          2181038
        ],
        "peak_user_memory_bytes_runs": [
          2181038
        ],
        "row_count": 4,
        "status": "PASS",
        "wall_seconds": [
          1.136
        ]
      },
      "lineitem_rows": 6001215,
      "scale_factor": 1.0,
      "status": "FAIL",
      "variant_1_v7": {
        "error_message": "Task 20261026_161046_00100_rsnyw.2.0.0.0 cancelled due to stuck operator: tid=122 opCall=FilterProject.10::getOutput duration= 40m 0s",
        "error_name": "GENERIC_INTERNAL_ERROR",
        "peak_memory": {
          "peakTotalMemoryReservation": 732954624,
          "peakUserMemoryReservation": 732954624
        },
        "status": "FAIL"
      }
    }
  ],
  "session": {
    "native_expression_max_array_size_in_reduce": "3000000"
  }
}
```

## Verification and teardown

Before the final schema-quoting correction, the focused harness unit file and
release infrastructure suite passed together: 105 passed, 2 deselected. After
import ordering was fixed, the harness file alone passed: 18 passed. These
counts refer to the pre-correction tree, not the measured commit. The final
schema-quoting correction passed Ruff checks and formatting and the commit
hooks. No pytest was run after the owner imposed the test hold. No
`make pr-preflight` or release-curation dry run was run because that hold
remained in force. Existing release-cut removal and LEFTOVER entries already
cover `tests/unit/test_tpchavoc_presto_variant_check.py`; no new test file was
added.

The isolated worker was stopped and removed with `container stop pcpp-native-1`
and `container rm pcpp-native-1`. The launcher stopped coordinator PID 15358.
All created coordinator data, worker configuration, extracted server files,
and downloaded archive under `/tmp/pcpp` were removed after this record
captured the results. No push or PR operation was attempted.

`container ls -a` after teardown listed only the unrelated QuestDB container.
`lsof -nP -iTCP:18081 -iTCP:7777 -sTCP:LISTEN` returned no entries
(exit status 1), confirming no listeners on either test port.
