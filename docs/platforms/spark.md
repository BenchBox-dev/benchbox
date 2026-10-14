<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Apache Spark Platform

```{tags} intermediate, guide, spark, dataframe-platform
```

Apache Spark is the most widely deployed distributed computing framework for large-scale data processing. It provides a unified analytics engine supporting batch processing, streaming, and machine learning workloads.

## Features

- **Distributed execution** - Scale across thousands of nodes
- **Multiple deployment modes** - Local, standalone, YARN, Kubernetes
- **Rich format support** - Parquet, ORC, CSV, Delta Lake, Iceberg
- **Adaptive Query Execution** - Dynamic runtime optimization
- **Catalyst optimizer** - Sophisticated query planning
- **DataFrame and SQL APIs** - Flexible programming models

## Installation

```bash
pip install pyspark

pip install pyspark==3.5.0

pip install delta-spark
```

The first command installs PySpark. The second installs a specific version. The third adds Delta Lake support.

## Configuration

### Environment Variables

```bash
export SPARK_HOME=/path/to/spark
export PYSPARK_PYTHON=python3
```

These variables configure the Spark installation that PySpark uses.

### CLI Options

```bash
benchbox run --platform spark --benchmark tpch --scale 1.0 \
  --platform-option driver_memory=4g \
  --platform-option adaptive_enabled=true
```

The CLI accepts `driver_memory` and `adaptive_enabled` for Spark and runs with the default `local[*]` master. To set
the master URL, executor resources, or other Spark settings, use the Python API.

### Adapter Options

These are `SparkAdapter` keyword arguments for the Python API.

| Option | Default | Description |
|--------|---------|-------------|
| `master` | local[*] | Spark master URL |
| `app_name` | BenchBox | Spark application name |
| `driver_memory` | 4g | Driver memory allocation |
| `executor_memory` | 4g | Executor memory allocation |
| `executor_cores` | 2 | Cores per executor |
| `num_executors` | auto | Number of executors |
| `warehouse_dir` | Spark default | Hive warehouse location |
| `table_format` | parquet | Default table format |
| `spark_config` | none | Extra Spark settings, applied last |

## Usage Examples

### Local Mode

```bash
benchbox run --platform spark --benchmark tpch --scale 0.1
```

This runs locally with all cores.

### Cluster Mode

```python
from benchbox.platforms.spark import SparkAdapter

adapter = SparkAdapter(
    master="spark://master:7077",
    num_executors=10,
    executor_memory="16g",
)
```

This runs on a Spark cluster.

### Python API

```python
from benchbox import TPCH
from benchbox.platforms.spark import SparkAdapter

adapter = SparkAdapter(
    master="local[*]",
    driver_memory="4g",
    app_name="TPC-H Benchmark",
)

benchmark = TPCH(scale_factor=1.0)
benchmark.generate_data()
adapter.load_benchmark(benchmark)
results = adapter.run_benchmark(benchmark)
```

The example initializes the adapter, then loads and runs the benchmark.

### Delta Lake Integration

```python
from benchbox.platforms.spark import SparkAdapter

adapter = SparkAdapter(
    master="local[*]",
    table_format="delta",
    spark_config={
        "spark.sql.extensions": "io.delta.sql.DeltaSparkSessionExtension",
        "spark.sql.catalog.spark_catalog": "org.apache.spark.sql.delta.catalog.DeltaCatalog",
    }
)
```

This initializes the adapter with Delta Lake.

## Deployment Modes

### Local Mode

Best for development and small datasets:

```python
adapter = SparkAdapter(master="local[4]")
```

`local[4]` uses 4 cores.

### Standalone Cluster

```bash
$SPARK_HOME/sbin/start-master.sh

$SPARK_HOME/sbin/start-worker.sh spark://master:7077
```

The commands start the master and a worker. Then point the adapter at the master:

```python
adapter = SparkAdapter(master="spark://master:7077")
```

### Kubernetes

```python
adapter = SparkAdapter(master="k8s://https://kubernetes:443")
```

The driver runs in client mode, in the BenchBox process.

## Performance Tuning

### Adaptive Query Execution (AQE)

Enable AQE for dynamic optimization:

```python
adapter = SparkAdapter(
    spark_config={
        "spark.sql.adaptive.enabled": "true",
        "spark.sql.adaptive.coalescePartitions.enabled": "true",
        "spark.sql.adaptive.skewJoin.enabled": "true",
    }
)
```

### Memory Configuration

```python
adapter = SparkAdapter(
    driver_memory="8g",
    executor_memory="16g",
    spark_config={
        "spark.memory.fraction": "0.8",
        "spark.memory.storageFraction": "0.3",
    }
)
```

### Shuffle Optimization

```python
adapter = SparkAdapter(
    spark_config={
        "spark.sql.shuffle.partitions": "200",
        "spark.sql.files.maxPartitionBytes": "128m",
    }
)
```

## Benchmark Recommendations

### Scale Factor Guidelines

| Scale Factor | Recommended Mode | Executors | Memory |
|--------------|------------------|-----------|--------|
| 0.1 | Local | 1 | 4 GB |
| 1.0 | Local/Small cluster | 2-4 | 8 GB each |
| 10.0 | Cluster | 10+ | 16 GB each |
| 100.0 | Large cluster | 50+ | 32 GB each |

### Best Practices

1. **Use columnar formats** - Parquet or ORC for best performance
2. **Enable AQE** - Adaptive execution handles skew automatically
3. **Right-size partitions** - 128 MB to 1 GB per partition
4. **Cache wisely** - Only cache frequently accessed data

## Query Plan Analysis

```bash
benchbox run --platform spark --benchmark tpch \
  --capture-plans
```

Spark provides detailed physical and logical plans:

```python
spark.sql("SELECT ...").explain(extended=True)
```

This prints the query plan.

## Limitations

- **Infrastructure overhead** - Requires cluster for large datasets
- **Startup time** - JVM warm-up affects small benchmarks
- **Memory management** - Complex tuning for optimal performance

## Troubleshooting

### Out of Memory

Increase memory:

```python
adapter = SparkAdapter(
    driver_memory="8g",
    executor_memory="16g",
    spark_config={
        "spark.sql.shuffle.partitions": "400",
    }
)
```

### Shuffle Spill

Reduce the shuffle partition size by raising the partition count:

```python
adapter = SparkAdapter(
    spark_config={
        "spark.sql.shuffle.partitions": "400",
        "spark.shuffle.spill.compress": "true",
    }
)
```

### Connection to Master Failed

Verify that the master is running by requesting its web UI at port 8080, and check firewall rules.

```bash
curl http://master:8080
```

## Related Documentation

- [Databricks Platform](databricks.md) - Managed Spark service
- [Trino Platform](trino.md) - Alternative distributed SQL
- [Presto Platform](presto.md) - Alternative distributed SQL
