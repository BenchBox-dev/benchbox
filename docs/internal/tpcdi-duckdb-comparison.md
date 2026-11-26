# TPC-DI comparison with the DuckDB implementation

BenchBox compares its TPC-DI DuckDB warehouse with the one built by
`djouallah/tpcdi`, the only open DuckDB implementation that gates on an audit.
It is a comparison point, not an oracle: it runs PDGF directly, splits
`CustomerMgmt.xml`, rewrites one answer-key file, keeps DOUBLE columns and the
Yield and 52-week defects, and uses a rewritten audit.

Rules set by the owner:

- Where that project and the specification differ, the specification wins. Every
  difference, and every behaviour of the project that is kept, is a row of
  `benchbox/core/tpcdi/etl/DEPARTURES.yaml`, which the owner approves.
- A difference from the DuckDB implementation that is not in
  `scripts/tpcdi/EXPECTED_DIFFERENCES.yaml` is a finding that blocks acceptance.
- Its code runs only from a clone outside the repository, on a copy of the
  staging files, through `make heavy-run`, with output under
  `~/Developer/benchmark_runs`. Nothing from its repository is committed:
  only the facts on this page and the expected-differences list.
- DIGen output, the appendix SQL and the audit SQL are never edited. Staging
  files produced by DIGen are never edited; the copy is.

`benchbox/core/tpcdi/etl/DEPARTURES.yaml` is the catalogue. Every entry of the
expected-differences list cites a row of it in its `departure` field, and the
harness refuses an entry that cites none or cites a row that is not in the
catalogue. A catalogue row whose `status` is `pending` names a behaviour of
BenchBox that a later load step implements; the row exists so the difference is
already accounted for when that step lands.

## Pinned versions

| Item | Value |
|---|---|
| `djouallah/tpcdi` | `5305b1e7f8cc15a4cb80c0633c859d03f8005acb` (2026-07-20, MIT) |
| duckrun | `7043903`, the pin of that project's `requirements.txt` |
| Python | 3.11.17 (the project's CI uses 3.11) |
| dbt-core / dbt-duckdb | 1.12.7 / 1.11.1 |
| duckdb in the clone's environment | 1.5.6 (see below) |
| deltalake / obstore | 1.5.0 / 0.12.1 |
| BenchBox DuckDB (harness, official audit) | the repository lock |

duckrun at that commit requires `duckdb>=1.5.4` and no upper bound. With DuckDB
2.0.1 its first write (`CREATE OR REPLACE TABLE Audit`) raises
`ResourceInUseException`, so the environment pins `duckdb>=1.5.4,<2`.

## Procedure

Everything below lives outside the repository, here under
`~/Developer/benchmark_runs/tpcdi-duckdb-comparison/` (the 2026-11-22 run used
`tpcdi-dj-comparison/`). `$SF` is the scale factor and `$STAGING` is a DIGen
output directory made by BenchBox.

1. Generate the staging with BenchBox (Java 8 only: set `BENCHBOX_JAVA_HOME`
   to the Zulu 8 JDK under `~/Developer/tpc-tools`), through the heavy-work lock:

   ```
   GEN='BENCHBOX_JAVA_HOME=<zulu8-home> uv run python gen.py <run-dir>'
   make heavy-run CMD="$GEN"
   ```

   where `gen.py` calls `TPCDIBenchmark(scale_factor=SF, output_dir=...).generate_digen_source_data()`.
   The manifest hash of every staging is recorded in the results below.

2. Clone and pin, then build the environment:

   ```
   git clone https://github.com/djouallah/tpcdi.git dj
   git -C dj checkout 5305b1e7f8cc15a4cb80c0633c859d03f8005acb
   uv venv --python 3.11 venv
   VIRTUAL_ENV=$PWD/venv uv pip install "duckrun @ git+https://github.com/djouallah/duckrun.git@7043903" obstore dbt-core pandas pyyaml "duckdb>=1.5.4,<2"
   PATH=$PWD/venv/bin:$PATH dbt deps --project-dir dj/sequential --profiles-dir dj/sequential
   ```

3. Copy the staging (`cp -R $STAGING $RUN/staging`) and apply the two changes
   the project makes to its own generator output, by calling its functions on
   the copy: split `Batch1/CustomerMgmt.xml` into chunks of 20000 actions
   (`generate_data._split_customermgmt`) and recount the Batch 2 and 3
   `C_DOB_TO`/`C_DOB_TY` answer-key values (`fix_audit_key.repair_seed_root`).
   Neither changes a data row: the split keeps every `TPCDI:Action` element, and
   the recount rewrites only `Customer_audit.csv` of the copy. At scales 3 and 5
   the recount changes nothing; at scale 10 it changes Batch 2 `C_DOB_TO` from 1
   to 0.

   The project's data generation step is not used (`--skip-generate`): it runs
   PDGF directly, which BenchBox does not do. Its other expectations (the
   `Batch1`..`Batch3` layout, `*_audit.csv`, `Generator_audit.csv`) are met by
   unmodified DIGen output. The extra `digen_report.txt` and
   `_datagen_manifest.json` in the copy are not read.

4. Build the warehouse through the heavy-work lock:

   ```
   WAREHOUSE_PATH=$RUN/warehouse TPCDI_DIR=$RUN/staging DBT_SCHEMA=tpcdi_seq TPCDI_SF=$SF \
     python dj/sequential/scripts/run.py --sf $SF --skip-generate --staging $RUN/staging --schema tpcdi_seq
   ```

   This creates Delta tables under `$RUN/warehouse/tpcdi_seq`, runs the three
   batches, its `dbt test` (94 tests) and its own audit. Its audit process
   reached 8.79 GiB at scale 10 and the heavy-work watchdog (8 GiB budget)
   stopped it, so the audit is rerun on its own with
   `SET memory_limit = '5GB'` and a temporary directory under the run directory,
   by running `dj/sequential/tools/run_sequential_audit.py` from a wrapper that
   sets both on the connection `duckrun.connect` returns. The SQL is unchanged.

5. Export the Delta tables to one DuckDB file with the `delta` extension
   (`CREATE TABLE "<name>" AS SELECT * FROM delta_scan('<dir>')`) for every table
   except `bronze*`, `stg_customermgmt` and `FinWire`. One type is adapted:
   `Prospect.income` is VARCHAR in the project (it also stores it as text) and
   the official audit's `COALESCE(Income, 0)` does not bind against text on
   DuckDB, so the exported copy has `ALTER TABLE Prospect ALTER income TYPE INTEGER`
   (every value is numeric or NULL). Nothing else is adapted: the official audit
   binds against the project's tables as they are.

6. Run the official audit over the exported file:

   ```
   AUDIT='uv run python scripts/tpcdi/compare_warehouses.py <export.duckdb> --audit-only --memory-limit 5GB --json <out.json>'
   make heavy-run CMD="$AUDIT"
   ```

   The text is the unmodified `tpcdi_audit.sql`, loaded through
   `load_appendix_sql("audit", "duckdb")`.

7. Compare two warehouses built from the same manifest (BenchBox's first):

   ```
   uv run python scripts/tpcdi/compare_warehouses.py <benchbox.duckdb> <implementation.duckdb> --audit --json <out.json>
   ```

   Exit 0 means every difference is on the list, 1 means at least one is not
   (the output names the table), 2 means the list is invalid.

   Comparing the implementation's warehouse with itself under `--audit` exits 1
   with exactly one finding: `DimCustomer customer tier alerts`, Batch 1,
   `Mismatch` vs `Mismatch`. That is expected, because that side carries
   DIGen's uncorrected `C_TIER_INV` key, and no entry accepts a left-side
   Mismatch, so a BenchBox warehouse that skips the correction is refused.

## What the harness compares

Per table present on both sides:

- row counts;
- rows only on one side, matched on the natural key (for the history-tracked
  dimensions with `EffectiveDate`); surrogate keys are compared as columns;
- per column, the rows that differ, after both sides are normalised:
  integer widths and `DECIMAL` precision are ignored, a `DOUBLE` against a
  `DECIMAL(p,s)` is rounded to `s`, a boolean against a number is cast to an
  integer, and any other type difference is compared as text;
- column and type differences, and tables present on one side only;
- a key that is not unique on either side is a finding.

`DImessages` is compared as counts per `(BatchID, MessageType, MessageSource,
MessageText)`, so timestamps and message data are ignored, and the counts per
batch and type are printed for both sides. With `--audit` each side runs the
official audit and every `(test, batch, description)` outcome is compared. A
result other than OK on the left side is a finding even when the right side
agrees, unless an `audit` entry accepts that exact pair.

Tables without a natural key in the harness, and tables whose key is not unique, are compared as multisets of
normalised whole rows: a group of identical rows whose count differs is reported, so a duplicated row is a
difference even when the row counts are equal. A row-count mismatch that the reported differences do not account
for is always a finding.

## Expected differences

`scripts/tpcdi/EXPECTED_DIFFERENCES.yaml` holds one entry per known, explained
difference. Every entry has `id`, `kind`, `clause` (the specification clause that
makes BenchBox right), `reason` and `departure` (the id of the catalogue row that
records the difference).

| `kind` | Fields | Accepts |
|---|---|---|
| `table_missing` | `table`, `side` | a table present only on `side` |
| `column_missing` | `table`, `column`, `side` | a column present only on `side` |
| `column_type` | `table`, `column`, `left_type`, `right_type` | that exact pair of types (`*` matches) |
| `column_value` | `table`, `column`, `condition` | differing rows for which `condition` holds, over `l` (left row) and `r` (right row) |
| `rows_only` | `table`, `side`, `condition` | rows only on `side` for which `condition` holds, over `t` |
| `messages` | `condition` | differing message groups for which `condition` holds, over `g` (`batchid`, `messagetype`, `messagesource`, `messagetext`, `left_count`, `right_count`) |
| `audit` | `test`, `batch`, `left_result`, `right_result` | that outcome pair (`*` matches) |

A `column_value` or `rows_only` entry accepts a column or side only when its
condition holds for every differing row; one row outside it leaves the whole
column unaccepted. Entries that accept nothing in a run are printed as unused.

The list holds the differences that follow from the project's known departures
and from the observations below. The entries for the historical dimension tables
have been exercised against a BenchBox warehouse (see the next section); the
others are derived from the project's SQL and from its tables at scales 3, 5 and
10, and the owner's review has to confirm each against the BenchBox build that
first loads their tables.

## Historical dimension tables (2026-11-24)

The implementation can stop after its historical phase
(`sequential/scripts/run.py --batches 1`). Its historical warehouse was built at
scales 3 and 5 from the same DIGen output as BenchBox's (DIGen output is
identical between runs at one scale) and compared with BenchBox's warehouse
after staging and the historical DimCompany, DimSecurity, Financial,
DimCustomer, DimAccount and Prospect transformations. Both sides were reduced
to those tables, the reference tables and the Alert and Status rows of
`DImessages`, because the fact tables and the batch validation are not built
yet.

| Scale | Rows equal on all six tables | Differences | Unaccepted |
|---|---|---|---|
| 3 | yes | `SK_SecurityID` on 2243 rows, BatchDate rows of Batches 2 and 3, alert and status message groups, exact numeric types | 0 |
| 5 | yes | `SK_SecurityID` on 3750 rows, BatchDate rows of Batches 2 and 3, alert and status message groups, exact numeric types | 0 |

The implementation writes its alerts after Batch 3, so its historical warehouse
holds none. Against its full three-batch run, BenchBox's Batch 1 alert counts at
scale 3 are equal for customer tier (252) and date of birth (8) and larger for
SPRating (154 against 113), because the implementation makes repeated alerts
distinct.

The trade and fact tables refer to securities through `SK_SecurityID`. BenchBox
numbers securities by effective date and symbol, the implementation by effective
date alone with ties in no defined order. When both warehouses hold
`DimSecurity`, the harness therefore compares every other table's
`SK_SecurityID` as the `Symbol` and `EffectiveDate` of the security it refers to.

## Historical trade and fact tables (2026-11-25)

The same comparison was repeated after the historical DimTrade,
FactCashBalances, FactHoldings, FactWatches and FactMarketHistory
transformations, with those tables and `DimTime` kept on both sides.

| Scale | Rows equal on all five tables | Differences | Unaccepted |
|---|---|---|---|
| 3 | yes | PERatio on 4538 rows, Yield on 1272413 rows, trade and market alert groups, `DimTime.TimeValue` type | 0 |
| 5 | yes | PERatio on 6192 rows, Yield on 2400580 rows, trade and market alert groups, `DimTime.TimeValue` type | 0 |

DimTrade, FactCashBalances, FactHoldings and FactWatches are equal row for row,
and so are the 52-week highs and lows with their dates. Of the PERatio
differences at scale 3, 4464 are rows where the implementation falls back to
Financial rows older than the four calendar quarters before the trading date
and BenchBox stores no ratio, and 74 are rows where missing quarters change the
sum. Both sides divide in DOUBLE, so a yield whose exact quotient ends in half a
cent may round either way on either side; the yield entry therefore accepts a
BenchBox value within half a cent of the scaled implementation value.

## Initialization, historical load and audit (2026-11-25)

`scripts/tpcdi/run_phases.py` runs initialization, the historical load and the
audit on DuckDB in one invocation, through the DuckDB adapter:

```bash
uv run -- python scripts/tpcdi/run_phases.py --scale-factor 3 \
  --staging <DIGen output> --database <new .duckdb> --output <result .json> \
  --allow-third-party-extensions
```

The CLI still refuses TPC-DI. The adapter's load phase first checks the staging
manifest, refusing staging that changed or was generated at another scale
factor, and loads the extension that reads CustomerMgmt.xml; neither step is
timed. It then runs initialization: it drops and
recreates the warehouse and the raw staging tables, runs Appendix B for BatchID
0 and stores the completion record's timestamp and the monotonic time taken
when it commits. The power phase refuses to start unless initialization ran in
the same invocation. It loads Batch 1 and runs the historical transformations
and Appendix B for BatchID 1.
The measured time is the difference between the two monotonic times. The audit
then loads every `*_audit.csv`, corrects only the attributes the independent
recount finds wrong, runs Appendix A unchanged apart from the recorded
`FROM DUMMY TABLE` substitution, and changes no warehouse table other than
Audit.

The result's `validation_details.tpcdi_audit` block lists every audit row with
its outcome and whether it can be evaluated after Batch 1, the excluded tests,
the answer-key corrections, the DValues too wide for `DECIMAL(15,5)` (stored as
NULL; Appendix A reads no DValue) and both phase records. The status is FAILED
when an evaluable test is not OK and NOT_VALIDATED otherwise; no TPC-DI result
reads PASSED.

Which rows can be evaluated is read from the audit SQL itself: a per-batch row
counts when its batch is no later than the latest loaded batch; a whole-run test
is excluded when it requires 3 batches or 4 completion records, or when it sums
an Audit attribute that has rows for a later batch.

| Scale | Evaluable audit rows OK | Excluded whole-run tests | Answer-key correction | Historical load | Unaccepted differences, 20 Batch 1 tables |
|---|---|---|---|---|---|
| 3 | 135 of 135 | 13 | C_TIER_INV 251 → 252 | 4.79 s | 0 |
| 5 | 135 of 135 | 13 | C_TIER_INV 380 → 381 | 7.41 s | 0 |

The excluded tests are DImessages Phase complete records, DImessages batches,
the eight `<table> batches` tests (DimAccount, DimCustomer, DimTrade,
FactCashBalances, FactHoldings, FactMarketHistory, FactWatches, Prospect), and
DimTrade canceled trades, charge alerts and commission alerts, which sum Audit
attributes that also have rows for Batches 2 and 3. Three negative controls (an
invalid trade type, one suppressed customer-tier alert, one Trade row removed
from a copy of staging with the manifest check bypassed) each make the run
FAILED and are refused by `publish_admission`; the same copy is refused by the
manifest check when it is not bypassed.

## Results of the early run (2026-11-22)

Staging by BenchBox at `24b7592956` with Zulu 8, DIGen 1.1.0, tools SHA-256
`d9c10de5dafa781ddd4df72453f50af2f0c9c7ccfa0cbae99ed0be70a2ede22b`.

| Scale | Manifest hash | Project: batches 1 / 2 / 3 | Project's dbt tests | Project's audit | Official audit over its tables |
|---|---|---|---|---|---|
| 3 | `c9bbe7bc9d20593f17a645271e96e27c0e156670b583b012f76a0316e77dae14` | 11.1 s / 4.9 s / 5.0 s | 94 of 94 | 257 of 258 OK | 257 of 258 OK |
| 5 | `1d96eb6d977c101ca7e8d8669181e81e096b80a96f37a04e56ddbefb11e20ed2` | 14.1 s / 5.7 s / 6.2 s | 94 of 94 | 257 of 258 OK | 257 of 258 OK |
| 10 | `eed88cce0addcf67172b310312a5c14e996fa240a87002e859334634c7dcf749` | 26.7 s / 8.5 s / 7.8 s | 94 of 94 | 258 of 258 OK | 258 of 258 OK |

The only outcome that is not OK, at scales 3 and 5 and in both audits, is
`DimCustomer customer tier alerts`, Batch 1 (`Mismatch`): DIGen's answer key
for `C_TIER_INV` in Batch 1 is off by one. At scale 10 the project's tables pass
that test with the key as generated. The per-test outcomes, the logs and the
exported warehouses are under
`~/Developer/benchmark_runs/tpcdi-dj-comparison/runs/sf{3,5,10}/`
(`official_audit.json`, `official_audit.txt`, `dj_audit.log`, `run.log`).
