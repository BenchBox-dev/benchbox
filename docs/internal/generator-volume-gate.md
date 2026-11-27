# Generator volume gate

A change to a pure-Python generator or its `generator_specs.yaml` can multiply
the data a benchmark emits without any test noticing. The `generator-volume`
job in `ci.yml` catches that before merge. `core` requires it through its
aggregate step, so no ruleset change is involved. The filter reads a changed-file
list built with `--no-renames`, so a gated file that is renamed and edited in
one pull request still fires it under its old name.

## When it runs

`ci-paths` sets `generator-volume-needed` when the diff touches
`benchbox/core/<benchmark>/generator.py` or `generator_specs.yaml` of one of:
amplab, h2odb, coffeeshop, tsbs_devops, joinorder_synthetic,
vector_search, write_primitives, transaction_primitives. For vector_search it
also fires on `benchmark.py`, which sets the default vector width; and for both primitives
benchmarks on `benchbox/core/primitives/generator_base.py`, which generates
their data; for coffeeshop on the seed files under `benchbox/data/coffeeshop/`
and on `benchbox/utils/coffeeshop_seed_loader.py`, which reads them;
for tsbs_devops on `benchbox/core/tsbs_devops/schema.py` (table order and
schema); and for joinorder_synthetic on `benchbox/core/joinorder/schema.py`.
It also fires on `tests/unit/generators/sf1_size_baseline.json`:
a changed size entry selects its benchmark, so an entry cannot be raised in one
pull request and matched by a larger generator in the next. Any other change
skips the job and the aggregate accepts the skip.
`scripts/generator_volume_gate.py` holds the authoritative path table; a unit
test keeps the workflow filter exactly equal to it in both directions, and the
job fails if a generator path fires it without selecting a benchmark
(`--require-affected`), so a wider filter cannot pass as a skipped
measurement.

## What it checks

For each touched benchmark, one `benchbox run --platform duckdb --scale 1
--phases generate,load,power --compression none` run:

- the load and power phases must succeed on DuckDB (every power query passes,
  rows loaded), so a baseline update cannot certify a generator that no longer
  loads or answers its queries at SF 1;
- the generated source bytes, counted by the same rules as
  `scripts/measure_sf1_sizes.py`, must be within the `tolerance` recorded in
  `tests/unit/generators/sf1_size_baseline.json` (5%) of that benchmark's
  baseline entry. The data of the smoke run is the measured output; nothing is
  generated twice.

A drift fails unless the same diff updates the baseline entry to the new size.
An edited entry that still does not match the generator also fails. The
tolerance always comes from the base branch's copy of the baseline, so a diff
cannot loosen the threshold it is judged by; a new baseline file supplies its
own. A diff that raises the baseline's tolerance fails outright, because the
looser threshold would judge later pull requests; tightening it is accepted.

The gate, the baseline, `scripts/measure_sf1_sizes.py` (which decides which
files count) and `benchbox/utils/datagen_manifest.py` are soundness paths, so
a change to how bytes are counted is reviewed as a gate change.

## Updating the baseline

Run `uv run -- python scripts/measure_sf1_sizes.py --benchmark <name>`, copy
`total_bytes` into the baseline, and update the calibrated-size page that
`scripts/doc_contracts.py` feeds in the same change.

## Running it locally

Put `uv run -- python -m scripts.generator_volume_gate --base-ref origin/develop` in a script and
run the script through `make heavy-run` (heavy: SF 1 data generation). `--base-ref` names the base
the diff and the baseline are read from.

## Measured at SF 1

Eight runs on 2026-11-22, one at a time through `make heavy-run`, each the
gate's own command (`benchbox run --platform duckdb --scale 1 --phases
generate,load,power --compression none --non-interactive --quiet`), timed with
`/usr/bin/time -l` (wall seconds and maximum resident set size). Bytes are
`sum_directory_bytes(<output>/datagen)`, the gate's own count. All eight runs
exited 0 with no failed power query and rows loaded.

| Benchmark | Measured bytes | Previous baseline bytes | Drift | Seconds | Peak GiB |
|---|---|---|---|---|---|
| amplab | 679784321 | 679784321 | 0.000% | 22.66 | 2.19 |
| h2odb | 1413528658 | 1413528658 | 0.000% | 128.09 | 1.28 |
| coffeeshop | 900141206 | 900141206 | 0.000% | 39.69 | 1.45 |
| tsbs_devops | 919665111 | 919491614 | +0.019% | 61.53 | 1.21 |
| joinorder_synthetic | 453176012 | 444365227 | +1.983% | 28.00 | 2.05 |
| vector_search | 1342083621 | 1342083621 | 0.000% | 44.23 | 0.97 |
| write_primitives | 1572580199 | 1572187172 | +0.025% | 120.17 | 1.75 |
| transaction_primitives | 1572580199 | 1572187172 | +0.025% | 55.35 | 1.67 |

The baseline entries of the four benchmarks that differ were set to the
measured values. Causes:

- joinorder_synthetic: the generator is seeded (`random.seed(42)`), and a direct
  generator run today gives the same 453176012 bytes as the gate. The previous
  entry, measured on 2026-09-26, predates the change that plants canonical JOB
  predicate values and shifts year and kind distributions in the generated rows.
- tsbs_devops: the generator is unseeded, so each run differs. A direct
  generator run today gave 919818161 bytes and the gate run 919665111.
- write_primitives and transaction_primitives: the direct generator writes each
  TPC-H table as one file (25 counted files, 1572187172 bytes); the gate's run
  writes ten chunk files per table (79 counted files: 65 table, staging and
  reference files totalling 1330705764 bytes and 14 auxiliary CSV files totalling
  241874435 bytes). The table files carry the same bytes in both layouts; the
  staging and auxiliary files are unseeded and differ by 0.3 MB in total.

## Time limits

Each benchmark's time limit is four times its measured time, rounded up to a
whole minute, with a minimum of 3 minutes; the job limit is four times the
measured total, rounded to five minutes. A smoke run that exceeds its limit is
killed and reported as a failed benchmark (`timeout: benchbox run exceeded the
<N> s limit`) in the result table and the exit code.

| Benchmark | Measured seconds | 4x (seconds) | Limit (minutes) |
|---|---|---|---|
| amplab | 22.66 | 90.64 | 3 |
| h2odb | 128.09 | 512.36 | 9 |
| coffeeshop | 39.69 | 158.76 | 3 |
| tsbs_devops | 61.53 | 246.12 | 5 |
| joinorder_synthetic | 28.00 | 112.00 | 3 |
| vector_search | 44.23 | 176.92 | 3 |
| write_primitives | 120.17 | 480.68 | 9 |
| transaction_primitives | 55.35 | 221.40 | 4 |

The eight runs total 499.72 s; four times that is 1998.88 s (33.3 minutes), so
the `generator-volume` job limit is 35 minutes. The limits come from runs on one
local machine; a hosted runner can be slower.

## Coverage limits

- Native-binary generators (tpch, tpcds, ssb and the benchmarks built on them)
  and download-based ones (nyctaxi, flightdata, clickbench, joinorder) are not
  covered.
- Shared modules a generator imports (`benchbox/utils/` helpers such as
  `cloud_storage.py`, `compression_mixin.py`, `arrow_rows.py`,
  `datagen_manifest.py`, and
  `benchbox/core/manifest_utils.py`) are not gated: they serve many
  benchmarks, and a change there does not run this job.
- DuckDB completing at SF 1 says nothing about Spark, DataFusion or other
  engines; platform timeouts at the new volume need the nightly or UAT runs.
- A change that alters content but not size passes the size check.
- TPC-DI is unavailable: its historical load (Batch 1) runs on DuckDB from DIGen output, and the incremental batches (2 and 3) are not done yet.
- The smoke run of tsbs_devops is not seeded (`seed` defaults to `None`), so its
  size differs by about 0.02% between runs; the 5% tolerance absorbs it.
- write_primitives and transaction_primitives stage and auxiliary files with
  unseeded random content; their sizes also vary by about 0.03% between runs.
