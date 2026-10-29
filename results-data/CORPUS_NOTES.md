# Corpus Generation Notes - 2026-04-03

## Results from variant and loader bugs (2026-10-28)

Seven ranked bundles recorded wrong values because of BenchBox's own
hand-written query variants or data loader, not an engine. Each now carries
`known_defects: ["defective-variant-or-loader-wrong-result"]` in its
`.manifest.json` sidecar, which the explorer pipeline turns into
`ranking_exclusion_reason=known_defective_data`. The bundle JSON is unchanged;
four sidecars already existed (the three CoffeeShop bundles and the DataFusion
ClickBench bundle) and the three TPC-DI ones were created with
`result_source: internal`. Replacing them means fixing the variant or loader,
then rerunning the cells and ingesting the new bundles; those fixes and reruns
follow in separate changes. Each short id resolves to exactly one bundle.

- ClickHouse Local CoffeeShop SF0.01, SF0.1, SF1 `370b71e2`, `975cb3a1`,
  `4ce8154e`: the hand-written TM1 variant records 1 row at every scale against
  4 on DuckDB (decimal division truncates in the ClickHouse variant).
- ClickHouse Local TPC-DI SF0.01, SF0.1, SF1 `01f31a9c`, `4d6e3213`,
  `b6f8b467`: the hand-written AQ6 variant uses `stddevPop` and a denominator
  that omits the join fan-out, and the AQ3 commission ratio of 0.0999
  truncates to 0. The recorded AQ6 and AQ3 row counts (5 or 12, and 50) match
  DuckDB, so the defect is in the values, which the bundle does not record.
- DataFusion ClickBench SF1 `8014d1c9`: the loader treated empty strings as
  NULL and ignored the `__NULL__` marker, so 750,021 empty `SearchPhrase`
  values became NULL and Q6 (`COUNT(DISTINCT SearchPhrase)`) returned 17
  against 18 on DuckDB.

Flagging these leaves the ClickHouse Local CoffeeShop and TPC-DI SF0.01
cohorts with only DuckDB ranked, so the `DOCUMENTED_SHALLOW_COHORTS` entries
for both change from DuckDB and ClickHouse Local to DuckDB alone. The
CoffeeShop and TPC-DI SF0.1 and SF1 cohorts and the ClickBench SF1 cohort stay
at four ranked platforms, above the floor.

ClickHouse `map_access` (Read Primitives) returns 0 for a missing map key
where DuckDB returns NULL. The ClickHouse Local Read Primitives SF0.01 and
SF0.1 bundles (`9f79ff77`, `3f5c94e9`) ran it successfully with 10 rows, the
same count as DuckDB, so the bundle records nothing that shows the wrong value.
Both now carry `known_defects: ["defective-variant-or-loader-wrong-result"]`
in their `.manifest.json` sidecars, so their timings are not ranked while the
query is wrong (tracked in `clickhouse-map-access-missing-key-returns-zero`).
This leaves DataFusion as the only ranked Read Primitives identity at SF0.01
and SF0.1, and the `DOCUMENTED_SHALLOW_COHORTS` entries for both change from
ClickHouse Local and DataFusion to DataFusion alone. The bundle JSON is
unchanged. Unflag the sidecars when the query is corrected and rerun.

## Fork 30.24.0.17 published-impact audit (2026-10-26)

- Spark and Databricks (fork #73): `DATE_TRUNC('DAY', x)` was written
  `TRUNC(x, 'DAY')`, which returns NULL. No shipped query changes text; the only
  DAY truncation is NYC Taxi `trips-per-day`, which already translated to
  `DATE_TRUNC` and recorded 365 rows at SF 0.1, 1 and 10 on Databricks. No
  sidecar changed.
- QuestDB (fork #74): TPC-DS Q16, Q94 and Q95 and eleven other TPC-DS and
  TPC-H queries now lower EXISTS, NOT EXISTS and IN subqueries to joins. No
  QuestDB bundle exists, so no sidecar changed.
- SQLite (fork #75): CREATE INDEX no longer carries NULLS FIRST or NULLS LAST.
  Only Write Primitives creates indexes through translation, and no published
  SQLite bundle ran it, so no sidecar changed.
- Raw-text benchmarks (TSBS DevOps, Data Vault, NYC Taxi, Flight Data, Vector
  Search, Write Primitives, Metadata Primitives) now translate from DuckDB on
  every platform. The executed comparison on DuckDB, SQLite, DataFusion and
  PostgreSQL found no changed result, so no recorded row was wrong and no
  `known_defects` id was added.

## Fork 30.24.0.15 published-impact audit (2026-10-25)

- QuestDB unpadded date literals: QuestDB read `CAST('1999-5-01' AS DATE)` as
  NULL, so TPC-DS Q16, Q94 and Q95 (base and OBT) filtered on NULL date bounds.
  No QuestDB bundle exists (367 manifests checked), so no sidecar changed.
  DataFusion text is unchanged for dates, and ClickHouse (chDB 26.9.2.1) reads
  unpadded text, so ClickHouse bundles need no change.
- NULL ordering (fork #67): translation now keeps the source dialect's NULL
  placement where the target has no NULLS clause for the position. The audit
  found TPC-DS Q74 on T-SQL, Doris and StarRocks as the only query whose sort
  keys hold NULLs in the data checked. No published bundle ran an affected
  query with NULL sort keys, and no `known_defects` id was added. The
  explicit `NULLS LAST` on Snowflake, Databricks and BigQuery noted under the
  2026-10-24 audit preserves the DuckDB reference placement and is kept.
- Spark and Databricks (fork #69): `\1` regular-expression references are
  written `$1`, and sub-day `DATE_TRUNC` units are no longer written as
  `TRUNC`, which returns NULL for them. This fixes ClickBench Q29 (recorded
  0, 1 and 1 rows at SF 0.1, 1 and 10 against 0, 0 and 6) and Q43 (1 row at
  every scale against 2, 22 and 159) on Databricks. The three Databricks
  ClickBench bundles (`clickbench_sf01_databricks_sql_20261003_220608_70c9438e`,
  `clickbench_sf1_databricks_sql_20261003_230712_3a9e1d61`,
  `clickbench_sf10_databricks_sql_20261003_231900_2437b487`) recorded those wrong
  rows and now carry `known_defects: ["defective-superseded-sql-translation"]`
  (the existing id; no new id). Flagging the SF0.1 bundle leaves two ranked
  platforms (BigQuery and Snowflake) in the ClickBench SF0.1 cohort, so it is
  listed in `DOCUMENTED_SHALLOW_COHORTS` in `validate_corpus.py`. The bundles
  are not rerun or edited; replacing them means rerunning with the current
  wheel, which restores the cohort.

## Results that ran superseded SQL translations (2026-10-24)

BenchBox's SQL transpilation fix-ups moved into the permanent SQLGlot fork
(PRs #3185, #3235, #3239, #3257, #3260, #3291, #3294, #3295 and #3342, plus
the TPC-DS OBT v0.2 renderer in #3361/#3380). Every bundle in the corpus was
produced before those changes (bundles span 2026-08-23 to 2026-10-13), by
BenchBox 0.4.2 or earlier on upstream sqlglot 30.6.0 or 30.18.0. Comparing
the SQL each benchmark generated then with the SQL it generates now found 124
bundles whose executed SQL has since changed. 43 of them recorded wrong
values, wrong rows or queries that failed; the rest changed only in timing or
SQL text.

Eighteen bundles with wrong values from superseded SQL were still ranked.
Each now carries `known_defects: ["defective-superseded-sql-translation"]` in
its `.manifest.json` sidecar, which the explorer pipeline turns into
`ranking_exclusion_reason=known_defective_data`; the bundles stay available
for comparison. Two ClickHouse Local Data Vault bundles (below) were already
unranked under `defective-macdbgen-addresses`; they gained the same id without
changing ranking. Ten sidecars were created for bundles that had none, recording
`result_source: internal` so trust labels are unchanged; the other eight were
extended. `corpus-inventory.json` is untouched because no bundle was added or
removed. Replacing them means rerunning the cells with the current fork wheel,
not editing these bundles.

Flagged (short hash is the final segment of the bundle name):

- ClickHouse Local TPC-H SF1 `f22a0442`, `678507a6` (#3342, #3235): Q6's
  `BETWEEN 0.06 - 0.01 AND 0.06 + 0.01` bound was folded to a range that
  dropped `l_discount = 0.07` rows, Q8's quotient truncated in decimal
  division, and Q14/Q17 text changed.
- ClickHouse Local TPC-DS SF1 `c250741b`, `4eb148cf` (#3342, #3235): ROLLUP
  NULL keys and decimal division changed values in Q2, Q5, Q12, Q14, Q20, Q36,
  Q59, Q66, Q77, Q80 and Q98.
- DataFusion TPC-DI SF0.01, SF0.1, SF1 `b9971b30`, `51f0bade`, `85837db4`
  (#3239, #3260, #3291): DataFusion was translated as PostgreSQL and divided
  integers exactly as written. AQ8 returned 1 row against 2 to 3 on every
  other engine and EQ8 returned 0 (`45 / 50` is integer division on DataFusion).
- DataFusion TPC-DS SF1 `f8619d78`, `5207d224`, `6bed4cfc`, `754e0bfc` and
  SF10 `15b41887` (#3239, #3291): integer division gave wrong values in Q21,
  Q78 and Q83, and Q12, Q36, Q59, Q66 and Q98 differ in precision; 25 query
  texts changed.
- DataFusion CoffeeShop SF0.01, SF0.1, SF1 `bb01b471`, `387c45c7`, `e1266562`
  (#3291): QC1 and SA3 divided `SUM(quantity)` by `COUNT` as integers; SA1,
  SA4 and TR1 are equivalent.
- SQLite CoffeeShop SF0.01, SF0.1, SF1 `16f67069`, `3aadc9a8`, `5bd85b97`
  (#3257, #3291): the same QC1 and SA3 integer-division truncation.
- ClickHouse Local Data Vault SF0.01 and SF0.1 `c202ec05`, `b4fd5612` (fork
  30.24.0.12): Q6's `sat_lineitem` `BETWEEN 0.06 - 0.01 AND 0.06 + 0.01` bounds
  were evaluated as Float64 and dropped the `l_discount = 0.07` rows (SF0.01 sum
  734493.7281 against 1193053.2253 on DuckDB). These two were already unranked,
  keep their existing `defective-macdbgen-addresses` id and gain the new one;
  they are not among the eighteen ranked bundles counted above.

Flagging the DataFusion and ClickHouse Local TPC-DI SF0.01 bundles and the
DataFusion and SQLite CoffeeShop SF0.01 bundles leaves two ranked platforms in
those two cohorts (DuckDB and ClickHouse Local), so both are listed in
`DOCUMENTED_SHALLOW_COHORTS` in `validate_corpus.py` alongside the TPC-H
cohorts. They are restored by rerunning the flagged cells.

Already flagged, and also ran superseded SQL: 24 bundles keep their existing
ids and gain no new one. They are the 22 ClickHouse Local, ClickHouse Server
and SQLite TPC-H, TPC-H skew and TPC-Havoc bundles (Q6 bound fold in #3342
for ClickHouse; SQLite Q6 `BETWEEN` float bounds fixed in #2512 and moved to
the fork by #3239, with the Q8 cast in #3257), the two SQLite read_primitives
bundles whose `timeseries_trend_analysis`, `olap_cube_analysis`,
`olap_rollup_analysis`, `statistical_variance_stddev`, `groupby_all_complex`
and `decimal_arithmetic` queries failed before #3291 and now run, and the six
SF1 TPC-DS OBT bundles (#3361/#3380). The ones carrying
`defective-macdbgen-addresses` or `defective-tpcds-obt-parameters` stay
unranked for that reason too.

Changed SQL but no flag (results are not wrong; do not read these as
defects):

- ClickHouse Local decimal-division and `safe_division` text (#3342, #3235)
  changed values only at decimal precision or on a zero divisor: CoffeeShop,
  NYC Taxi, FlightData, TPC-DI, read_primitives and DataVault bundles (16).
  Rerun them with the ClickHouse batch.
- DataFusion TPC-H Q11/Q16/Q18/Q20 rewrites (#3294) and the Q8 cast (#3291):
  timing only; Q16 row counts equal DuckDB's (296, 2762, 18314). Same for
  DataFusion TPC-H skew Q8, TPC-Havoc `1_v4`/`8_v*` and read_primitives
  `decimal_arithmetic` (precision or timing).
- DataFusion ClickBench SF1 `8014d1c9` (#3185) ran untranslated ClickHouse
  text; 27 of 43 query texts change, all 43 row counts match DuckDB.
- SQLite ClickBench SF1 `6eb94fe6` (#3185, #3291): Q19 and Q43 failed before
  and now run.
- TPC-DI EQ7 (#3294): the query now uses a derived-table form. Row counts are
  unchanged at 3 and the changelog says to treat the old text as superseded.
  It affects BigQuery, Databricks and Snowflake at SF0.1, SF1 and SF10 and the
  DuckDB, DataFusion and ClickHouse Local runs. A whole-bundle flag would
  unrank every query in those bundles for one changed query, so EQ7 is
  recorded here only.
- TPC-DS Q58 (#3295, qualified `ORDER BY ss_items.item_id`) on BigQuery,
  Databricks and Spark, and Q90 (#3342, guard placement) on every SQL TPC-DS
  bundle except ClickHouse and SQLite: no failure and no wrong row count was
  observed. Q90's guard only matters when `pmc = 0`, and every run returned
  one row.
- ClickHouse timing boundary (#3235): the sub-millisecond parse now sits
  outside the timer. It touches the AMPLab, H2ODB, SSB, JoinOrder and
  ClickBench ClickHouse Local bundles (11) and changes no value.

Other issues found while checking, outside these PRs:

- SQLite ClickBench SF1 `6eb94fe6` returns row counts different from the other
  nine engines for 26 of 43 queries; see "SQLite ClickBench quoted empty
  strings" below.
- Databricks ClickBench SF1 Q29 and Q43 differ from the other engines. This is
  the case already described under Cloud ClickBench (2026-10-04).
- Since v0.4.1, Snowflake and Databricks translate `DESC` ordering to
  `DESC NULLS LAST` (TSBS DevOps and DataVault Q11), which can reorder rows
  containing NULLs.
- SQLite zero-padded date literals (fork 30.24.0.13): TPC-DS Q16, Q94 and Q95, base
  and OBT, filtered on `NULL` date bounds on SQLite, and TPC-Havoc `1_v7` failed
  there. No published SQLite bundle is affected: the only SQLite TPC-DS bundle
  (`tpcds_sf1_sqlite_sql_20260824_083944_c6745a86`) ran query 1 alone, and there
  is no SQLite `tpcds_obt` or `tpchavoc` bundle. No sidecar changed.
- SQLite `GROUP BY ()`, PostgreSQL and DataFusion TPC-Havoc `1_v7`, and PostgreSQL TPC-DS OBT Q2, Q36, Q70, Q78 and Q86 (fork 30.24.0.16): these failed on the engines with syntax or function errors. No published bundle is affected: there are no PostgreSQL bundles and no SQLite `tpchavoc` or `tpcds_obt` bundle, and the DataFusion TPC-Havoc bundles skipped `1_v7`, so they recorded no wrong rows. No sidecar changed.
- ClickHouse DataVault Q6 was wrong because the Q6 bound fold applied only to
  the `lineitem` table, so `sat_lineitem` kept `BETWEEN 0.06 - 0.01 AND
  0.06 + 0.01`. Fork 30.24.0.12 folds every table and column, so rerunning
  DataVault on the current wheel fixes it; the two bundles are flagged above.

## TPC-Havoc cross-variant row-count divergence audit (2026-10-17)

The three SF=0.01 TPC-Havoc SQL bundles (`tpchavoc_sf001_duckdb_sql_20260826_163147_d96baca2.json`,
`tpchavoc_sf001_datafusion_sql_20260826_165844_721d2f5e.json`, and
`tpchavoc_sf001_clickhouse_local_sql_20260826_171608_ce266327.json`) ran `16_v6` before its
cross-joined `excluded` supplier subquery (`ps_suppkey <> excluded_suppkey`) was replaced with
`NOT IN`. Because SF=0.01 has zero suppliers matching `'%Customer%Complaints%'`, the cross-join
produced `rows: 0` with `status: SUCCESS` while the remaining Q16 variants returned `rows: 296`.
In addition, the August 2026 DataFusion (`SF=0.01`, `0.1`, `1.0`) and ClickHouse Local (`SF=0.01`,
`0.1`, `1.0`) TPC-Havoc bundles recorded pre-fix `15_v*` float-equality or ClickHouse
`2_v8`/`3_v7`/`7_v7`/`9_v7`/`10_v7`/`10_v8` row-count divergences across variants of the same
base query.

All seven `.manifest.json` sidecars now carry `defective-tpchavoc-variant-divergence` in
`known_defects`. Bundle validation (`benchbox/validation/bundle.py`) now enforces cross-variant
row-count agreement across all successful measurement variants of each base query in a
`tpchavoc` bundle, rejecting any new submission with divergent variant row counts.

## TPC-DS OBT parameter defect (2026-10-16)

The six TPC-DS OBT bundles at SF1 (BigQuery, ClickHouse Local, Databricks,
DataFusion, DuckDB and Snowflake) ran queries whose `dist()` and `sub()`
template parameters were rendered as `0` and whose other parameters were
fixed template defaults instead of the dsqgen draw. For example, Q93 filtered
on `r_reason_desc = '0'`, so it and many other statements returned no rows.
Each bundle's `.manifest.json` sidecar now carries `known_defects:
["defective-tpcds-obt-parameters"]`; three sidecars were created for bundles
that had none, recording `result_source: internal` so trust labels are
unchanged. The explorer pipeline removes these bundles from rankings; they
remain available for comparison. Query rendering now takes every parameter
from the dsqgen parameter log for the run's seed and scale, as TPC-DS does.
Rerunning the six cells with the fixed renderer, then ingesting the new
bundles, is a follow-up.

## SQLite ClickBench quoted empty strings (2026-10-25)

The SQLite loader split ClickBench rows on `|` without reading CSV quoting, so
the generator's quoted empty string `""` was stored as the two characters `""`
instead of an empty string, and a quoted field holding `|`, a quote or a
newline would have shifted columns. Current develop at SF1 (1,000,000 rows, the
same generated file loaded into SQLite and DuckDB) matched DuckDB on 24 of 43
queries before the fix and returned 10 rows against 9 for Q11 and Q12, and
wrong values for `SearchPhrase <> ''` and `MobilePhoneModel <> ''` groups. The
loader now parses quoted fields with the manifest quote character, and every
non-empty SQLite result matches DuckDB.

Published bundle `clickbench_sf1_sqlite_sql_20260824_101152_6eb94fe6` recorded
wrong rows for 23 queries (Q8 1 row against 20, Q9, Q10, Q16 to Q18, Q34 to
Q36 one row against 8 or 10, Q11 to Q15, Q22, Q24 to Q27, Q31, Q32, Q37 and
Q38 zero rows against 8 or 10) and failed Q19, Q29 and Q43. Its sidecar now
carries `known_defects: ["defective-sqlite-clickbench-row-counts"]`; the
bundle file is unchanged. The recorded counts are lower than a current-code
run produces, so the bundle was loaded with older code or data; which one is
not established. Rerunning the cell and ingesting the new bundle is a
follow-up.

## DuckDB version matrix (operator-run, 2026-08-29)

The Results Explorer corpus includes a reproducible DuckDB version-over-version matrix at
TPC-H, TPC-DS, ClickBench, and SSB SF10. BenchBox's ClickBench generator creates
synthetic data linearly from the scale factor, so it is measured at SF10 like the
other workloads. It uses three independent power runs per cell and reports medians.
The published corpus has one median bundle per version/benchmark cell (28 bundles total);
the 84 raw repetitions are retained only in the external operator output. The package
points are DuckDB 1.0.0, 1.1.3, 1.2.2, 1.3.2, 1.4.4, 1.5.5, and
1.6.0.dev365 (the current latest 1.6 development wheel at capture time).

The runner and analyzer are `scripts/run_duckdb_version_matrix.py` and
`scripts/analyze_duckdb_version_matrix.py`, which run the DuckDB spec of the engine-neutral
`scripts/run_version_matrix.py` and `scripts/analyze_version_matrix.py`. The analyzer's
`--explorer-bundles-dir` option creates the 28 median bundles for promotion. All raw
data, databases, logs, and analysis outputs remain in the external operator output
directory; only those anonymized median bundles are promoted here. DuckDB development wheels can report a separate internal
engine build string, so the raw bundle retains it while Explorer identifies the run by
the resolved package version.

The 28 promoted median bundles carry maintainer-run submission manifests with
per-bundle SHA-256 hashes. Their Apple M4 CPU identity is an operator attestation,
not a recovered measurement: the retained raw matrix archive records the same
Darwin/arm64 client host as the standing single-machine corpus attestation, while
the historical capture path omitted CPU model and vendor. The exact provenance
rewrite and old-to-new result IDs are recorded in
`bundles/cpu-identity-attestation-pr-1946.manifest.json`.

## Platforms Run

### TPC-H (SF 0.01)
- **DuckDB 1.4.3** - PASSED, 22 queries, 3 measurement runs
- **DataFusion 51.0.0** - PASSED, 22 queries, 3 measurement runs
- **Polars 1.37.1** - PASSED, 22 queries, 3 measurement runs (DataFrame mode)

### SSB / Star Schema (SF 0.01)
- **DuckDB 1.4.3** - PASSED, 13 queries, 3 measurement runs
- **DataFusion 51.0.0** - PASSED, 13 queries, 3 measurement runs
- **SQLite 3.50.4** - PASSED, 13 queries, 3 measurement runs

## Skips and Notes

### Polars-df SSB - Skipped (0 queries)
`polars-df` with SSB benchmark emitted 0 queries during execution ("No queries
found for execution"). SSB DataFrame queries are not implemented for the Polars
platform. SQLite was used as the third platform for SSB instead.

### Export bug fix
The `benchbox export` command failed with `TypeError: type NoneType doesn't
define __round__ method` when `load_time_ms` was `None` in `table_statistics`.
Fixed in `benchbox/core/results/schema.py` by guarding `round()` calls with
`is not None` checks.

### Export --last filter caveat
`benchbox export --last --benchmark ssb --platform duckdb` did not find SSB
results because the schema v2 `benchmark.id` field is `star_schema` (not `ssb`).
The `--last --benchmark` filter compares against the JSON `benchmark.id`, so
results were exported directly by filename.

## Cohort Depth
Both cohorts meet the >=3-platform depth criterion required for the Compare view:
- `tpch SF=0.01`: DuckDB, DataFusion, Polars
- `star_schema SF=0.01`: DuckDB, DataFusion, SQLite

## Execution-variant identity (schema 2.3 and later)
Bundles that record `platform.variant` count toward cohort depth under the variant
string; every bundle committed here predates that field, so all existing identities
are unchanged platform names and no cohort gains or loses depth from the rename.
Gateway-routed bundles are excluded from native ranking with
`ranking_exclusion_reason=gateway_routed` and contribute no identity.

## Execution-class identity

Where a run executed changes its hardware, so a ranking identity also carries the
execution class: `native` (a host or a cloud VM) or `container` (a Linux container,
including one on a Mac). A `container` bundle gets a `^container` suffix after the
engine and gateway suffixes (`clickhouse-local^container`) and its Explorer label reads
`ClickHouse Local (Linux container on macOS)` (without `on macOS` when the host OS is unknown). A `native` bundle, and every bundle that records
no class, keeps the plain identity, so no historical identity or cohort count moves.

- New runs record `execution_class` (plus `container_runtime`, `container_host_os` and
  `execution_class_source`) in `environment` and `environment.client_host`, next to
  `os`, `cpu_count` and `memory_gb`. Detection reads `/.dockerenv`,
  `/run/.containerenv`, `KUBERNETES_SERVICE_HOST` and PID 1's cgroup
  (`execution_class_source: observed`). A Linux kernel on an Apple CPU vendor with no
  marker is recorded as a `container` on a `macOS` host with source `inferred`. A Linux
  host with neither is recorded as `native` with source `inferred`, because absence of a
  marker does not prove bare metal; a non-Linux host is `native` with source `observed`.
  `BENCHBOX_CONTAINER_HOST_OS` overrides the host OS. Cloud and other VMs are not told
  apart from bare metal and stay `native`. When Docker runtime discovery finds the engine's
  container (`platform_runtime.runtime_type: docker_container`, collection `available`, source
  `observed`, and the container publishing the engine's host port: `container.host_port_matched`) and
  the client is macOS or Windows, where Docker runs in a Linux VM, the run is recorded as a
  `container` with runtime `docker`, the client's host OS and source `observed`. On a Linux
  client Docker shares the host kernel and the class is not changed.
- A bundle that predates the field can be attested in its `.manifest.json` sidecar with
  `execution_class`, `execution_class_evidence` (required) and optionally
  `container_runtime` and `container_host_os`. The sidecar only fills a bundle that
  records no class or whose recorded class has source `inferred`; a conflicting
  `observed` class is an error. Bundle JSON is never edited.
- `validate_corpus.py` and the Explorer pipeline both call
  `benchbox.core.results.execution_variant.published_variant_id` with the bundle path,
  so they cannot disagree; `tests/unit/scripts/test_corpus_execution_class_identity.py`
  holds that parity.
- Local comparison (`ExecutionVariant`) treats a native run and a container run of the
  same engine as incomparable.

### Annotating Linux-guest-on-Apple-silicon bundles

The ClickHouse Local reruns of 2026-10-26 record `Linux 6.18.15`, 4 CPUs, 9.8 GB and
CPU vendor Apple, with no container marker: they ran in a Linux container on a Mac, not
on the native macOS host. `scripts/annotate_execution_class.py` finds bundles that
record a Linux OS on an Apple CPU vendor and no execution class and attests the class in
their manifest sidecars only:

```bash
uv run -- python scripts/annotate_execution_class.py
uv run -- python scripts/annotate_execution_class.py --write
uv run -- python scripts/annotate_execution_class.py --write --attest-docker-servers
uv run -- python results-data/validate_corpus.py
```

By default the tool selects bundles that record no class and a Linux OS on an Apple CPU
vendor. With `--attest-docker-servers` it also selects a self-hosted `localhost_port`
server engine driven from a Darwin client whose Docker discovery was attempted but failed
(`platform_runtime.runtime_type: unknown` with a `container` block). That second rule
records the maintainer's knowledge that the engine ran in Docker on the Mac; the bundles
themselves only show `docker_not_found`, so the sidecar evidence says so. It covers CedarDB,
ClickHouse Server and StarRocks (TPC-H SF 0.01, known-defective and unranked) and Apache
Doris (TPC-H SF 1) and attests `container_runtime: docker`. LakeSail, which also listens on
localhost, records `local_process` and is not selected.

The first command lists the sidecars it would change; the second writes them. The
already-published `tpcds_obt_sf1_clickhouse_local_sql_20261026_132451_e8646728` bundle
has the same environment and carries the attestation; it is unranked, so no cohort moves.

## Zero-query DataFrame withdrawal (2026-08-24)

Sixty legacy DataFrame bundles were withdrawn because they reported
`summary.validation=passed` after executing zero queries. They are
non-measurements, not truthful partial results. Each bundle's manifest was
removed with it.

Removing only those bundles would have left ten represented cohorts below the
three-platform corpus floor. Because truthful replacements were not yet
available, the remaining 17 SQL bundles in those cohorts were temporarily
withdrawn too; those 17 were not classified as invalid. This preserves the
cohort invariant without counting empty results as coverage:

- AMPLab SF 0.1 and 1.0
- CoffeeShop SF 0.1 and 1.0
- H2O-DB SF 0.1 and 1.0
- SSB SF 1.0
- TSBS DevOps SF 0.01, 0.1, and 1.0

The removal commit contains the exact 77-bundle and 77-manifest path list.
Restore a cohort only with at least three truthful platform results. Fresh
DataFrame results additionally require real validation evidence; execution
success alone is not a validation pass. NYC Taxi and TSBS DevOps regeneration
also waits for their native temporal-literal fix.

## Legacy validation-claim normalization (2026-08-25)

The 136-bundle develop corpus contained 52 legacy bundles whose
`phases.validation.status` was `NOT_RUN` while `summary.validation` claimed
`passed` (23 bundles) or `partial` (29 bundles). These are historical claims,
not rerun evidence. Their summary status is now `not_run`; their query timing
and failure records remain unchanged.

This preserves truthful partial measurements as non-ranking capability
evidence. It does not promote failed queries or infer validation results. A
future rerun may replace the `not_run` claim only when the validation phase
records actual evidence. Submission admission also rejects a `passed` or
`partial` summary claim paired with an unrun validation phase.

## Pre-2026-08-23 results withdrawn (2026-08-28)

All 130 bundles run before 2026-08-23 were removed from the develop corpus,
together with their 114 `.manifest.json` sidecars (244 files). What remains is
9 bundles across 3 cohorts.

**This was a trust decision, not a soundness finding.** Unlike the 2026-07-16
tuned drop (#1176 proved the tuning config never reached platform adapters) and
the 2026-08-24 zero-query withdrawal (bundles claimed `passed` after executing
nothing), no defect was found in the removed results. The maintainer no longer
trusts measurements taken before 2026-08-23 and asked for them to be withdrawn.
Do not go looking for a bug report; there isn't one.

What went:

- 12 bundles from the original 2026-04-03/04 corpus generation
- 111 bundles from the 2026-05-02 maintainer UAT sweep (committed in #164)
- 3 JoinOrder bundles from the 2026-05-12 canonical UAT
- 4 SSB seed-lane bundles from 2026-07-30

Removing them would have left TPC-H SF1 holding only Polars and PySpark, below
the three-platform floor in `validate_corpus.py`. A fresh maintainer DuckDB run
at TPC-H SF1 (DuckDB 1.3.2, 66 queries, 0 failed, validation PASSED) was added
in the preceding commit to hold the cohort. It also gives that cohort a SQL
reference point against two DataFrame-mode results.

The three migration manifests in `bundles/` were deliberately kept:
`path-privacy-migration.manifest.json` is the `DEFAULT_MANIFEST` in
`_project/scripts/results_explorer_corpus_migrate.py` and is name-referenced by
`sync-results-data-to-published.yml`. They are tooling audit records, excluded
from bundle discovery by `COMPANION_SUFFIXES`.

Restoring any withdrawn cohort means fresh runs, not reverting this commit.
`REGENERATION.md` is the precedent for how to document what a restore needs.
The removal commit carries the exact 244-path list.

## Cloud TPC-H results withdrawn (2026-09-25)

The nine TPC-H bundles from the first live cloud runs (BigQuery, Databricks,
and Snowflake at SF 0.1, 1, and 10, run 2026-09-18/19) were removed from the
corpus with their nine `.manifest.json` sidecars (18 files). The maintainer
identified the published results as incorrect. They must not be shown in the
Results Explorer or used for comparisons.

Removed public result IDs:

- `tpch-bigquery-sf0.1-20260918-97c7acc0`, `tpch-bigquery-sf1.0-20260918-47b8218f`,
  `tpch-bigquery-sf10.0-20260918-ad3f00af`
- `tpch-databricks-sf0.1-20260918-45dbaeea`, `tpch-databricks-sf1.0-20260918-01a3150a`,
  `tpch-databricks-sf10.0-20260918-a0ee4f11`
- `tpch-snowflake-sf0.1-20260919-c5d751f5`, `tpch-snowflake-sf1.0-20260919-11abeacf`,
  `tpch-snowflake-sf10.0-20260919-08fb3536`

The TPC-H SF 0.1 and SF 1 cohorts keep at least three local platforms. The
SF 10 cohort retains its seven local DuckDB version-matrix results; only
cloud-platform coverage at SF 10 is gone.
The same paths must also be removed from `published-results` by a
deletion-only PR, because the mirror's union overlay does not propagate
develop-side deletions. Restoring cloud coverage means fresh runs, not
reverting this removal.

## Live cloud corpus (2026-10-02)

84 maintainer-run bundles from live BigQuery, Snowflake and Databricks runs
(2026-09-20 to 2026-10-01) were added with `result_source: internal`
submission manifests. They cover 28 benchmark/scale cohorts, one bundle per
platform in each, so every cohort meets the three-identity floor:
nyctaxi, flightdata, tsbs_devops, tpch_skew, datavault and tpchavoc at SF 0.1,
1 and 10; tpcds_obt at SF 1; read_primitives at SF 0.01 and 0.1;
write_primitives and transaction_primitives at SF 0.01, 0.1 and 1; and
metadata_primitives at SF 1. This is the first coverage for
metadata_primitives, write_primitives, transaction_primitives and tsbs_devops.

Before publication, every bundle was re-run through the public anonymiser
(`_project/scripts/results_explorer_corpus_migrate.py`) after it gained rules
for the Databricks `warehouse_id` and saved cloud `default_output_location`,
which earlier runs had stored raw. Only those fields and the content-derived
result IDs changed.

What these results are and are not:

- Databricks and Snowflake runs disabled the result cache per session and
  record a validated cache receipt; BigQuery jobs run with
  `use_query_cache=False` (not recorded in the bundle).
- FlightData SF 10 uses real BTS months only.
- Three BigQuery bundles carry reviewed validator overrides
  (`<bundle>.override.json`): `timing-plateau` for tpch_skew SF 0.1, and
  `small-scale-floor` for transaction_primitives SF 0.01 and SF 0.1.
- Write and transaction bundles raise the `result-rows-empty` warning because
  DML operations report no result rows; operations the benchmark catalog marks
  unsupported on a platform (for example `RETURNING`, bulk loads) are
  recorded as SKIPPED, not failed.
- Five older BigQuery bundles (nyctaxi SF 10, flightdata SF 0.1/1/10,
  tpch_skew SF 10) predate the `provenance.source` field; their manifests
  record `result_source: internal`.

## Live cloud corpus, second batch (2026-10-03)

19 maintainer-run bundles from live BigQuery and Snowflake runs (2026-09-19 to
2026-09-20) were added with `result_source: internal` submission manifests.
Each joins an existing cohort, so the cohort count is unchanged:
SSB at SF 0.1, 1 and 10 on Snowflake; AMPLab at SF 0.1 and 1 and JoinOrder at
SF 1 on BigQuery and Snowflake; ClickBench at SF 1 on BigQuery and Snowflake;
and CoffeeShop and H2ODB at SF 0.1 and 1 on BigQuery and Snowflake.

The runs had no submission manifests. Each manifest was written from the
original run output, and every bundle was re-run through the public anonymiser
(`_project/scripts/results_explorer_corpus_migrate.py`), so no raw bucket,
dataset or warehouse name remains.

What these results are and are not:

- Every bundle passes `scripts/validate_submission.py --require-manifest` with
  no error, warning or override.
- BigQuery jobs ran with the query cache disabled in configuration. Snowflake
  runs declare the result cache off in configuration. Neither bundle records a
  cache-control receipt, so both claims rest on the declared setting.
- ClickBench timings move little across scale: from SF 0.1 to SF 10 the
  geometric mean grows 1.45x on BigQuery and 1.33x on Snowflake. BenchBox's
  generator creates 1,000,000 rows per scale factor, so fixed per-query
  overhead dominates. Only the SF 1 cells are included.
- Not included: Databricks cells (the runs enabled the result cache and have
  no receipt), BigQuery SSB (needs validator overrides), ClickBench SF 0.1 and
  SF 10 (scale-invariant), TPC-DS and TPC-DI. TPC-H is covered in the next
  section.

## Live cloud TPC-H, fresh runs (2026-10-03)

Nine maintainer-run TPC-H bundles from live runs on 2026-10-03 were added with
`result_source: internal` submission manifests: SF 0.1, 1 and 10 on BigQuery,
Snowflake and Databricks. They replace the cloud coverage withdrawn on
2026-09-25 and were run on code that includes the cloud fixes made since:
Snowflake and Databricks session cache receipts, load hardening, region
capture and the Snowflake query-history fix.

What these results are and are not:

- The SF 1 and SF 10 runs used `--official` with `--seed 42` and are stamped
  `compliance_class: official`. The SF 0.1 runs are
  `compliance_class: unofficial_subscale`, as every sub-SF-1 TPC-H run is. They
  were written with suppressed TPC metrics, so the Explorer and
  `validate_corpus.py` derive their Power@Size from the exported final-iteration
  timings, mark it derived, and rank them labelled Subscale · Unofficial. No
  QphH@Size is computed for them.
- Four bundles pass `scripts/validate_submission.py --require-manifest` with
  no error, warning or override: SF 1 on all three platforms and SF 10 on
  BigQuery. Snowflake SF 10 and Databricks SF 10 pass with the overrides
  described below. The three SF 0.1 bundles fail the submission class gate for
  the reason above and are admitted through the lenient mirror lane.
- Snowflake and Databricks runs disabled the result cache per session and
  record a validated cache receipt with the cache disabled. BigQuery jobs ran
  with the query cache disabled in configuration.
- Row counts match the TPC-H scale factor (LINEITEM 600,572 at SF 0.1,
  6,001,215 at SF 1 and 59,986,052 at SF 10) and all 22 queries ran in every
  bundle.
- Two bundles carry reviewed validator overrides (`<bundle>.override.json`):
  `scale-invariant` for Snowflake SF 10 and for Databricks SF 10. On both
  platforms the geometric mean grows about 1.35x from SF 0.1 to SF 10 while
  LINEITEM grows 100x. The result cache is confirmed off and measurement runs
  at 0.8 to 0.9 of warmup, so per-query overhead explains the flat timings.
- The BigQuery SF 0.1 bundle also shows a `timing-plateau` finding (per-query
  means span 824 to 1173 ms, a fixed per-job latency floor); it is carried in
  the lenient lane without an override because the class gate already applies.

## TPC-DI cloud data comparison (2026-10-03)

Four TPC-DI SQL bundles cover SF 0.1 and SF 1 on BigQuery and Snowflake. The
BigQuery measurements were retained from 2026-09-20. Snowflake loaded fresh
data generated with seed 42 and generation algorithm version 1 on 2026-10-03.
All four bundles execute the complete 38-query set without failures and pass
strict submission validation with their manifests without overrides. SF 10
and the Databricks bundles are described in the next section.

At each scale, all sixteen table row counts agree across the two platforms.
Total rows are 299,864 at SF 0.1 and 1,442,264 at SF 1.
QVQ12 and QAQ7 result row counts also agree. The Snowflake runs record validated
cache-off receipts; the retained BigQuery configuration records its query cache
as disabled.

These checks establish agreement in recorded table counts and the two named
query counts. They do not establish full result-value or dataset-content
equivalence. The retained BigQuery bundles have no generation-version or
generation-hash fields. At SF 1, existing local TPC-DI bundles report QVQ12=9
and QAQ7=1, while these cloud bundles report QVQ12=32 and QAQ7=2. Their timings
must be assessed with that result difference in mind.

The public anonymization pass preserves query results, timings, table counts
and phase evidence. Submission manifests record the hashes of the public
bundles. Original measurements remain in the operator output archive.

## BigQuery SSB timing (2026-10-03)

Three BigQuery SSB SQL bundles from 2026-09-19 cover SF 0.1, SF 1 and SF 10.
Query caching is recorded off, and measurement runs at 0.95 to 0.97 of warmup.
Loaded rows grow from 625,757 to 62,322,557 and scanned bytes from 0.846 GB to
84.476 GB, while the geometric mean grows only 1.37x (894 to 1,228 ms) and the
median per-query mean 1.34x. At SF 0.1 and SF 1, per-query means span 758 to
1,142 ms and 765 to 1,070 ms.

These results are consistent with fixed per-job and client latency dominating
light queries. Provider queue and execution timings were not recorded, so that
explanation is an inference. The SF 0.1 and SF 1 bundles carry approved
timing-plateau overrides, and the SF 0.1 and SF 10 bundles carry approved
scale-invariant overrides.

## TPC-DI at SF 10 and on Databricks (2026-10-04)

This adds TPC-DI SF 10 on BigQuery and Snowflake, and SF 0.1, SF 1 and SF 10 on
Databricks, to the four TPC-DI cloud bundles described above. Databricks loaded
data generated on 2026-10-03 with seed 42 and recorded validated cache-disabled
receipts. All nine cloud bundles execute the complete 38-query set without
failures and pass strict submission validation, including combined scale
validation, without overrides.

At each scale, all sixteen table row counts agree across the three platforms.
Total rows are 299,864 at SF 0.1, 1,442,264 at SF 1 and 11,891,664 at SF 10.
QVQ12 and QAQ7 result row counts also agree across the three platforms at every
scale (100 and 1 at SF 0.1, 32 and 2 at SF 1, 1 and 1 at SF 10). The caveats of
the section above still apply: these checks do not establish full result-value
equivalence, and the local TPC-DI bundles return different QVQ12 and QAQ7 counts.

## Cloud TPC-DS bundles (2026-10-04)

Nine TPC-DS SQL bundles cover SF 0.1, SF 1 and SF 10 on BigQuery, Snowflake and
Databricks. The BigQuery measurements were retained from 2026-09-20. Snowflake
and Databricks were rerun on 2026-10-03 with validated cache-disabled receipts.
The earlier Snowflake runs had a 4.2-second per-query floor from a harness
defect, and the earlier Databricks runs had the result cache on; these runs have
neither problem. All three platforms load the same row counts at each scale
(1,176,171 at SF 0.1, 19,557,376 at SF 1 and 191,496,659 at SF 10), and every
query part succeeds.

Five bundles are unofficial and keep their recorded classifications. The SF 0.1
bundles on all three platforms are `unofficial_subscale`, and BigQuery SF 1 and
SF 10 are `unofficial_nonstandard` because they ran without `--official`. They
make no official TPC-DS claim and are carried in the lenient publication lane.
Snowflake and Databricks SF 1 and SF 10 ran with `--official --seed 42`.

On Snowflake, scanned bytes grow from 1.047 GB to 51.102 GB between SF 0.1 and
SF 10 while the geometric mean grows only 1.38x (899 to 1,239 ms). The limited
scaling is consistent with per-query overhead and warehouse performance at these
data volumes; provider timings were not recorded, so the cause is not fully
decomposed. The Snowflake SF 0.1 and SF 10 bundles carry approved
scale-invariant overrides. Databricks timings grow 1.57x over the same range and
need no override.

## Cloud ClickBench (2026-10-04)

Seven ClickBench SQL bundles add SF 0.1 and SF 10 on BigQuery and Snowflake, and
SF 0.1, SF 1 and SF 10 on Databricks, beside the published BigQuery and
Snowflake SF 1 bundles. ClickBench loads 100,000, 1,000,000 and 10,000,000 rows
at SF 0.1, 1 and 10. All 43 queries complete at every scale. Caching is recorded
off on BigQuery and Snowflake, and Databricks records validated cache-disabled
receipts.

Across the 100x row increase, the BigQuery geometric mean grows 1.45x while
scanned bytes grow from 0.690 GB to 51.713 GB. The Snowflake geometric mean
grows 1.33x, and its output row counts match BigQuery for every query at every
scale. Measured client statement overhead is 364 to 408 ms on BigQuery and 82
to 90 ms on Snowflake, and the heavier queries grow at least 2x on both. This
is consistent with fixed job and client overhead dominating the lighter
queries rather than cached results or a load defect. Provider queue and
execution phases were not captured. Each BigQuery and Snowflake SF 0.1 and
SF 10 bundle carries an approved scale-invariant override. The Databricks
geometric mean grows 1.68x (366 to 616 ms) and needs no override.

Databricks does not return the same results as BigQuery and Snowflake for two
queries. Q29 returns one row at SF 1 and SF 10, where BigQuery and Snowflake
return 0 and 6, because the Databricks SQL keeps the `\1` regular-expression
backreference instead of Spark's `$1`, so every referrer falls into one group.
Q43 returns one row at every scale, where BigQuery and Snowflake return 2, 22
and 159; its cause has not been identified. The other 41 queries return the
same row counts on all three platforms. Q29 and Q43 timings on Databricks
measure different work and are not comparable with the other platforms.

## Databricks cache-off reruns (2026-10-04)

Ten Databricks SQL bundles add SSB SF 0.1, 1 and 10, AMPLab SF 0.1 and 1,
JoinOrder SF 1, CoffeeShop SF 0.1 and 1, and H2ODB SF 0.1 and 1 to cohorts that
already hold BigQuery and Snowflake bundles. The earlier Databricks runs of
these cells had the result cache on and were not published. These runs on
2026-10-03 record validated cache-disabled receipts and region us-east-2. Every
query succeeds in three measurement rounds, each bundle loads the same total row
count as the Snowflake bundle of its benchmark and scale, and all ten pass
strict submission validation, including combined scale validation, without
overrides.

## Public-path single-pass status (2026-08-05)

Verified with `results_explorer_corpus_migrate.py` dry-run: 0/207 bundles changed under the current public anonymization pass. The `test_rederiv_fresh_public_pass_equals_curated_for_all_fields` gate pins the fixed point.

---

# Regeneration - strip residual empty `client_host` (2026-08-05 / 2026-08-06)

**Related PR:** #1614 (`fix/strip-empty-client-host-corpus`)
**Date:** 2026-08-05 (local) / 2026-08-06 (UTC commit)

## Reason

After `machine_id` was dropped from public environment maps, some already
public-shaped primary bundles retained residual empty `client_host: {}`
objects. Those hollow maps were identifier-only leftovers, not real host
profiles. Anonymization policy now **always omits empty optional environment
maps** (including already-empty `{}` residuals) so the public shape has no
hollow blocks.

## What landed together

Code change and corpus re-derive shipped in one fixed-point commit so stored
bytes match the fresh public shape:

1. **Policy** (`benchbox/core/results/anonymization.py`): omit empty optional
   maps under `_PUBLIC_EMPTY_OPTIONAL_MAP_KEYS` even when the stored input was
   already `{}` (not only when non-empty content was stripped to empty).
2. **Corpus**: re-derived **105** primary bundles under
   `results-data/bundles/` (plus inventory refresh) so checked-in bytes match
   re-anonymization output. Count verified as the primary-bundle delta vs
   `origin/develop` on this branch (105 `results-data/bundles/*.json` primaries;
   not plans/tuning/manifest sidecars).

No full corpus rewrite beyond those residual hollow maps; non-empty
`client_host` profiles and unrelated fields were left alone. Anonymization
policy was not expanded beyond empty optional map omission.

## How to verify

- Unit: `tests/unit/core/results/test_anonymization.py` —
  `test_already_empty_client_host_is_omitted` (and related public-unread
  identifier drop cases).
- Corpus fixed point: re-anonymize primary bundles and assert byte-identical
  publication (existing re-derived / fixed-point corpus gates; no empty
  `client_host` objects remain in primary bundles).
- Spot check: `rg -n '"client_host": \{\}' results-data/bundles` should not
  match residual hollow maps in primary result JSON.


## CPU identity backfill (2026-08-29) — OPERATOR ATTESTATION, NOT MEASURED

Every bundle in this corpus now carries `cpu_model: "Apple M4"`,
`cpu_vendor: "Apple"`, and `cpu_identity_provenance: "user_attested"`, which
the Explorer read model normalizes to the family `apple_silicon`. **These values
were not measured. They are an operator attestation.**

### Why no measured value exists

The capture path was defective, in three independent ways:

1. `get_system_info` sourced `cpu_model` from `platform.processor()`, which on
   Darwin returns the bare architecture `"arm"`. `normalize_cpu_family("arm")`
   is `"unknown"`, so even where a value was recorded it said nothing.
2. `SystemInfo.to_dict` emitted `cpu_cores` / `total_memory_gb` / `os_version`
   while `ClientHostEnvironment.from_system_profile` reads `cpu_count` /
   `memory_gb` / `os_release`, so those three were silently dropped from every
   bundle, and `cpu_vendor` was never produced at all.
3. The DataFrame adapters descend from a hierarchy that never runs the SQL
   path's environment capture, so 43 of these 151 bundles recorded no client
   host whatsoever.

Defects 1 and 2 are fixed in `fix/cpu-identity-capture-source`. Defect 3 is
tracked as `dataframe-client-host-capture-gap`. Across the 3,845 raw local
results only 4 carry a CPU, and all four post-date those fixes — so there was
nothing in the archive to recover.

### The attestation

The project maintainer attests that every run in this corpus executed on a
single machine — natively, or driving Apple container Linux images whose
engines share that host's CPU. No other machine has been used in the project's
development.

Recorded evidence is consistent with it but does not by itself establish the
model: every bundle that records a client host records `Darwin`/`arm64`, and
the raw local archive shows a single `machine_id`. `arm64` + `Darwin` implies
Apple Silicon; it does not distinguish an M1 from an M4. The specific model
rests on the attestation alone.

### What was and was not written

Only the CPU identity and its typed provenance were written. For the 43 DataFrame bundles
that had no client host, `os`, `arch` and `python` were **not** synthesized:
the attestation covers which machine ran the corpus, not a given run's OS
release or interpreter version. That gap closes forward, not retroactively.

### Result IDs were renumbered

`result_id` embeds a SHA-256 prefix of the raw bundle bytes, so all 151 were
renumbered. Precedent: `path-privacy-migration` and `unread-identifier-field-drop`
each renumbered all 207 entries of the corpus of their day. Every old → new
mapping is recorded in `results-data/bundles/cpu-identity-attestation.manifest.json`.
The later in-band provenance migration and its second result-ID mapping are
recorded in `results-data/bundles/cpu-identity-provenance-v2.manifest.json`.
Readers distinguish attested values from measured ones through the typed
`cpu_identity_provenance` field rather than by guessing from historical context.

Reproduce with:

    uv run -- python _project/scripts/results_explorer_cpu_attestation_backfill.py
    # add --write to apply

## TPC-DS OBT SF1 reruns on the lettered catalog (2026-10-26)

Three TPC-DS OBT SF1 bundles (`result_source: internal`) replace the
defective-parameter local bundles of the same platforms, which stay flagged
`defective-tpcds-obt-parameters` as comparison-only history:

- DuckDB `tpcds_obt_sf1_duckdb_sql_20261026_090851_b4cd9b9e`: 93 served
  queries, 279 measured executions, 0 failures, fresh data generation, load of
  5,041,336 rows in 148.6 s.
- DataFusion `tpcds_obt_sf1_datafusion_sql_20261026_091037_b6577a1e`: 93
  served queries, 0 failures, same 5,041,336 rows.
- ClickHouse Local `tpcds_obt_sf1_clickhouse_local_sql_20261026_132451_e8646728`:
  chDB 4.1.6 (engine 26.1) run in a Linux container, 93 served queries, 5,041,336
  rows loaded in 579.2 s. Q41 and Q44 fail on this engine (see below).

OBT Q14, Q23 and Q24 run as the lettered statements `14a`/`14b`, `23a`/`23b`
and `24a`/`24b`, matching base TPC-DS, so these bundles record 93 ids where the
older bundles record 90 under `14`, `23` and `24`. Every id returns the same
row count on DataFusion and on DuckDB, and on ClickHouse for every id except Q41
and Q44. ClickHouse Q16, Q94 and Q95 return the same non-empty aggregates as
DuckDB (141 / 655,918.84 / -89,198.97, 22 / 28,582.64 / -5,127.54 and
91 / 142,930.93 / -24,381.00 order count, shipping cost and net profit).

Seven ids return no rows at SF1 with the default parameter draw on DuckDB and
DataFusion (Q23b, Q24a, Q24b, Q25, Q29, Q41, Q58); ClickHouse returns no rows
for the same ids except Q41, which fails. The plain TPC-DS queries return no
rows for the same draw on the same DuckDB database.

### Documented ClickHouse Local exclusions: Q41 and Q44

chDB 26.1 raises `NOT_IMPLEMENTED` for both queries: Q41 ("Cannot check
Distinct plan step for correlated expressions") and Q44 ("can't find correlated
column ... in current header"). The failure reproduces outside BenchBox with
a correlated subquery over a `DISTINCT` subselect, with
`allow_experimental_correlated_subqueries` set or unset. The owner approved
publishing the ClickHouse bundle without these two queries.

The rule lives in `DOCUMENTED_PLATFORM_QUERY_EXCLUSIONS`
(`benchbox/core/results/query_status.py`): benchmark `tpcds_obt` on platform
`clickhouse-local`, driver (chDB) version `4.1.6` only, query ids `41` and `44`
only. With `--trust-internal-source` and an internal-source manifest, submission
validation accepts a `partial` summary for a bundle whose only failed measurements
are those ids and whose summary query counts match its per-query rows; the other
clean-bundle consistency checks still run. Community admission stays strict. Any other
failure, platform, driver version or query id, or any summary count that
disagrees with the rows, keeps the bundle rejected.

The rule does not rewrite results or counts. The bundle records Q41 and Q44 as
`FAILED`, the Explorer reports it as `partial` with 6 failed measurements, and
like every other bundle with failed queries it is excluded from rankings. Its
summary timings cover 91 query ids, so they are not like-for-like with the
93-id DuckDB and DataFusion bundles. With ClickHouse Local unranked, the
`tpcds_obt` SF1 cohort has two comparison identities (DataFusion and DuckDB);
it is listed in `DOCUMENTED_SHALLOW_COHORTS` in `validate_corpus.py` beside the
known-defective bundles of the same platforms.


## Local reruns for published results computed with superseded SQL translations (2026-10-26)

Fifty-eight replacement local bundles (`result_source: internal`) replace the
flagged local bundles computed with superseded SQL translations across ClickHouse
Local, DataFusion, and SQLite. Forty-four bundles were generated with bundled SQLGlot fork
30.24.0.17 and fourteen with fork 30.25.0.2 (see Wheel consistency), all with raw-text DuckDB
source dialect routing (#3544), under the SQLite database helper-index restoration path (#3563).

Defective predecessors keep their manifest sidecar entries (`defective-superseded-sql-translation`,
`defective-macdbgen-addresses`, `defective-tpchavoc-variant-divergence`,
`defective-sqlite-clickbench-row-counts`) as unranked comparison history.

### Hardware environment and container execution

The DataFusion and SQLite replacement bundles ran natively on the Darwin Apple M4
host (10 CPUs, 16 GB RAM), matching the DuckDB baseline.

ClickHouse Local replacement bundles (26 bundles) ran in the Linux container
lane (`benchbox-agent`, Linux 6.18.15 aarch64, 4 vCPUs, 10 GB RAM) using chDB
4.1.6, because published chDB 4.1.6 macOS wheels fail to load on macOS 27
(mis-aligned LINKEDIT string pool loader error), matching the container runner
precedent established in the TPC-DS OBT SF1 reruns (#3550). Consequently,
ClickHouse Local timings reflect the containerized virtualized execution
environment and 4-vCPU allocation rather than the bare-metal Darwin Apple M4
host. These bundles rank under the separate comparison identity
`clickhouse-local^container` (`published_variant_id`, #3618) and carry the label
`ClickHouse Local (Linux container on macOS)`; the execution class is recorded in
their manifest sidecars by `scripts/annotate_execution_class.py`, and the bundle
JSON is unchanged. Container timings are never ranked against Darwin host runs
under one identity.

### Coverage and validation

- ClickHouse Local (26 bundles): covers TPC-H (SF0.01, SF0.1, SF1), TPC-H Skew
  (SF0.01, SF0.1, SF1), TPC-DS (SF1), TPC-Havoc (SF0.01, SF0.1, SF1), and the
  CH-4 group (CoffeeShop SF0.01/0.1/1, DataVault SF0.01/0.1, FlightData SF0.01/0.1/1,
  NYCTaxi SF0.01/0.1/1, Read Primitives SF0.01/0.1, TPC-DI SF0.01/0.1/1).
  TPC-Havoc variant divergence persists on ClickHouse Local and continues to carry
  `defective-tpchavoc-variant-divergence` in its manifest sidecars.
- DataFusion (20 bundles): covers TPC-DI (SF0.01, SF0.1, SF1), TPC-DS (SF1, SF10),
  CoffeeShop (SF0.01, SF0.1, SF1), TPC-H (SF0.01, SF0.1, SF1), Read Primitives
  (SF0.01, SF0.1), TPC-H Skew (SF0.01, SF0.1, SF1), TPC-Havoc (SF0.01, SF0.1, SF1),
  and ClickBench (SF1).
- SQLite (12 bundles): covers TPC-H (SF0.01, SF0.1, SF1), TPC-H Skew (SF0.01, SF0.1, SF1),
  CoffeeShop (SF0.01, SF0.1, SF1), Read Primitives (SF0.01, SF0.1), and ClickBench (SF1).
  TPC-H SF0.1 executes with restored helper indexes in 10.6s (Q21 warm 89ms) and
  SF0.01 in 2.1s (Q21 warm 15ms), resolving the indexless-reuse plan regression.
  Read Primitives bundles record `partial` summary status due to unsupported complex
  collection operations, matching predecessor behavior.

All 58 bundles pass `scripts/validate_submission.py --require-manifest --trust-internal-source --allow-partial-validation`
with zero errors.

### Documented shallow cohorts and ranking status

Ingesting these replacements raises TPC-H SF0.01, TPC-H SF0.1, CoffeeShop SF0.01,
and TPC-DI SF0.01 to >=3 comparison identities, restoring their platform floors.
Three cohort groups become newly shallow in `DOCUMENTED_SHALLOW_COHORTS`
(`results-data/validate_corpus.py`):
- `datavault` SF0.01 and SF0.1 (`clickhouse-local^container` only, 1 identity);
- `read_primitives` SF0.01 and SF0.1 (DataFusion only, 1 rankable identity, as the ClickHouse
  Local bundles are excluded from ranking for the `map_access` defect described above);
- `tpchavoc` SF0.01, SF0.1, and SF1.0 (DataFusion only, 1 rankable identity, as ClickHouse
  Local remains excluded from ranking via `defective-tpchavoc-variant-divergence`).

Replacement TPC-H SF1 (ClickHouse Local, DataFusion, SQLite) and TPC-DS SF1/SF10
(ClickHouse Local, DataFusion) bundles carry compliance_class 'unofficial_nonstandard'
(internal test type power with default parameters), preserving unranked comparison evidence.

### Audit tail: json_extract_simple and TPC-Havoc text edits

Census command: `git archive origin/develop results-data/bundles | tar -x`, then a
Python pass over every bundle JSON including subdirectories. At origin/develop
e4401a0ed it found 422 bundles with queries, 64 of them in subdirectories.

- `json_extract_simple`: two historical bundles (3465d4f3, c1aeca88; chDB 4.1.6,
  Darwin) ran the earlier SQL. Their replacements 9f79ff77 and 3f5c94e9 already use
  the fixed `JSONExtractRaw` SQL; the text is identical on SQLGlot wheels 30.24.0.17
  and 30.25.0.1 and the recorded 1000 rows with SUCCESS are identical. No rerun is
  needed.
- TPC-Havoc 14_v1, 14_v7 and 8_v7 now wrap empty filtered numerators in COALESCE
  (#3118), and 18_v9 uses row_number with ORDER BY/LIMIT 100 (#3145). The 18 bundles
  published before 2026-10-17 ran the earlier text. Their recorded row counts are the
  same under both texts. Only 8_v7 at SF0.01 returned NULL market shares where the
  current text returns 0.0. Bundles record no result values, so no recorded field is
  affected. Note only; no new defect id.

### Wheel consistency

Fourteen replacement bundles ran on fork 30.25.0.2 and the other 44 on fork 30.24.0.17. The
bundles do not record the wheel: the translation record names only the translator (`sqlglot`) and
the dialects, and the vendored wheel carries no dist-info. The wheel in force is the vendored pin
`benchbox/_vendor/sqlglot.json` (`version` and `sha256` of the archive beside it) in the checkout
each run was started from, and `uv.lock` has no SQLGlot entry. Per-bundle values below come from
that pin at the checkout's commit; every run used the editable project install of its worktree, so
the checkout's `benchbox/_vendor` is what loaded.

| id | cell | run timestamp (clock) | checkout commit | wheel | evidence |
|---|---|---|---|---|---|
| `95d5bdc8` | DataFusion coffeeshop SF0.01 | 2026-10-26 11:28:37 EDT | `7b8484a4c` | 30.24.0.17 | A |
| `e1a0a0b3` | DataFusion tpch_skew SF0.01 | 2026-10-26 11:28:59 EDT | `7b8484a4c` | 30.24.0.17 | A |
| `70d6c9e1` | DataFusion tpchavoc SF0.01 | 2026-10-26 11:29:44 EDT | `7b8484a4c` | 30.24.0.17 | A |
| `001e082b` | DataFusion coffeeshop SF0.1 | 2026-10-26 11:32:01 EDT | `7b8484a4c` | 30.24.0.17 | A |
| `b6e365e8` | DataFusion tpch_skew SF0.1 | 2026-10-26 11:33:43 EDT | `7b8484a4c` | 30.24.0.17 | A |
| `b7c3e0be` | DataFusion tpchavoc SF0.1 | 2026-10-26 11:34:14 EDT | `7b8484a4c` | 30.24.0.17 | A |
| `c6832587` | DataFusion coffeeshop SF1 | 2026-10-26 11:34:47 EDT | `7b8484a4c` | 30.24.0.17 | A |
| `2fce59f7` | DataFusion tpcds SF1 | 2026-10-26 11:35:58 EDT | `7b8484a4c` | 30.24.0.17 | A |
| `5ef63e23` | DataFusion tpch SF1 | 2026-10-26 11:36:13 EDT | `7b8484a4c` | 30.24.0.17 | A |
| `5441c82c` | DataFusion tpch_skew SF1 | 2026-10-26 11:36:27 EDT | `7b8484a4c` | 30.24.0.17 | A |
| `7c126887` | DataFusion tpchavoc SF1 | 2026-10-26 11:38:07 EDT | `7b8484a4c` | 30.24.0.17 | A |
| `9b8837db` | DataFusion tpcds SF10 | 2026-10-26 11:47:39 EDT | `7b8484a4c` | 30.24.0.17 | A |
| `3d030f43` | SQLite coffeeshop SF0.01 | 2026-10-26 11:47:44 EDT | `7b8484a4c` | 30.24.0.17 | A |
| `d19931e6` | SQLite tpch_skew SF0.01 | 2026-10-26 11:49:05 EDT | `7b8484a4c` | 30.24.0.17 | A |
| `5f688a96` | SQLite coffeeshop SF0.1 | 2026-10-26 11:49:34 EDT | `7b8484a4c` | 30.24.0.17 | A |
| `432838b7` | SQLite tpch_skew SF0.1 | 2026-10-26 12:33:34 EDT | `7b8484a4c` | 30.24.0.17 | A |
| `4bfe0dd1` | SQLite clickbench SF1 | 2026-10-26 12:34:37 EDT | `7b8484a4c` | 30.24.0.17 | A |
| `9953f53c` | SQLite coffeeshop SF1 | 2026-10-26 12:39:49 EDT | `7b8484a4c` | 30.24.0.17 | A |
| `8b8f54de` | DataFusion tpch SF0.01 | 2026-10-26 12:39:55 EDT | `7b8484a4c` | 30.24.0.17 | A |
| `113a3ebe` | SQLite tpch SF1 | 2026-10-26 12:42:24 EDT | `7b8484a4c` | 30.24.0.17 | A |
| `1bf2c529` | DataFusion tpch SF0.1 | 2026-10-26 12:42:30 EDT | `7b8484a4c` | 30.24.0.17 | A |
| `dbf8bb59` | SQLite tpch_skew SF1 | 2026-10-26 12:44:53 EDT | `7b8484a4c` | 30.24.0.17 | A |
| `93a0b6f9` | SQLite tpch SF0.1 | 2026-10-26 15:57:23 EDT | `5e82bcaa3` | 30.24.0.17 | B |
| `14b260b9` | SQLite tpch SF0.01 | 2026-10-26 15:57:39 EDT | `5e82bcaa3` | 30.24.0.17 | B |
| `3d2fa5e9` | ClickHouse Local datavault SF0.01 | 2026-10-26 17:40:57 UTC | `7b8484a4c` | 30.24.0.17 | A |
| `c1d5ea31` | ClickHouse Local flightdata SF0.01 | 2026-10-26 17:41:11 UTC | `7b8484a4c` | 30.24.0.17 | A |
| `ce89d00e` | ClickHouse Local nyctaxi SF0.01 | 2026-10-26 17:41:17 UTC | `7b8484a4c` | 30.24.0.17 | A |
| `9f79ff77` | ClickHouse Local read_primitives SF0.01 | 2026-10-26 17:41:33 UTC | `7b8484a4c` | 30.24.0.17 | A |
| `71dc6c6c` | ClickHouse Local tpch SF0.01 | 2026-10-26 17:41:47 UTC | `7b8484a4c` | 30.24.0.17 | A |
| `3b4c54c2` | ClickHouse Local tpch_skew SF0.01 | 2026-10-26 17:41:52 UTC | `7b8484a4c` | 30.24.0.17 | A |
| `5de9fab8` | ClickHouse Local tpchavoc SF0.01 | 2026-10-26 17:42:15 UTC | `7b8484a4c` | 30.24.0.17 | A |
| `ac9b732e` | ClickHouse Local datavault SF0.1 | 2026-10-26 17:42:54 UTC | `7b8484a4c` | 30.24.0.17 | A |
| `7ac18c39` | ClickHouse Local flightdata SF0.1 | 2026-10-26 17:44:36 UTC | `7b8484a4c` | 30.24.0.17 | A |
| `46bfd3fb` | ClickHouse Local nyctaxi SF0.1 | 2026-10-26 17:45:47 UTC | `7b8484a4c` | 30.24.0.17 | A |
| `3f5c94e9` | ClickHouse Local read_primitives SF0.1 | 2026-10-26 17:46:37 UTC | `7b8484a4c` | 30.24.0.17 | A |
| `25806a27` | ClickHouse Local tpch SF0.1 | 2026-10-26 17:47:09 UTC | `7b8484a4c` | 30.24.0.17 | A |
| `23306369` | ClickHouse Local tpch_skew SF0.1 | 2026-10-26 17:47:16 UTC | `7b8484a4c` | 30.24.0.17 | A |
| `d542bf14` | ClickHouse Local tpchavoc SF0.1 | 2026-10-26 17:47:55 UTC | `7b8484a4c` | 30.24.0.17 | A |
| `3b8b6f57` | ClickHouse Local flightdata SF1 | 2026-10-26 17:50:01 UTC | `7b8484a4c` | 30.24.0.17 | A |
| `4d422b5f` | ClickHouse Local nyctaxi SF1 | 2026-10-26 17:50:23 UTC | `7b8484a4c` | 30.24.0.17 | A |
| `4b9e5193` | ClickHouse Local tpcds SF1 | 2026-10-26 17:55:29 UTC | `7b8484a4c` | 30.24.0.17 | A |
| `f2bafd17` | ClickHouse Local tpch SF1 | 2026-10-26 17:57:54 UTC | `7b8484a4c` | 30.24.0.17 | A |
| `bb56ec24` | ClickHouse Local tpch_skew SF1 | 2026-10-26 17:58:45 UTC | `7b8484a4c` | 30.24.0.17 | A |
| `34b1129c` | ClickHouse Local tpchavoc SF1 | 2026-10-26 18:13:02 UTC | `7b8484a4c` | 30.24.0.17 | A |
| `fc04d558` | SQLite read_primitives SF0.01 | 2026-10-28 09:28:29 EDT | `be144e04f` | 30.25.0.2 | C |
| `8edf1667` | SQLite read_primitives SF0.1 | 2026-10-28 09:29:52 EDT | `be144e04f` | 30.25.0.2 | C |
| `093d2262` | DataFusion clickbench SF1 | 2026-10-28 10:05:25 EDT | `6acbcee6f` | 30.25.0.2 | D |
| `328320ea` | DataFusion tpcdi SF0.01 | 2026-10-28 10:05:44 EDT | `6acbcee6f` | 30.25.0.2 | D |
| `fb67aa79` | DataFusion tpcdi SF0.1 | 2026-10-28 10:05:54 EDT | `6acbcee6f` | 30.25.0.2 | D |
| `ffe633e9` | DataFusion tpcdi SF1 | 2026-10-28 10:06:15 EDT | `6acbcee6f` | 30.25.0.2 | D |
| `2594968f` | ClickHouse Local coffeeshop SF0.01 | 2026-10-28 14:06:52 UTC | `6acbcee6f` | 30.25.0.2 | D |
| `da48d384` | ClickHouse Local coffeeshop SF0.1 | 2026-10-28 14:07:04 UTC | `6acbcee6f` | 30.25.0.2 | D |
| `2888c2d3` | ClickHouse Local coffeeshop SF1 | 2026-10-28 14:07:31 UTC | `6acbcee6f` | 30.25.0.2 | D |
| `3c9d9330` | ClickHouse Local tpcdi SF0.01 | 2026-10-28 14:08:02 UTC | `6acbcee6f` | 30.25.0.2 | D |
| `61e36e9c` | ClickHouse Local tpcdi SF0.1 | 2026-10-28 14:08:31 UTC | `6acbcee6f` | 30.25.0.2 | D |
| `30a5c9d1` | ClickHouse Local tpcdi SF1 | 2026-10-28 14:09:13 UTC | `6acbcee6f` | 30.25.0.2 | D |
| `ac8474d7` | DataFusion read_primitives SF0.01 | 2026-10-28 15:59:34 EDT | `a6a78db53` | 30.25.0.2 | E |
| `a45d3e71` | DataFusion read_primitives SF0.1 | 2026-10-28 15:59:41 EDT | `a6a78db53` | 30.25.0.2 | E |

Run timestamps are the bundle's `run.timestamp`. Host runs (DataFusion, SQLite) record the Darwin
host's local time (EDT, UTC-4). ClickHouse Local runs executed in the `benchbox-agent` Linux
container and record its UTC clock; the matching host log modification times are 13:40-14:13 EDT on
2026-10-26 and 10:06-10:09 EDT on 2026-10-28.

Evidence, all read from the local worktrees' git history and files:

- A: worktree `BenchBox.wt-superseded-reruns` at `7b8484a4c` (#3544), the HEAD from 2026-10-26
  11:08:46 EDT (`git reflog --date=iso`) until the fast-forward at 15:57:05 EDT. The pin at that commit
  is 30.24.0.17, archive sha256 `f91d0991...4fc526`. The first run log (`/tmp/sgrr/logs`, modification
  time 11:09:49 EDT) postdates it, as do all runs in this class. The ClickHouse Local runs
  (`/tmp/sgrr/runq_ch.sh`) ran `uv run --frozen -- benchbox run` in the container with that worktree as
  the working directory.
- B: the same worktree after the fast-forward to `5e82bcaa3` at 15:57:05 EDT; the pin is unchanged
  (30.24.0.17, same sha256). Both SQLite TPC-H runs (15:57:23 and 15:57:39) fall in this window.
- C: worktree `BenchBox.wt-sqlglot-adopt-2` at `be144e04f` (branch `chore/adopt-sqlglot-30-25-0-2`, the
  #3649 branch; HEAD unchanged since 08:23:31 EDT on 2026-10-28, clean tree). Its pin is 30.25.0.2 with
  archive sha256 `6207c352...ec8fa2`, and `shasum -a 256` of the vendored archive equals the pin. The SQLite
  Read Primitives replacements ran there (`cd BenchBox.wt-sqlglot-adopt-2 && uv run --frozen --
  benchbox run ...`, recorded in the agent session transcript) at 09:28-09:29 EDT, before the
  adoption merged as `e071365f6` at 09:28:01 EDT and before `BenchBox.wt-superseded-reruns` was rebased.
- D: worktree `BenchBox.wt-superseded-reruns` rebased onto `0033859cb` (which contains `e071365f6`) at
  10:03:08-10:03:18 EDT on 2026-10-28, tip `6acbcee6f`. The pin is 30.25.0.2 and the vendored pin files
  have a modification time of 10:03:08. The runs (`/tmp/sgrr-rerun/run_cell.sh`, logs modified
  10:05-10:09 EDT) postdate the rebase. Between the 2026-10-27 22:23 rebase and this one that worktree
  carried 30.25.0.1, and no bundle was run in that window.
- E: the same worktree at `a6a78db53` (rebased onto `c8fa62236`, pin 30.25.0.2) at the 15:59 EDT
  DataFusion Read Primitives runs (`uv run -- benchbox run --platform datafusion ...`, recorded in the
  agent session transcript).

Limits. The Linux virtualenv `/tmp/bbvenv-linux-sgrr` used by the ClickHouse Local runs no longer
exists, so its `benchbox._vendor` cannot be inspected; the class A and D values for those 26 bundles
rest on the worktree pin and the run scripts. No run log prints the wheel. Uncommitted working-tree
changes at run time are not recorded; none is evidenced. No bundle is attributed to 30.25.0.1, and no
bundle is `unknown`.

Reproduction:

```
git show --name-only --diff-filter=A --format= 431cbe4c7 -- 'results-data/bundles/*.json' | grep -v manifest
jq -r '.run.timestamp' results-data/bundles/<bundle>.json
git -C <worktree> reflog --date=iso
git -C <worktree> show <commit>:benchbox/_vendor/sqlglot.json
shasum -a 256 <worktree>/benchbox/_vendor/*.zip
```

Later reruns use 30.25.0.1 or newer and earlier valid bundles are kept. The #3587 comparison
translated 72,483 queries across both wheels and changed 9 outputs, all with a DuckDB target,
none of which is a target of these bundles.

### Staging path

Maintainer-run local bundles do not use the pseudonym salt. The 58 bundles were produced from
the original run output in `~/Developer/benchmark_runs/results` through the public anonymiser,
`_project/scripts/results_explorer_corpus_migrate.py` (`AnonymizationManager()` with no salt),
the same path as the earlier local bundles, with each `.manifest.json` written from the original
run output (`result_source: internal`, `submitted_by: maintainer`) and `export.anonymized` set to
`true`. Their machine pseudonyms (including `database_5d7b725135a6`, also present in 23 bundles
already on develop) are therefore the unsalted public pseudonyms. `scripts/stage_seed_corpus_bundle.py`
belongs to the CI seed-corpus lane, which holds the salt as the `BENCHBOX_MACHINE_ID_SALT` secret,
and is not the maintainer path. Re-running the anonymiser on the raw results reproduces all 58
bundle files byte for byte. Bundle JSON is not modified.

### Submission validation refusal path

Without `--trust-internal-source`, `scripts/validate_submission.py --require-manifest`
refuses 17 of the 58 bundles: 12 for a non-official `benchmark.compliance_class`
(`unofficial_subscale` or `unofficial_nonstandard`), 3 ClickHouse Local TPC-Havoc
bundles for inconsistent per-variant row counts (the documented
`defective-tpchavoc-variant-divergence`), and 2 SQLite Read Primitives bundles with
`summary.validation` of `partial`. These are internal-source bundles, so the supported path is
`--require-manifest --trust-internal-source --allow-partial-validation`, under which
all 58 pass with zero errors. The internal-source trust path for these classes is
tracked by `flagged-local-bundles-compliance-class-gate-20261025`.

### Value evidence against DuckDB (wheel 30.25.0.1)

Each of the 58 bundles was checked with a capture-only run of the same benchmark, scale
and SQL on SQLGlot 30.25.0.1 (`--phases generate,load,power --iterations 1`, full result
rows captured per query, 50,000-row cap, no new bundle written; ClickHouse captures apply the adapter's query rewrite and per-query settings, as the timed run does) and compared per query
with a DuckDB capture of the same data. A query is `passed` when the row counts are equal,
rows align on their non-numeric columns, every numeric value is within relative tolerance
1e-6, and it is also consistent with the bundle's own warm-up row count. A query is
`unproven` when the DuckDB result is empty or an all-zero/NULL aggregate, or when the
capture hit the row cap; unproven queries are not counted as passed. `whitespace-only`
means the rows are equal only after trimming leading or trailing whitespace. TPC-DS row
counts vary per stream, so only the warm-up stream is comparable with a capture. NYC Taxi
query parameters are unseeded, so both the DuckDB and the ClickHouse Local NYC Taxi
captures pinned one parameter seed and are not compared with the bundle row counts. SQLite
`bundle-failed` queries failed in the bundle itself and were not captured.


`clickhouse-local`

| benchmark | SF | passed | whitespace-only | unproven | discrepancy | bundle-failed | no capture/reference |
|---|---|---|---|---|---|---|---|
| coffeeshop | 0.01 | 10 | 0 | 0 | 1 | 0 | 0 |
| coffeeshop | 0.1 | 10 | 0 | 0 | 1 | 0 | 0 |
| coffeeshop | 1 | 10 | 0 | 0 | 1 | 0 | 0 |
| datavault | 0.01 | 20 | 1 | 1 | 0 | 0 | 0 |
| datavault | 0.1 | 20 | 2 | 0 | 0 | 0 | 0 |
| flightdata | 0.01 | 20 | 0 | 0 | 0 | 0 | 0 |
| flightdata | 0.1 | 19 | 0 | 0 | 1 | 0 | 0 |
| flightdata | 1 | 20 | 0 | 0 | 0 | 0 | 0 |
| nyctaxi | 0.01 | 23 | 0 | 0 | 2 | 0 | 0 |
| nyctaxi | 0.1 | 25 | 0 | 0 | 0 | 0 | 0 |
| nyctaxi | 1 | 25 | 0 | 0 | 0 | 0 | 0 |
| read_primitives | 0.01 | 101 | 9 | 16 | 20 | 0 | 1 |
| read_primitives | 0.1 | 94 | 8 | 21 | 23 | 0 | 1 |
| tpcdi | 0.01 | 34 | 0 | 3 | 1 | 0 | 0 |
| tpcdi | 0.1 | 35 | 0 | 2 | 1 | 0 | 0 |
| tpcdi | 1 | 35 | 0 | 2 | 1 | 0 | 0 |
| tpcds | 1 | 88 | 0 | 14 | 1 | 0 | 0 |
| tpch | 0.01 | 20 | 1 | 1 | 0 | 0 | 0 |
| tpch | 0.1 | 20 | 2 | 0 | 0 | 0 | 0 |
| tpch | 1 | 19 | 3 | 0 | 0 | 0 | 0 |
| tpch_skew | 0.01 | 17 | 2 | 3 | 0 | 0 | 0 |
| tpch_skew | 0.1 | 17 | 3 | 2 | 0 | 0 | 0 |
| tpch_skew | 1 | 17 | 3 | 2 | 0 | 0 | 0 |
| tpchavoc | 0.01 | 177 | 7 | 8 | 7 | 0 | 0 |
| tpchavoc | 0.1 | 177 | 16 | 0 | 6 | 0 | 0 |
| tpchavoc | 1 | 167 | 26 | 0 | 6 | 0 | 0 |
| **total (26 cells)** | | **1220** | **83** | **75** | **72** | **0** | **2** |

`datafusion`

| benchmark | SF | passed | whitespace-only | unproven | discrepancy | bundle-failed | no capture/reference |
|---|---|---|---|---|---|---|---|
| clickbench | 1 | 25 | 0 | 8 | 10 | 0 | 0 |
| coffeeshop | 0.01 | 11 | 0 | 0 | 0 | 0 | 0 |
| coffeeshop | 0.1 | 11 | 0 | 0 | 0 | 0 | 0 |
| coffeeshop | 1 | 11 | 0 | 0 | 0 | 0 | 0 |
| read_primitives | 0.01 | 112 | 0 | 15 | 11 | 0 | 0 |
| read_primitives | 0.1 | 102 | 0 | 20 | 16 | 0 | 0 |
| tpcdi | 0.01 | 34 | 0 | 3 | 1 | 0 | 0 |
| tpcdi | 0.1 | 35 | 0 | 2 | 1 | 0 | 0 |
| tpcdi | 1 | 35 | 0 | 2 | 1 | 0 | 0 |
| tpcds | 1 | 89 | 0 | 14 | 0 | 0 | 0 |
| tpcds | 10 | 97 | 0 | 5 | 1 | 0 | 0 |
| tpch | 0.01 | 21 | 0 | 1 | 0 | 0 | 0 |
| tpch | 0.1 | 22 | 0 | 0 | 0 | 0 | 0 |
| tpch | 1 | 22 | 0 | 0 | 0 | 0 | 0 |
| tpch_skew | 0.01 | 19 | 0 | 3 | 0 | 0 | 0 |
| tpch_skew | 0.1 | 20 | 0 | 2 | 0 | 0 | 0 |
| tpch_skew | 1 | 20 | 0 | 2 | 0 | 0 | 0 |
| tpchavoc | 0.01 | 201 | 0 | 8 | 0 | 0 | 0 |
| tpchavoc | 0.1 | 209 | 0 | 0 | 0 | 0 | 0 |
| tpchavoc | 1 | 209 | 0 | 0 | 0 | 0 | 0 |
| **total (20 cells)** | | **1305** | **0** | **85** | **41** | **0** | **0** |

`sqlite`

| benchmark | SF | passed | whitespace-only | unproven | discrepancy | bundle-failed | no capture/reference |
|---|---|---|---|---|---|---|---|
| clickbench | 1 | 27 | 0 | 8 | 8 | 0 | 0 |
| coffeeshop | 0.01 | 11 | 0 | 0 | 0 | 0 | 0 |
| coffeeshop | 0.1 | 11 | 0 | 0 | 0 | 0 | 0 |
| coffeeshop | 1 | 11 | 0 | 0 | 0 | 0 | 0 |
| read_primitives | 0.01 | 92 | 0 | 16 | 6 | 38 | 0 |
| read_primitives | 0.1 | 85 | 0 | 21 | 8 | 38 | 0 |
| tpch | 0.01 | 21 | 0 | 1 | 0 | 0 | 0 |
| tpch | 0.1 | 22 | 0 | 0 | 0 | 0 | 0 |
| tpch | 1 | 21 | 0 | 0 | 1 | 0 | 0 |
| tpch_skew | 0.01 | 19 | 0 | 3 | 0 | 0 | 0 |
| tpch_skew | 0.1 | 19 | 0 | 2 | 1 | 0 | 0 |
| tpch_skew | 1 | 19 | 0 | 2 | 1 | 0 | 0 |
| **total (12 cells)** | | **358** | **0** | **53** | **25** | **76** | **0** |

Counts reconcile to the 58 bundles: 26 ClickHouse Local, 20 DataFusion, 12 SQLite.

#### Discrepancy adjudication

An earlier capture of the ClickHouse cells re-ran each query without the adapter's
`rewrite_query_for_clickhouse` and per-query settings, which produced spurious TPC-H Q6,
decimal-ratio, TPC-DS and Havoc discrepancies; those were capture artefacts and the tables
above come from the corrected capture. The DataFusion and SQLite captures already used the
same rewrite path as the timed run.

Real defects found (the bundles hold wrong values or rows for these queries):

- ClickHouse Local Coffeeshop TM1 returns one `Evening` row instead of four day parts (TIME
  stored as a string); TPC-DI AQ6 differs from DuckDB (hand-written variant).
- ClickHouse Local `map_access` returns 0 for a missing key, and the approximate, struct,
  array and map Read Primitives shapes differ from DuckDB (20 and 23 queries in the SF0.01
  and SF0.1 bundles, left unadjudicated individually).
- DataFusion TPC-DI EQ7 returns wrong scores (Int64 division) in all three bundles;
  DataFusion ClickBench Q6 returns 17 where DuckDB and SQLite return 18 (loader null
  marker); DataFusion `pivot_basic` returned 2,100 rows against 2 in the original two Read
  Primitives bundles, which were replaced on fork 30.25.0.2 (see the DataFusion section below).
- SQLite Read Primitives `pivot_basic` (PIVOT dropped), `unpivot_basic` (UNPIVOT dropped)
  and `orderby_all_desc` (no ordering) succeeded with wrong results in the original two
  bundles. They were replaced on fork 30.25.0.2 (see the section below); the SQLite table
  above holds the replacement counts.

Accepted as engine semantics or ties:

- The 8 SQLite ClickBench discrepancies are nondeterminism: Q18 has no ORDER BY; Q16, Q17,
  Q19, Q31, Q32, Q33 and Q36 tie at the LIMIT 10 cutoff, the count multisets equal DuckDB's
  and every SQLite row's count matches DuckDB's exact count. DataFusion ClickBench Q16 to
  Q19, Q24, Q31 to Q33 and Q36 have the same shape (multisets verified except Q24).
- ClickHouse Local TPC-DS Q78 differs in 3 of 100 rows by float tie rounding.
- 83 ClickHouse Local queries are `whitespace-only` (loader trims leading or trailing
  whitespace).
- Remaining ORDER BY ... LIMIT differences (TOPN, running sum, `orderby_simple`,
  `any_value`) differ only in rows tied at the LIMIT cutoff.
- ClickHouse Local TPC-Havoc `2_v8`, `3_v7`, `7_v7`, `9_v7`, `10_v7`, `10_v8` row counts and
  `13_v1` differ from DuckDB, consistent with the documented
  `defective-tpchavoc-variant-divergence`.

Still open: ClickHouse Local Flight Data `delay-by-airport` SF0.1 (7.9e-4 relative) and NYC
Taxi SF0.01 `top-routes` and `airport-trips`; ClickHouse Local `json_extract_simple` has no
DuckDB reference. SQLite TPC-H Q11 and TPC-H skew Q9 and Q11 were reported as one-row
differences; re-run directly against DuckDB on the retained SF1 TPC-H, SF0.1 and SF1 skew
databases (the bundle's query, translated for each engine), both engines return the same rows
(TPC-H Q11 1,048; skew Q9 76 at SF0.1 and 98 at SF1; skew Q11 22 at SF0.1 and 1,048 at SF1)
with every value within relative 1e-6, so no discrepancy reproduces.

Cells reran after the engine fixes merged: ClickHouse Local Coffeeshop (3) and TPC-DI (3),
DataFusion TPC-DI (3) and ClickBench SF1 (see the rerun section at the end), and DataFusion
Read Primitives (2, see the DataFusion section below). ClickHouse Local Read Primitives (2) are
not rerun; their differences are algorithmic or comparator printing, and `map_access` is tracked in
`clickhouse-map-access-missing-key-returns-zero`.

### SQLite Read Primitives replacement on fork 30.25.0.2 (2026-10-28)

The two SQLite Read Primitives bundles of this set (`5d5687fd` at SF0.01 and `82ce61a4` at SF0.1,
both on fork 30.24.0.17) held wrong results marked SUCCESS: `pivot_basic` returned 2,100 and
22,576 rows against DuckDB's 2 (the generator wrote the query without its PIVOT),
`unpivot_basic` returned 100 rows of literal column names against 200 (UNPIVOT dropped) and
`orderby_all_desc` ran without its ordering (the Netezza source reads `ORDER BY ALL DESC` as a
column named `ALL`, which SQLite received as the constant `"ALL"`). DataFusion `pivot_basic` had the
same cause. Fork BenchBox-dev/benchbox-sqlglot#78 (release 30.25.0.2, adopted in BenchBox#3649)
lowers a single-aggregate PIVOT to conditional aggregation and a plain-column UNPIVOT to a
`UNION ALL`, expands `ORDER BY ALL` to ordinal keys with the query's direction, and raises an
unsupported error for any other pivot so the query is recorded as a failure.

Replacements, run through the project CLI exactly as the earlier SQLite reruns (`benchbox run
--platform sqlite --benchmark read_primitives --scale <sf> --phases generate,load,power --tuning
notuning --result-source internal --non-interactive`, wheel 30.25.0.2, same host, the
read-primitives tables loaded in the shared SQLite database already present) and staged through
the public anonymiser (see Staging path above; database pseudonym `database_5d7b725135a6` is unchanged):

| scale | replaced bundle | replacement bundle |
|---|---|---|
| 0.01 | `5d5687fd` | `fc04d558` |
| 0.1 | `82ce61a4` | `8edf1667` |

The replaced bundles are removed from `results-data/bundles`, the ledger seed and the corpus
inventory; their ids stay in this note as the superseded history. The run reused the loaded SQLite
database, so the replacement bundles record `data_loading` and `schema_creation` phases of 0 ms
and carry no `data_generation_hash`; query results and per-query timings come from the same
tables. Both pass `scripts/validate_submission.py --require-manifest --trust-internal-source
--allow-partial-validation` with zero errors and the corpus validation is unchanged.

Capture comparison against DuckDB (capture run on 30.25.0.2, `compare.py` row comparison, values within
1e-6 relative):

| query | DuckDB rows | SQLite before (SF0.01 / SF0.1) | SQLite after (SF0.01 / SF0.1) |
|---|---|---|---|
| `pivot_basic` | 2 | 2,100 / 22,576 (wrong) | 2 / 2 (pass) |
| `unpivot_basic` | 200 | 100 / 100 (wrong) | 200 / 200 (pass) |
| `orderby_all_desc` | 100 | 100 unordered (wrong) | 100 / 100 (pass) |
| `orderby_all_simple` | 25 | 25 unordered | 25 / 25 (pass) |

DataFusion `pivot_basic` on 30.25.0.2 returns 2 rows at both scales and matches DuckDB.

Per-cell adjudication after the rerun (passed / unproven / bundle-failed / discrepancy):
SF0.01 92 / 16 / 38 / 6 (was 88 / 16 / 38 / 10), SF0.1 85 / 21 / 38 / 8 (was 83 / 21 / 38 / 10).
The only other status change is `topn_aggregate_2columns`, which moves between passed and
discrepancy at the two scales: it ties at the LIMIT cutoff and its SQL is byte-identical on both
wheels. The 14,060-translation comparison of 30.25.0.1 and 30.25.0.2 over 370 shipped queries and
38 targets changed eight outputs, all `pivot_basic`, `unpivot_basic`, `orderby_all_simple` and
`orderby_all_desc` on SQLite and DataFusion.

### DataFusion Read Primitives replacement on fork 30.25.0.2 (2026-10-28)

The two DataFusion Read Primitives bundles of this set (`a4c2be65` at SF0.01 and `4cf65e8f` at
SF0.1, both on fork 30.24.0.17) recorded `pivot_basic` as SUCCESS with 2,100 and 22,576 rows
against DuckDB's 2, because the PIVOT was dropped (the same defect as SQLite above). They were
rerun on 30.25.0.2 through the project CLI (`benchbox run --platform datafusion --benchmark
read_primitives --scale <sf> --phases generate,load,power --tuning notuning --result-source
internal --non-interactive`, DataFusion 54.0.0 on the Darwin host, same data) and staged through
the public anonymiser as above. Both report 414 of 414 queries passed.

| scale | replaced bundle | replacement bundle |
|---|---|---|
| 0.01 | `a4c2be65` | `ac8474d7` |
| 0.1 | `4cf65e8f` | `a45d3e71` |

Compared per query with the replaced bundles, `pivot_basic` is the only query whose status or
row count changed (2,100 and 22,576 rows became 2 and 2, equal to DuckDB's); every other query
returns the same rows as before. The replaced bundles are removed from `results-data/bundles`,
the ledger seed and the corpus inventory; their ids stay in this note as superseded history. The
discrepancy counts in the DataFusion table above predate the replacement and include
`pivot_basic` (one fewer discrepancy at each scale after it).

### Rerun of the variant and loader defect cells on fork 30.25.0.2 (2026-10-28)

Ten of the 58 bundles ran on fork 30.24.0.17 with a BenchBox variant or loader that has since
been fixed. They were rerun after the fixes merged (CoffeeShop TM1 on ClickHouse #3644, ClickHouse
TPC-DI AQ6 variant removed and DataFusion EQ7 DOUBLE cast #3648, DataFusion ClickBench null marker
#3646, SQLGlot fork 30.25.0.2 #3649), on a worktree rebased onto origin/develop. Each cell ran one
at a time under `lockf /tmp/benchbox-heavy.lock` through the project CLI (`benchbox run
--platform <p> --benchmark <b> --scale <sf> --phases generate,load,power --tuning notuning
--result-source internal --non-interactive`, `BENCHBOX_OUTPUT_DIR=~/Developer/benchmark_runs`),
with ClickHouse Local in the `benchbox-agent` Linux container (chDB 4.1.6) and DataFusion (54.0.0)
natively on the Darwin host. The DataFusion ClickBench data directory was removed first so the
CSV was converted to Parquet again with the corrected loader. Bundles record BenchBox 0.5.0rc1;
the wheel (30.25.0.2) is the vendored pin `benchbox/_vendor/sqlglot.json` (see Wheel consistency). Staging used the
public anonymiser, as for the rest of the set (see Staging path above).

| cell | scale | replaced bundle | replacement bundle |
|---|---|---|---|
| ClickHouse Local CoffeeShop | 0.01 | `3580340d` | `2594968f` |
| ClickHouse Local CoffeeShop | 0.1 | `be08f469` | `da48d384` |
| ClickHouse Local CoffeeShop | 1 | `260b80d1` | `2888c2d3` |
| ClickHouse Local TPC-DI | 0.01 | `4b0f48c4` | `3c9d9330` |
| ClickHouse Local TPC-DI | 0.1 | `3afba5da` | `61e36e9c` |
| ClickHouse Local TPC-DI | 1 | `b2190761` | `30a5c9d1` |
| DataFusion TPC-DI | 0.01 | `fd444036` | `328320ea` |
| DataFusion TPC-DI | 0.1 | `6d43a62b` | `fb67aa79` |
| DataFusion TPC-DI | 1 | `9d41dd46` | `ffe633e9` |
| DataFusion ClickBench | 1 | `726529fb` | `093d2262` |

The replaced bundles are removed from `results-data/bundles`, the ledger seed and the corpus
inventory; their ids stay in this note as superseded history. All ten report zero failed
queries. The ClickHouse Local replacements record the `container` execution class in the bundle
itself (kernel and Apple CPU vendor, source `inferred`), so `scripts/annotate_execution_class.py`
adds no sidecar attestation and they rank under `clickhouse-local^container`.

Capture comparison against DuckDB (capture-only run on the new code, `--iterations 1`, key-aligned
`compare.py`, numeric values within relative 1e-6; the DuckDB captures are the earlier ones on the same
data):

| cell | scale | passed | failed | unproven | targeted query |
|---|---|---|---|---|---|
| ClickHouse Local CoffeeShop | 0.01, 0.1, 1 | 11, 11, 11 | 0 | 0 | TM1 returns 4 day parts with DuckDB's values |
| ClickHouse Local TPC-DI | 0.01, 0.1, 1 | 35, 36, 36 | 0 | 3, 2, 2 | AQ6 5, 12, 12 rows, values equal |
| DataFusion TPC-DI | 0.01, 0.1, 1 | 35, 36, 36 | 0 | 3, 2, 2 | EQ7 3 rows, values equal |
| DataFusion ClickBench | 1 | 27 | 8 | 8 | Q6 returns 18 (was 17) |

The unproven TPC-DI queries are `A3` (SF0.01 only), `VQ6` and `AQ9`, whose DuckDB result is empty or
all zero; they were not changed by the reruns. The eight DataFusion ClickBench failures are
nondeterminism, as in the SQLite ClickBench adjudication: Q18 has no ORDER BY, and Q16, Q17, Q19, Q31,
Q32, Q33 and Q36 tie at the LIMIT 10 cutoff (the rows above the cutoff and the count multisets equal
DuckDB's). No result changed unexpectedly.

Corpus validation (`results-data/validate_corpus.py`) exits 0 before and after: 480 bundles, 41 ranked
cohorts at or above the three-identity floor and 10 documented shallow cohorts in both runs. Each of
the ten bundles passes `scripts/validate_submission.py --require-manifest`, with and without
`--trust-internal-source --allow-partial-validation`, with zero errors, so none adds to the refusal
list handed to `flagged-local-bundles-compliance-class-gate-20261025`.
