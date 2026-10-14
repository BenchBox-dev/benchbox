<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Amazon Athena for Apache Spark Platform

```{tags} intermediate, guide, athena-spark, cloud-platform
```

Athena for Apache Spark is AWS's interactive Spark service with sub-second startup times. Unlike EMR Serverless or Glue, it uses a notebook-style execution model with persistent sessions.

## Features

- **Sub-second Startup** - Pre-provisioned Spark capacity for instant execution
- **Interactive Sessions** - Notebook-style execution with persistent state
- **Serverless** - No cluster management required
- **S3 Integration** - Native S3 and Glue Data Catalog support
- **Session-based** - Efficient for multiple queries in a session

## Installation

This installs the AWS client dependency, `boto3`, via the `cloud-spark-aws` extra.

```bash
uv add benchbox --extra cloud-spark-aws
```

## Prerequisites

1. **Spark-enabled Athena workgroup** (created via Console or CLI)
2. **S3 bucket** for data staging
3. **AWS credentials** configured:
   - AWS CLI: `aws configure`
   - Environment variables: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`
   - IAM role (on EC2/ECS/Lambda)

## Configuration

Athena Spark needs a Spark-enabled workgroup (`compute_resource`) and an S3 staging location (`s3_staging_dir`).
Pass both as platform options, as in the examples below. BenchBox does not read them from environment variables.
AWS credentials come from the standard AWS sources listed in the Authentication section below.

### CLI Usage

The commands cover, in order: basic usage, a custom region, recording the Spark engine version the workgroup
runs, a custom DPU configuration, and a dry run that previews queries.

```bash
benchbox run --platform athena-spark --benchmark tpch --scale 1.0 \
  --platform-option compute_resource=my-spark-workgroup \
  --platform-option s3_staging_dir=s3://my-bucket/benchbox

benchbox run --platform athena-spark --benchmark tpch --scale 1.0 \
  --platform-option compute_resource=my-spark-workgroup \
  --platform-option s3_staging_dir=s3://my-bucket/benchbox \
  --platform-option region=eu-west-1

benchbox run --platform athena-spark --benchmark tpch --scale 1.0 \
  --platform-option compute_resource=my-spark-workgroup \
  --platform-option s3_staging_dir=s3://my-bucket/benchbox \
  --platform-option "runtime_version=PySpark engine version 3"

benchbox run --platform athena-spark --benchmark tpch --scale 1.0 \
  --platform-option compute_resource=my-spark-workgroup \
  --platform-option s3_staging_dir=s3://my-bucket/benchbox \
  --platform-option coordinator_dpu_size=2 \
  --platform-option max_concurrent_dpus=40

benchbox run --platform athena-spark --benchmark tpch --dry-run ./preview \
  --platform-option compute_resource=my-spark-workgroup \
  --platform-option s3_staging_dir=s3://my-bucket/benchbox
```

### Platform Options

| Option | Default | Description |
|--------|---------|-------------|
| `compute_resource` | *required* | Spark-enabled Athena workgroup name (`workgroup` is a deprecated alias) |
| `s3_staging_dir` | *required* | S3 path for data staging |
| `region` | us-east-1 | AWS region |
| `database` | benchbox | Glue Data Catalog database |
| `runtime_version` | (not recorded) | Spark engine version you expect the workgroup to run (e.g. `PySpark engine version 3`), recorded in results as requested. The workgroup sets the engine version; BenchBox never sends it when starting a session. `engine_version` is a deprecated alias |
| `coordinator_dpu_size` | 1 | Coordinator DPU size |
| `max_concurrent_dpus` | 20 | Maximum concurrent DPUs |
| `default_executor_dpu_size` | 1 | Default executor DPU size |
| `session_idle_timeout_minutes` | 15 | Session idle timeout |
| `timeout_minutes` | 60 | Calculation timeout |
| `driver_version` | (latest) | Pin the Python driver package version (e.g. `1.2.0`) |
| `driver_auto_install` | false | Auto-install the requested driver version via uv if missing |

### Testing a Specific pyathena Version

```bash
benchbox run --platform athena-spark --benchmark tpch \
  --platform-option driver_version=3.0.0 \
  --platform-option driver_auto_install=true \
  --platform-option compute_resource=my-spark-workgroup \
  --platform-option s3_staging_dir=s3://my-bucket/benchbox
```

The driver package for Athena Spark is `pyathena`.

See {ref}`driver-version-management` for the full guide, including why `uv run` may
revert a manually-installed version and how to work around it.

## Python API

The example builds the adapter from the same settings the CLI uses, starts a session, creates the schema, loads
data to S3 and creates tables, executes a query via the session, and finally terminates the session.
`from_config` accepts `compute_resource`; the constructor takes the workgroup as `workgroup=`.

```python
from benchbox.platforms.aws import AthenaSparkAdapter

adapter = AthenaSparkAdapter.from_config(
    {
        "compute_resource": "my-spark-workgroup",
        "s3_staging_dir": "s3://my-bucket/benchbox",
        "region": "us-east-1",
    }
)

adapter.create_connection()

adapter.create_schema("tpch_benchmark")

adapter.load_data(
    tables=["lineitem", "orders", "customer"],
    source_dir="/path/to/tpch/data",
)

result = adapter.execute_query("SELECT COUNT(*) FROM lineitem")

adapter.close()
```

## Execution Model

Athena Spark uses a session-based execution model:

1. **Session Start** - Start a session in a Spark-enabled workgroup
2. **Calculation Submit** - Submit SQL or PySpark calculations
3. **Result Retrieval** - Results written to S3 automatically
4. **Session End** - Terminate session when complete (or auto-terminate on idle)

## Creating a Spark-Enabled Workgroup

### Via AWS Console

1. Go to Athena Console
2. Navigate to **Workgroups**
3. Click **Create workgroup**
4. Select **Apache Spark** as the engine type
5. Configure:
   - Name: e.g., "spark-benchbox"
   - Query result location: s3://your-bucket/athena-results/
   - DPU allocation settings
6. Create workgroup

### Via AWS CLI

```bash
aws athena create-work-group \
  --name spark-benchbox \
  --configuration '{"EngineVersion": {"SelectedEngineVersion": "PySpark engine version 3"}}' \
  --description "Spark workgroup for BenchBox"
```

## DPU Configuration

| Size | vCPUs | Memory | Use Case |
|------|-------|--------|----------|
| 1 DPU | 4 | 16 GB | Development, small datasets |
| 2 DPU | 8 | 32 GB | General benchmarking |
| 4 DPU | 16 | 64 GB | Large scale factors |

## Authentication

Athena Spark uses standard AWS authentication:

### AWS CLI (Development)

```bash
aws configure
```

### Environment Variables (Automation)

```bash
export AWS_ACCESS_KEY_ID=your-access-key
export AWS_SECRET_ACCESS_KEY=your-secret-key
export AWS_REGION=us-east-1
```

### IAM Role (EC2/ECS/Lambda)

No configuration needed - automatically uses instance/task role.

## Cost Estimation

Athena Spark uses DPU-hour billing:

| Resource | Price |
|----------|-------|
| DPU-hour | ~$0.35 |

| Scale Factor | Data Size | Est. Runtime | Est. Cost* |
|--------------|-----------|--------------|------------|
| 0.01 | ~10 MB | ~15 min | ~$0.10 |
| 1.0 | ~1 GB | ~45 min | ~$0.75 |
| 10.0 | ~10 GB | ~2 hours | ~$3.00 |
| 100.0 | ~100 GB | ~5 hours | ~$10.00 |

*Estimates based on 2 DPUs. Actual costs vary.

## Session vs Batch Comparison

| Aspect | Athena Spark (Sessions) | EMR Serverless (Batches) |
|--------|------------------------|--------------------------|
| Startup | Sub-second | Seconds to minutes |
| Model | Interactive sessions | Batch jobs |
| State | Persists in session | Stateless per job |
| Use Case | Ad-hoc, exploration | Production pipelines |
| Billing | DPU-hour | vCPU-hour + GB-hour |

## Troubleshooting

### Common Issues

**Workgroup not Spark-enabled:**
- Athena Spark requires a workgroup with Apache Spark engine
- SQL workgroups will not work
- Create a new Spark-enabled workgroup

**Session fails to start:**
- Check workgroup DPU limits
- Verify IAM permissions for Athena and S3
- Check service quotas

**Calculation timeout:**
- Increase `timeout_minutes` for large scale factors
- Check session hasn't auto-terminated
- Review calculation logs in CloudWatch

**S3 access denied:**
- Check S3 bucket permissions
- Verify IAM role has S3 read/write access
- Ensure bucket is in same region

## Related

- [Amazon Athena SQL](athena.md) - SQL-based Athena (Trino)
- [EMR Serverless](emr-serverless.md) - Batch Spark workloads
- [AWS Glue](aws-glue.md) - ETL-focused Spark
- [Cloud Platforms Overview](cloud-platforms.md) - All cloud platforms
