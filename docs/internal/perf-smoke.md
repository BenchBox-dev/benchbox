# Release Performance Smoke

BenchBox runs a small perf-smoke job on pull requests to the release branch
to catch orchestration-overhead regressions early. It's deliberately narrow: one platform
(DuckDB), one benchmark (TPC-H), one scale (SF=0.01), one phase (power).
The goal is to surface regressions in the result builder, dialect
translation, validation passes, and lazy-loading machinery - not to
benchmark DuckDB itself.

## How it works

1. CI runs `benchbox run --platform duckdb --benchmark tpch --scale 0.01 --phases power`.
2. CI calls `benchbox compare` against the checked-in baseline at
   `_project/baselines/perf_smoke_duckdb_tpch_001.json` with
   `--fail-on-regression 10% --min-regression-delta 7ms --min-aggregate-regression-delta 82ms`.
3. Result JSON (baseline + current) is uploaded as a 14-day artifact.

Workflow lives at `.github/workflows/perf-smoke.yml`. The nightly T3 perf
domain in `.github/workflows/nightly-v2.yml` runs the same comparison against
the same baseline.

Detection limits of this gate, with measured examples, are recorded in
[nightly-perf-gate-review.md](nightly-perf-gate-review.md).

## Noise floor

At SF=0.01 every TPC-H query runs in 3 to 13 ms on a hosted runner, so a
percentage threshold alone flags scheduling jitter. A query counts as
regressed only when it is more than 10% slower **and** more than 7 ms
slower than the baseline. Totals and other aggregates count as regressed
only when they are more than 10% slower **and** more than 82 ms slower.

The per-query floor comes from the nine `t3-perf-result` artifacts retained from
scheduled nightly runs between 2026-09-30 and 2026-10-08. `benchbox compare`
judges each query by its last recorded execution, so the spread is measured
on that value: for each query, the difference between its highest and
lowest value across those runs. The largest spread was 5.4 ms (Q18). That is
above the 4 ms level at which a flat 5 ms floor stops being safe, so the
floor is 1.25 times the largest spread rounded up to a whole millisecond:
7 ms. Recompute it from fresh artifacts whenever the baseline is refreshed.

The aggregate floor uses the same 1.25 rule on the same-runner-class totals.
Five come from the sources ledger next to the baseline (highest 586.1 ms); the sixth,
604.7 ms, is the `t3-perf-result` of dispatched nightly-v2 verification run 37991400931,
also on AMD EPYC 7763. Together they span 539.8 to 604.7 ms, a spread of 64.9 ms, so the
floor is 1.25 times 64.9 rounded up to a whole millisecond: 82 ms. With the 547 ms
baseline it lets the noisiest observed night (+57.7 ms) pass while a uniform 2x slowdown
still fails.

## Skipping the check

Attach the `skip-perf-smoke` label to the PR. Use it only for:

- Intentional performance shifts where the baseline needs refresh.
- GH-runner flake that you've confirmed by re-running the workflow.

Don't use it to merge code you know regresses perf. The baseline refresh
below is the right move.

## Refreshing the baseline

The baseline is rebuilt from several CI runs, never from one run. A single
run carries its own noise, so copying it over the baseline only moves the
noise problem. The checked-in file is the per-query median of retained
`t3-perf-result` artifacts from scheduled nightly runs on `develop`.

Hosted runners come from several CPU models, and a night's total time can
differ by up to 25% between them (in the first refresh: EPYC 9V74 about
440 ms, Xeon 520 to 535 ms, EPYC 7763 540 to 590 ms). A baseline from a
mixed pool fails the nights that land on the slower hardware, so the
builder uses only the results from the slowest CPU model that has at least
five of them. A slower or equal night then compares cleanly, and a faster
night cannot fail. The cost is sensitivity: a regression smaller than the
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

The script chooses the CPU model itself and refuses to build when none has
five results; pass `--cpu-model` to override it. It prints the largest
per-query spread across all the sources and the floor that follows from it. If either floor changes, update
`--min-regression-delta` and `--min-aggregate-regression-delta` in both workflows and the Noise floor section above.

When the aggregate check fails and no single query explains it, check the
night's CPU model against the baseline's before treating it as a regression:
the floor only covers the spread seen on the baseline's own runner class.

## Troubleshooting

- **Flake on GH runners**: re-run the workflow once. If it persists,
  attach `skip-perf-smoke` and open an issue so the baseline or threshold
  can be reviewed; don't chase the flake under PR pressure.
- **Compare command errors**: validate the baseline with
  `uv run -- python -c 'import json; json.load(open("_project/baselines/perf_smoke_duckdb_tpch_001.json"))'`.
- **Baseline drift over time**: if the noise floor creeps but no single
  PR regressed, refresh via the procedure above rather than raising the
  threshold.
