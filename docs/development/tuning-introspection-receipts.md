# Post-load introspection receipts

Design note for post-load catalog introspection. It builds on the
applied-tuning ledger (ADR-001,
`docs/development/tuning-adr-001-trust-and-hash-semantics.md`) and the
`benchbox.core.tuning.applied_ledger` module.

## Problem

The applied ledger records *what the execution path actually ran* and
derives `applied_unverified` when at least one tuning statement executed.
That is still self-attested: it certifies the statement did not raise, not
that the requested physical layout is present in the database. `ADR-001`
reserves `applied_verified` for a separate code path that corroborates the
ledger against the real catalog. This is that path.

## Trust rule (soundness)

`applied_verified` is claimable **only** via catalog corroboration, never
from the ledger alone (`AppliedTuningLedger.overall_status` never returns
it). Concretely, a run is upgraded `applied_unverified -> applied_verified`
iff:

1. introspection ran without error (bounded catalog reads, wrapped
   non-fatal); and
2. there is **at least one** catalog-backed (`verifiable`) executed
   ddl/post_load statement; and
3. **every** verifiable statement is `corroborated` -- none `absent`, none
   `mismatch`, none `unverifiable`; and
4. the ledger contains no failed ddl/post_load statement and no dropped
   tuning intent. Each physical failure receives a blocking `unverifiable`
   receipt entry with a code-authored failure reason, and dropped intents are
   copied into the receipt's additive `dropped` section. Raw driver detail
   remains in the ledger statement's `error` field, not the receipt reason.

Any introspection failure, timeout, or single non-corroborated statement keeps
an otherwise eligible run at `applied_unverified` with a receipt recording why.
An all-physical-failed ledger derives `failed` before corroboration, and an
empty ledger cannot earn the upgrade. The introspection step must never fail or
materially slow a run.

The receipt summary calls the count participating in this decision
`gate_relevant_total`. It includes `corroborated`, `absent`, `mismatch`, and
blocking `unverifiable` entries; `transient` and `maintenance` are excluded.
The earlier name `verifiable_total` was removed because a count that includes
`unverifiable` entries was internally contradictory. There are no production
consumers of this additive receipt-summary key; the upgrade decision continues
to use the entry verdicts directly.

## Where the trust code lives

The adapter-side corroboration, drift-check routing, layout-operation fold,
apply-phase status, introspector selection, and ledger read-back live in
`benchbox/platforms/base/tuning_trust.py`.
`PlatformAdapter._corroborate_applied_ledger`,
`_attach_applied_ledger_payload`, `_build_drift_check_payload`, and
`_fold_layout_operations_into_ledger` remain as one-line delegates, so callers
and subclasses keep their existing entry points. `run_enhanced_benchmark`
derives its apply-phase status and reads the ledger back through that module
too, including the validation-failure path, which attaches the payload via
the same delegate. The DuckDB, ClickHouse, and Snowflake
`get_tuning_introspector` overrides are one-line delegates to that module's
per-platform factories -- the admitted verdict-producing set stays exactly
those three platforms (see the ADR-001 verification-reach addendum), and no
new introspector is constructed. The DataFrame ledger status derivation and
result attach live in `benchbox/platforms/dataframe/tuning_trust.py`, with
`TuningConfigurableMixin` keeping one-line delegates. `applied_verified` is
still emitted only by `corroborate_applied_ledger`, whose behavior is
unchanged: it returns the status untouched unless the derived status is
`applied_unverified`, and any introspector or corroboration error leaves the
status as it was and logs at debug level.

The adapter files themselves (`adapter.py`, `duckdb.py`,
`clickhouse/adapter.py`, `snowflake.py`, the DataFrame mixins) keep their
trust-relevant methods as one-line delegates pinned by tests, so a change that
routes around these modules fails the suite.

## Requested-versus-rendered reconciliation

Many adapters accept a tuning request and render nothing for it: the
PostgreSQL family, BigQuery, Trino, the managed Spark platforms, DataFusion
and others. Before reconciliation such a run recorded an empty ledger, so the
result could not show that the request was ignored. The ADR-001 ledger
vocabulary addendum (2026-10-04) decides that every requested intent ends as
`executed`, `failed`, `dropped(reason)` or `satisfied_by(reference)`.

`benchbox/core/tuning/reconciliation.py` implements that rule.
`PlatformAdapter.run_enhanced_benchmark` calls it once per fresh tuned SQL
platform run,
after the data loads and the layout operations are folded into the ledger,
through the one-line delegate `_reconcile_requested_tuning` and
`tuning_trust.reconcile_requested_tuning`. It never runs at apply time,
because post-load mechanisms such as the DuckDB sort index and the CTAS sort
have not run yet. Reused databases, dry runs and untuned runs are skipped.
DataFrame platforms use a different tuning configuration and are not
reconciled.

The requested intents are each table's partitioning, clustering,
distribution and sorting columns, each enabled constraint type that the
benchmark declares, and each enabled platform optimization flag. For each
intent, in order:

1. **Evidence.** An executed `ddl` or `post_load` statement on the same table
   whose SQL carries the footprint of that tuning type, with the requested
   columns named in that clause in the requested order, realizes the intent.
   Column names inside string literals do not count, except in property-style
   clauses such as `partitioned_by = ARRAY['col']`. Footprints are generic
   (`ORDER BY`, index column lists, `CLUSTER BY`, `PARTITION BY`,
   `DISTRIBUTED BY`, `PRIMARY KEY` and so on), plus the registry mechanism
   where a platform folds one type into another clause: ClickHouse clustering
   into `ORDER BY`, Snowflake partitioning into `CLUSTER BY`. When the
   registry renders the type through `ddl` and the statement is in the `ddl`
   phase, a `satisfied` entry references that statement's index, unless one
   already covers the intent. Otherwise the intent needs no entry.
2. **Already accounted.** A failed attempt on the same table and type, or an
   existing dropped or satisfied entry for it (adapter drops, sorted-ingestion
   entries, Snowflake's already-present clustering key), leaves the intent
   as it is.
3. **Drop.** When the registry marks the type `none` or `:preview_only` for
   the platform, the drop reason is the registry note. Otherwise, including
   platforms with no registry entry, it is `adapter rendered no statement`.

Until the physical sort rewrite is recorded as an executed ledger statement
(ADR-001 addendum of 2026-10-04 on sort evidence), a table in the adapter's executed
sorted-ingestion list counts as sort evidence. That only withholds a drop;
it never adds corroboration.

Reconciliation can only append dropped and satisfied entries. It never
changes a statement, the applied ledger hash, or a receipt verdict, so it
cannot make a run more verified: a drop blocks `applied_verified`, and a
satisfied entry is never a verdict and never corroboration. Drops never
produce `failed`; a ledger whose only outcomes are drops is `noop`. If
reconciliation raises, it records the blocking drop `tuning_reconciliation`
with the error instead of passing silently.

A constraint toggle is an intent only when the benchmark declares a
constraint of that type. The run's benchmark is asked for its `CREATE TABLE`
statements with every constraint enabled, and the constraint types found in
them are the declared ones. No shipped benchmark declares `UNIQUE` or `CHECK`
constraints, so enabling them records nothing. Declared primary or foreign
keys that the platform does not render are still dropped. When the benchmark
cannot answer (no `get_create_tables_sql`, an error, or a result that is not
SQL text), every enabled toggle is reconciled, which errs toward a drop.

## Statement classes (per phase x mechanism)

`corroborate()` gates every ledger statement by recorded status and phase,
then classifies each **executed** statement and applies a
per-class rule. Classification is by generic SQL shape so the core stays
platform-agnostic; the per-platform catalog *reads* live in the
`Introspector` implementations.

| Ledger statement (shape)                     | phase          | class         | corroboration rule                                                                 | drives verification? |
| -------------------------------------------- | -------------- | ------------- | ---------------------------------------------------------------------------------- | -------------------- |
| `CREATE INDEX [IF NOT EXISTS] n ON T (cols)` | ddl/post_load  | `index`       | a catalog `index` fact on `T` whose columns equal `cols` (order- and case-normal)  | yes                  |
| `CREATE TABLE ... ORDER BY (cols)`           | ddl/post_load  | `sort_key`    | a catalog `sort_key` fact on `T` whose columns equal `cols`                         | yes                  |
| `CREATE TABLE ... PARTITION BY (cols)`       | ddl/post_load  | `partition_key` | a catalog `partition_key` fact on `T` whose columns equal `cols`                 | yes                  |
| `ALTER TABLE T CLUSTER BY (cols)`, `CREATE TABLE ... CLUSTER BY (cols)` | ddl/post_load | `cluster_key` | a catalog `cluster_key` fact on `T` whose columns equal `cols`, in order | yes |
| `SET ...`, `PRAGMA ...`                       | any            | `transient`   | session/config, no persistent catalog footprint -- noted, **non-blocking**         | no                   |
| `OPTIMIZE TABLE ...` / maintenance           | ddl/post_load  | `maintenance` | merge/compaction op, no distinct catalog footprint -- noted, **non-blocking**      | no                   |
| `ALTER TABLE T [RESUME/SUSPEND] RECLUSTER`   | ddl/post_load  | `maintenance` | reorganizes existing data; the clustering KEY is the catalog footprint -- **non-blocking** | no          |
| session-phase statement (any)                | session        | `transient`   | transient SET -- noted, **non-blocking** (documented default below)                | no                   |
| failed statement                             | ddl/post_load  | `unverifiable`| the requested physical change did not execute; failure reason is recorded -- **blocks** | n/a (blocks)     |
| failed statement                             | session        | `transient`   | transient session failure is noted but has no persistent catalog footprint -- **non-blocking** | no          |
| anything else                                | ddl/post_load  | `unverifiable`| no corroboration rule -- **blocks** the upgrade (conservative: stay unverified)    | n/a (blocks)         |

The generic classifier's non-blocking prefix sets are intentionally narrow and
are documentation, not an invitation to expand the gate: transient prefixes
are exactly `set `, `pragma `, `set\t`, `pragma\t`, `reset `, and `use `;
maintenance prefixes are exactly `optimize `, `vacuum`, `analyze`, and
`compact `. Any other ddl/post_load shape remains blocking `unverifiable`.

A `CREATE TABLE` carrying **both** an `ORDER BY` and a `PARTITION BY` (the
ClickHouse MergeTree shape) classifies as both `sort_key` and `partition_key`
and emits one receipt entry per clause, so each key must corroborate on its
own. Corroborating only the first clause would let a run reach
`applied_verified` while the other configured key never applied.

**Verdicts** (per statement): `corroborated`, `absent` (expected object not
in catalog), `mismatch` (object present, columns differ -- carries a short
diff), plus the non-blocking notes `transient` / `maintenance` and the
blocking `unverifiable`.

Example summary/entry shape (the ledger's statement order is preserved):

```json
{
  "summary": {"corroborated": 1, "transient": 1, "gate_relevant_total": 1},
  "entries": [
    {"statement": "CREATE INDEX i ON t (a)", "phase": "post_load", "verdict": "corroborated"},
    {"statement": "SET threads=4", "phase": "session", "verdict": "transient"}
  ]
}
```

Clause matching first blanks string literals, quoted identifiers, line
comments, and block comments with a small lexical scanner, then applies the
auditable regex shapes above. Clause-looking text in a `DEFAULT` literal or
comment therefore cannot create an intent or suppress ClickHouse's tuned-key
recording. Empty parsed column tuples never corroborate: even an empty catalog
tuple is treated as a fail-closed mismatch, not trivial equality.

### Session SETs are corroboration-eligible? No (default).

Session `SET`/`PRAGMA` statements are transient: they configure the
connection, leave no catalog trace, and vanish when the session closes.
They are **noted** in the receipt (so the reader sees they ran) but do
**not** block verification and cannot, on their own, earn it. A run whose
only tuning is session SETs stays `applied_unverified` -- there is nothing
physical to corroborate. The applied-ledger hash still includes those executed
session records in their true chronology; exclusion here is specific to the
catalog-corroboration gate, not to ledger identity.

A failed session statement follows the same transient gate rule: it is
visible in the receipt but does not block a sibling physical statement that
the catalog corroborates. Conversely, session success cannot mask a run in
which every attempted ddl/post_load statement failed; that ledger derives
`failed`, not `applied_unverified`.

## Per-platform catalog reads (Introspector)

Each platform's `Introspector.introspect(connection, ledger) -> IntrospectedState`
returns structured catalog facts (`kind`, `table`, `columns`, `name`,
`evidence`), bounded to the tables the ledger touched and wrapped
non-fatal. **No screen-scraping of engine DDL text** where a structured
catalog exists. ClickHouse and Snowflake keep a hard catalog-row limit,
then filter the bounded result to table names extracted from every supported
ledger statement shape, including `ALTER TABLE`. Their `truncated` flag measures
the relevant rows after filtering, so an at-cap catalog full of unrelated tables
does not reject a complete target snapshot. A target omitted by the hard limit
still yields an `absent` verdict (and therefore cannot verify); a relevant result
that itself reaches the cap remains explicitly truncated.

- **DuckDB** (`benchbox/platforms/duckdb_introspection.py`): reads
  `duckdb_indexes()` -- the `table_name` / `index_name` / `expressions`
  columns (structured; `expressions` is the indexed-column list). One
  bounded query, filtered to the ledger's tables. Corroborates each
  `CREATE INDEX` ledger entry against its `index` row.

  DuckDB index statements are corroborated against the physical identifiers
  `create_schema` produced. The tuning builder resolves each logical table and
  column through `duckdb_tables()` / `duckdb_columns()` (case-insensitive,
  returning the catalog's stored spelling) and emits the index DDL with those
  names quoted, for example `ON "lineitem" ("l_orderkey", "l_linenumber")`.
  Quoting matters: an index written against a differently-cased name
  (`ON LINEITEM (L_ORDERKEY)` over a table created as `lineitem`) is stored by
  DuckDB as the bound expression `(LINEITEM.L_ORDERKEY)`. That fact still
  produces a `mismatch`, and its diff states that the catalog stored a bound
  expression because the identifier case in the DDL differs from the catalog.
  `normalize_columns` is deliberately unchanged, so a bound form is never
  unwrapped into a match.

  The loader's `CREATE OR REPLACE TABLE ... ORDER BY` CTAS drops the initial
  index, so `DuckDBAdapter.apply_ctas_sort` re-creates the same index after the
  CTAS using the shared `_duckdb_sort_index_sql` helper.
  `_record_sort_index_layout_op` records the re-creation as a `post_load`
  layout operation, and `PlatformAdapter._fold_layout_operations_into_ledger`
  folds it into the ledger before corroboration. Dry runs capture SQL but
  neither execute nor record the re-creation.

  A corroborated index is evidence of the index, not of row order: an index can
  exist when the data was never sorted. Reaching `applied_verified` also needs
  every other statement in the ledger to corroborate, so a run that also
  requests constraints, partitioning or CHECK constraints stays
  `applied_unverified` until those have catalog rules. A sort-only
  configuration built through the real `create_schema` is pinned by
  `tests/unit/platforms/test_physical_identifier_resolution.py::TestDuckDBPhysicalIdentifiers::test_sort_only_config_reaches_applied_verified_without_mismatch`.

  DuckLake cannot build indexes (`CREATE INDEX` raises `DuckLake does not
  support indexes`). Its adapter records each requested sort or clustering
  index as a dropped intent with the reason `ducklake: CREATE INDEX
  unsupported`, issues no `CREATE INDEX`, and does not use this introspector.

- **Snowflake** (`benchbox/platforms/snowflake_introspection.py`): reads
  `INFORMATION_SCHEMA.TABLES.CLUSTERING_KEY` with bound, normalized schema
  parameters, then filters the structured rows to ledger tables. Both
  `LINEAR(A, B)` and `(A, B)` catalog forms normalize to the same exact,
  order-sensitive column tuple while the live catalog spelling remains
  unconfirmed.

  A matching catalog key certifies only that Snowflake accepted the clustering
  **key metadata**. It does not certify that existing micro-partitions have
  finished reclustering: `ALTER TABLE ... CLUSTER BY` updates metadata before
  asynchronous maintenance completes, and `RESUME RECLUSTER` remains a
  non-blocking maintenance statement. This is intentionally weaker than the
  ClickHouse receipt, whose CREATE-time sorting and partition keys describe the
  physical MergeTree layout. When a reused Snowflake table already has the
  requested key, the adapter skips ALTER, records the intent as
  dropped/already-present, and separately reads `AUTO_CLUSTERING_ON` to resume
  maintenance when it is suspended.

- **ClickHouse** (`benchbox/platforms/clickhouse/introspection.py`): reads
  `system.tables` (`name` / `sorting_key` / `partition_key` -- structured
  comma-separated key expressions), filtered to `currentDatabase()` and the
  ledger's tables. Returns `sort_key` / `partition_key` facts as receipt
  **evidence**.

  Multi-column catalog keys may be returned as `(a, b)` or `tuple(a, b)`;
  receipt normalization removes exactly one balanced outer wrapper so those
  forms compare with the DDL intent `a, b`. Matching remains an exact,
  order-sensitive tuple comparison. Inner function-call parentheses are not
  stripped: for example, `f(a), b` remains distinct from `f(a, b)`.

  Expression spelling is deliberately not canonicalized. Verbatim expressions
  such as `toYYYYMM(ts)` corroborate, while ClickHouse rewrites such as
  `INTERVAL 1 DAY` to `toIntervalDay(1)` remain an honest fail-closed mismatch.
  Reimplementing ClickHouse's version-dependent expression canonicalizer here
  could over-certify a different expression.

  Table identity also stays conservative. Generic corroboration compares the
  normalized table strings exactly; it does not suffix-match a
  schema-qualified intent such as `analytics.t` to a bare catalog fact `t`.
  Current introspectors are scoped to a database/schema and emit the names their
  structured catalogs provide. Until the contract carries qualification on
  both sides, an ambiguous qualified-vs-bare pair remains `absent` rather than
  widening matching and risking certification of the wrong table.

  ClickHouse applies `ORDER BY` / `PARTITION BY` at `CREATE TABLE` time in the
  *schema* phase, which is not wrapped by the tuning recording connection, so
  `create_schema` records the executed statement onto the ledger itself
  (`_record_tuned_sort_key_op`, at `connection.execute` time so the
  order-sensitive `applied_ledger_hash` keeps true chronology). Only a table
  carrying a *tuned* clause is recorded: a MergeTree table's `sorting_key` is
  always present, so corroborating an untuned table's engine-mandatory
  `ORDER BY` would be a trivially-true, unearned upgrade. A table tuned with a
  partition key only is recorded too -- omitting it would leave an unapplied
  partition uncorroborated while a sibling table's sort key carried the run to
  `applied_verified`. The ledger's other ClickHouse entries stay
  `OPTIMIZE TABLE` (maintenance, `non-blocking`) and session SETs.

## Wiring

`PlatformAdapter.run_enhanced_benchmark` folds post-load layout operations
before session configuration, then derives `overall_status` after execution
while the connection remains open. When that status is `applied_unverified`
and the adapter exposes an
introspector (`get_tuning_introspector()`), run introspection guarded,
corroborate, and upgrade to `applied_verified` iff the receipt corroborates.
The receipt rides inside the applied ledger
(`AppliedTuningLedger.to_payload(status=..., receipt=...)`), which the bundle
carries at `platform.tuning.applied` -- no file of its own, and no new plumbing
through `result_capture.py`. It shipped in a
`.applied.json` companion until that was retired into the bundle; bundles
published earlier still carry it there and are still read.

The explorer ingests and renders receipt text verbatim, so public-export
anonymization is the only publication gate for the receipt. Anonymized
exports therefore remove the top-level receipt `error`; every entry's
`statement`, `reason`, `detail`, `diff`, and `evidence`; table, column, and
index identifiers; observed catalog details; and both top-level and
receipt-level dropped-intent text. Additive `*_redacted` / `redacted` markers,
structural verdicts, summary counts, and `corroborated` remain. Non-anonymized
exports retain the full diagnostic text.
