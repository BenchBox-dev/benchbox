<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# AWS Glue Platform

```{tags} intermediate, guide, aws-glue, cloud-platform
```

AWS Glue is a fully managed, serverless ETL service that runs Apache Spark for distributed data processing. BenchBox integrates with Glue to execute benchmarks as batch jobs, leveraging the Glue Data Catalog for metadata and S3 for data storage.

## Features

- **Serverless Spark** - Automatic cluster provisioning and scaling
- **Pay-per-use** - Charged per DPU-hour (~$0.44/DPU-hour)
- **Glue Data Catalog** - Centralized metadata compatible with Athena/EMR
- **S3 integration** - Native support for S3 data lakes
- **Spark optimization** - Includes cloud-spark shared infrastructure for config tuning

## Installation

This installs the AWS client dependency, `boto3`, via the `cloud-spark-aws` extra.

```bash
uv add benchbox --extra cloud-spark-aws
```

## Prerequisites

1. **AWS Account** with Glue service access
2. **S3 Bucket** for data staging and job scripts
3. **IAM Role** with the following permissions:
   - `glue:CreateJob`, `glue:StartJobRun`, `glue:GetJobRun`
   - `glue:CreateDatabase`, `glue:GetDatabase`, `glue:CreateTable`
   - `s3:GetObject`, `s3:PutObject`, `s3:ListBucket`
4. **AWS Credentials** configured via CLI, environment, or IAM role

## Configuration

### Environment Variables

`GLUE_S3_STAGING_DIR` and `GLUE_JOB_ROLE` are required. `AWS_REGION`, `AWS_PROFILE`, `GLUE_DATABASE` and
`GLUE_VERSION` are optional.

```bash
export GLUE_S3_STAGING_DIR=s3://your-bucket/benchbox/
export GLUE_JOB_ROLE=arn:aws:iam::123456789012:role/GlueBenchmarkRole

export AWS_REGION=us-east-1
export AWS_PROFILE=default
export GLUE_DATABASE=benchbox
export GLUE_WORKER_TYPE=G.1X
export GLUE_NUM_WORKERS=2
export GLUE_VERSION=4.0
```

### CLI Usage

The first command is basic usage. The second uses custom workers. The third is a dry run that previews queries.

```bash
benchbox run --platform glue --benchmark tpch --scale 1.0 \
  --platform-option s3_staging_dir=s3://bucket/benchbox/ \
  --platform-option job_role=arn:aws:iam::123456789012:role/GlueRole

benchbox run --platform glue --benchmark tpch --scale 10.0 \
  --platform-option s3_staging_dir=s3://bucket/benchbox/ \
  --platform-option job_role=arn:aws:iam::123456789012:role/GlueRole \
  --platform-option worker_type=G.2X \
  --platform-option number_of_workers=10

benchbox run --platform glue --benchmark tpch --dry-run ./preview \
  --platform-option s3_staging_dir=s3://bucket/benchbox/ \
  --platform-option job_role=arn:aws:iam::123456789012:role/GlueRole
```

### Platform Options

| Option | Default | Description |
|--------|---------|-------------|
| `s3_staging_dir` | *required* | S3 path for data staging |
| `job_role` | *required* | IAM role ARN for job execution |
| `region` | us-east-1 | AWS region |
| `database` | benchbox | Glue Data Catalog database |
| `worker_type` | G.1X | Worker type (G.025X, G.1X, G.2X, Z.2X) |
| `number_of_workers` | 2 | Number of workers (min 2 for standard) |
| `glue_version` | 4.0 | Glue version (3.0 or 4.0) |
| `timeout_minutes` | 60 | Job timeout in minutes |

### Worker Types

| Type | vCPU | Memory | DPU | Cost/Hour | Best For |
|------|------|--------|-----|-----------|----------|
| G.025X | 0.25 | 0.5GB | 0.25 | ~$0.11 | Development |
| G.1X | 4 | 16GB | 1 | ~$0.44 | Standard workloads |
| G.2X | 8 | 32GB | 2 | ~$0.88 | Memory-intensive |
| Z.2X | 8 | 64GB | 2 | ~$0.88 | ML workloads |

## Python API

```python
from benchbox.platforms.aws import AWSGlueAdapter

adapter = AWSGlueAdapter(
    s3_staging_dir="s3://my-bucket/benchbox/",
    job_role="arn:aws:iam::123456789012:role/GlueRole",
    region="us-east-1",
    database="tpch_benchmark",
    worker_type="G.1X",
    number_of_workers=4,
)

adapter.create_schema("tpch_sf1")

adapter.load_data(
    tables=["lineitem", "orders", "customer"],
    source_dir="/path/to/tpch/data",
)

result = adapter.execute_query("SELECT COUNT(*) FROM lineitem")

adapter.close()
```

## Execution Model

Unlike interactive query services, Glue executes benchmarks as batch jobs:

1. **Job Creation** - BenchBox creates a Glue job with optimized Spark configuration
2. **Script Upload** - Query execution script is uploaded to S3
3. **Job Submission** - Job run is started with the query as an argument
4. **Polling** - BenchBox polls for job completion
5. **Result Retrieval** - Results are read from S3 output location

This batch model means:
- Longer startup times (30-60 seconds per job)
- Higher throughput for large queries
- DPU-hour billing for entire job duration
- Results persisted in S3

## Spark Configuration

BenchBox automatically optimizes Spark configuration based on benchmark type and scale factor. The automatic
configuration includes Adaptive Query Execution (AQE) settings, shuffle partition tuning, memory allocation and join
optimization. For TPC-H at SF=10 with 4 G.1X workers, it sets `spark.sql.shuffle.partitions = 200`,
`spark.sql.adaptive.enabled = true` and `spark.sql.adaptive.skewJoin.enabled = true`.

## Cost Estimation

| Scale Factor | Data Size | Workers | Est. Runtime | Est. Cost |
|--------------|-----------|---------|--------------|-----------|
| 0.01 | ~10 MB | 2x G.1X | ~10 min | ~$0.15 |
| 1.0 | ~1 GB | 2x G.1X | ~30 min | ~$0.44 |
| 10.0 | ~10 GB | 4x G.1X | ~2 hours | ~$3.52 |
| 100.0 | ~100 GB | 10x G.2X | ~4 hours | ~$35.20 |

*Estimates based on standard TPC-H workloads. Actual costs vary.*

## IAM Role Policy

Minimum required IAM policy for the Glue job role:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "glue:CreateJob",
        "glue:DeleteJob",
        "glue:GetJob",
        "glue:StartJobRun",
        "glue:GetJobRun",
        "glue:GetJobRuns",
        "glue:CreateDatabase",
        "glue:GetDatabase",
        "glue:CreateTable",
        "glue:GetTable",
        "glue:UpdateTable",
        "glue:DeleteTable"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::your-bucket",
        "arn:aws:s3:::your-bucket/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:*:*:*"
    }
  ]
}
```

## Troubleshooting

### Common Issues

**Job fails to start:**
- Verify IAM role has correct trust relationship for Glue
- Check S3 bucket permissions
- Ensure Glue service is available in your region

**Slow job startup:**
- First job in a session has cold start (~30-60s)
- Subsequent jobs use warm pools if available
- Use G.1X or larger for production

**Query timeout:**
- Increase `timeout_minutes` for large scale factors
- Add more workers for parallel execution
- Check Glue console for detailed job metrics

**Data not found:**
- Verify S3 paths are correct (s3:// prefix required)
- Check Glue Data Catalog for table definitions
- Ensure table format matches uploaded files (parquet)

## Comparison with Athena

| Aspect | AWS Glue | Amazon Athena |
|--------|----------|---------------|
| Execution | Batch jobs (Spark) | Interactive queries (Trino) |
| Billing | Per DPU-hour | Per TB scanned |
| Startup | 30-60 seconds | Instant |
| Best for | ETL, large batch | Ad-hoc analytics |
| Spark features | Full Spark SQL | Trino SQL |
| Catalog | Glue Data Catalog | Glue Data Catalog |

## Related

- [Apache Spark Platform](spark.md) - Local/cluster Spark usage
- [Amazon Athena Platform](athena.md) - Serverless Trino queries
- [Cloud Platforms Overview](cloud-platforms.md) - All cloud platforms
