# DataFrame Examples

This directory contains examples demonstrating BenchBox's DataFrame benchmarking capabilities.

## Overview

BenchBox supports native DataFrame benchmarking alongside traditional SQL database benchmarking. These examples show how to:

1. Run TPC-H/TPC-DS queries using Polars DataFrame API
2. Run queries using Pandas DataFrame API
3. Compare performance across platforms
4. Compare SQL vs DataFrame execution paradigms

## Prerequisites

Install the DataFrame platforms, then generate the benchmark data. The data is
required before any example can execute.

```bash
pip install polars pandas

benchbox run --platform duckdb --benchmark tpch --scale 0.01 --phases load
benchbox run --platform duckdb --benchmark tpcds --scale 1 --phases load
```

## Examples

### Basic TPC-H Examples

**Polars TPC-H** (`polars_tpch.py`)
```bash
python examples/dataframe/polars_tpch.py
```
Demonstrates running TPC-H queries using Polars' expression API with lazy evaluation.

**Pandas TPC-H** (`pandas_tpch.py`)
```bash
python examples/dataframe/pandas_tpch.py
```
Demonstrates running TPC-H queries using Pandas' DataFrame API with eager evaluation.

### TPC-DS Example

**Polars TPC-DS** (`polars_tpcds.py`)
```bash
python examples/dataframe/polars_tpcds.py
```
Demonstrates running TPC-DS queries (more complex than TPC-H with 99 queries).

### Comparison Examples

**Cross-Platform Comparison** (`cross_platform_comparison.py`)
```bash
python examples/dataframe/cross_platform_comparison.py
```
Compares query execution time across Polars and Pandas for the same queries.

**SQL vs DataFrame** (`sql_vs_dataframe.py`)
```bash
python examples/dataframe/sql_vs_dataframe.py
```
Compares SQL execution (DuckDB) vs DataFrame execution (Polars) for equivalent queries.

## Family-Based Architecture

BenchBox uses a family-based architecture for DataFrame support:

### Expression Family (Polars, PySpark, DataFusion)
```python
result = (
    df.filter(col('shipdate') <= lit(cutoff))
    .group_by('status')
    .agg(col('quantity').sum().alias('total_qty'))
)
```

### Pandas Family (Pandas, cuDF, Dask)
```python
filtered = df[df['shipdate'] <= cutoff]
result = filtered.groupby('status').agg({'quantity': 'sum'})
```

## Running with CLI

For production benchmarking, use the BenchBox CLI:

```bash
benchbox run --platform polars-df --benchmark tpch --scale 0.1

benchbox run --platform pandas-df --benchmark tpch --scale 0.01

benchbox compare results_polars.json results_pandas.json
```

## Related Documentation

- [DataFrame Platforms Overview](https://benchbox.dev/docs/platforms/dataframe.html)
- [Pandas DataFrame Platform](https://benchbox.dev/docs/platforms/pandas-dataframe.html)
- [TPC-H Guide](https://benchbox.dev/docs/guides/tpc/tpc-h-official-guide.html)
- [TPC-DS Guide](https://benchbox.dev/docs/guides/tpc/tpc-ds-official-guide.html)
