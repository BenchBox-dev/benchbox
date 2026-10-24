# ADR-001: Tuning Trust Model and Hash Semantics

## Status

Accepted, 2026-07-12. Decided by the project maintainer.

## Context

A 2026-07-12 review of the tuning system surfaced two unresolved product
questions that blocked soundness fixes to the tuning pipeline:

**1. Trust model.** Published tuning claims are, today, self-attested and
nothing says so:

- `tunings_applied` recorded on a benchmark result is the *requested*
  tuning configuration, not a record of what was actually applied:
  `effective_tuning_config.to_dict()` is captured directly as
  `tunings_applied_dict` (`benchbox/platforms/base/adapter.py:743-747`),
  regardless of whether individual DDL clauses or session `SET`s
  succeeded.
- `tuning_validation_status` only distinguishes `APPLIED` /
  `FAILED_TO_SAVE` / `NOT_APPLICABLE` based on whether a metadata-table
  write succeeded — it certifies that *something* was written, not that
  the requested tuning was physically realized in the database.
- No mechanism lets an evaluator verify a published tuning claim against
  the actual database state.
- Published results already carry a provenance trust label
  (`maintainer-run`, `community-submission`, `vendor-supplied`), and a
  further `verified` label is reserved for future third-party attestation.
  Tuning claims need an equivalent label without forking that vocabulary.

**2. Hash semantics.** The tuning "hash" concept is four disjoint,
inconsistent things across the codebase, and the one field meant to
carry it is never populated:

- `benchbox/core/tuning/interface.py:846-863` —
  `get_configuration_hash()`: full SHA-256 over `self.to_dict()`
  (canonical JSON, sorted keys, no truncation).
- `benchbox/core/tuning/profile_validation.py:324-333` —
  `hash_tuning_template()`: a *different* SHA-256-based hash, truncated
  to 16 hex characters, over `to_dict()` (or `repr()` as a fallback for
  non-dict-like inputs).
- The bundle's `tuning_config_hash` field: defined but never set by any
  code path — bundles ship with no populated hash at all.
- The Results Explorer ingest pipeline's `_tuning_hash()`: an 8-character SHA-256 hash computed independently
  by the explorer ingest pipeline from `{"mode": ..., "detail": ...}`,
  derived from whatever ad hoc `tuning_mode`/`tuning_config` keys happen
  to be present in the ingested JSON — not derived from the canonical
  tuning object at all.

None of these four notions agree on what is hashed (requested template,
effective config, ad hoc explorer-side dict) or on truncation length,
and none of them capture the physically applied statements. This ADR
decides what the hash(es) must certify going forward.

## Decision

### 1. Trust model: self-attested, labeled; introspection is the design direction; third-party attestation stays deferred

All published tuning claims remain **self-attested**. This is labeled
explicitly in the explorer UI and in docs — evaluators must not be able
to mistake a self-attested claim for an independently verified one.
This ADR fixes the decision, not the UI copy.

Post-load schema-introspection receipts (querying the live database
after load to confirm the DDL/settings that were requested actually took
effect, and attaching that as a receipt alongside the bundle) are the
**design direction for future verification**. They are not implemented
by this decision and are not a blocker for the current soundness work.

Third-party attestation (an independent party re-running and certifying
a result) stays **deferred**, consistent with the `verified` trust
label that is reserved for results but not yet implemented. This
decision does not fork or extend that vocabulary; it reuses
"self-attested" as the tuning-specific instance of the same "not
independently verified" concept the results trust labels already
anticipate.

### 2. Hash semantics: two hashes, each with a distinct, named purpose

The single ambiguous `hash` / `tuning_config_hash` field, and all
repr-based or ad hoc hashing (including the explorer's `_tuning_hash()`
derived from loosely-typed ingested JSON), are **not valid** going
forward. They are replaced by exactly two named hashes:

1. **`requested_config_hash`** — canonical SHA-256 over
   `UnifiedTuningConfiguration.to_dict()`, serialized as JSON with
   `sort_keys=True` and compact separators (`(",", ":")`), full 64-hex-
   character digest (no truncation). This is the **platform-independent
   template identity**: two runs that requested the same tuning template
   produce the same `requested_config_hash` regardless of which platform
   executed it or whether every clause actually applied.

2. **`applied_ledger_hash`** — a hash over the **applied-statement
   ledger**: the ordered record of DDL clauses, post-load statements,
   and session `SET`s that were *actually executed* against the target
   platform. (Exact failure representation and ordering guarantees are
   left to the implementation; §3 records them.) This is the
   **platform-specific physical identity** of the run — it changes if
   the platform renders the same requested template into different
   physical statements, or if some statements fail to apply.

Both hashes are carried on the bundle. Neither hash is optional, and
neither substitutes for the other: `requested_config_hash` answers "did
two runs ask for the same tuning," `applied_ledger_hash` answers "did
two runs physically do the same thing to the database."

The exact serialization format of the applied-statement ledger itself
(what constitutes a "statement," ordering guarantees, failure
representation) is an implementation detail; §3 records the shape that
landed.

### 3. Realized ledger: the `.applied.json` companion and vocabulary

The applied-statement ledger (`benchbox.core.tuning.applied_ledger`) is
produced **by the execution path** — each tuning-relevant statement is
recorded as it runs against a transparent recording proxy over the
tuning/session connection — and is never reconstructed from the
requested config. It is exported as an additive `.applied.json` companion
alongside the existing `.tuning.json` (which is unchanged and still
carries the *requested* config + `requested_config_hash`).

`.applied.json` shape (the ledger's `to_payload(status=...)`):

```json
{
  "status": "applied_unverified",
  "applied_ledger_hash": "<sha256 | null>",
  "statements": [
    {"statement": "CREATE INDEX IF NOT EXISTS ...", "phase": "ddl",
     "status": "executed", "mechanism": null, "table": null}
  ],
  "dropped": [
    {"intent": "partitioning:LINEITEM", "reason": "handled at load time"}
  ]
}
```

- **`applied_ledger_hash`** — `sha256` over the ordered list of
  *tuning* statement records (executed statements that are not
  `harness_default` and, in the `session` phase, carry a tuning
  mechanism), serialized as canonical JSON
  (`sort_keys=True`, compact separators). List order is preserved (only
  each record's keys are sorted), so statement chronology is part of the
  identity. `null` when no tuning statement executed (no physical layout to
  identify). Mirrored onto the bundle's `platform.tuning` summary and the
  `.tuning.json` companion next to `requested_config_hash`.
- **`phase`** ∈ {`ddl`, `post_load`, `session`}; statement `status` ∈
  {`executed`, `failed`, `harness_default`}. `dropped` records requested intents that never
  rendered to a statement (capability-filtered / load-time-only).

`tuning_validation_status` vocabulary (execution-derived, all-lowercase),
replacing the old metadata-write proxy where `APPLIED` meant only "a
metadata INSERT succeeded":

| status | meaning |
| --- | --- |
| `not_applicable` | tuning disabled, or no effective configuration |
| `noop` | tuning requested but no tuning statement executed and none failed; a ledger of only harness or unattributed session statements reads `noop` |
| `applied_unverified` | ≥1 tuning statement executed (a ddl/post_load statement, or a session statement that carries a tuning mechanism); self-attested, not yet introspection-corroborated. Harness and unattributed session statements do not count. |
| `applied_verified` | executed **and** corroborated by a post-load introspection receipt; every physical statement must corroborate, with no failed ddl/post_load statement or dropped intent. Only the receipt path emits it; never the ledger alone. |
| `failed` | ≥1 ddl/post_load statement was attempted and all such statements failed, even if a session statement executed; or no tuning statement executed and a statement failed; or the apply path raised |
| `not_validated` | dataclass default, pre-run (before any derivation) |

Metadata-persistence outcome is **no longer** a tuning status: the old
`FAILED_TO_SAVE` is downgraded to the separate boolean
`tuning_metadata_saved` note, fully decoupled from the tuning status.

Legacy back-compat readers map old uppercase statuses via
`LEGACY_STATUS_MAP`: `NOT_APPLICABLE→not_applicable`,
`APPLIED→applied_unverified`, `FAILED_TO_SAVE→applied_unverified` (both
old values meant "a statement executed" under the new model), and
`NOT_VALIDATED→not_validated`.

## Consequences

Implementations must honor these decisions as fixed constraints:

- **Applied-statement ledger.** The implementation builds the ledger this
  ADR assumes, computes `applied_ledger_hash` from it, and never conflates
  `tunings_applied` (requested) with what was physically applied.
  `tuning_validation_status` must be able to express partial application,
  not just `APPLIED`/`FAILED_TO_SAVE`/`NOT_APPLICABLE`. Populating
  `applied_ledger_hash` onto the bundle needs the bundle's
  `requested_config_hash` field and export scaffolding to exist first, so
  the ledger work is what completes the "both hashes carried on the
  bundle" requirement.
- **Bundle provenance and config export.** The bundle populates
  `requested_config_hash` (replacing the never-set `tuning_config_hash`
  field) and carries and displays the self-attested trust label alongside
  it. This step does **not** populate `applied_ledger_hash`: the ledger
  depends on it, not the other way round, so requiring the hash here could
  not be satisfied in build order.
- **Mode vocabulary and facets.** Any facet or mode-derived data surfaced
  to evaluators must not imply verification beyond self-attestation, and
  must source hash values from the two canonical hashes above rather than
  recomputing an ad hoc hash (e.g., the explorer must stop deriving its
  own hash from ingested `tuning_mode`/`tuning_config` JSON and instead
  consume `requested_config_hash` / `applied_ledger_hash` directly from
  the bundle).

Any doc or UI surface that implies tuning claims are verified (rather
than self-attested) is out of compliance with this ADR and must be
corrected.

## Rejected options

- **Single hash.** Rejected: a single hash cannot simultaneously answer
  "same requested template" and "same physical execution" — the review
  found four different single-hash notions already in conflict for
  exactly this reason. Collapsing to one hash would just relabel the
  ambiguity rather than resolve it.
- **Build introspection verification now.** Rejected for this decision
  cycle: schema-introspection receipts require new per-platform
  introspection logic and a receipt format, which is real implementation
  work with its own design surface. It is adopted as the direction for
  future verification but is explicitly out of scope for the current
  soundness remediation pass.
- **Leave tuning claims unlabeled (implicitly trusted).** Rejected:
  this is the status quo and is what the review flagged as unsound —
  evaluators currently have no signal that `tunings_applied` is a
  request, not a certified outcome.

## Addendum (2026-07-23): drift-validation bundle routing

The open question was where the rerun **drift-validation** result belongs in the published bundle. When a run
reuses an existing database, `TuningValidator` compares the database's
persisted tuning metadata against the expected
`UnifiedTuningConfiguration` (`platforms/base/tuning_config.py`
`_validate_database_tunings` → `MetadataValidationResult`). That result
was previously computed and then dropped — only the first three error
strings survived, as validation messages; the structured drift
(`drifted_sections`, `configuration_mismatches`, `missing_tables` /
`extra_tables`) never reached the bundle.

**Decision:** the drift-validation result rides in the existing
`.applied.json` companion (ADR §3) as an additive `drift_check` section —
**not** a new sibling file. This reuses the established companion (the
same file that already carries the applied-statement ledger and the
introspection receipt), matches the `MetadataValidationResult` docstring's
own anticipation of an "applied-ledger drift_check companion", and keeps
one place to look for "what actually happened to the database this run".

`drift_check` shape (`MetadataValidationResult.to_payload()`): `is_valid`
plus, when non-empty, `errors`, `warnings`, `missing_tables`,
`extra_tables`, `configuration_mismatches`, and `drifted_sections`. It is
**descriptive only** and is never a source of `applied_verified` — that
status remains reserved for post-load introspection corroboration.

Scope and honesty constraints:

- **Reused databases only.** A fresh database just persisted its metadata,
  so nothing could have drifted; `drift_check` is emitted only when
  `database_was_reused` and the run is tuned.
- **Empty-ledger carry.** A reused DB re-applies no tuning DDL, so its
  applied-statement ledger is empty. The companion is still written when a
  `drift_check` is present (the "nothing captured → no companion" prune in
  `build_applied_ledger_payload` is relaxed to keep a drift-only companion).
- **Anonymized exports.** `drift_check` free text (`errors` can embed an
  exception's path/DSN; `warnings` / `configuration_mismatches` /
  `missing_tables` / `extra_tables` can embed catalog/table identifiers) is
  dropped under the same policy as the statement/receipt text, leaving the
  structural `is_valid` + `drifted_sections` and a `drift_redacted` marker.

## Addendum (2026-08-01): introspection and fail-closed gating landed

The original Decision section recorded post-load introspection as a future
direction. It has since landed in `benchbox.core.tuning.introspection` and
`PlatformAdapter._corroborate_applied_ledger`; this addendum updates current
state without rewriting that historical decision.

`applied_verified` now requires at least one gate-relevant physical intent,
every such intent corroborated against structured catalog facts, and no failed
ddl/post_load statement or dropped tuning intent. Failures receive blocking
`unverifiable` receipt entries, while dropped intents are copied into the
receipt. The summary key is `gate_relevant_total` because it counts all verdicts
that participate in the decision, including fail-closed `unverifiable`; the
former `verifiable_total` name had no production consumer and was misleading.

The applied-ledger hash continues to preserve statement chronology: JSON object
keys are sorted for canonical serialization, but the executed-statement list is
never reordered. Post-load layout operations that do not pass through the
recording connection are folded into that list at their actual execution phase
before session statements, via
`PlatformAdapter._fold_layout_operations_into_ledger`.

## Addendum (2026-08-01): complete reused-database drift boundaries

Metadata schema version 3 extends the sentinel hashes to cover primary- and
foreign-key enablement plus the complete per-column tuning attributes (type,
sort direction, null placement, and compression). Readers branch on the
persisted version: version 2 retains its historical hash shape and produces an
explicit reduced-coverage warning; malformed or future versions fail closed.
This prevents a shape upgrade from manufacturing drift in a legacy database.

Extra persisted table tunings are errors, not warnings. Reuse would otherwise
run an allegedly narrower configuration against physical layout left behind by
an earlier run. The validator never deletes those tunings; normal lifecycle
policy recreates or refuses the database.

A notuning run likewise cannot reuse a database carrying tuning metadata. It
is refused and recreated rather than publishing a baseline result against a
known tuned layout. Metadata load failures are distinct errors, never reported
as clean absence. Marker persistence remains non-fatal to the original run,
but the manager exposes that degraded write and a later reuse without complete
markers reports reduced drift coverage. None of these `drift_check` outcomes
can contribute to `applied_verified`.

## Addendum (2026-09-17): the ledger moved into the bundle

The realized ledger described in §3 no longer ships as a `.applied.json`
companion, and the requested configuration no longer ships as `.tuning.json`.
Both now live in the result bundle under `platform.tuning`: `requested` for the
configuration a run asked for, `applied` for the ledger of what it executed
(status, statements, dropped intents, receipt, drift check). The hashes,
`validation_status`, and `tuning_policy_generation` keep the places §3 gave
them, on the `platform.tuning` summary.

Nothing about the trust semantics changes. The two hashes remain distinct,
`applied_verified` is still earned only by a corroborating receipt, and the
ledger is still produced by the execution path rather than reconstructed from
the requested config. What changes is the file layout: answering "what did this
run request, and what did it apply?" required stitching three files, so a bundle
separated from its companions silently lost the answer and every consumer
reimplemented the stitch.

Readers still accept both companions, so bundles exported before this keep
loading unchanged; nothing writes them any more.

## Addendum (2026-10-04): evidence for a physical sort with no catalog footprint

On DuckDB, "sorting" is two separate things: an ART index (`idx_<table>_sort`),
which `duckdb_indexes()` reports, and a physical rewrite of the table (CTAS or
the foreign-key-safe in-place sort in
`benchbox/platforms/base/sorted_ingestion.py`) that orders the rows and leaves
nothing in the catalog. The ledger records the index. It records only skips and
failures of the rewrite, never a success, so `corroborate()` can check the index
alone. An index can exist while the data was never sorted, and MotherDuck sorts
without creating any index. This addendum decides how a sort is evidenced.

**Decision: record the rewrite and attest it, without widening corroboration.**

- **Recording.** A successful physical rewrite is recorded as an executed ledger
  statement with phase `post_load` and `mechanism="sorted_ingestion"`.
  `_classify` gives it a new verdict constant, `ATTESTED = "attested"`.
- **Receipt entry.** The existing `ReceiptEntry` shape is used, with
  `verdict: "attested"`, `phase: "post_load"`, `table`, the `statement`, and
  `reason: "physical sort executed; no catalog footprint"`. No new top-level
  receipt fields are added.
- **The trust rule does not change.** `corroborate()` sets `corroborated` only
  when at least one statement has a verifiable verdict (`corroborated`,
  `absent`, `mismatch`, `unverifiable`), every such statement is `corroborated`,
  the state is not degraded or truncated, and the ledger has no dropped intents.
  `transient` and `maintenance` are non-blocking and never count as
  corroboration. `attested` joins them as non-blocking, stays out of
  `_VERIFIABLE_VERDICTS`, and can never satisfy or replace an intent that can be
  verified against the catalog. Index intents remain catalog-verified.
- **A requested sort with no executed rewrite is a dropped intent.** The
  post-load reconciliation records it, and a dropped intent blocks the upgrade.
  The rule lives in the reconciliation; `corroborate()` stays free of
  configuration.
- **Attested evidence alone never reaches `applied_verified`.** A run whose only
  sort evidence is attested, such as MotherDuck with no index, stays
  `applied_unverified`. `applied_verified` needs at least one corroborated
  verifiable statement.
- **Explorer label** for an attested entry: "Recorded; not catalog-checkable".

Rejected options:

- **Leave sort intents unverifiable without catalog evidence.** Rejected: it
  would make DuckDB sorting unable to reach `applied_verified` even when the
  index is corroborated, and it gives no record that the rewrite ran.
- **Probe the data order.** Rejected: a bounded read of row order would break
  the rule that introspection never measurably slows a run.

## Addendum (2026-10-04): which constraints are catalog-verifiable

Constraint-bearing `CREATE TABLE` statements are captured as tuning DDL
(`primary key`, `foreign key`, `unique` and `check` in
`benchbox/core/tuning/applied_ledger.py`), but `_classify` returns
`UNVERIFIABLE` for all of them. Every shipped DuckDB template, and the
`tuned-fallback` and `auto` configurations, enable constraints, so none of those
runs can verify even though DuckDB reports constraints in `duckdb_constraints()`.

**Decision: verify PRIMARY KEY, UNIQUE and FOREIGN KEY on DuckDB only.**

- **Kind.** Add `KIND_CONSTRAINT = "constraint"` with a `constraint_type`
  sub-field.
- **Source.** `duckdb_constraints()`. On the pinned DuckDB (1.5.5, from
  `uv.lock`) it exposes `constraint_type`, `constraint_column_names`,
  `referenced_table` and `referenced_column_names`, so foreign keys are
  verifiable there. Its output also includes `NOT NULL` rows, which are not
  tuning intents and are ignored. If a later DuckDB drops the referenced
  columns, foreign keys revert to `unverifiable` on DuckDB.
- **CHECK stays `unverifiable`, which blocks.** The expression is text, and the
  decision does not guess. A shipped template or `auto`/`tuned-fallback`
  configuration that enables CHECK constraints on DuckDB must disable them, and
  the change is noted in the changelog.
- **Every other platform:** constraint statements stay `unverifiable`, which
  blocks. A missing structured source never becomes a guess.
- **Match rules.** Identifiers are normalized with the existing
  `normalize_identifier` and `normalize_columns`.
  - PRIMARY KEY and UNIQUE match when the column lists are equal, in order.
  - FOREIGN KEY matches only when three things are equal: the child columns in
    order, the referenced table, and the referenced columns in order. `_Intent`
    and `IntrospectedObject` gain `referenced_table` and `referenced_columns`,
    used only by foreign-key intents and facts, so a key pointing at the wrong
    table cannot corroborate.
  - A constraint whose fact lacks the referenced fields is `unverifiable`, not
    `corroborated`.

## Addendum (2026-10-04): ledger outcome and phase vocabulary

Three behaviors blur what the ledger says happened. A deliberate skip, such as a
Delta-only or Hudi layout operation on Databricks, is folded in as
`STATEMENT_FAILED`. An intent that DDL already realized, such as a ClickHouse or
StarRocks sort expressed as `ORDER BY` in `CREATE TABLE`, is recorded as dropped
because the shared sorted-ingestion hook says it does not support a CTAS sort.
Phases are unvalidated: Databricks emits `pre_load`, `overall_status` filters on
`{ddl, post_load}`, and `record()` accepts any string.

**Decision.**

- **Skips are drops.** A deliberate skip is recorded as `dropped(reason)` with
  the reason `skipped: <cause>`. It blocks the upgrade and never produces
  `STATEMENT_FAILED` or an overall `failed`.
- **DDL-realized intents are `satisfied_by`.** An intent realized by DDL is
  recorded as `{"intent": ..., "satisfied_by": <index of the executed DDL
  statement in the ledger>, "reason": ...}`, serialized in its own `satisfied`
  list next to `dropped`. It is non-blocking, is never a verdict, and never
  counts as corroboration. Only the referenced DDL statement's own verdict
  counts.
- **Closed phase set.** The phases are `{ddl, post_load, session}`. `record()`
  maps known aliases (`pre_load`, `schema` and `create` to `ddl`; `postload`,
  `post-load` and `maintenance` to `post_load`) and coerces any other value to
  `ddl` with a warning log. Capture never raises. A unit test asserts that every
  in-tree producer passes a member of the closed set.
- **No new status values.** `overall_status` considers every physical phase.
  Drops never produce `failed`. A ledger whose only physical outcomes are drops
  returns `noop`. Executed statements together with drops return
  `applied_unverified`; the drops already block verification in `corroborate()`.

Every requested intent therefore ends as `executed`, `failed`,
`dropped(reason)` or `satisfied_by(reference)`.

## Addendum (2026-10-04): verification reach beyond DuckDB and ClickHouse

Only DuckDB, ClickHouse and Snowflake have introspectors. Candidates for other
platforms read structured catalogs (Databricks `DESCRIBE DETAIL`, BigQuery
`INFORMATION_SCHEMA.COLUMNS`, Redshift `SVV_TABLE_INFO`, PostgreSQL `pg_index`,
StarRocks `information_schema.tables_config`, Trino and Iceberg `$properties`,
Firebolt `information_schema.indexes`, Synapse `sys.pdw_*`, MotherDuck
`duckdb_indexes()`). The published corpus has no tuned bundle on any of them,
and several render no layout at execution today.

**Decision: no new verdict-producing introspector in this cycle.** Existing
introspectors for DuckDB, ClickHouse and Snowflake stay as they are. The
admitted set of additional platforms is empty.

A platform is admitted later only when all three hold:

1. its layout renders at execution (its capability-registry entry is neither
   `none` nor `:preview_only`);
2. tuned runs on it are planned (a tuned corpus bundle exists or one is
   scheduled);
3. a live confirmation run on that platform is approved.

Any introspector must be bounded (filter inside the SQL `WHERE` and measure
truncation on the raw row count before filtering), non-raising, and read
structured catalogs only.

A facts-only presentation of raw catalog facts for platforms without an
introspector is deferred, not built here. The support matrix lists those
platforms as having no introspector.

Immediate work under this decision is limited to recording it and fixing
ClickHouse introspector truncation, so that rows are filtered in the query and
truncation is measured before filtering.

## References

- `benchbox/platforms/base/adapter.py:743-747`
- `benchbox/core/tuning/interface.py:846-863`
- `benchbox/core/tuning/profile_validation.py:324-333`

## Addendum (2026-10-05): fail-closed tuned-run marker

Decision: every tuned run writes a run-kind marker row (existing `benchbox_tuning_metadata` row shape, no DDL change) before applying any physical tuning and fails instead of tuning when the marker cannot be written, so a notuning run refuses any database carrying the marker even when full tuning metadata was never saved; baselines still write nothing.

## Addendum (2026-10-17): landed trust evidence and its limits

Changes since the 2026-10-05 addendum that later work must not undo:

- **Effective session configuration is evidence, not identity.** Spark, LakeSail
  and Velox runs read the live session configuration back at session creation,
  after configuration and at run start, and record the redacted values and any
  discrepancies in `platform.effective_session_config`. The block never enters
  `requested_config_hash`, `applied_ledger_hash` or the drift markers, and it
  has no digest of its own, because the captured values include per-run fields
  such as `spark.app.id`. It carries its own layout `version`; the bundle schema
  version did not change for it.
- **A no-op re-create is satisfied, not executed.** When DuckDB re-issues
  `CREATE INDEX IF NOT EXISTS` after its in-place sort rewrite, the ledger
  records a `satisfied` entry pointing at the original index statement. Like
  every satisfied entry it is not part of `applied_ledger_hash` and never counts
  as corroboration.
- **Benchmark-specific drops carry their reason.** DuckDB and SQLite create
  TPC-DS tables without foreign keys and record requested foreign keys as a
  dropped intent with that reason, so the run cannot reach `applied_verified`
  while claiming keys it did not create.
- **Liveness is tested, not assumed.** A real-CLI integration gate
  (`tests/integration/tuning/test_tuning_liveness_e2e.py`, CI job
  `tuning-liveness`) pins which platform, benchmark and mode cells reach
  `applied_verified`. The generated support matrix
  (`docs/reference/tuning-platform-support.md`) lists the platforms that can:
  only those with a catalog introspector and at least one tuning type applied
  at execution.
