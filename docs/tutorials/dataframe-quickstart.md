# DataFrame Benchmarking Quickstart

```{tags} beginner, tutorial, dataframe-platform, polars
```

Run TPC-H using DataFrame APIs (Polars, Pandas) instead of SQL.

## Why DataFrame Benchmarking?

- **Compare paradigms**: SQL vs DataFrame execution on same queries
- **Data science workflows**: Benchmark tools you actually use
- **No database required**: Pure Python execution

## Quick Start with Polars

```bash
benchbox run --platform polars-df --benchmark tpch --scale 0.1
```

This runs all 22 TPC-H queries using Polars' DataFrame API.

## Available DataFrame Platforms

| Platform | CLI Name | Best For |
|----------|----------|----------|
| Polars | `polars-df` | Speed, production workloads |
| Pandas | `pandas-df` | Compatibility, reference |
| PySpark | `pyspark-df` | Distributed computing |
| DataFusion | `datafusion-df` | Arrow-native processing |

## SQL vs DataFrame Comparison

```bash
benchbox run --platform duckdb --benchmark tpch --scale 0.1 --output ./benchmark_runs

benchbox run --platform polars-df --benchmark tpch --scale 0.1 --output ./benchmark_runs
```

The first command runs SQL execution on DuckDB. The second runs DataFrame execution on Polars. Find the two result files with `benchbox results --limit 2` and pass them to `benchbox compare`.

## How It Works

BenchBox translates TPC-H queries into DataFrame operations:

**SQL (TPC-H Q1):**
```sql
SELECT l_returnflag, l_linestatus,
       SUM(l_quantity), SUM(l_extendedprice)
FROM lineitem
WHERE l_shipdate <= DATE '1998-12-01' - INTERVAL 90 DAY
GROUP BY l_returnflag, l_linestatus
ORDER BY l_returnflag, l_linestatus
```

**DataFrame (Polars):**
```python
lineitem.filter(
    pl.col("l_shipdate") <= date(1998, 9, 2)
).group_by(
    "l_returnflag", "l_linestatus"
).agg(
    pl.col("l_quantity").sum(),
    pl.col("l_extendedprice").sum()
).sort("l_returnflag", "l_linestatus")
```

Both produce identical results.

## Performance Tuning

Enable auto-tuning for optimal performance:

```bash
benchbox run --platform polars-df --benchmark tpch --tuning auto
```

Or use a custom configuration:

```bash
benchbox tuning defaults --platform polars

benchbox run --platform polars-df --benchmark tpch --tuning tuning.yaml
```

The first command shows the available settings. The second runs with a custom tuning file.

## Pandas Example

```bash
benchbox run --platform pandas-df --benchmark tpch --scale 0.01
```

Pandas is slower than Polars for large datasets. Use scale factors of 0.01 to 0.1 for Pandas benchmarks.

## Cross-Platform DataFrame Comparison

```bash
benchbox run --platform polars-df --benchmark tpch --output ./benchmark_runs
benchbox run --platform pandas-df --benchmark tpch --output ./benchmark_runs
benchbox run --platform datafusion-df --benchmark tpch --output ./benchmark_runs
```

Find the three result files with `benchbox results --limit 3` and pass them to `benchbox compare`. The three runs and the final comparison cover the DataFrame platforms.

## TPC-DS Support

DataFrame mode also supports TPC-DS (99 queries):

```bash
benchbox run --platform polars-df --benchmark tpcds --scale 1
```

Note: TPC-DS also supports sub-SF1 development runs with the patched generator,
but those runs remain unofficial.

## Programmatic Usage

DataFrame execution is driven through the CLI (`benchbox run --platform polars-df ...`). For embedding benchmarks in Python, use the same benchmark API as SQL mode (`TPCH(...).generate_data()`, `get_query(n)`) and execute through a DataFrame adapter. See the [DataFrame Platform Guide](../platforms/dataframe.md) for the adapter interface.

## Next Steps

- [DataFrame Platform Guide](../platforms/dataframe.md) - Detailed configuration
- [DataFrame Migration Guide](../guides/dataframe-migration.md) - Adopting DataFrame mode
- [Tuning Reference](../platforms/dataframe.md#tuning-system) - Performance optimization
