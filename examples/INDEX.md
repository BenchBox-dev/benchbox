# BenchBox Examples Index

**Quick navigation to find the right example for your needs**

This index helps you discover examples organized by your goal, experience level, platform, and benchmark type. Start here to find exactly what you need without digging through directories.

## 🚀 Quick Start: "I want to..."

| I want to... | Start here | Time | Difficulty |
|--------------|------------|------|------------|
| **Run my first benchmark** | [`getting_started/local/duckdb_tpch_power.py`](getting_started/local/duckdb_tpch_power.py) | 2 min | ⭐ Easy |
| **Preview queries without running** | [`getting_started/cloud/bigquery_tpch_dry_run.py`](getting_started/cloud/bigquery_tpch_dry_run.py) | 1 min | ⭐ Easy |
| **Test specific queries only** | [`getting_started/intermediate/duckdb_tpch_query_subset.py`](getting_started/intermediate/duckdb_tpch_query_subset.py) | 3 min | ⭐⭐ Medium |
| **Benchmark a cloud database** | [`getting_started/cloud/databricks_tpch_power.py`](getting_started/cloud/databricks_tpch_power.py) | 5 min | ⭐⭐ Medium |
| **Use Jupyter notebooks** | [`notebooks/databricks_benchmarking.ipynb`](notebooks/databricks_benchmarking.ipynb) | 10 min | ⭐⭐ Medium |
| **Compare platforms** | See [Multi-Platform Comparison](#-by-use-case) | 30 min | ⭐⭐⭐ Advanced |
| **Set up CI/CD testing** | See [CI/CD Examples](#-by-use-case) | 15 min | ⭐⭐⭐ Advanced |
| **Create custom benchmark** | See [Custom Benchmarks](#-by-use-case) | 60 min | ⭐⭐⭐ Advanced |

---

## 📚 By Experience Level

### Beginner (0-2 hours with BenchBox)
**Goal**: Get first successful benchmark run and understand basics

**Start here:**
1. **Hello World** (2 min): [`getting_started/local/duckdb_tpch_power.py`](getting_started/local/duckdb_tpch_power.py)
   - Zero configuration required
   - Generates data and runs TPC-H power test
   - Perfect for learning the workflow

2. **Try Different Benchmark** (3 min): [`getting_started/local/duckdb_tpcds_power.py`](getting_started/local/duckdb_tpcds_power.py)
   - Same workflow, different queries
   - See TPC-DS complexity

3. **Minimal Dataset** (1 min): [`duckdb_coffeeshop.py`](duckdb_coffeeshop.py)
   - Tiny dataset for quick experimentation
   - Great for testing integrations

**Next Steps**: Move to Intermediate once comfortable with local examples

### Intermediate (2-10 hours with BenchBox)
**Goal**: Use cloud platforms, understand configuration, run targeted tests

**Recommended path:**
1. **Query Subset** (5 min): [`getting_started/intermediate/duckdb_tpch_query_subset.py`](getting_started/intermediate/duckdb_tpch_query_subset.py)
   - Run specific queries instead of full suite
   - Faster iteration for testing

2. **Cloud Dry-Run** (3 min): [`getting_started/cloud/bigquery_tpch_dry_run.py`](getting_started/cloud/bigquery_tpch_dry_run.py)
   - Preview without spending cloud credits
   - Validate configuration

3. **Cloud Execution** (10 min): [`getting_started/cloud/databricks_tpch_power.py`](getting_started/cloud/databricks_tpch_power.py)
   - Run on real cloud platform
   - Set up authentication

4. **Interactive Notebooks** (15 min): [`notebooks/databricks_benchmarking.ipynb`](notebooks/databricks_benchmarking.ipynb)
   - Explore results interactively
   - Visualize performance

**Next Steps**: Try tuning configurations, explore advanced workflows

### Advanced (10+ hours with BenchBox)
**Goal**: Production workflows, CI/CD, multi-platform comparison, custom benchmarks

**Recommended path:**
1. **Unified Runner** (varies): [`unified_runner.py`](unified_runner.py)
   - Multi-platform, multi-benchmark runner
   - Full configuration support
   - Production-ready patterns

2. **Tuning Configurations** (varies): [`tunings/`](tunings/)
   - Compare tuned vs baseline performance
   - Platform-specific optimizations

3. **Advanced Patterns**: See [PATTERNS.md](PATTERNS.md)
   - Multi-platform comparison
   - Result analysis and comparison
   - CI/CD integration
   - Custom benchmark development

---

## 🎯 By Use Case

### Evaluating a New Database
**Goal**: Understand performance characteristics before adoption

**Path:**
1. **Quick Test** (5 min): [`getting_started/local/duckdb_tpch_power.py`](getting_started/local/duckdb_tpch_power.py) with `--scale 0.01`
2. **Realistic Scale** (30 min): Same script with `--scale 1.0`
3. **Multiple Benchmarks** (60 min): Try TPC-H, TPC-DS, ClickBench
4. **Platform Comparison**: Run same benchmark on multiple platforms using [`unified_runner.py`](unified_runner.py)

**Resources:**
- Platform comparison: [`notebooks/`](notebooks/)
- Tuning configurations: [`tunings/`](tunings/)
- Configuration examples: [`config/`](config/)

### Performance Regression Testing
**Goal**: Catch performance regressions in CI/CD

**Path:**
1. **Baseline Run**: Save current performance to JSON
2. **Test Run**: Run benchmark on new code
3. **Comparison**: Use result comparison utilities
4. **CI Integration**: See CI/CD examples (coming soon)

**Key Features:**
- `--dry-run` flag for fast validation
- `--scale 0.01` for quick CI runs
- JSON export for result storage
- Comparison utilities

### Benchmarking Before Migration
**Goal**: Validate performance before migrating platforms

**Path:**
1. **Current Platform**: Run benchmarks on source database
2. **Target Platform Dry-Run**: Preview on destination
3. **Target Platform Test**: Run on destination (small scale)
4. **Full Migration Test**: Run at production scale
5. **Compare Results**: Analyze differences

**Resources:**
- Multi-platform notebooks: [`notebooks/`](notebooks/)
- Platform configs: [`config/`](config/)
- Tuning examples: [`tunings/`](tunings/)

### Optimizing Database Performance
**Goal**: Find best configuration for your workload

**Path:**
1. **Baseline**: Run with no tuning ([`tunings/{platform}/*_notuning.yaml`](tunings/))
2. **Tuned**: Run with optimizations ([`tunings/{platform}/*_tuned.yaml`](tunings/))
3. **Compare**: Analyze performance differences
4. **Customize**: Create custom tuning configuration
5. **Iterate**: Test variations

**Resources:**
- Tuning guide: [`tunings/README.md`](tunings/README.md)
- Example configurations: [`tunings/duckdb/`](tunings/duckdb/), [`tunings/databricks/`](tunings/databricks/)

### Quick Smoke Testing
**Goal**: Fast validation during development

**Path:**
1. Use [`getting_started/intermediate/duckdb_tpch_query_subset.py`](getting_started/intermediate/duckdb_tpch_query_subset.py)
2. Run 2-3 representative queries
3. Verify results quickly

**Tips:**
- Use `--queries 1,6` for fastest tests
- Use `--scale 0.01` for minimal data
- Use `--dry-run` to skip execution

### Learning Benchmark Standards
**Goal**: Understand TPC benchmarks and methodology

**Path:**
1. **TPC-H**: [`getting_started/local/duckdb_tpch_power.py`](getting_started/local/duckdb_tpch_power.py)
   - Start with power test (22 sequential queries)
   - Learn about scale factors
2. **TPC-DS**: [`getting_started/local/duckdb_tpcds_power.py`](getting_started/local/duckdb_tpcds_power.py)
   - More complex queries
   - Larger schema
3. **Documentation**: See [Benchmarks catalog](https://benchbox.dev/docs/benchmarks/index.html)

---

## 🗄️ By Platform

### Local / Embedded Databases

#### DuckDB (In-Memory or Persistent)
**Examples:**
- Basic: [`getting_started/local/duckdb_tpch_power.py`](getting_started/local/duckdb_tpch_power.py)
- TPC-DS: [`getting_started/local/duckdb_tpcds_power.py`](getting_started/local/duckdb_tpcds_power.py)
- Query subset: [`getting_started/intermediate/duckdb_tpch_query_subset.py`](getting_started/intermediate/duckdb_tpch_query_subset.py)
- CoffeeShop: [`duckdb_coffeeshop.py`](duckdb_coffeeshop.py)

**Tuning Configs:** [`tunings/duckdb/`](tunings/duckdb/) (24 configurations)

**Prerequisites:** None (DuckDB installed automatically)

#### SQLite
**Examples:** Via [`unified_runner.py`](unified_runner.py) with `--platform sqlite`

**Prerequisites:** None (SQLite built into Python)

### Cloud Data Warehouses

#### Databricks
**Examples:**
- Getting started: [`getting_started/cloud/databricks_tpch_power.py`](getting_started/cloud/databricks_tpch_power.py)
- Notebook: [`notebooks/databricks_benchmarking.ipynb`](notebooks/databricks_benchmarking.ipynb)
- Configuration: [`config/databricks.yaml`](config/databricks.yaml)

**Tuning Configs:** [`tunings/databricks/`](tunings/databricks/) (10 configurations)

**Prerequisites:**
- `DATABRICKS_TOKEN` environment variable
- `DATABRICKS_HOST` environment variable
- `databricks-sql-connector` package

#### BigQuery
**Examples:**
- Dry-run: [`getting_started/cloud/bigquery_tpch_dry_run.py`](getting_started/cloud/bigquery_tpch_dry_run.py)
- Notebook: [`notebooks/bigquery_benchmarking.ipynb`](notebooks/bigquery_benchmarking.ipynb)
- Configuration: [`config/bigquery.yaml`](config/bigquery.yaml)

**Prerequisites:**
- `GOOGLE_APPLICATION_CREDENTIALS` or `BIGQUERY_PROJECT`
- `google-cloud-bigquery` package

#### Snowflake
**Examples:**
- Notebook: [`notebooks/snowflake_benchmarking.ipynb`](notebooks/snowflake_benchmarking.ipynb)
- Configurations: [`config/snowflake.yaml`](config/snowflake.yaml), [`config/snowflake_keypair.yaml`](config/snowflake_keypair.yaml)

**Prerequisites:**
- Snowflake credentials (username/password or key pair)
- `snowflake-connector-python` package

#### Redshift
**Examples:**
- Notebook: [`notebooks/redshift_benchmarking.ipynb`](notebooks/redshift_benchmarking.ipynb)
- Configurations: [`config/redshift.yaml`](config/redshift.yaml), [`config/redshift_with_keys.yaml`](config/redshift_with_keys.yaml)

**Prerequisites:**
- AWS credentials
- `redshift-connector` package

#### ClickHouse
**Examples:**
- Notebook: [`notebooks/clickhouse_benchmarking.ipynb`](notebooks/clickhouse_benchmarking.ipynb)
- Configurations: [`config/clickhouse.yaml`](config/clickhouse.yaml), [`config/clickhouse_cloud.yaml`](config/clickhouse_cloud.yaml)

**Prerequisites:**
- ClickHouse server access
- `clickhouse-driver` package

### Additional Platforms (via unified_runner.py)

The following platforms are supported via [`unified_runner.py`](unified_runner.py) and platform adapters:

| Platform | Usage | Prerequisites |
|----------|-------|---------------|
| **PostgreSQL** | `--platform postgresql` | PostgreSQL server, `psycopg2` |
| **Trino** | `--platform trino` | Trino cluster, `trino` package |
| **Presto** | `--platform presto` | Presto cluster, `presto-python-client` |
| **Apache Spark** | `--platform spark` | Spark cluster, `pyspark` |
| **AWS Athena** | `--platform athena` | AWS credentials, `pyathena` |
| **Firebolt** | `--platform firebolt` | Firebolt account, `firebolt-sdk` |
| **Azure Synapse** | `--platform azure_synapse` | Azure credentials |
| **DataFusion** | `--platform datafusion` | `datafusion` package |
| **Polars** | `--platform polars` | `polars` package |
| **CUDF (GPU)** | `--platform cudf` | NVIDIA GPU, `cudf` package |

See [Platform Documentation](https://benchbox.dev/docs/platforms/index.html) for detailed setup guides.

---

## 📊 By Benchmark

### TPC-H (Standard OLAP)
**Best for:** General analytical query performance, decision support

**Examples:**
- **Beginner**: [`getting_started/local/duckdb_tpch_power.py`](getting_started/local/duckdb_tpch_power.py) - Power test (22 queries)
- **Intermediate**: [`getting_started/intermediate/duckdb_tpch_query_subset.py`](getting_started/intermediate/duckdb_tpch_query_subset.py) - Select specific queries
- **Cloud**: [`getting_started/cloud/databricks_tpch_power.py`](getting_started/cloud/databricks_tpch_power.py) - Databricks execution
- **Advanced**: [`unified_runner.py`](unified_runner.py) with `--benchmark tpch --phases power,throughput`

**Scale Factors:** 0.01 (tiny), 0.1 (small), 1.0 (standard), 10+ (large)
**Tuning:** [`tunings/duckdb/tpch_tuned.yaml`](tunings/duckdb/tpch_tuned.yaml), [`tunings/databricks/tpch_tuned.yaml`](tunings/databricks/tpch_tuned.yaml)

### TPC-DS (Complex Analytics)
**Best for:** Complex analytical workloads, decision support, BI reporting

**Examples:**
- **Beginner**: [`getting_started/local/duckdb_tpcds_power.py`](getting_started/local/duckdb_tpcds_power.py) - Power test
- **Advanced**: [`unified_runner.py`](unified_runner.py) with `--benchmark tpcds`

**Scale Factors:** 0.01 (tiny), 0.1 (small), 1.0 (standard), 10+ (large)
**Tuning:** [`tunings/duckdb/tpcds_tuned.yaml`](tunings/duckdb/tpcds_tuned.yaml), [`tunings/databricks/tpcds_tuned.yaml`](tunings/databricks/tpcds_tuned.yaml)

### TPC-DI (Data Integration)
**Best for:** ETL performance, data warehouse loading, data transformation

**Examples:**
- **Advanced**: [`unified_runner.py`](unified_runner.py) with `--benchmark tpcdi`

**Note:** TPC-DI is more complex and requires specific setup. See documentation for details.

### SSB (Star Schema Benchmark)
**Best for:** Star schema queries, data warehouse analytics, BI workloads

**Examples:**
- **Advanced**: [`unified_runner.py`](unified_runner.py) with `--benchmark ssb`

**Tuning:** [`tunings/duckdb/ssb_tuned.yaml`](tunings/duckdb/ssb_tuned.yaml), [`tunings/databricks/ssb_tuned.yaml`](tunings/databricks/ssb_tuned.yaml)

### ClickBench
**Best for:** Real-time analytics, web analytics, time-series data

**Examples:**
- **Advanced**: [`unified_runner.py`](unified_runner.py) with `--benchmark clickbench`

**Tuning:** [`tunings/duckdb/clickbench_tuned.yaml`](tunings/duckdb/clickbench_tuned.yaml)

### NYC Taxi (Real-World Analytics)
**Best for:** Real-world data patterns, transportation analytics, geospatial queries

**Examples:**
- **Beginner**: [`getting_started/local/duckdb_nyctaxi.py`](getting_started/local/duckdb_nyctaxi.py) - Real taxi data analytics
- **Advanced**: [`unified_runner.py`](unified_runner.py) with `--benchmark nyctaxi`

**Scale Factors:** 0.01 (tiny), 0.1 (small), 1.0 (30M rows), 10+ (large)

### TSBS DevOps (Time-Series)
**Best for:** Infrastructure monitoring, time-series databases, DevOps workloads

**Examples:**
- **Beginner**: [`getting_started/local/duckdb_tsbs_devops.py`](getting_started/local/duckdb_tsbs_devops.py) - DevOps metrics
- **Advanced**: [`unified_runner.py`](unified_runner.py) with `--benchmark tsbs_devops`

**Scale Factors:** 0.01 (10 hosts), 0.1 (10 hosts), 1.0 (100 hosts)

### TPC-H Data Vault (Enterprise DWH)
**Best for:** Enterprise data warehouse patterns, Hub-Link-Satellite modeling

**Examples:**
- **Advanced**: [`unified_runner.py`](unified_runner.py) with `--benchmark datavault`

**Note:** TPC-H Data Vault uses Data Vault 2.0 modeling derived from TPC-H schema.

### Other Benchmarks
- **TPC-DS-OBT (One Big Table)**: Via [`unified_runner.py`](unified_runner.py) with `--benchmark tpcds_obt` - Denormalized analytics
- **TPC-H Skew**: Via [`unified_runner.py`](unified_runner.py) with `--benchmark tpch_skew` - Non-uniform data distributions
- **TPC-H Havoc**: Via [`unified_runner.py`](unified_runner.py) with `--benchmark tpchavoc` - Query optimizer stress testing
- **AMPLab Big Data Benchmark**: Via [`unified_runner.py`](unified_runner.py) with `--benchmark amplab`
- **H2O.ai Database Benchmark**: Via [`unified_runner.py`](unified_runner.py) with `--benchmark h2odb`
- **Join Order Benchmark**: Via [`unified_runner.py`](unified_runner.py) with `--benchmark joinorder --scale 1`
- **Read Primitives**: Via [`unified_runner.py`](unified_runner.py) with `--benchmark read_primitives`
- **Write Primitives**: Via [`unified_runner.py`](unified_runner.py) with `--benchmark write_primitives`
- **Transaction Primitives**: Via [`unified_runner.py`](unified_runner.py) with `--benchmark transaction_primitives`
- **CoffeeShop (Minimal)**: [`duckdb_coffeeshop.py`](duckdb_coffeeshop.py)

---

## 🛠️ By Feature

### Dry-Run Mode (Preview Without Execution)
**Examples that support `--dry-run`:**
- All `getting_started/` examples
- [`unified_runner.py`](unified_runner.py)
- Cloud examples

**Usage:** `python example.py --dry-run ./preview`

**Outputs:**
- Configuration summary (JSON/YAML)
- Generated SQL queries
- Schema DDL
- Estimated resource usage

### Tuning Configurations
**Directory:** [`tunings/`](tunings/)

**Available platforms:**
- DuckDB: 24 configurations (tuned vs notuning for each benchmark)
- Databricks: 10 configurations (tuned vs notuning)

**Usage:** Via [`unified_runner.py`](unified_runner.py) with `--tuning tuned|notuning`

**See:** [`tunings/README.md`](tunings/README.md) for detailed guide

### Query Subset Selection
**Example:** [`getting_started/intermediate/duckdb_tpch_query_subset.py`](getting_started/intermediate/duckdb_tpch_query_subset.py)

**Usage:** `--queries 1,6,12` to run specific queries only

**Use cases:**
- Quick smoke tests
- Debugging specific queries
- Performance profiling

### Result Export Formats
**Supported formats:**
- JSON (default)
- CSV
- HTML

**Usage:** Via [`unified_runner.py`](unified_runner.py) with `--formats json,csv,html`

### Interactive Notebooks
**Directory:** [`notebooks/`](notebooks/)

**Available platforms:**
- Databricks
- BigQuery
- Snowflake
- Redshift
- ClickHouse

**See:** [`notebooks/README.md`](notebooks/README.md) for details

---

## 📖 Additional Resources

### Documentation
- **Getting Started**: <https://benchbox.dev/docs/usage/getting-started.html>
- **Examples Guide**: <https://benchbox.dev/docs/usage/examples.html>
- **CLI Reference**: <https://benchbox.dev/docs/usage/cli-quick-start.html>
- **Benchmarks**: <https://benchbox.dev/docs/benchmarks/index.html>
- **Platform Selection**: <https://benchbox.dev/docs/platforms/platform-selection-guide.html>
- **Configuration**: <https://benchbox.dev/docs/usage/configuration.html>

### Example Guides
- **Main README**: [README.md](README.md) - Overview and quick navigation
- **Common Patterns**: [PATTERNS.md](PATTERNS.md) - Reusable workflow patterns
- **Getting Started**: [getting_started/README.md](getting_started/README.md) - Beginner examples
- **Notebooks Guide**: [notebooks/README.md](notebooks/README.md) - Interactive examples
- **Tuning Guide**: [tunings/README.md](tunings/README.md) - Performance optimization
- **Dry-Run Samples**: [dry_run_samples/README.md](dry_run_samples/README.md) - Example outputs

### Support
- **Troubleshooting**: <https://benchbox.dev/docs/usage/troubleshooting.html>
- **GitHub Issues**: [github.com/joeharris76/benchbox/issues](https://github.com/joeharris76/benchbox/issues)

---

## 🔍 Find Examples By

### Time Available
- **< 5 minutes**: Local examples with `--scale 0.01`, dry-run mode
- **5-15 minutes**: Query subset, cloud dry-run, small scale benchmarks
- **15-60 minutes**: Full power test at scale 0.1, multiple benchmarks
- **1+ hours**: Large scale tests, multi-platform comparison, throughput tests

### Difficulty
- **⭐ Easy**: [`getting_started/local/`](getting_started/local/) - Zero config, local execution
- **⭐⭐ Medium**: [`getting_started/cloud/`](getting_started/cloud/), [`notebooks/`](notebooks/) - Cloud platforms, configuration
- **⭐⭐⭐ Advanced**: [`unified_runner.py`](unified_runner.py), custom workflows - Full features, production patterns

### Goal
- **Learning**: Start with local examples, progress to cloud
- **Testing**: Use query subset, dry-run mode, small scales
- **Benchmarking**: Use realistic scales, tuning configs, multiple benchmarks
- **Production**: Use [`unified_runner.py`](unified_runner.py), CI/CD integration, result export

---

**Can't find what you need?** Check [PATTERNS.md](PATTERNS.md) for common workflow patterns or [README.md](README.md) for additional navigation options.
