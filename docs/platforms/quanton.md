<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Onehouse Quanton Platform

```{tags} intermediate, guide, quanton, cloud-platform, spark
```

Onehouse Quanton is a serverless managed Spark compute runtime that delivers 2-3x better price-performance versus AWS EMR and Databricks, with multi-table-format support for Apache Hudi, Apache Iceberg, and Delta Lake.

## Features

- **Multi-format native** - Hudi, Iceberg, and Delta Lake support
- **Serverless Spark** - No cluster management overhead
- **Apache XTable** - Cross-format metadata translation
- **Cost-efficient** - No per-cluster fees, pay-as-you-go compute
- **Open standards** - 100% Spark SQL compatible

## Prerequisites

- Onehouse account ([onehouse.ai](https://www.onehouse.ai/))
- AWS account with S3 access for data staging
- Onehouse API key

## Installation

```bash
pip install requests boto3
```

Install the required dependencies directly. There is no dedicated BenchBox extra for Quanton.

## Configuration

### Environment Variables (Recommended)

```bash
export ONEHOUSE_API_KEY=your-onehouse-api-key
```

### CLI Options

```bash
benchbox run --platform quanton --benchmark tpch --scale 1.0 \
  --platform-option api_key=your-api-key \
  --platform-option s3_staging_dir=s3://your-bucket/benchbox-data \
  --platform-option table_format=iceberg
```

## Platform Options

| Option | Default | Description |
|--------|---------|-------------|
| `api_key` | (env) | Onehouse API key |
| `s3_staging_dir` | (required) | S3 path for data staging (e.g., s3://bucket/path) |
| `region` | us-east-1 | AWS region for cluster deployment |
| `database` | benchbox | Database name for benchmarks |
| `table_format` | iceberg | Table format: iceberg, hudi, or delta |
| `cluster_size` | small | Cluster size: small, medium, large, xlarge |
| `record_key` | (auto) | Hudi record key field (required for hudi format) |
| `precombine_field` | (optional) | Hudi precombine field for ordering during updates |
| `hudi_table_type` | COPY_ON_WRITE | Hudi table type: COPY_ON_WRITE or MERGE_ON_READ |

## Table Format Selection

Quanton supports three open table formats, each with distinct strengths:

| Format | Best For | Key Features |
|--------|----------|--------------|
| **Iceberg** | Enterprise data lakes, multi-engine access | Schema evolution, hidden partitioning, partition evolution |
| **Hudi** | Streaming workloads, record-level updates | Record-level ACID, incremental processing, efficient upserts |
| **Delta** | Databricks interop, time travel queries | ACID transactions, unified batch/streaming |

### Iceberg (Default)

```bash
benchbox run --platform quanton --benchmark tpch --scale 1.0 \
  --platform-option s3_staging_dir=s3://bucket/data \
  --platform-option table_format=iceberg
```

### Hudi

```bash
benchbox run --platform quanton --benchmark tpch --scale 1.0 \
  --platform-option s3_staging_dir=s3://bucket/data \
  --platform-option table_format=hudi \
  --platform-option record_key=l_orderkey \
  --platform-option precombine_field=l_shipdate
```

Hudi requires `record_key` for write operations.

### Delta Lake

```bash
benchbox run --platform quanton --benchmark tpch --scale 1.0 \
  --platform-option s3_staging_dir=s3://bucket/data \
  --platform-option table_format=delta
```

## Usage Examples

### Basic Benchmark

```bash
benchbox run --platform quanton --benchmark tpch --scale 1.0 \
  --platform-option s3_staging_dir=s3://my-bucket/benchbox
```

### Production Benchmark

```bash
benchbox run --platform quanton --benchmark tpch --scale 10.0 \
  --platform-option s3_staging_dir=s3://my-bucket/benchbox \
  --platform-option cluster_size=medium \
  --platform-option table_format=iceberg
```

### Cross-Format Comparison

```bash
iceberg_result=$(benchbox run --platform quanton --benchmark tpch --scale 1.0 \
  --platform-option s3_staging_dir=s3://my-bucket/benchbox \
  --platform-option table_format=iceberg \
  --output ./quanton-iceberg-run --quiet)
hudi_result=$(benchbox run --platform quanton --benchmark tpch --scale 1.0 \
  --platform-option s3_staging_dir=s3://my-bucket/benchbox \
  --platform-option table_format=hudi \
  --output ./quanton-hudi-run --quiet)
delta_result=$(benchbox run --platform quanton --benchmark tpch --scale 1.0 \
  --platform-option s3_staging_dir=s3://my-bucket/benchbox \
  --platform-option table_format=delta \
  --output ./quanton-delta-run --quiet)

benchbox compare "$iceberg_result" "$hudi_result"
benchbox compare "$iceberg_result" "$delta_result"
```

Each comparison uses the Iceberg run as the baseline and compares one result file pair.

### Python API

```python
from benchbox import TPCH
from benchbox.platforms.onehouse import QuantonAdapter

adapter = QuantonAdapter(
    api_key="your-onehouse-api-key",
    s3_staging_dir="s3://my-bucket/benchbox-data",
    region="us-east-1",
    table_format="iceberg",
    cluster_size="small",
)

benchmark = TPCH(scale_factor=1.0)
benchmark.generate_data()
adapter.load_benchmark(benchmark)
results = adapter.run_benchmark(benchmark)
```

## Cluster Sizing

| Size | Workers | Recommended Scale | Use Case |
|------|---------|-------------------|----------|
| Small | 1-2 | SF 0.01-1.0 | Development, testing |
| Medium | 2-5 | SF 1.0-10.0 | Standard benchmarks |
| Large | 5-10 | SF 10.0-100.0 | Production workloads |
| XLarge | 10+ | SF 100.0+ | Large-scale analytics |

## Hudi-Specific Configuration

When using Hudi table format, additional configuration is required:

### Record Key

The record key uniquely identifies each record for ACID operations:

```bash
--platform-option record_key=l_orderkey,l_linenumber

--platform-option record_key=o_orderkey
```

For the TPC-H `lineitem` table use a composite key, and for `orders` use its primary key.

### Precombine Field

The precombine field orders records during deduplication:

```bash
--platform-option precombine_field=l_shipdate
```

This example uses a date field for ordering.

### Table Type

Choose between COPY_ON_WRITE (faster reads) or MERGE_ON_READ (faster writes):

```bash
--platform-option hudi_table_type=COPY_ON_WRITE

--platform-option hudi_table_type=MERGE_ON_READ
```

`COPY_ON_WRITE` is the default and suits analytics workloads. `MERGE_ON_READ` suits write-heavy workloads.

## Cost Optimization

### Serverless Benefits

- No idle cluster costs
- Pay only for compute time used
- Automatic scaling based on workload

### Cluster Auto-Stop

Clusters automatically terminate after idle timeout (default: 15 minutes).

### S3 Data Reuse

Data staged to S3 is reused across runs:

```bash
benchbox run --platform quanton --benchmark tpch --scale 1.0 \
  --platform-option s3_staging_dir=s3://bucket/data

benchbox run --platform quanton --benchmark tpch --scale 1.0 \
  --platform-option s3_staging_dir=s3://bucket/data
```

The first run uploads the data. Subsequent runs skip the upload.

## Troubleshooting

### Authentication Failed

Verify that the API key is valid:

```bash
curl -H "Authorization: Bearer $ONEHOUSE_API_KEY" \
  https://api.onehouse.ai/v1/health
```

### S3 Access Denied

Ensure your AWS credentials have access to the S3 staging bucket:

```bash
aws s3 ls s3://your-bucket/benchbox-data/
```

### Job Timeout

For large-scale benchmarks, increase the timeout:

```bash
benchbox run --platform quanton --benchmark tpch --scale 100.0 \
  --platform-option timeout_minutes=120
```

### Hudi Write Failures

Ensure record_key is specified for Hudi format. Without it, the run fails with a "No record_key configured" error. Fix it by adding the `record_key` parameter:

```bash
--platform-option record_key=primary_key_column
```

## Related Documentation

- [Databricks](databricks.md) - Alternative lakehouse platform
- [Spark](spark.md) - Open-source Spark
- [Open Table Formats Guide](../advanced/format-conversion.md) - Delta, Iceberg, and Hudi comparison
- [Platform Comparison](comparison-matrix.md) - Full platform matrix
