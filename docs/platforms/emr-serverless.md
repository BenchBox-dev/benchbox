<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Amazon EMR Serverless Platform

```{tags} intermediate, guide, emr-serverless, cloud-platform
```

Amazon EMR Serverless is AWS's serverless deployment option for running Apache Spark without managing clusters. BenchBox integrates with EMR Serverless to execute benchmarks with automatic scaling and sub-second startup times.

## Features

- **Serverless** - No clusters to manage, automatic scaling
- **Fast startup** - Sub-second cold starts with pre-initialized capacity
- **Cost-effective** - Pay only for vCPU-hours and memory-GB-hours used
- **Integrated** - Native S3 and Glue Data Catalog integration
- **Spark optimization** - Uses cloud-spark shared infrastructure for config tuning

## Installation

```bash
uv add benchbox --extra cloud-spark-aws
```

This installs the AWS client dependency, `boto3`, via the `cloud-spark-aws` extra.

## Prerequisites

1. **AWS Account** with EMR Serverless access
2. **S3 Bucket** for data staging and job scripts
3. **IAM Execution Role** with the following permissions:
   - `emr-serverless:StartApplication`, `emr-serverless:GetApplication`
   - `emr-serverless:StartJobRun`, `emr-serverless:GetJobRun`
   - `s3:GetObject`, `s3:PutObject`, `s3:ListBucket`
   - `glue:GetDatabase`, `glue:CreateDatabase`, `glue:GetTable`, `glue:CreateTable`
4. **AWS Credentials** configured via CLI, environment, or IAM role

## Configuration

### CLI Usage

The BenchBox CLI does not accept the EMR Serverless staging, role, or application settings, so run benchmarks
through the Python API described below. The CLI can still preview the generated queries without connecting to AWS:

```bash
benchbox run --platform emr-serverless --benchmark tpch --dry-run ./preview
```

### Adapter Options

These are `EMRServerlessAdapter` keyword arguments. Set `application_id` to use an existing application, or set
`create_application=True` to create one.

| Option | Default | Description |
|--------|---------|-------------|
| `s3_staging_dir` | *required* | S3 path for data staging |
| `execution_role_arn` | *required* | IAM role ARN for job execution |
| `application_id` | - | Existing EMR Serverless application ID |
| `create_application` | false | Create new application if ID not provided |
| `application_name` | - | Name for a new application |
| `region` | us-east-1 | AWS region |
| `database` | benchbox | Glue Data Catalog database |
| `release_label` | emr-7.0.0 | EMR release label |
| `timeout_minutes` | 60 | Job timeout in minutes |

## Python API

```python
from benchbox.platforms.aws import EMRServerlessAdapter

adapter = EMRServerlessAdapter(
    application_id="00f12345abc67890",
    s3_staging_dir="s3://my-bucket/benchbox/",
    execution_role_arn="arn:aws:iam::123456789012:role/EMRRole",
    region="us-east-1",
    database="tpch_benchmark",
)

adapter = EMRServerlessAdapter(
    s3_staging_dir="s3://my-bucket/benchbox/",
    execution_role_arn="arn:aws:iam::123456789012:role/EMRRole",
    create_application=True,
    application_name="benchbox-tpch",
)

adapter.create_schema("tpch_sf1")

adapter.load_data(
    tables=["lineitem", "orders", "customer"],
    source_dir="/path/to/tpch/data",
)

result = adapter.execute_query("SELECT COUNT(*) FROM lineitem")

adapter.close()
```

The first adapter uses an existing application, and the second creates a new one. `create_schema` creates the database in the Glue Data Catalog. `load_data` uploads data to S3 and creates Glue tables. Each query is submitted as a job run. `close` cleans up and optionally stops the application.

## Execution Model

EMR Serverless executes benchmarks as job runs within an application:

1. **Application** - Container for job runs with auto-start/stop
2. **Job Submission** - Query script uploaded to S3 and submitted
3. **Automatic Scaling** - Resources provisioned based on workload
4. **Result Retrieval** - Results read from S3 output location
5. **Resource Tracking** - vCPU-hours and memory-GB-hours logged

## Pre-Initialized Capacity

For sub-second startup, configure pre-initialized workers:

```python
adapter = EMRServerlessAdapter(
    application_id="00f12345abc67890",
    s3_staging_dir="s3://bucket/benchbox/",
    execution_role_arn="arn:aws:iam::123456789012:role/EMRRole",
    initial_capacity={
        "Driver": {
            "workerCount": 1,
            "workerConfiguration": {
                "cpu": "4 vCPU",
                "memory": "16 GB"
            }
        },
        "Executor": {
            "workerCount": 4,
            "workerConfiguration": {
                "cpu": "4 vCPU",
                "memory": "16 GB"
            }
        }
    },
)
```

**Note**: Pre-initialized capacity is charged even when idle.

## Cost Estimation

| Resource | Price |
|----------|-------|
| vCPU-hour | ~$0.052624 |
| Memory GB-hour | ~$0.0057785 |

| Scale Factor | Data Size | Est. Runtime | Est. Cost |
|--------------|-----------|--------------|-----------|
| 0.01 | ~10 MB | ~20 min | ~$0.05 |
| 1.0 | ~1 GB | ~1 hour | ~$0.50 |
| 10.0 | ~10 GB | ~3 hours | ~$3.00 |
| 100.0 | ~100 GB | ~8 hours | ~$16.00 |

*Estimates based on auto-scaling. Actual costs vary.*

## IAM Role Policy

Minimum required IAM policy for the execution role:

```json
{
  "Version": "2012-10-17",
  "Statement": [
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
        "glue:GetDatabase",
        "glue:CreateDatabase",
        "glue:GetTable",
        "glue:CreateTable",
        "glue:UpdateTable"
      ],
      "Resource": "*"
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

Also add a trust relationship for EMR Serverless:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "emr-serverless.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

## Troubleshooting

### Common Issues

**Application fails to start:**
- Verify IAM role has correct trust relationship
- Check region has EMR Serverless available
- Review application configuration in console

**Job fails immediately:**
- Check S3 bucket permissions
- Verify execution role has required permissions
- Review job logs in S3 or CloudWatch

**Slow startup:**
- Configure pre-initialized capacity for warm workers
- Check application auto-start is enabled
- Review network configuration (VPC settings)

**Query timeout:**
- Increase `timeout_minutes` for large scale factors
- Check for data skew in queries
- Review Spark configuration

## Comparison with Other Platforms

| Aspect | EMR Serverless | AWS Glue | EMR on EC2 |
|--------|---------------|----------|------------|
| Cluster management | None | None | Full control |
| Startup time | Sub-second* | 30-60s | Minutes |
| Billing | vCPU + memory hours | DPU-hours | Instance hours |
| Use case | Interactive | ETL batch | Long-running |
| Scaling | Automatic | Automatic | Manual/Auto |

*With pre-initialized capacity

## Related

- [AWS Glue Platform](aws-glue.md) - Serverless ETL with Glue
- [Apache Spark Platform](spark.md) - Local/cluster Spark usage
- [Cloud Platforms Overview](cloud-platforms.md) - All cloud platforms
