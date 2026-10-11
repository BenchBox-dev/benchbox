<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# DataFrame Cross-Platform Comparison

```{tags} intermediate, guide, dataframe-platform
```

## Comparing DataFrame Platforms

Use the unified `benchbox compare` command to compare results across DataFrame platforms. Run the benchmarks for each
platform first, then compare the results:

```bash
polars_result=$(benchbox run --platform polars-df --benchmark tpch --scale 0.01 --output ./polars-run --quiet)
pandas_result=$(benchbox run --platform pandas-df --benchmark tpch --scale 0.01 --output ./pandas-run --quiet)

benchbox compare "$polars_result" "$pandas_result"
```

### SQL vs DataFrame Comparison

Run the SQL benchmark and the DataFrame benchmark, then compare them:

```bash
duckdb_result=$(benchbox run --platform duckdb --benchmark tpch --scale 0.01 --output ./duckdb-run --quiet)
polars_result=$(benchbox run --platform polars-df --benchmark tpch --scale 0.01 --output ./polars-run --quiet)

benchbox compare "$duckdb_result" "$polars_result"
```

### Visualization

Run this in the same shell session as the first comparison block, which captures the exact result paths:

```bash
benchbox visualize "$polars_result" "$pandas_result" --chart-type performance_bar
```

## Platform Categories

| Category | Platforms | Use Case |
|----------|-----------|----------|
| **Single Node** | Polars, Pandas, DataFusion | In-memory analysis, medium datasets |
| **Distributed** | PySpark, Dask, LakeSail | Large datasets, cluster computing |
| **GPU Accelerated** | cuDF | CUDA-enabled GPU acceleration |

## Best Practices

1. **Start small**: Use `--scale 0.01` to verify before scaling up
2. **Same machine**: Run all platforms on the same hardware for fair comparison
3. **Multiple iterations**: Use power run iterations for statistical confidence
4. **Match your workload**: Test at scale factors representative of production data

## Related Documentation

- [DataFrame Quickstart](../tutorials/dataframe-quickstart.md) - Getting started with DataFrame benchmarking
- [DataFrame Benchmarking Guide](dataframe-migration.md) - Adopting DataFrame benchmarking
- [Visualization Guide](../visualization/chart-generation-guide.md) - Chart customization
