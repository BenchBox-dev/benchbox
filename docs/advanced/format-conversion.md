# Open Table Formats Guide

```{tags} advanced, guide
```

Convert benchmark data to modern columnar formats for improved query performance, storage efficiency, and platform compatibility.

> **Looking for conceptual guidance?** See the [Table Format Guides](../guides/table-formats/index.md) for in-depth explanations of when to use each format and benchmarking considerations.

## Overview

BenchBox supports six open table formats for storing benchmark data:

| Format | Type | Key Features | Best For |
|--------|------|--------------|----------|
| **Parquet** | File | Fast, universal support | General analytics, portability |
| **Vortex** | File | High compression, fast scans | Analytical workloads, storage efficiency |
| **Delta Lake** | Table | ACID, time travel | Production data lakes, Databricks |
| **Iceberg** | Table | Schema evolution, hidden partitioning | Enterprise data platforms |
| **Apache Hudi** | Table | Record-level ACID, incremental processing | Streaming workloads, upserts |
| **DuckLake** | Table | ACID, time travel, DuckDB-native | DuckDB-native analytics, local development |

## Why Use Columnar Formats?

TPC benchmarks generate data in TBL format (pipe-delimited text files). Converting to columnar formats provides:

### Performance Benefits

- **Faster queries**: Columnar storage enables reading only required columns (improvement varies by query selectivity)
- **Predicate pushdown**: Filter data at the storage layer before loading into memory
- **Partition pruning**: Skip irrelevant data partitions entirely
- **Statistics-based optimization**: Min/max values and row counts per column chunk

### Storage Benefits

- **3-5x compression**: Columnar layout enables efficient encoding and compression
- **Type-aware encoding**: Dictionary encoding for strings, RLE for repeated values
- **Compression algorithms**: Snappy, Gzip, or Zstd for different size/speed tradeoffs

### Platform Compatibility

| Platform | Parquet | Vortex | Delta Lake | Iceberg | Hudi | DuckLake |
|----------|---------|--------|------------|---------|------|----------|
| DuckDB | Native | Extension | Extension | - | - | Native |
| Spark | Native | - | Native | Native | Native | - |
| Databricks | Native | - | Native | Native | Native | - |
| Quanton | Native | - | Native | Native | Native | - |
| Snowflake | External | - | External | Native | - | - |
| BigQuery | External | - | - | External | - | - |
| Polars | Native | - | Native | - | - | - |
| DataFusion | Native | Experimental | - | - | - | - |

## Quick Start

### Basic Conversion

```bash
benchbox run --platform duckdb --benchmark tpch --scale 1 --phases generate

benchbox convert --input ./benchmark_runs/tpch_sf1 --format parquet
```

### Query Converted Data

```python
import duckdb

conn = duckdb.connect()
result = conn.execute("""
    SELECT l_returnflag, SUM(l_extendedprice) as revenue
    FROM read_parquet('./benchmark_runs/tpch_sf1/lineitem.parquet')
    GROUP BY l_returnflag
""").fetchdf()
```

## Format Deep Dive

### Apache Parquet

The most widely supported columnar format. Ideal for analytics workloads and data interchange.

**Structure:**
```
customer.parquet
├── Row Group 0
│   ├── Column: c_custkey (INT64, snappy)
│   ├── Column: c_name (STRING, dictionary)
│   └── Column: c_acctbal (DECIMAL, snappy)
├── Row Group 1
│   └── ...
└── Footer (schema, statistics)
```

**Usage:**
```bash
benchbox convert --input ./data --format parquet

benchbox convert --input ./data --format parquet --compression zstd

benchbox convert --input ./data --format parquet --partition l_shipdate
```

**Reading Parquet:** the snippets below use DuckDB, PyArrow, Polars and Pandas, in that order.
```python
conn.execute("SELECT * FROM read_parquet('customer.parquet')")

import pyarrow.parquet as pq
table = pq.read_table('customer.parquet')

import polars as pl
df = pl.read_parquet('customer.parquet')

import pandas as pd
df = pd.read_parquet('customer.parquet')
```

### Vortex

A high-performance columnar file format optimized for analytical workloads with excellent compression.

**Structure:**
```
customer.vortex
├── Encoded column chunks
│   ├── Column: c_custkey (INT64, compressed)
│   ├── Column: c_name (STRING, dictionary)
│   └── Column: c_acctbal (DECIMAL, compressed)
└── Footer (schema, statistics)
```

**Installation:**
```bash
uv add vortex-data
```

**Usage:**
```bash
benchbox convert --input ./data --format vortex

benchbox convert --input ./data --format vortex --compression zstd
```

**Reading Vortex:** the first snippet uses the Python `vortex` library. The DuckDB snippet needs the `vortex` extension.
```python
import vortex
array = vortex.io.read('customer.vortex')
table = array.to_arrow()

conn.execute("INSTALL vortex; LOAD vortex;")
conn.execute("SELECT * FROM read_vortex('customer.vortex')")
```

### Delta Lake

Open table format with ACID transactions, built on Parquet files.

**Structure:**
```
customer/
├── _delta_log/
│   ├── 00000000000000000000.json  # Initial commit
│   └── 00000000000000000001.json  # Update commit
├── part-00000-*.parquet
└── part-00001-*.parquet
```

**Features:**
- **ACID Transactions**: Concurrent reads/writes with isolation
- **Time Travel**: Query historical versions of data
- **Schema Evolution**: Add/remove columns without rewriting
- **Unified Batch/Streaming**: Same table for both workloads

**Installation:**
```bash
uv add deltalake
```

**Usage:**
```bash
benchbox convert --input ./data --format delta

benchbox convert --input ./data --format delta --compression zstd
```

**Reading Delta Lake:** the snippets below use the Python `deltalake` library, DuckDB (which needs the `delta` extension) and Spark, in that order.
```python
from deltalake import DeltaTable
dt = DeltaTable('./customer')
df = dt.to_pandas()

conn.execute("INSTALL delta; LOAD delta;")
conn.execute("SELECT * FROM delta_scan('./customer')")

df = spark.read.format("delta").load("./customer")
```

**Time Travel:** the first line queries a specific version and the second queries by timestamp.
```python
dt = DeltaTable('./customer', version=0)

dt = DeltaTable('./customer', as_of='2024-01-15T10:00:00')
```

### Apache Iceberg

Modern table format designed for large-scale data lakes with enterprise features.

**Structure:**
```
customer/
├── metadata/
│   ├── v1.metadata.json
│   ├── snap-*.avro  # Snapshot manifests
│   └── *.avro       # Manifest files
└── data/
    └── *.parquet    # Data files
```

**Features:**
- **Hidden Partitioning**: Partition columns not exposed in queries
- **Schema Evolution**: Full schema changes without data rewrite
- **Snapshot Isolation**: Consistent reads during writes
- **Partition Evolution**: Change partitioning without rewriting data

**Installation:**
```bash
uv add pyiceberg
```

**Usage:**
```bash
benchbox convert --input ./data --format iceberg

benchbox convert --input ./data --format iceberg --partition l_shipdate
```

**Reading Iceberg:** the first snippet uses PyIceberg and the last uses Spark.
```python
from pyiceberg.catalog import load_catalog
catalog = load_catalog("local", **{"type": "sql", "uri": "sqlite:///catalog.db"})
table = catalog.load_table("default.customer")
df = table.scan().to_pandas()

df = spark.read.format("iceberg").load("./customer")
```

### Apache Hudi

Open table format optimized for incremental processing and record-level operations.

**Structure:**
```
customer/
├── .hoodie/
│   ├── hoodie.properties      # Table metadata
│   └── *.commit               # Commit timeline
└── data/
    └── *.parquet              # Data files with Hudi metadata
```

**Features:**
- **Record-Level ACID**: Update and delete individual records efficiently
- **Incremental Processing**: Query only changed data since last commit
- **COPY_ON_WRITE**: Faster reads, better for analytics workloads
- **MERGE_ON_READ**: Faster writes, better for streaming workloads
- **Time Travel**: Query historical versions via commit timeline

**Installation:** Hudi requires PySpark with the `hudi-spark-bundle`. Install PySpark, then configure Spark with
`spark.jars.packages=org.apache.hudi:hudi-spark3.5-bundle_2.12:0.14.0`.
```bash
pip install pyspark
```

**Usage:**
Hudi operations require PySpark SQL. For direct Hudi support, use the Quanton platform (recommended). Hudi tables need a
record key, which the Quanton adapter takes as a Python API argument:

```python
from benchbox.platforms.onehouse import QuantonAdapter

adapter = QuantonAdapter(
    s3_staging_dir="s3://my-bucket/benchbox",
    table_format="hudi",
    record_key="l_orderkey",
)
```

**Reading Hudi:** the first query is a full read. The second is an incremental query that returns changes since the given commit time.
```python
spark = SparkSession.builder \
    .config("spark.jars.packages", "org.apache.hudi:hudi-spark3.5-bundle_2.12:0.14.0") \
    .getOrCreate()

df = spark.read.format("hudi").load("./customer")

df = spark.read.format("hudi") \
    .option("hoodie.datasource.query.type", "incremental") \
    .option("hoodie.datasource.read.begin.instanttime", "20240101000000") \
    .load("./customer")
```

**Time Travel:** this query reads the table as of a specific timestamp.
```python
df = spark.read.format("hudi") \
    .option("as.of.instant", "20240115100000") \
    .load("./customer")
```

### DuckLake

DuckDB's native open table format with full ACID transactions, time travel, and optimal DuckDB performance.

**Structure:**
```
customer/
├── metadata.ducklake          # DuckLake metadata catalog
└── data/
    └── *.parquet              # Data stored as Parquet files
```

**Features:**
- **ACID Transactions**: Full transactional support with snapshot isolation
- **Time Travel**: Query historical versions of data
- **Schema Evolution**: Add/modify columns without rewriting data
- **Native DuckDB Integration**: Optimal performance with DuckDB query engine
- **Predicate Pushdown**: Filter data at the storage layer

**Installation:** DuckLake is part of DuckDB 1.2.0 and later, so it needs no separate installation. The `ducklake`
extension is installed automatically on first use.

**Usage:**
```bash
benchbox convert --input ./data --format ducklake

benchbox convert --input ./data --format ducklake --compression zstd
```

**Reading DuckLake:**
```python
import duckdb

conn = duckdb.connect()

conn.execute("INSTALL ducklake; LOAD ducklake;")

conn.execute("""
    ATTACH 'ducklake:./customer/metadata.ducklake' AS ducklake_db
    (DATA_PATH './customer/data')
""")

result = conn.execute("SELECT * FROM ducklake_db.main.customer").fetchdf()
```

**Time Travel:** DuckLake supports querying historical snapshots. Querying a specific version works when your DuckDB
version supports it.
```python
conn.execute("""
    SELECT * FROM ducklake_db.main.customer
    AT SNAPSHOT 'snapshot_id'
""")
```

**When to Use DuckLake:**
- **DuckDB-centric workflows**: When DuckDB is your primary analytics engine
- **Local development**: Fast iteration with full ACID guarantees
- **Single-engine scenarios**: When you don't need multi-engine compatibility
- **Benchmarking DuckDB**: Compare DuckLake vs Delta Lake vs Iceberg performance

## Compression Guide

### Compression Algorithms

| Algorithm | Compression | Speed | Use Case |
|-----------|-------------|-------|----------|
| **Snappy** | Moderate | Fast | Default, good balance |
| **Gzip** | Good | Slow | Cold storage, archival |
| **Zstd** | Best | Moderate | Large datasets, cloud storage |
| **None** | None | Fastest | Debugging, already compressed |

### Compression Benchmarks (TPC-H SF=10)

```
Format          | Algorithm | Size (MB) | Compress Time | Query Time
----------------|-----------|-----------|---------------|------------
TBL (raw)       | -         | 10,240    | -             | baseline
Parquet         | snappy    | 3,413     | 45s           | 0.32x
Parquet         | gzip      | 2,560     | 120s          | 0.35x
Parquet         | zstd      | 2,048     | 65s           | 0.33x
Delta Lake      | snappy    | 3,450     | 48s           | 0.34x
```

### Choosing Compression

Use `snappy` for fast iteration during development. Use `zstd` for production, where storage size matters, and for
cloud storage, where it minimizes transfer costs. The three commands below show these cases in that order.

```bash
benchbox convert --input ./data --format parquet --compression snappy

benchbox convert --input ./data --format parquet --compression zstd

benchbox convert --input ./data --format parquet --compression zstd
```

## Partitioning Guide

### When to Partition

Partitioning is beneficial when:
- Table has >1M rows
- Queries frequently filter on specific columns (dates, regions, status)
- Data has natural partitioning boundaries

### Partition Column Selection

**Good partition columns:**
- Date/time columns (l_shipdate, o_orderdate)
- Categorical columns with 10-1000 distinct values (l_returnflag, c_mktsegment)
- Region/geography columns

**Bad partition columns:**
- High cardinality columns (customer_id, order_id)
- Columns rarely used in filters

### Partitioning Examples

The first command partitions by a single date column and creates `lineitem/l_shipdate=1996-01-01/part-*.parquet`.
The second partitions by multiple columns, hierarchically, and creates
`lineitem/l_returnflag=N/l_linestatus=O/part-*.parquet`.

```bash
benchbox convert --input ./data --format parquet --partition l_shipdate

benchbox convert --input ./data --format parquet \
    --partition l_returnflag --partition l_linestatus
```

### Querying Partitioned Data

This DuckDB query uses Hive partitioning, so it reads only the relevant partition. DuckDB pushes partition pruning
filters down automatically.

```sql
SELECT * FROM read_parquet('./lineitem/**/*.parquet', hive_partitioning=true)
WHERE l_shipdate = '1996-03-15';
```

## Platform Integration

### DuckDB

```python
import duckdb

conn = duckdb.connect()

conn.execute("SELECT * FROM read_parquet('./data/*.parquet')")

conn.execute("INSTALL delta; LOAD delta;")
conn.execute("SELECT * FROM delta_scan('./data/customer')")

conn.execute("INSTALL ducklake; LOAD ducklake;")
conn.execute("""
    ATTACH 'ducklake:./data/customer/metadata.ducklake' AS ducklake_db
    (DATA_PATH './data/customer/data')
""")
conn.execute("SELECT * FROM ducklake_db.main.customer")

conn.execute("""
    SELECT * FROM read_parquet('./lineitem/**/*.parquet', hive_partitioning=true)
    WHERE l_shipdate >= '1996-01-01'
""")
```

### Polars

```python
import polars as pl

df = pl.read_parquet('./data/customer.parquet')

df = pl.read_delta('./data/customer')

lf = pl.scan_parquet('./data/*.parquet')
result = lf.filter(pl.col('c_acctbal') > 1000).collect()
```

### PySpark

```python
from pyspark.sql import SparkSession

spark = SparkSession.builder \
    .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension") \
    .getOrCreate()

df = spark.read.parquet('./data/customer.parquet')

df = spark.read.format("delta").load('./data/customer')

df = spark.read.format("iceberg").load('./data/customer')

df = spark.read.format("hudi").load('./data/customer')
```

### Databricks

Parquet files are auto-detected, and Delta Lake is the default format. The first two lines read Parquet and Delta Lake.

```python
df = spark.read.load('/mnt/data/customer.parquet')

df = spark.read.load('/mnt/data/customer')

df = spark.read.format("iceberg").load('/mnt/data/customer')

df = spark.read.format("hudi").load('/mnt/data/customer')
```

### Onehouse Quanton

Quanton supports all three table formats (Delta Lake, Iceberg and Hudi) via Spark SQL. Use the BenchBox CLI for
Delta Lake and Iceberg, for example `benchbox run --platform quanton --benchmark tpch --platform-option table_format=delta`.
Hudi needs a record key, which only the Python API can set.

## Best Practices

### 1. Choose the Right Format

| Scenario | Recommended Format |
|----------|-------------------|
| One-time analysis | Parquet |
| Shared datasets | Parquet |
| Maximum compression | Vortex |
| DuckDB/DataFusion analytics | Vortex |
| Production data lake | Delta Lake |
| Multi-engine access | Iceberg |
| Schema changes expected | Iceberg |
| Streaming/upsert workloads | Hudi |
| Record-level updates | Hudi |
| Time travel needed | Delta Lake, DuckLake |
| DuckDB-native workflows | DuckLake |
| Local development with ACID | DuckLake |

### 2. Compression Strategy

Use `snappy` for development, for fast iteration. Use `zstd` for production, to optimize storage, and for cloud
storage, to minimize costs.

```bash
--compression snappy

--compression zstd

--compression zstd
```

### 3. Validation

Always validate for TPC compliance. Skip validation (`--no-validate`) only for exploratory work.

```bash
benchbox convert --input ./data --format parquet --validate

benchbox convert --input ./data --format parquet --no-validate
```

### 4. Directory Organization

```
benchmark_runs/
├── tpch_sf1_tbl/           # Raw TBL files
├── tpch_sf1_parquet/       # Parquet conversion
├── tpch_sf1_vortex/        # Vortex for high compression
├── tpch_sf1_ducklake/      # DuckLake for DuckDB-native workflows
├── tpch_sf10_delta/        # Delta Lake for larger scale
├── tpch_sf10_hudi/         # Hudi for streaming/upsert workloads
└── tpch_sf100_iceberg/     # Iceberg for production
```

## Troubleshooting

### Conversion Fails with "vortex not installed"

```bash
uv add vortex-data
```

### Conversion Fails with "deltalake not installed"

```bash
uv add deltalake
```

### Conversion Fails with "pyiceberg not installed"

```bash
uv add pyiceberg
```

### Conversion Fails with "DuckLake extension not available"

DuckLake requires DuckDB >= 1.2.0. The extension is auto-installed on first use:

```python
import duckdb
conn = duckdb.connect()
conn.execute("INSTALL ducklake; LOAD ducklake;")
```

If installation fails, ensure you have a compatible DuckDB version:

```bash
uv add "duckdb>=1.2.0"
```

### Row Count Mismatch

If validation fails with row count mismatch:
1. Check source TBL files for corruption
2. Regenerate data with `--force datagen`
3. If intentional, use `--no-validate`

### Large File Handling

For very large scale factors (SF > 100), use Zstd for the best compression, and skip validation if conversion is
slow:
```bash
benchbox convert --input ./data --format parquet --compression zstd

benchbox convert --input ./data --format parquet --no-validate
```

## See Also

- [CLI Reference: convert](../reference/cli/convert.md) - Full command options
- [Data Generation](../usage/data-generation.md) - Generating benchmark data
- [DuckDB Platform](../platforms/duckdb.md) - DuckDB integration
- [Performance Optimization](performance-optimization.md) - Query performance tuning

### Table Format Guides

For conceptual depth on each format, see the Table Format Guides:

- [Table Format Guides Overview](../guides/table-formats/index.md) - When format choice matters for benchmarks
- [Parquet Deep Dive](../guides/table-formats/parquet-deep-dive.md) - Row groups, compression options, statistics
- [Delta Lake Guide](../guides/table-formats/delta-lake-guide.md) - Transaction overhead, OPTIMIZE, Z-ORDER
- [Apache Iceberg Guide](../guides/table-formats/iceberg-guide.md) - Multi-engine support, partition evolution
- [Vortex Guide](../guides/table-formats/vortex-guide.md) - Composable encodings, maturity considerations
