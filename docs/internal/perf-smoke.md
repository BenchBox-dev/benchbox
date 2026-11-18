# Performance Smoke

BenchBox runs a small perf-smoke job nightly
to catch orchestration-overhead regressions early. It's deliberately narrow: one platform
(DuckDB), one benchmark (TPC-H), one scale (SF=0.01), one phase (power).
The goal is to surface regressions in the result builder, dialect
translation, validation passes, and lazy-loading machinery - not to
benchmark DuckDB itself.

## How it works

1. CI runs `benchbox run --platform duckdb --benchmark tpch --scale 0.01 --phases power`.
2. CI calls `benchbox compare` against the checked-in baseline at
   `_project/baselines/perf_smoke_duckdb_tpch_001.json` with
   `--fail-on-regression 10% --min-regression-delta 5ms --min-aggregate-regression-delta 31ms`.
3. Result JSON (baseline + current) is uploaded as a 14-day artifact.

The gate runs in the nightly T3 perf domain of `.github/workflows/nightly-v2.yml`.
The pull-request job on the retired `release` branch no longer exists.

Detection limits of this gate, with measured examples, are recorded in
[nightly-perf-gate-review.md](nightly-perf-gate-review.md).

## Noise floor

At SF=0.01 every TPC-H query runs in 3 to 13 ms on a hosted runner, so a
percentage threshold alone flags scheduling jitter. A query counts as
regressed only when it is more than 10% slower **and** more than 5 ms
slower than the baseline. Totals and other aggregates count as regressed
only when they are more than 10% slower **and** more than 31 ms slower.

The per-query floor comes from the five `t3-perf-result` artifacts of nightly runs
43254687916, 43453477982, 43604460150, 43726725879 and 43916000915 (2026-11-12 to
2026-11-16), all recorded with `run.timing_method` 2. `benchbox compare`
judges each query by its last recorded execution, so the spread is measured
on that value: for each query, the difference between its highest and
lowest value across those runs. The largest spread was 2.7 ms (Q18). That is
below the 4 ms level at which a flat 5 ms floor stops being safe, so the
floor is the flat 5 ms. Recompute it from fresh artifacts whenever the baseline is refreshed.

The aggregate floor uses the same 1.25 rule on the totals of those five runs:
261.1 to 285.7 ms, a spread of 24.6 ms, so the floor is 1.25 times 24.6 rounded
up to a whole millisecond: 31 ms. The baseline total is 308.6 ms (per-query
maximum, below), so every source night passes and a uniform 2x slowdown still fails.

## Refreshing the baseline

The baseline is rebuilt from several CI runs, never from one run. A single
run carries its own noise, so copying it over the baseline only moves the
noise problem. The checked-in file is the per-query median of retained
`t3-perf-result` artifacts from scheduled nightly runs on `develop`, or the per-query
maximum when no CPU model has enough results (see below).

Every source must record the same `run.timing_method`; the builder refuses
results that mix methods or record none, and writes that method into the
baseline's `run` block. `benchbox compare` refuses to compare results of
different timing methods, so refresh the baseline whenever the timing method
of the nightly result changes.

Hosted runners come from several CPU models, and a night's total time can
differ by up to 25% between them (in the first refresh: EPYC 9V74 about
440 ms, Xeon 520 to 535 ms, EPYC 7763 540 to 590 ms). A baseline from a
mixed pool fails the nights that land on the slower hardware, so the
builder uses only the results from the slowest CPU model that has at least
five of them. A slower or equal night then compares cleanly, and a faster
night cannot fail. When no CPU model has five results, the builder falls
back to the per-query maximum over at least five results of any CPU model
(each query entry is the highest value seen), so a faster night cannot fail
there either; the current baseline was built this way from four CPU models
(EPYC 7763, Xeon Platinum 8573C, Xeon 6975P-C, EPYC 9V74). The cost is sensitivity: a regression smaller than the
speed difference can pass on the fastest hardware. The floor is the one
value still derived from all retained runs, because a night on any CPU
model is compared with the baseline.

Which source runs were used, their CPU models and totals, and the floor
derivation are recorded next to the baseline in
`_project/baselines/perf_smoke_duckdb_tpch_001.sources.json`.

Refresh after:

- An intentional perf-improving change has landed and you want to lock
  in the new floor.
- A runner change or a DuckDB upgrade that shifts every query broadly.

Procedure:

```
# 1. Pick at least five unexpired scheduled nightly runs (artifacts are
#    kept for 14 days) and download each artifact into its own empty
#    directory.
gh run list --workflow nightly-v2.yml --event schedule --limit 15
gh run download <run-id> -n t3-perf-result -D <empty-dir>/<run-id>

# 2. Build the baseline and its sources record. Pass each result as
#    <run-id>=<path to the tpch_sf001_duckdb_sql_*.json file>.
uv run -- python scripts/perf_smoke_baseline.py \
  <run-id>=<path> <run-id>=<path> ... \
  --output _project/baselines/perf_smoke_duckdb_tpch_001.json \
  --sources-output _project/baselines/perf_smoke_duckdb_tpch_001.sources.json

# 3. Replay every source result with the gate options. Each one must pass
#    unless it carries a real slowdown, which the pull request names.
uv run -- benchbox compare _project/baselines/perf_smoke_duckdb_tpch_001.json \
  <path> --fail-on-regression 10% --min-regression-delta <floor>ms \
  --min-aggregate-regression-delta <aggregate-floor>ms

# 4. Stage both files. _project/ is ignored, so a first-time sources
#    record needs `git add -f`; edits to the tracked files do not.
#    Open the pull request: it replaces the file the gate compares
#    against, so a failing Performance Smoke check on it is read against
#    the old baseline.
```

The script chooses the CPU model itself and falls back to the per-query maximum
when none has five results (it still refuses fewer than five results in total);
pass `--cpu-model` to require one model. The rule used is recorded as
`selection_rule` in the sources record. It prints the largest
per-query spread across all the sources and the floor that follows from it. If either floor changes, update
`--min-regression-delta` and `--min-aggregate-regression-delta` in `.github/workflows/nightly-v2.yml` and the Noise floor section above.

When the aggregate check fails and no single query explains it, check the
night's CPU model against the baseline's before treating it as a regression:
the floor only covers the spread seen on the baseline's own runner class.

## Troubleshooting

- **Flake on GH runners**: re-run the nightly perf domain once. If it persists,
  open an issue so the baseline or threshold can be reviewed.
- **Compare command errors**: validate the baseline with
  `uv run -- python -c 'import json; json.load(open("_project/baselines/perf_smoke_duckdb_tpch_001.json"))'`.
- **Baseline drift over time**: if the noise floor creeps but no single
  PR regressed, refresh via the procedure above rather than raising the
  threshold.
