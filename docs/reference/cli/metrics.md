(cli-metrics)=
# `metrics` - Performance Metrics

```{tags} reference, cli, metrics
```

Calculate TPC Power@Size, Throughput@Size, and TPC-H QphH@Size from benchmark results.

## Basic Syntax

```bash
benchbox metrics <subcommand> [OPTIONS]
```

## Subcommands

### `qphh` - TPC-H QphH@Size

Calculate Power@Size, Throughput@Size, and QphH@Size from TPC-H power and throughput results. QphH@Size requires official compliance, all 22 completed power queries, successful RF1/RF2 in Power, at least two completed throughput query streams, and one successful refresh pair per stream on consecutive update sets. Refresh functions run only when the TPC-H run sets `--benchmark-option tpch_refresh_functions=true`, on DuckDB, SQLite, PostgreSQL, Snowflake, SingleStore, and the adapters built on them. If a condition is missing, the command still reports Power@Size and Throughput@Size, omits QphH@Size, and lists the reasons; JSON output carries them in `qphh_unavailable`. QphDS@Size is not computed because BenchBox does not run the TPC-DS data maintenance phases it requires.

**Formula:**

```
Power@Size       = 3600 × SF / geometric_mean(RF1, final power iteration's 22 query times, RF2)
Throughput@Size  = Queries × 3600 × SF / Throughput_Phase_Wall_Time
QphH@Size        = sqrt(Power@Size × Throughput@Size), rounded to 0.1

Where Queries = 22 × streams for TPC-H and 99 × streams for TPC-DS.
```

The command exits non-zero when either file has suppressed TPC metrics, an unofficial sub-scale or non-standard compliance class, failed queries, or a failed throughput phase. A missing QphH condition does not cause a non-zero exit.

```bash
benchbox metrics qphh --power-results <path> --throughput-results <path> [OPTIONS]
```

**Required:**
- `--power-results PATH`: Path to power test results JSON file
- `--throughput-results PATH`: Path to throughput test results JSON file

**Optional:**
- `--scale-factor FLOAT`: Scale factor (auto-detected from results if not provided)
- `--format [text|json]`: Output format (default: `text`)
- `--output PATH`: Save output to file

## Usage Examples

```bash
benchbox metrics qphh \
  --power-results results/power/results.json \
  --throughput-results results/throughput/results.json

benchbox metrics qphh \
  --power-results power.json \
  --throughput-results throughput.json \
  --scale-factor 100

benchbox metrics qphh \
  --power-results power.json \
  --throughput-results throughput.json \
  --format json --output qphh.json
```

The first command calculates QphH@Size from eligible TPC-H results. The second specifies the scale factor explicitly, and the third exports to a JSON file.

## Notes

- Scale factor is auto-detected from the `environment.scale_factor` field in result files. If the power and throughput results have mismatched scale factors, an error is raised.
- The command uses `Power@Size` and `Throughput@Size` from the result files' `tpc_metrics` when present. Otherwise it derives Power@Size from the final power iteration and Throughput@Size from the throughput phase wall-clock duration, never from summed query time.

## Related

- [run](run.md) - Run benchmarks with power and throughput phases
