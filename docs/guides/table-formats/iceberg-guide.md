# Apache Iceberg Guide

```{tags} guide, formats, iceberg, multi-engine
```

Iceberg provides table format features with true multi-engine support. This guide covers how to benchmark with Iceberg across different query engines, catalog configuration, and multi-engine workflows.

## Overview

Apache Iceberg was created at Netflix to solve a specific problem: running the same analytics across multiple query engines without locking into one vendor. Unlike Hive tables (Hive metastore-dependent), Iceberg tables work identically across Spark, Trino, Flink, Athena, Snowflake, and more.

For benchmarking, Iceberg's engine independence enables fair comparisons. We can load TPC-H data once, store it in Iceberg format, and run the same queries on Spark, Trino, and Athena. The data is identical across engines, isolating engine performance from data format differences.

We added Iceberg support to BenchBox for users building multi-engine lakehouses and those running on platforms where Iceberg is the native format (Athena, Starburst, Snowflake).

## Multi-Engine Support

### Supported Engines

The same Iceberg table is readable by:

- Apache Spark
- Trino and Presto
- Apache Flink
- Amazon Athena
- Snowflake
- BigQuery
- DuckDB (via extension)

This multi-engine support comes from a carefully designed metadata layer that doesn't depend on any specific query engine's implementation.

### Why Engine Independence Matters

For benchmarking, engine independence means:

- Load data once, query from multiple engines
- Fair comparisons (identical data across engines)
- Production-like conditions for multi-engine architectures
- No vendor lock-in in your benchmark methodology

## Iceberg Architecture

### Metadata Files

```
iceberg_table/
├── metadata/
│   ├── v1.metadata.json
│   ├── v2.metadata.json (current version)
│   └── snap-xxxxx.avro (manifest list)
├── data/
│   ├── partition=2024-01/
│   │   └── data-xxxxx.parquet
│   └── partition=2024-02/
│       └── data-xxxxx.parquet
└── (catalog points here)
```

### Manifest Lists and Files

- **Metadata files**: Track table state, schema, and partitioning
- **Manifest lists**: Point to manifest files per snapshot
- **Manifest files**: List data files and their statistics

### Data Files

Data files are typically Parquet (or ORC, Avro) containing actual data. Iceberg manages the metadata layer; the underlying storage format is Parquet in most cases.

## Iceberg vs Delta Lake

| Aspect | Iceberg | Delta Lake |
|--------|---------|------------|
| Engine lock-in | None | Databricks-optimized |
| Catalog options | REST, Hive, Glue, Nessie | Unity Catalog, Hive |
| Partition evolution | Yes (metadata-only) | Limited |
| Hidden partitioning | Yes | No |
| Community | Apache project | Linux Foundation |

Both formats provide ACID transactions and time travel. The key difference is Iceberg's broader engine support and more flexible partitioning.

## Performance Considerations

### Manifest Overhead

Iceberg tracks files through a hierarchy of manifests:

1. Read current metadata file
2. Read manifest list (points to manifests)
3. Read relevant manifest files
4. Filter to needed data files based on partition pruning
5. Read data files

**Overhead characteristics:**

- Grows with file count and table history
- Mitigated by manifest caching (engine-dependent)
- Partition pruning reduces manifest reads

For TPC-H benchmarks with standard data loads, manifest overhead is typically under 50ms. For tables with millions of files, consider compaction.

### Mitigation Strategies

- Use partition pruning to reduce manifest reads
- Enable manifest caching where available
- Compact manifests for tables with many small files
- Use `expire_snapshots` to clean up historical metadata

## Advanced Partitioning

### Partition Evolution

Iceberg allows changing partition schemes without rewriting data:

```sql
CREATE TABLE events (...) PARTITIONED BY (event_date);

ALTER TABLE events ADD PARTITION FIELD hour(event_time);
```

The table is originally partitioned by date. The `ALTER` statement adds hour-level partitioning for new data, with no rewrite.

**Benchmark implications:**

- Partition evolution is metadata-only (fast)
- Queries automatically use appropriate partitioning per file
- Historical data keeps its original partitioning

### Hidden Partitioning

Iceberg can partition on transforms without exposing partition columns in queries:

```sql
CREATE TABLE orders
  PARTITIONED BY (month(order_date))
  AS SELECT * FROM raw_orders;

SELECT * FROM orders WHERE order_date = '2024-03-15';
```

The table is partitioned by a month transform. The query filters by exact date, and Iceberg applies partition pruning automatically.

**Benchmark implications:**

- Simpler query writing (no partition column gymnastics)
- Partition pruning happens automatically
- Consistent query behavior across engines

### Partition Transforms

Iceberg supports several partition transforms:

| Transform | Example | Use Case |
|-----------|---------|----------|
| identity | `identity(region)` | Exact value partitioning |
| year | `year(date_col)` | Annual aggregations |
| month | `month(date_col)` | Monthly aggregations |
| day | `day(date_col)` | Daily partitions |
| hour | `hour(timestamp_col)` | Hourly partitions |
| bucket | `bucket(100, id)` | Distribute by hash |
| truncate | `truncate(10, string_col)` | Prefix partitioning |

## Catalog Configuration

With `table_format="iceberg"`, the BenchBox Spark adapter registers Iceberg on Spark's session catalog with
`spark.sql.catalog.spark_catalog.type=hive`. BenchBox has no catalog option of its own. To use a different catalog,
such as a REST catalog, AWS Glue, or Nessie, pass the Iceberg catalog properties through `spark_config` in the Python
API. These settings are applied after the defaults, so they override them:

```python
from benchbox.platforms.spark import SparkAdapter

adapter = SparkAdapter(
    table_format="iceberg",
    spark_config={
        "spark.sql.catalog.spark_catalog.type": "rest",
        "spark.sql.catalog.spark_catalog.uri": "http://localhost:8181",
    },
)
```

See the Apache Iceberg Spark configuration documentation for the properties each catalog type needs.

| Catalog | Use Case |
|---------|----------|
| Hive Metastore | Spark/Hadoop ecosystems; the BenchBox Spark default |
| REST Catalog | Standard, portable across engines |
| AWS Glue | AWS-native (Athena, EMR) |
| Nessie | Git-like versioning |

## Multi-Engine Workflows

### Write with One Engine, Read with Another

A key Iceberg benefit: load data with Spark, query with Trino (or vice versa).

**BenchBox multi-engine benchmark:**

```bash
benchbox run --platform spark --benchmark tpch --scale 10 \
  --table-format iceberg --phases load

benchbox run --platform trino --benchmark tpch --scale 10 \
  --table-format iceberg --phases power

benchbox run --platform athena --benchmark tpch --scale 10 \
  --table-format iceberg --phases power
```

Run the commands in order. The first generates and loads data with Spark. The second runs queries with Trino on the same Iceberg tables. The third runs queries with Athena on the same tables through Glue.

### Ensuring Consistent Results

BenchBox validates query results against reference answers, catching:

- Type handling differences between engines
- NULL behavior variations
- Ordering differences

```bash
benchbox compare spark-results.json trino-results.json
```

This compares results from different engines.

### Common Multi-Engine Issues

**1. Schema evolution lag**

Some engines support new Iceberg schema features before others. If you add a column with Spark, older Trino versions might not see it.

**2. Partition pruning variations**

Partition pruning implementation differs by engine. One engine might prune more aggressively than another.

**3. Statistics availability**

Column-level statistics may not be shared across all engines. This affects query optimization.

**4. Caching effects**

Engine-specific caching can skew comparisons. BenchBox uses cold cache between runs to mitigate this.

## When to Use Iceberg

### Best-Fit Scenarios

| Scenario | Why Iceberg |
|----------|-------------|
| Multi-engine comparison | Same data, fair comparison |
| Lakehouse architectures | Production-like conditions |
| AWS-native platforms | Athena, EMR native support |
| Cross-platform portability | Engine-independent format |

### When to Use Parquet Instead

| Scenario | Why Parquet |
|----------|-------------|
| Single-engine benchmarks | Simpler, no catalog needed |
| Local development | No external catalog required |
| Quick comparisons | Less setup overhead |
| DuckDB-only | Native Parquet is faster |

## See Also

- [Table Format Guides](index.md): Overview of all formats
- [Format Conversion Reference](../../advanced/format-conversion.md): CLI commands for format conversion
- [Delta Lake Guide](delta-lake-guide.md): Alternative table format
- [Parquet Deep Dive](parquet-deep-dive.md): Foundation format for Iceberg <!-- content-ok: cliche -->
