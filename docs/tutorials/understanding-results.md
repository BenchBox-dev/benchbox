# Understanding Benchmark Results

```{tags} beginner, tutorial, validation
```

Learn how to interpret BenchBox output and what the metrics mean.

## Result Overview

After running a benchmark, BenchBox produces:
- **Console summary** - Quick overview of timing and validation
- **JSON results** - Detailed metrics for each query
- **Manifest files** - Data generation metadata

## Viewing Results

```bash
benchbox results --limit 1

benchbox export --last --format json --output-dir ./results
```

## Key Metrics

### Run Timing

The `run` object carries end-to-end numbers:

| Field | Meaning |
|-------|---------|
| `run.total_duration_ms` | End-to-end benchmark duration in milliseconds |
| `run.query_time_ms` | Total query execution time in milliseconds |
| `summary.timing.total_ms` | Sum of measured query times in milliseconds |
| `summary.timing.geometric_mean_ms` | Geometric mean query time (drives Power@Size) |
| `summary.validation` | `passed`, `failed`, or `partial` |
| `summary.queries` | `total`, `passed`, and `failed` execution counts |

### Per-Query Metrics

Each entry in `queries` looks like this:

```json
{
  "id": "Q1",
  "ms": 14.6,
  "rows": 4,
  "status": "SUCCESS",
  "stream": 0,
  "iter": 2,
  "run_type": "measurement",
  "test_type": "power"
}
```

| Field | Meaning |
|-------|---------|
| `ms` | Query runtime in milliseconds |
| `rows` | Number of result rows |
| `status` | SUCCESS, ERROR, or TIMEOUT |
| `stream` / `iter` / `run_type` | Which stream, iteration, and phase produced the row |

## Understanding Validation

BenchBox validates query correctness by comparing row counts. The run-level
verdict is `summary.validation`:

| Value | Meaning | Action |
|-------|---------|--------|
| `passed` | Every check matched expected | No action needed |
| `failed` | A check differed from expected | Check query translation |
| `partial` | Some queries failed to run | Inspect the `ERROR` entries |

A failed query carries an `ERROR` status and an error message instead of timing data.

## TPC Metrics

For TPC-H/TPC-DS, BenchBox calculates Power@Size and Throughput@Size:

```
Power@Size      = (SF × 3600) / geometric_mean(query times, final iteration)
Throughput@Size = (Q × SF × 3600) / T_throughput
```

Where:
- `SF` = Scale Factor (0.01, 0.1, 1, 10, etc.)
- `Q` = queries scored in the throughput test: 22 × streams for TPC-H, 99 × streams for TPC-DS
- `T_throughput` = wall-clock duration of the throughput test, from the first stream's first query to the last stream's last query (connection setup is excluded)

**Higher is better.** Compare these metrics only at the same scale factor.

BenchBox exports QphH@Size only for an official TPC-H run with all 22 power queries, successful RF1 and RF2 in Power, at least two completed throughput query streams, and one successful refresh pair per throughput stream using consecutive update sets. Failed queries, incomplete phases, query subsets, missing refresh evidence, or a nonofficial compliance class withhold the composite; `qphh_unavailable` lists the reasons. BenchBox does not export QphDS@Size (TPC-DS), which also needs data maintenance and load times. A failed query or throughput phase also withholds Throughput@Size.

### Price/Performance

Not calculated by BenchBox (requires cost data), but you can derive it:

```
Price/Performance = (Platform Cost) / Throughput@Size
```

## Comparing Results

```bash
benchbox run --platform duckdb --benchmark tpch --output ./benchmark_runs
benchbox run --platform sqlite --benchmark tpch --output ./benchmark_runs
```

Find the two result files with `benchbox results --limit 2` and pass them to `benchbox compare`.

The comparison shows:
- Per-query timing differences
- Relative speedup/slowdown
- Validation status alignment

## Result File Locations

BenchBox stores results in the runs root: `BENCHBOX_OUTPUT_DIR` when set, otherwise `benchmark_runs/` next to your checkout:

```
benchmark_runs/
├── results/                                  # JSON result files
│   └── tpch_sf001_duckdb_sql_<timestamp>_<id>.json
├── databases/                                # Platform database files
│   └── tpch_sf001/
│       └── tpch_sf001_notuning_noconstraints.duckdb
└── datagen/                                  # Generated data (default location)
    └── tpch_sf001/
        ├── lineitem.tbl.zst
        ├── orders.tbl.zst
        └── _datagen_manifest.json
```

`--output DIR` redirects generated data to `DIR/<benchmark>_<scale>/` (for example `/tmp/bb-truth/tpch_sf001/` with `--output /tmp/bb-truth`); result files and databases stay in the runs root.

## Interpreting Slow Queries

If a query is unexpectedly slow:

1. **Check scale factor** - Larger data takes longer
2. **Review query plan** - Use platform's EXPLAIN
3. **Compare baselines** - Run on DuckDB for reference
4. **Check validation** - Ensure correct results

```bash
benchbox run --dry-run ./analysis --platform duckdb --benchmark tpch
```

This exports the query SQL as `query_<id>.sql` files under `./analysis/<benchmark>_<platform>_queries_<timestamp>/`, alongside the dry-run JSON/YAML, schema, and DDL preview.

## Next Steps

- [Comparing Platforms](comparing-platforms.md) - Run on multiple databases
- [Result Export Documentation](../reference/result-formats.md) - Full JSON schema
- [Performance Guide](../performance/dataframe-optimization.md) - Optimization tips
