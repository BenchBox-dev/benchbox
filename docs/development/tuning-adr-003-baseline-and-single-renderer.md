# ADR-3: Baseline Definition and the Single Tuning-DDL Renderer

## Status

Accepted, 2026-07-12. Decided by the project maintainer.

## Date

2026-07-12

## Context

This decision follows a 2026-07-12 review of the tuning system.

### `notuning` is not a baseline today

"notuning" is supposed to mean platform defaults plus whatever an engine
mandates for a working schema. In practice two platforms attach behavior to
the *absence* of tuning, or unconditionally, rather than to the tuned path:

- **ClickHouse inverts the pack.** `benchbox/platforms/clickhouse/tuning.py:74-100`
  applies an aggressive OLAP session pack — `join_algorithm=grace_hash`,
  `grace_hash_join_initial_buckets=8`, `optimize_aggregation_in_order=1`,
  `group_by_two_level_threshold=100000`, and 50%-of-memory spill thresholds
  for external group-by/sort — **only when `self.tuning_enabled` is
  `False`**. A user who explicitly asks for tuning gets none of this; a user
  who explicitly asks for no tuning gets a curated performance profile. The
  label and the behavior are backwards.
- **StarRocks injects unconditionally.** `benchbox/platforms/starrocks/workload.py:214-221`
  appends `DISTRIBUTED BY HASH(`col`) BUCKETS 8` to every `CREATE TABLE` that
  doesn't already declare a distribution clause, in every tuning mode. This is
  schema-shape physical layout the engine requires to have a working table,
  not an optimization choice — but nothing in bundle metadata currently
  labels it as engine-mandatory versus a tuning decision.

The same review also found that session-level `SET` statements (the
ClickHouse pack above, and equivalent StarRocks query settings) are not
recorded in the tuning bundle in any mode today — there is no ledger entry a
later run or an auditor can compare against.

### Three parallel rendering universes

Tuning DDL is rendered by three independent code paths that do not share
logic and can drift silently:

- `benchbox/core/tuning/generators/*` (one module per platform family,
  reachable through `benchbox/core/tuning/ddl_generator.py:get_ddl_generator`).
  This path is exercised by exactly one caller today: the dry-run preview
  (`benchbox/core/dryrun.py:1066-1091`).
- `SparkDDLGeneratorMixin.generate_tuning_clauses`
  (`benchbox/platforms/base/cloud_spark/mixins.py:290-322`), used by the
  cloud Spark/Onehouse execution path (`benchbox/platforms/onehouse/quanton_adapter.py`).
  It returns the same `TuningClauses` dataclass as `core/tuning/generators/*`
  (imported for the type only, at the fallback branch on line 320) but builds
  it independently through its own `_generate_delta_tuning` /
  `_generate_iceberg_tuning` / `_generate_hudi_tuning` /
  `_generate_parquet_tuning` / `_generate_hive_tuning` methods rather than
  consuming the generators module's per-format logic — it is a third
  renderer, not an existing generators caller, and needs its own
  migration/equivalence pass during consolidation, not just deletion of the
  dead `generate_tuning_clause` methods below.
- Runtime execution calls `apply_standard_unified_tuning()` and then the
  adapter's `apply_table_tunings()` hook in
  `benchbox/platforms/base/tuning_config.py`. That is the production path that
  touches real database connections. Per-adapter singular
  `generate_tuning_clause` methods are direct-test-only/dead where no adapter
  hook calls them; they are not the non-Spark runtime renderer.

Because dry-run preview renders through `generators/*` and execution renders
through `apply_table_tunings()`, a preview can show DDL that the real run never
issues. Some singular adapter implementations are dead: `ClickHouse.generate_tuning_clause`
(`benchbox/platforms/clickhouse/tuning.py:354`) has zero production callers —
`rg -n "\.generate_tuning_clause\(" benchbox --include=*.py` (excluding the
`def` lines) returns nothing; it is exercised only by unit tests that call
the adapter method directly.

### Linked open question

Databricks liquid clustering raised two open questions (whether a DBR
incompatibility is a hard error or a warning; whether manual-mode
`liquid_clustering_columns` over 4 keys fails) that are instances of the
same policy axis this ADR settles: when does an engine-specific tuning constraint
block a run outright, versus warn and proceed? See Consequences.

## Decision

1. **Baseline definition.** `notuning` means platform defaults plus
   engine-mandatory schema choices only — nothing else. Curated
   session-optimization packs (e.g. the ClickHouse OLAP pack at
   `benchbox/platforms/clickhouse/tuning.py:74-100`) move to the tuned path,
   or to an explicitly recorded harness-defaults block if they must apply
   regardless of tuning mode; they must never apply silently only when
   tuning is disabled. Engine-mandatory physical layout that a working table
   requires (e.g. StarRocks `DISTRIBUTED BY HASH` injection at
   `benchbox/platforms/starrocks/workload.py:214-221`) is permitted in
   baseline, but must be labeled as engine-mandatory in bundle metadata so it
   is never mistaken for an applied tuning. Session-level `SET` statements
   are recorded in the bundle in every mode, not only when tuning is
   enabled. This settles the warn-vs-fail direction for the
   Databricks liquid-clustering questions: compatibility checks warn first; a run is blocked only for explicit,
   named unsafe combinations, not by default.

2. **Single renderer.** `core/tuning/generators/*` becomes the single
   tuning-DDL renderer. Adapter mixins are migrated to consume the
   generators rather than maintaining independent `generate_tuning_clause`
   implementations, so dry-run preview and real execution call the same
   rendering function and cannot drift. Renderers with zero production
   callers (e.g. `ClickHouse.generate_tuning_clause`) are deleted during
   consolidation rather than migrated. Migration proceeds per platform, each
   with a before/after DDL snapshot test proving the adapter-mixin output and
   the generator output are equivalent prior to cutover.

## Consequences

- Decision 2 and the labeling half of decision 1 are implemented through
  one capability registry, per-platform migration to the generators with
  before/after DDL snapshot tests, and deletion of dead renderers such as
  `ClickHouse.generate_tuning_clause`.
- The recording half of decision 1 is implemented by the applied-statement
  ledger: session-level `SET` statements (ClickHouse OLAP pack, StarRocks
  query settings) get bundle ledger entries in every mode, and
  `validation_status` stops certifying only that a metadata-table `INSERT`
  succeeded.
- For Databricks liquid clustering, DBR compatibility checks warn first and
  block only explicit unsafe runtimes. A hard failure for an explicit `>4`
  manual `liquid_clustering_columns` already matches this ADR's "block only
  explicit unsafe combinations" direction and stands unchanged.
- The ClickHouse OLAP session pack and any similar per-platform pack must be
  re-homed (tuned path or harness-defaults block) as part of the renderer
  consolidation work, not left in place with a comment.

## Rejected Options

1. **Keep session packs where they are, add recording only.** Record the
   ClickHouse pack's `SET` statements in the bundle ledger but leave them
   firing only when tuning is disabled. Rejected: this fixes the
   observability gap (a reader now knows what ran) but not the semantic bug
   (baseline still promises "no tuning" while shipping a curated OLAP
   profile). "notuning" would remain a false label with better paperwork.
2. **Consolidate into adapter mixins instead of generators.** Make each
   adapter's `generate_tuning_clause` the source of truth and have dry-run
   preview call adapter instances instead of the standalone generators.
   Rejected: dry-run preview needs to render DDL without a live connection
   or fully constructed adapter instance, which the generators already
   support and most adapter mixins do not; a capability registry keyed by
   platform type is also cleaner to build against a stateless generator
   interface than against ~20 adapter classes with mixed constructor
   requirements. Generators additionally already cover more platforms
   (17 modules) than adapters currently delegate to.

## Addendum (2026-08-01): landed policy seam and current consolidation state

The baseline inversion identified above has landed: ClickHouse's curated OLAP
pack runs only on the tuned path, while harness-operational and SQL-correctness
settings apply in every mode and are recorded as session statements. StarRocks
keeps only its engine-mandatory distribution fallback in baseline; tuned layout
is rendered by `core.tuning.generators.starrocks`. DuckDB and ClickHouse also
consume their core generators on their execution paths.

The consolidation is explicit rather than inferred from a package version.
`benchbox.core.tuning.policy_generation.TUNING_POLICY_GENERATION` stamps
`adr-003` into tuned bundle summaries and tuning companions, and the Explorer
warns on comparisons across that seam. The capability registry is the current
inventory of migrated and remaining adapter-renderer parity: entries that still
have a distinct execution renderer remain documented gaps, not evidence that
the repository-wide migration is complete.

## Addendum (2026-10-04): the local result file is not anonymized

`ResultExporter` anonymizes by default, and the CLI exports the user's own local
result through that default. Receipt statements, reasons and errors are redacted
in the file the user reads, and there is no flag to turn it off, so a user cannot
see why tuning did not verify.

**Decision: write the local result unanonymized; keep every outward path
anonymized.**

- The CLI writes the local result file unanonymized by default.
- Every publish, public export and submission path keeps full anonymization,
  including scrubbing of receipt statements, reasons and errors.
- The local file's tuning receipt and ledger text (statements, reasons and
  errors) is unscrubbed, so the user can read why verification did not succeed.
  Scrubbing of that text applies to anonymized exports only.
- The bundle states truthfully whether it was anonymized through the existing
  `export.anonymized` field.
- `scripts/publication/check_artifact_privacy.py` and submission validation
  continue to reject an unanonymized bundle, and a test pins that.
- The exporter's separate redaction of connection-identity keys on
  non-anonymized exports stays on for local files.
- The change is limited to exporter defaults and CLI wiring. The anonymizer
  (`benchbox/core/results/anonymization.py` and `anonymization_specs.yaml`) is
  not changed by this decision.

Rejected options: a `--no-anonymize` flag, which leaves the unhelpful default in
place for everyone who does not find it; and keeping the default while printing
an unredacted console summary, which leaves the file itself uninformative.

## Addendum (2026-10-04): tuning in load-only runs

`_execute_load_only_mode` (`benchbox/core/runner/runner.py`) calls
`create_schema` and `load_data` directly and never `apply_unified_tuning`. On
DuckDB with a sort-only custom configuration the loader still sorted and
re-created the indexes, but constraints, pre-load DDL, dropped-intent accounting
and platform optimizations were skipped, no metadata was saved, and the bundle
recorded `tuning_mode: custom` with `platform.tuning: null`.

**Decision: load-only runs get the full tuning lifecycle.** The mode runs the
same lifecycle as a full run: apply, reconcile, metadata save, ledger, receipt,
and the `platform.tuning` export. A bundle's `tuning_mode` must never claim
tuning without a `platform.tuning` section.

The alternative, refusing every `--tuning` value other than `notuning` in
load-only runs with an actionable error, was considered and not chosen.

## Addendum (2026-10-04): requested-but-not-rendered is a named unsafe combination

Decision 1 says compatibility checks warn first and block only for explicit,
named unsafe combinations. The validator (`validate_for_platform_detailed`)
nevertheless accepts tuning that execution never applies. The capability
registry says so itself: PostgreSQL, Redshift, BigQuery and MySQL render nothing
at execution for some types. Partitioning is accepted on PostgreSQL, BigQuery,
Snowflake, Databricks and DuckDB, and distribution and sorting are accepted on
Redshift. Trino and Athena only warn on unsupported types because they are not
"known" platforms.

**Decision: three named unsafe combinations, each a validation error.**

1. **Requested but not rendered.** A requested tuning type that the capability
   registry marks `rendered_via="none"` or `:preview_only` for the target
   platform is a validation error.
2. **Unknown platforms.** Platforms the validator does not know, such as Trino
   and Athena, error on unsupported types in the same way as known platforms.
3. **ClickHouse primary key not a prefix of the sorting key.** An explicit
   `PRIMARY KEY` that is not a prefix of the tuned sorting key is a validation
   error before schema creation.

Everything not named here keeps warn-first behavior.

**Sequencing guard.** The severity change for items 1 and 2 lands together with
the change that makes the capability registry the single source for validator
severity and with the per-platform renderers that move registry entries from
`none` or `:preview_only` to rendered. It ships with a test that every shipped
template, every `tuned-fallback` configuration and every `auto` configuration
validates clean on its own platform. A shipped configuration that trips the new
errors is a defect to fix in that change, not a reason to weaken the rule. Item 3
is independent of that sequencing and lands with the ClickHouse key handling.

## Addendum (2026-10-04): DuckDB partitioning

Every shipped DuckDB tuned template requests partitioning: TPC-H (`LINEITEM`,
`ORDERS`), SSB (`LINEORDER`) and TPC-DS (six fact tables). DuckDB always drops
it (`benchbox/platforms/duckdb.py`), and a dropped intent vetoes
`applied_verified` in `corroborate()`. The curated templates therefore defeat
their own verification.

**Decision: remove partitioning from the DuckDB templates.**

- Remove partitioning from every `examples/tunings/duckdb/*_tuned.yaml` and from
  the packaged mirrors in `benchbox/core/tuning/templates/duckdb/`.
- Register DuckDB partitioning as `rendered_via="none"` in the capability
  registry.
- Keep `TestPackagedTemplatesParity` green.
- The requested-config hash of the DuckDB tuned templates changes; the changelog
  records it.

The alternative, implementing partitioning as Hive-partitioned Parquet in
external-table mode only, was considered and not chosen.

## Addendum (2026-10-07): ClickHouse TPC-H tuned run slower than baseline

At SF1 on ClickHouse 25.8, the curated TPC-H tuned run measured 1.6× to 4.5×
slower than `notuning`. The regression was first attributed to the template's
monthly `PARTITION BY`. Measurements on one loaded copy of the data, with the
layouts and session settings varied separately and the 21 queries that run on
the 5.25 GiB envelope timed in shuffled order, do not support that
attribution:

- Queries that never read `lineitem` or `orders` (Q2, Q11) regressed as much as
  the rest, and slowed 14× in the monthly cell, so the layout was not the cause.
- `join_algorithm=grace_hash` in the tuned session pack, on the unpartitioned
  baseline layout, raised the geometric mean 2.2× and ran Q3 and Q5 out of
  memory. The other pack settings were within noise (1.03× to 1.06×).
- At SF1, a partition adds no pruning beyond the sort key: rows read per query
  were identical for sorted layouts with and without partitioning (Q6 0.15×,
  Q14 0.05×, Q15 0.04×), and a finer partition was never faster than a coarser
  one.
- Across the 21 queries that run on the 5.25 GiB envelope (Q21 is excluded
  because it fails there without its statement-level `hash` override, described
  below), sorting by ship and order date alone ran at 0.79× the baseline
  geometric mean (21% faster). The slowest queries were Q3, Q5 and Q18. Q3 read
  0.54× the rows of the baseline and took 1.43× the time, Q5 read 0.84× and took
  1.49×, and Q10 read 0.42× and took 1.00× (medians of seven rounds). Q3 and Q5
  read fewer rows and still ran slower. Q18 read 2.15× the rows and took 1.77×
  the time. The cause of the Q3, Q5 and Q18 slowdowns is unproven.
- After a partitioned load, 81 to 86 background merges overlapped the timed
  queries and each query read about 200 parts instead of the settled 84.

**Decision.**

- Remove `join_algorithm` and `grace_hash_join_initial_buckets` from the tuned
  ClickHouse session pack.
- Remove partitioning from the ClickHouse TPC-H tuned template and its packaged
  mirror, keeping the date sort keys. Scale-tiered partitioning was not built:
  at SF1, on ClickHouse 25.8 and the 5.25 GiB envelope, a partition adds no
  pruning benefit for these tables, so there is no tier to select. Larger
  scales are unmeasured.
- Run TPC-H Q21 with a statement-level `join_algorithm = 'hash'` on tuned
  server runs. With the date-first sort key its default parallel hash join
  peaks above the 5.25 GiB envelope and fails; the single hash join peaks at
  3.4 GiB. Setting `hash` for the whole session instead raised the geometric
  mean 1.23× and Q13 4.2×, so it is limited to the one query that needs it.
- Wait for background merges to settle after a tuned server-mode load, so the first
  timed query does not compete with merges. The wait is bounded and is not
  counted as load time. The wait runs only after tuned server loads, not after
  `notuning` loads. A `notuning` cell may therefore time queries while merges
  still run, which favours the tuned cell. The comparisons below carry this
  asymmetry until the wait is recorded for every mode.
- A test rejects any ClickHouse tuned template that partitions a table on the
  column that already leads its sort key.

The measurements are SF1 only. Larger scales were not run: SF100 does not fit
the 16 GB measurement host, and SF10 needs more memory than the 5.25 GiB
envelope used here. The partitioning conclusion holds for SF1, ClickHouse 25.8
and the 5.25 GiB envelope. It is not shown for SF10 and larger, which remain
unmeasured; a layout study at those scales is follow-up work. Partitioning also
changes part counts, merges and parallelism, and these depend on scale.
Server results here ran in a Linux VM under Apple `container`, not under Linux
Docker.

**Per-template decisions.** These decisions are provisional and rest on SF1
only. The SSB and TPC-DS decisions rest on a single sequential CLI cell per
mode, not on repeated, shuffled rounds. A tuned template is kept when its
geometric mean beats `notuning` beyond noise, no query fails that passes
untuned, and every query more than 1.5× slower than untuned is named here. For
SSB and TPC-DS the rule is relaxed to a geometric mean no worse than
`notuning` within about 10% and no added failing query. That threshold was set after the SSB and TPC-DS
numbers were seen. The decisions stay provisional until a benefit rule fixed
before measurement replaces it.

| Template | Decision | Evidence (SF1, ClickHouse 25.8, 5.25 GiB) |
|---|---|---|
| TPC-H | Adjust: partitioning dropped, date sort keys kept | Sort-only layout ran at 0.79× the `notuning` geometric mean over 21 queries, excluding Q21, which fails at the 5.25 GiB envelope without its hash override. Slower queries (median of seven rounds): Q3 1.43×, Q5 1.49×, Q18 1.77×. The cause is unproven. |
| SSB | Keep | Fresh server, three cells, no failures: geometric mean 109.8 ms `notuning`, 61.2 ms tuned (0.56×), 64.1 ms tuned with `optimize_after_load`. Host load average stayed at or below 8.3. |
| TPC-DS | Keep, unchanged | Geometric mean 80.9 ms `notuning`, 84.1 ms tuned, 76.4 ms tuned with `optimize_after_load`. Q10 is the only failing query. It fails its warm-up and all three measured iterations in all three cells with "memory limit exceeded ... maximum: 4.67 GiB". The summary count of three failures is those three iterations of one query. |

### Correction (2026-10-09)

Several claims in the 2026-10-07 addendum went beyond what its measurements
show. The addendum is amended in place above. This section quotes each
superseded sentence so the original claim stays traceable.

- "a partition has no pruning benefit at any scale for these tables, so there is
  no tier to select." Only SF1 was measured. The conclusion now names SF1,
  ClickHouse 25.8 and the 5.25 GiB envelope, and lists SF10 and larger as
  unmeasured. The earlier sentence "The absence of a large-scale partitioning
  benefit is argued from the pruning result above, which does not depend on
  scale" is withdrawn: part counts, merges and parallelism change with scale,
  so the pruning result alone does not settle larger scales.
- "Its slowest queries were Q3 (1.49×) and Q5 (1.44×), because `lineitem` is no
  longer ordered by order key." and, in the TPC-H table row, "because
  `lineitem` is sorted by ship date instead of order key." The study does not
  show a cause. In the seven-round study, Q3 read 0.54× the rows and took 1.43×
  the time, Q5 read 0.84× and took 1.49×, and Q10 read 0.42× and took 1.00×.
  The cause is unproven. The superseded 1.49× and 1.44× were best-of-seven
  times. The ratios now quoted are medians of seven rounds. The statistic
  matters: the Q5 ratio is 1.10× as a geometric mean of the rounds and 1.49× as
  a median, so the size of the slowdown is itself uncertain.
- "ran at 0.79× the baseline geometric mean (21% faster)". The figure is a
  geometric mean over 21 queries. Q21 is excluded because it fails at the
  5.25 GiB envelope without its per-query hash override. The sentence now says
  so. The figure is the geometric mean of each query's seven-round geometric
  mean; the median-based figure for the same data is 0.78×.
- "The same three queries fail in every cell." One query fails: Q10. The three
  measured iterations and the warm-up all fail with a memory limit of 4.67 GiB,
  in all three cells. The count of three in the result summaries is those three
  iterations.
- "For SSB and TPC-DS the rule is relaxed to a geometric mean no worse than
  `notuning` within about 10% and no added failing query." The threshold was
  chosen after the numbers were seen, and the Keep decisions rest on single
  sequential CLI cells at SF1. They are now marked provisional and SF1-only.

The merge-settle wait runs only after tuned server loads, so `notuning` cells
may time queries while merges still run. This favours the tuned cell and is now
stated in the decision. The cause of the Q3, Q5 and Q18 slowdowns and the size
of any effect at larger scales remain open. No decision changed: the template
contents, the session pack contents and the Q21 override stay as decided.

## Addendum (2026-10-10): benefit rule for tuned-template verdicts

This rule was fixed before any verdict was recorded under it. No verdict below
may be recorded from a cell measured before this addendum merged. One
statistic is used throughout: every per-query ratio is the ratio of medians
over rounds. Earlier mixed statistics (best-of-seven, geometric mean of
geometric means) are superseded wherever verdicts are concerned.

**Arms.** `N` is `notuning`, `T` is the tuned template. Both arms load once
and both are settled after load. Rounds are shuffled and interleaved: 9 by
default, 7 minimum. The seed is fixed and recorded.

**Per query.** The median over rounds in each arm.

**Primary metric `G`.** The geometric mean, over queries that pass in both
arms, of `median_T / median_N`. Its 95% confidence interval comes from a
percentile bootstrap that resamples rounds 2,000 times, with the seed
recorded. Each cell also reports total elapsed time per arm, load and
merge-settle time per arm, failures per arm, and `G` recomputed with every
failed query penalized at the per-query timeout (default 300 s).

**Calibration gate.** Before any verdict on a platform, benchmark and scale,
an A/A run must have a 95% interval containing 1.0 and a point estimate
between 0.95 and 1.05. If it fails, raise rounds (up to 15) and re-run on a
quieter host. If it still fails, the cell is uncalibrated and yields no
verdict. An answer mismatch between identical arms fails calibration on its
own; a cell whose timed rounds did not run under the configured session is
therefore uncalibrated, not a verdict.

**Verdict.**

- **Regression** if any of these holds: a query that passes in `N` fails in
  `T`; any answer (row count or checksum) differs between the arms; the
  interval lower bound is above 1.0; or the verdict would otherwise be
  Neutral and `T`'s load plus merge-settle time is more than 1.5 times `N`'s.
- **Benefit** if the interval upper bound is below 1.0.
- **Neutral** otherwise.

**Load-plus-settle accounting.** Load and merge-settle times are recorded
separately per arm (merge settle in its own result phase), and the 1.5x
comparison uses their sum. Merge settle has a floor of about 2 s per arm, so
at small scales the ratio can exceed 1.5x on a few seconds of absolute
difference. The verdict therefore always reports the absolute load and
merge-settle times per arm alongside the ratio, and a Regression reached on
this ground alone names the absolute excess so a floor-driven ratio is
visible as such.

**Named slow queries.** Every query whose median ratio is above 1.5x and
whose per-query interval lower bound is above 1.0 is listed with its measured
cause, or "unproven" where no cause was measured. This is reporting, not a
veto.

**Scope.** A verdict records scale factor, memory rung, engine version and
date, and applies only to that tier. No per-benchmark relaxation.

**Outcome.** Benefit or Neutral keeps the template and sets its evidence
state. Regression removes the component the arms implicate (layout per table,
then each session setting), then re-measures, repeating until Benefit or
Neutral. If every component is removed and the cell is still Regression, the
tuned template is deleted (`tuned` then resolves to `tuned-fallback`) and the
deletion is recorded here.

**Evidence narrative.** The 2026-10-07 sentence "The cause of the Q3, Q5 and
Q18 slowdowns is unproven" is superseded as follows. Across two calibrated
harness cells (SF1, ClickHouse 25.8, 5.25 GiB rung), the date-first lineitem
order slows Q3 ~1.2x, Q5 ~1.5-1.6x and Q18 ~1.4-1.5x against `notuning`,
while an orderkey-first lineitem order restores all three to ~1.0x but
forfeits the Q6/Q14/Q15 date-pruning wins (Q6 1.02x, Q14 1.03x, Q15 0.99x
against 0.32x/0.45x/0.25x date-first) and is slower overall (G 0.97 against
0.83). Q18 is explained by lost primary-index pruning on the IN-subquery
values. Q3 and Q5 run the identical hash-join plan at identical peak memory
with 1.2-1.7x more user CPU on fewer input rows under date order. The Q13
42/41 split was a session-settings artifact (`join_use_nulls` applied at load
but lost before timed rounds), not engine behaviour; the correct answer is 42.
No layout changes: the shipped date-first layout still wins on the overall
geometric mean.
