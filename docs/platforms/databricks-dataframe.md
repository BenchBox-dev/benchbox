<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Databricks DataFrame Platform

```{tags} intermediate, guide, databricks-df, dataframe-platform, cloud-platform
```

> **CLI name:** `databricks-df` - use `benchbox run --platform databricks-df`

Databricks DataFrame executes BenchBox benchmarks using the PySpark DataFrame
API on Databricks compute, via Spark Connect or Databricks Connect. It reuses
the full SQL-mode Databricks infrastructure - Unity Catalog, UC Volume staging,
Delta Lake tables - but runs queries as DataFrame expressions instead of SQL.

## Features

- **PySpark DataFrame API** - filter, groupBy, agg, join expressions on Databricks compute
- **Spark Connect / Databricks Connect** - remote DataFrame execution
- **Shared SQL infrastructure** - same auth, UC Volume staging, Delta tables as `databricks` SQL mode
- **Expression family** - comparable to `pyspark-df`, `datafusion-df`, `polars-df`
- **Unity Catalog** - catalog / schema-aware execution

## When to Use

| Goal                                              | Recommended Platform     |
| ------------------------------------------------- | ------------------------ |
| Evaluate Databricks SQL Warehouse performance     | `databricks` (SQL)       |
| Evaluate PySpark on Databricks Connect compute    | `databricks-df`          |
| Compare SQL vs. DataFrame on the same workload    | Run both, same benchmark |

## Installation

This installs `databricks-sql-connector`, `databricks-sdk` and `databricks-connect`.

```bash
uv add benchbox --extra cloud-spark-databricks
```

The base `databricks` extra covers SQL-mode only; it does **not** pull in
`databricks-connect`. Use `cloud-spark-databricks` for DataFrame-mode runs
(the `benchbox[databricks-connect]` extra was removed; it is no longer a
valid install target). See
[Databricks Connect docs](https://docs.databricks.com/aws/en/dev-tools/databricks-connect).

## Authentication

Same credentials as the `databricks` SQL platform. Set them with
`benchbox setup --platform databricks`, or in the `databricks` section of
`~/.benchbox/credentials.yaml`, where values may reference environment
variables as `${VAR}`:

| Key                | Purpose                                           |
| ------------------ | ------------------------------------------------- |
| `server_hostname`  | Workspace URL (`xxx.cloud.databricks.com`)        |
| `access_token`     | Personal access token                             |
| `http_path`        | SQL Warehouse HTTP path (used for the load phase) |

The DataFrame adapter selects its cluster through `compute_resource`
(`--platform-option compute_resource=<cluster-id>`, deprecated alias
`cluster_id`). `http_path` remains required for the load phase,
which uses the SQL connector regardless of execution mode.

## Usage

The first command runs TPC-H with DataFrame execution. The last two compare SQL and DataFrame on the same benchmark:
the first is SQL and the second is DataFrame.

```bash
benchbox run --platform databricks-df --benchmark tpch --scale 1.0

benchbox run --platform databricks    --benchmark tpch --scale 1.0
benchbox run --platform databricks-df --benchmark tpch --scale 1.0
```

## Supported Benchmarks

DataFrame-mode benchmarks require DataFrame query definitions. Covered today:

- TPC-H (22 queries)
- SSB (13 queries)
- Read Primitives (selected categories)
- Write Primitives (selected categories)

See the [DataFrame Platforms Overview](dataframe.md) for the full DataFrame
execution model and compatibility matrix.

## Notes

- Data loading uses the standard Databricks SQL connector (UC Volume staging +
  Delta Lake) regardless of execution mode. Only query execution differs.
- Credentials and Unity Catalog configuration are shared between `databricks`
  and `databricks-df` - a single authenticated environment serves both.
- Expect different performance profiles: SQL Warehouse (photon) vs. Databricks
  Connect (JVM Spark) execute the same logical query very differently.

## See Also

- [Databricks SQL](databricks.md) - sibling SQL-mode platform
- [DataFrame Platforms Overview](dataframe.md) - execution model
- [PySpark DataFrame](pyspark-dataframe.md) - self-hosted PySpark comparison
- [Snowpark Connect](snowpark-connect.md) - PySpark-compatible API on Snowflake
