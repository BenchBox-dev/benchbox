<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Amazon Athena Platform

```{tags} intermediate, guide, athena, cloud-platform
```

Amazon Athena is AWS's serverless interactive query service for analyzing data directly in Amazon S3 using standard SQL. Under the hood, Athena runs Trino, optimized for ad-hoc querying of data lakes.

## Features

- **Serverless** - No infrastructure to manage, scales automatically
- **Pay-per-query** - Charged based on data scanned (list price $5 per TB; `benchbox/core/cost/pricing_data.yaml` is the source of truth for estimates)
- **S3 native** - Query data directly in S3 without data movement
- **AWS Glue integration** - Uses Glue Data Catalog for metadata
- **Multiple formats** - Parquet, ORC, JSON, CSV, Avro support
- **Partition pruning** - Efficient queries on partitioned data

## Installation

```bash
pip install pyathena boto3

pip install "benchbox[athena]"
```

The first command installs the dependencies directly. The second installs them through BenchBox extras.

## Configuration

### Environment Variables

```bash
export AWS_ACCESS_KEY_ID=your_access_key
export AWS_SECRET_ACCESS_KEY=your_secret_key
export AWS_DEFAULT_REGION=us-east-1

export AWS_PROFILE=your-profile
```

Set the access key variables, or use an AWS profile instead.

### Interactive Setup

```bash
benchbox setup --platform athena
```

This saves the region, workgroup, S3 locations, and AWS authentication.
The region is not a `--platform-option` key; BenchBox uses the saved region,
or `us-east-1` when none is saved.

### CLI Options

```bash
benchbox run --platform athena --benchmark tpch --scale 1.0 \
  --platform-option compute_resource=primary \
  --platform-option s3_staging_dir=s3://your-bucket/athena-results/
```

BenchBox names the Glue database after the benchmark and scale factor.

### Platform Options

The **Set with** column shows where each setting comes from: the saved
credentials (`benchbox setup`), a `--platform-option` key on `benchbox run`,
or an `AthenaAdapter` argument in the Python API.

| Option | Set with | Default | Description |
|--------|----------|---------|-------------|
| `region` | setup | us-east-1 | AWS region |
| `compute_resource` | `--platform-option` | primary | Athena workgroup. Deprecated alias: `workgroup` |
| `s3_staging_dir` | `--platform-option` | (none) | S3 path for staging data. Unless setup saved an output location, query results go to `s3://<bucket>/athena-results/` |
| `s3_bucket` | `--platform-option` | (none) | S3 bucket for query results and data staging |
| `staging_root` | `--platform-option` | (none) | Same as `s3_staging_dir` |
| `aws_profile` | `--platform-option` | (none) | AWS credentials profile |
| `database` | Python API | (auto) | Glue Data Catalog database |
| `catalog` | Python API | AwsDataCatalog | Data catalog name |

## Usage Examples

### Basic Benchmark Run

```bash
benchbox run --platform athena --benchmark tpch --scale 1.0 \
  --platform-option s3_staging_dir=s3://my-bucket/athena-results/
```

### Python API

```python
from benchbox import TPCH
from benchbox.platforms.athena import AthenaAdapter

adapter = AthenaAdapter(
    region="us-east-1",
    compute_resource="primary",
    database="benchmarks",
    s3_staging_dir="s3://my-bucket/athena-results/",
)

benchmark = TPCH(scale_factor=1.0)
adapter.load_benchmark(benchmark)
results = adapter.run_benchmark(benchmark)
```

### External Table Mode

Skip CTAS materialization and register external tables directly over staged Parquet:

```bash
benchbox run --platform athena --benchmark tpch --scale 1.0 \
  --table-mode external \
  --platform-option staging_root=s3://my-bucket/benchbox/
```

This requires an S3 bucket (via `staging_root` or `s3_bucket`). Not compatible
with `--tuning tuned`.

## S3 Data Staging

BenchBox stages benchmark data to S3 before querying:

```bash
benchbox run --platform athena --benchmark tpch --scale 1.0 \
  --output s3://my-bucket/benchmarks/tpch_sf1/
```

The `--output` option specifies the data location.

### Recommended Data Format

For best performance, use Parquet with Snappy compression:

```bash
benchbox run --platform athena --benchmark tpch \
  --table-format parquet \
  --compression snappy
```

## Cost Optimization

### Reduce Data Scanned

Athena charges per TB scanned. The source of truth is
`benchbox/core/cost/pricing_data.yaml`: the current list rate is $5 per TB in
the documented regions and $9 per TB in `sa-east-1`. Optimize costs with:

1. **Columnar formats** - Parquet/ORC scan only needed columns
2. **Partitioning** - Partition by date/region for predicate pushdown
3. **Compression** - Smaller files = less data scanned

### Workgroup Limits

Set query data scan limits in your workgroup:

```bash
aws athena create-work-group \
  --name benchbox \
  --configuration "BytesScannedCutoffPerQuery=10737418240"
```

The cutoff value is in bytes, so 10737418240 is a 10 GB limit per query.

### Cost Estimation

Rough planning figures at the current list price (`benchbox/core/cost/pricing_data.yaml` is authoritative):

| Scale Factor | Data Size | Est. Full Run Cost |
|--------------|-----------|-------------------|
| 0.1 | ~100 MB | < $0.01 |
| 1.0 | ~1 GB | ~$0.02 |
| 10.0 | ~10 GB | ~$0.20 |
| 100.0 | ~100 GB | ~$2.00 |

## Performance Tips

### Use Partitioning

```sql
CREATE EXTERNAL TABLE lineitem (...)
PARTITIONED BY (l_shipdate STRING)
STORED AS PARQUET
LOCATION 's3://bucket/lineitem/'
```

### Optimize File Sizes

- **Minimum**: 128 MB per file
- **Optimal**: 256 MB - 1 GB per file
- Avoid many small files

## Limitations

- **Query timeout**: 30 minutes maximum
- **Result size**: 2 GB maximum per query
- **Concurrent queries**: Limited by workgroup (default: 20)
- **No updates**: Read-only queries on S3 data

## Troubleshooting

### Access Denied

```bash
aws s3 ls s3://your-bucket/
```

The command verifies S3 permissions. The IAM policy must include:

- `s3:GetObject`
- `s3:ListBucket`
- `s3:PutObject` (for results)
- `athena:StartQueryExecution`
- `glue:GetTable` and `glue:GetDatabase`

### Query Timeout

Athena enforces its own query timeout (see Limitations). BenchBox has no
setting to change it; raise the DML query timeout through an AWS service
quota increase.

### Data Not Found

```bash
aws glue get-table --database-name benchmarks --name lineitem

MSCK REPAIR TABLE lineitem;
```

The first command verifies that the Glue table exists. Run `MSCK REPAIR TABLE` for partitioned tables so Athena discovers their partitions.

## Related Documentation

- [BigQuery Platform](bigquery.md) - Google Cloud alternative
- [Redshift Platform](redshift.md) - AWS data warehouse
- [Trino Platform](trino.md) - Self-hosted Trino
- [Cloud Storage Guide](../guides/cloud-storage.md)
