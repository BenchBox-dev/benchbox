# Generator volume gate

A change to a pure-Python generator or its `generator_specs.yaml` can multiply
the data a benchmark emits without any test noticing. The `generator-volume`
job in `ci.yml` catches that before merge. `core` requires it through its
aggregate step, so no ruleset change is involved.

## When it runs

`ci-paths` sets `generator-volume-needed` when the diff touches
`benchbox/core/<benchmark>/generator.py` or `generator_specs.yaml` of one of:
tpcdi, amplab, h2odb, coffeeshop, tsbs_devops, joinorder_synthetic,
vector_search, write_primitives, transaction_primitives. For tpcdi it also
fires on the `generator/` package, `source_generators.py`, `scaling.py` (the
row-count formula) and `financial_data.py`; for vector_search on
`benchmark.py`, which sets the default vector width. Any other change skips
the job and the aggregate accepts the skip. `scripts/generator_volume_gate.py`
holds the authoritative path table; a unit test keeps the workflow filter
exactly equal to it in both directions, and the job fails if it runs without
selecting a benchmark (`--require-affected`), so a wider filter cannot pass as
a skipped measurement.

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
own.

## Updating the baseline

Run `uv run -- python scripts/measure_sf1_sizes.py --benchmark <name>`, copy
`total_bytes` into the baseline, and update the calibrated-size page that
`scripts/doc_contracts.py` feeds in the same change.

## Running it locally

Put `uv run -- python -m scripts.generator_volume_gate --base-ref origin/develop` in a script and
run the script through `make heavy-run` (heavy: SF 1 data generation). `--base-ref` names the base
the diff and the baseline are read from.

## Coverage limits

- Native-binary generators (tpch, tpcds, ssb and the benchmarks built on them)
  and download-based ones (nyctaxi, flightdata, clickbench, joinorder) are not
  covered.
- DuckDB completing at SF 1 says nothing about Spark, DataFusion or other
  engines; platform timeouts at the new volume need the nightly or UAT runs.
- A change that alters content but not size passes the size check.
