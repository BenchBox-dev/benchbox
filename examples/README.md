# BenchBox Examples

Practical code examples for common BenchBox workflows, database integrations, and performance testing scenarios.

## Quick Navigation

| I want to... | Script | Time | Prerequisites |
|--------------|--------|------|---------------|
| Run my first benchmark (local) | `getting_started/local/duckdb_tpch_power.py` | 2 min | None |
| Run TPC-DS on DuckDB | `getting_started/local/duckdb_tpcds_power.py` | 3 min | None |
| Try real-world taxi data | `getting_started/local/duckdb_nyctaxi.py` | 2 min | None |
| Test time-series workloads | `getting_started/local/duckdb_tsbs_devops.py` | 2 min | None |
| Preview queries without execution | `getting_started/cloud/bigquery_tpch_dry_run.py` | 1 min | None |
| Run benchmark on Databricks | `getting_started/cloud/databricks_tpch_power.py` | 5 min | `DATABRICKS_TOKEN` |
| Run specific queries only | `getting_started/intermediate/duckdb_tpch_query_subset.py` | 3 min | None |
| Use Jupyter notebooks | `notebooks/databricks_benchmarking.ipynb` | 10 min | Databricks workspace |
| Use unified runner (advanced) | `unified_runner.py` | Varies | Platform-specific |

## By Use Case

### Getting Started (Zero Configuration)

Start here if you're new to BenchBox. These examples work immediately with no setup required.

- **`getting_started/local/duckdb_tpch_power.py`** - Your first TPC-H benchmark on DuckDB
- **`getting_started/local/duckdb_tpcds_power.py`** - TPC-DS queries on DuckDB
- **`getting_started/local/duckdb_nyctaxi.py`** - Real NYC taxi data analytics
- **`getting_started/local/duckdb_tsbs_devops.py`** - DevOps time-series metrics
- **`duckdb_coffeeshop.py`** - Minimal example with a tiny dataset

### Cloud Platform Examples

Cloud examples require credentials but demonstrate production patterns.

- **`getting_started/cloud/bigquery_tpch_dry_run.py`** - Preview BigQuery setup without execution
- **`getting_started/cloud/databricks_tpch_power.py`** - Run TPC-H on Databricks SQL Warehouse

### Intermediate Patterns

Once you're comfortable with basics, explore these workflow patterns.

- **`getting_started/intermediate/duckdb_tpch_query_subset.py`** - Run specific queries instead of full suite

### Advanced / Unified Runner

Power-user tool for production workflows.

- **`unified_runner.py`** - Multi-platform runner with full configuration support

### Jupyter Notebooks

Interactive exploration and visualization.

- **`notebooks/databricks_benchmarking.ipynb`** - Databricks + Spark integration
- **`notebooks/bigquery_benchmarking.ipynb`** - BigQuery + pandas workflows
- **`notebooks/snowflake_benchmarking.ipynb`** - Snowflake data analysis
- **`notebooks/redshift_benchmarking.ipynb`** - Redshift benchmarking
- **`notebooks/clickhouse_benchmarking.ipynb`** - ClickHouse analytics

## By Platform

| Platform | Scripts | Notebooks | Prerequisites |
|----------|---------|-----------|---------------|
| **DuckDB** | `getting_started/local/duckdb_*.py`, `duckdb_coffeeshop.py` | `notebooks/duckdb_benchmarking.ipynb` | None (built-in) |
| **SQLite** | Via `unified_runner.py` | `notebooks/sqlite_benchmarking.ipynb` | None (built-in) |
| **Databricks** | `getting_started/cloud/databricks_*.py` | `notebooks/databricks_benchmarking.ipynb` | `DATABRICKS_TOKEN`, `DATABRICKS_HOST` |
| **BigQuery** | `getting_started/cloud/bigquery_*.py` | `notebooks/bigquery_benchmarking.ipynb` | `GOOGLE_APPLICATION_CREDENTIALS` |
| **Snowflake** | - | `notebooks/snowflake_benchmarking.ipynb` | `SNOWFLAKE_USER`, `SNOWFLAKE_PASSWORD`, etc. |
| **Redshift** | - | `notebooks/redshift_benchmarking.ipynb` | AWS credentials |
| **ClickHouse** | - | `notebooks/clickhouse_benchmarking.ipynb` | ClickHouse server |
| **PostgreSQL** | Via `unified_runner.py` | - | PostgreSQL server, `psycopg2` |
| **Trino** | Via `unified_runner.py` | - | Trino cluster, `trino` |
| **Athena** | Via `unified_runner.py` | - | AWS credentials, `pyathena` |
| **Firebolt** | Via `unified_runner.py` | - | Firebolt account, `firebolt-sdk` |
| **Spark** | Via `unified_runner.py` | - | Spark cluster, `pyspark` |
| **Polars** | Via `unified_runner.py` | - | `polars` package |

## By Benchmark

| Benchmark | Examples | Best For |
|-----------|----------|----------|
| **TPC-H** | `getting_started/local/duckdb_tpch_power.py`, `getting_started/cloud/*tpch*.py` | Standard analytical queries |
| **TPC-DS** | `getting_started/local/duckdb_tpcds_power.py` | Complex analytical workloads |
| **NYC Taxi** | `getting_started/local/duckdb_nyctaxi.py` | Real-world data analytics |
| **TSBS DevOps** | `getting_started/local/duckdb_tsbs_devops.py` | Time-series, DevOps monitoring |
| **CoffeeShop** | `duckdb_coffeeshop.py` | Quick testing, minimal data |
| **SSB** | Via `unified_runner.py` | Star schema, OLAP |
| **ClickBench** | Via `unified_runner.py` | Web analytics |
| **TPC-H Data Vault** | Via `unified_runner.py` | Enterprise DWH patterns |
| **TPC-DS-OBT** | Via `unified_runner.py` | Wide-table analytics |
| **TPC-H Skew** | Via `unified_runner.py` | Non-uniform data testing |

## Running Examples

### Local Examples (No Setup)

```bash
cd examples

python getting_started/local/duckdb_tpch_power.py

python getting_started/local/duckdb_tpch_power.py --scale 0.1
```

### Cloud Examples (Requires Credentials)

Set the environment variables first, then run the example:

```bash
export DATABRICKS_TOKEN="your-token"
export DATABRICKS_HOST="https://your-workspace.cloud.databricks.com"

python getting_started/cloud/databricks_tpch_power.py
```

### Dry Run Mode (Preview Without Execution)

All scripts support `--dry-run` to export queries and configuration without execution:

```bash
python getting_started/local/duckdb_tpch_power.py --dry-run ./preview
```

This creates:
- `./preview/queries/` - Generated SQL queries
- `./preview/summary.json` - Configuration and metadata

### Jupyter Notebooks

```bash
uv pip install jupyter

jupyter notebook notebooks/
```

Then open the desired notebook in your browser.

## Example Directory Structure

```
examples/
├── README.md                    # This file
├── INDEX.md                     # Quick navigation by goal/platform
├── MAPPING.md                   # Maps documentation to examples
├── PATTERNS.md                  # Common workflow patterns
├── getting_started/             # Beginner-friendly examples
│   ├── local/                   # Zero-config local examples
│   │   ├── duckdb_tpch_power.py
│   │   ├── duckdb_tpcds_power.py
│   │   ├── duckdb_nyctaxi.py
│   │   └── duckdb_tsbs_devops.py
│   ├── cloud/                   # Cloud platform examples
│   │   ├── bigquery_tpch_dry_run.py
│   │   └── databricks_tpch_power.py
│   └── intermediate/            # Intermediate patterns
│       └── duckdb_tpch_query_subset.py
├── features/                    # Feature-focused examples
├── use_cases/                   # Real-world problem patterns
├── notebooks/                   # Jupyter notebooks (10+ notebooks)
├── tunings/                     # Performance tuning configurations
│   ├── duckdb/                  # DuckDB tuning configs
│   └── databricks/              # Databricks tuning configs
├── config/                      # Platform connection configs
├── duckdb_coffeeshop.py        # Minimal example
└── unified_runner.py           # Advanced multi-platform runner
```

## Configuration Examples

Examples use sensible defaults but accept common CLI arguments:

- `--scale FLOAT` - Scale factor (default: 0.01 for quick testing)
- `--output PATH` - Output directory for results
- `--dry-run PATH` - Preview mode (no execution)
- `--verbose` / `-v` - Enable detailed logging

Example:
```bash
python getting_started/local/duckdb_tpch_power.py --scale 0.1 --verbose
```

## Tuning Configurations

The `tunings/` directory contains example tuning configurations for optimizing benchmark performance:

- Platform-specific tunings (partitioning, clustering, indexing)
- Benchmark-specific optimizations
- See `tunings/README.md` for details

## Integration with Documentation

For detailed explanations of concepts used in these examples, see:

- [Getting Started Guide](https://benchbox.dev/docs/usage/getting-started.html) - Installation and first steps
- [Usage Examples Documentation](https://benchbox.dev/docs/usage/examples.html) - Detailed patterns and workflows
- [CLI Quick Start](https://benchbox.dev/docs/usage/cli-quick-start.html) - Command-line interface reference
- [Platform Selection Guide](https://benchbox.dev/docs/platforms/platform-selection-guide.html) - Choosing the right platform
- [Configuration Handbook](https://benchbox.dev/docs/usage/configuration.html) - Advanced configuration options

## Troubleshooting

### Common Issues

**`ModuleNotFoundError: No module named 'benchbox'`**
Install BenchBox, or install it with platform-specific extras:
```bash
uv add benchbox

uv add benchbox --extra databricks
```

**`DATABRICKS_TOKEN not found`**
```bash
export DATABRICKS_TOKEN="your-token"
export DATABRICKS_HOST="https://your-workspace.cloud.databricks.com"
```

**`Permission denied` or authentication errors**
- Check credentials are valid and not expired
- Verify environment variables are set correctly
- See [Platform Selection Guide](https://benchbox.dev/docs/platforms/platform-selection-guide.html) for platform-specific auth help

### Getting Help

- [Troubleshooting Guide](https://benchbox.dev/docs/usage/troubleshooting.html) - Common errors and solutions
- [GitHub Issues](https://github.com/joeharris76/benchbox/issues) - Report bugs or request features

## Contributing Examples

Have a useful example to share? Contributions are welcome!

1. Follow the existing structure (`getting_started/`, `notebooks/`, etc.)
2. Include clear comments explaining key steps
3. Add entry to this README with description and prerequisites
4. Test the example with a fresh BenchBox installation
5. Submit a pull request

See [CONTRIBUTING.md](../CONTRIBUTING.md) for full contribution guidelines.

---

**Next Steps**: Start with `getting_started/local/duckdb_tpch_power.py` for a zero-config introduction to BenchBox!
