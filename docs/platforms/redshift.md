<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Redshift Platform

```{tags} intermediate, guide, redshift, cloud-platform
```

Amazon Redshift is a fully managed petabyte-scale data warehouse service in AWS. BenchBox provides comprehensive support for Redshift benchmarking with S3-based data loading via COPY command.

## Features

- **Columnar storage** - Optimized for analytics
- **Massively parallel processing** - Distributed query execution
- **RA3 nodes** - Managed storage with S3 backing
- **Concurrency scaling** - Automatic burst capacity
- **Redshift Spectrum** - Query S3 directly

## Prerequisites

- Amazon Redshift cluster (provisioned or Serverless)
- IAM role with S3 access for COPY command
- S3 bucket for data staging
- VPC security group allowing inbound connections

## Installation

```bash
pip install redshift_connector boto3

pip install "benchbox[redshift]"
```

## Configuration

### Interactive Setup (Recommended)

```bash
benchbox setup --platform redshift
```

This saves the endpoint, port, database, username, password, schema, S3
bucket, IAM role and AWS region in the `redshift:` section of
`~/.benchbox/credentials.yaml`. `benchbox run` reads them from there; the
endpoint, username and password are not `--platform-option` keys.

### Environment Variables

`benchbox setup --platform redshift` can fill in its prompts from these
variables. `benchbox run` does not read them.

```bash
export REDSHIFT_HOST=your-cluster.abc123xyz.us-east-1.redshift.amazonaws.com
export REDSHIFT_USERNAME=admin
export REDSHIFT_PASSWORD=your_password
export REDSHIFT_DATABASE=dev
export REDSHIFT_IAM_ROLE=arn:aws:iam::123456789:role/RedshiftS3Access
```

### CLI Options

```bash
benchbox setup --platform redshift

benchbox run --platform redshift --benchmark tpch --scale 1.0 \
  --platform-option compute_resource=my-cluster
```

## Platform Options

The **Set with** column shows where each setting comes from: the saved
credentials (`benchbox setup`, or the `redshift:` section of
`~/.benchbox/credentials.yaml`) or a `--platform-option` key on `benchbox run`.

| Option | Set with | Default | Description |
|--------|----------|---------|-------------|
| `host` | setup | (none) | Cluster or workgroup endpoint |
| `port` | setup | 5439 | Connection port |
| `username` | setup | (none) | Database username |
| `password` | setup | (none) | Database password |
| `schema` | setup | public | Schema name |
| `aws_region` | setup | us-east-1 | AWS region |
| `statement_timeout` | credentials file | 0 (none) | Statement timeout in milliseconds |
| `sslmode` | credentials file | require | SSL mode for the connection |
| `compute_resource` | `--platform-option` | (none) | Workgroup or cluster name (see Redshift Serverless below) |
| `iam_role` | `--platform-option` or setup | (none) | IAM role ARN for COPY |
| `s3_bucket` | `--platform-option` or setup | (none) | S3 bucket for staging |
| `staging_root` | `--platform-option` | (none) | S3 staging path (`s3://bucket/path`) |
| `driver_version` | `--platform-option` | (latest) | Pin the redshift_connector package version (e.g. `2.1.3`) |
| `driver_auto_install` | `--platform-option` | false | Auto-install the requested driver version via uv if missing |

### Testing a Specific Redshift Connector Version

```bash
benchbox run --platform redshift --benchmark tpch \
  --platform-option driver_version=2.1.3 \
  --platform-option driver_auto_install=true
```

The driver package for Redshift is `redshift-connector`.

See {ref}`driver-version-management` for the full guide, including why `uv run` may
revert a manually-installed version and how to work around it.

## Authentication Methods

### Standard Authentication

BenchBox connects with a database username and password. Save them with
`benchbox setup --platform redshift`, then run:

```bash
benchbox run --platform redshift --benchmark tpch --scale 1.0
```

### AWS Credentials for S3 Staging

The database connection always uses the saved username and password; BenchBox
does not support IAM database authentication. AWS credentials are used only to
stage data in S3 for `COPY`. Setup can save either an IAM role ARN
(`iam_role`) or an access key pair (`aws_access_key_id` and
`aws_secret_access_key`); with neither, `COPY` uses the cluster's default IAM
role.

### Redshift Serverless

```bash
benchbox run --platform redshift --benchmark tpch \
  --platform-option compute_resource=default
```

`compute_resource` names the workgroup when the host is a Serverless endpoint
(`*.redshift-serverless.amazonaws.com`) and the cluster when it is a
provisioned endpoint (`*.redshift.amazonaws.com`). With any other host, pass
`workgroup_name` or `cluster_identifier` instead.

## Usage Examples

### Basic Benchmark

```bash
benchbox run --platform redshift --benchmark tpch --scale 1.0
```

### With S3 Staging

Staging through S3 configures the COPY command. The IAM role is part of the stored Redshift credentials:

```bash
benchbox run --platform redshift --benchmark tpch --scale 100.0 \
  --output s3://your-bucket/benchbox/
```

### With Tuning

This applies distribution and sort keys:

```bash
benchbox run --platform redshift --benchmark tpch --scale 10.0 \
  --tuning tuned
```

### Python API

```python
from benchbox import TPCH
from benchbox.platforms.redshift import RedshiftAdapter

adapter = RedshiftAdapter(
    host="cluster.abc123.us-east-1.redshift.amazonaws.com",
    user="admin",
    password="secure_password",
    database="dev",
    iam_role="arn:aws:iam::123456789:role/RedshiftS3Access",
)

benchmark = TPCH(scale_factor=1.0)
benchmark.generate_data()
adapter.load_benchmark(benchmark)
results = adapter.run_benchmark(benchmark)
```

## Node Types

### RA3 Nodes (Recommended)

| Node Type | vCPU | Memory | Storage |
|-----------|------|--------|---------|
| ra3.xlplus | 4 | 32 GB | Managed |
| ra3.4xlarge | 12 | 96 GB | Managed |
| ra3.16xlarge | 48 | 384 GB | Managed |

### DC2 Nodes (Dense Compute)

| Node Type | vCPU | Memory | Storage |
|-----------|------|--------|---------|
| dc2.large | 2 | 15 GB | 160 GB |
| dc2.8xlarge | 32 | 244 GB | 2.56 TB |

### Serverless

Use serverless for variable workloads:

```bash
benchbox run --platform redshift --benchmark tpch \
  --platform-option compute_resource=benchbox-wg
```

## Performance Features

### Distribution Keys

BenchBox applies distribution keys with `--tuning tuned`. Large tables use key distribution, and small tables use `DISTSTYLE ALL`:

```sql
CREATE TABLE lineitem (...)
DISTKEY (l_orderkey)
SORTKEY (l_shipdate);

CREATE TABLE nation (...)
DISTSTYLE ALL;
```

Redshift tables are vacuumed and analyzed after loading by the existing
maintenance pass, which `auto_vacuum` and `auto_analyze` control (both on by
default). A tuned run adds an `ANALYZE <table>` after a table with a tuning
entry loads, only when `auto_analyze` is `false`; that time is reported as
`phases.post_load_maintenance` and is not counted in data-loading time.

### Sort Keys

A compound sort key suits queries that filter on the leading columns. An interleaved sort key suits queries that filter on multiple columns:

```sql
CREATE TABLE orders (...)
COMPOUND SORTKEY (o_orderdate, o_custkey);

CREATE TABLE lineitem (...)
INTERLEAVED SORTKEY (l_shipdate, l_receiptdate);
```

### WLM Configuration

BenchBox can route its queries to a WLM queue by setting `query_group` for the
session. Set the queue name with the `wlm_query_queue_name` argument of
`RedshiftAdapter` in the Python API; `benchbox run` has no option for it.
Configure the queues themselves, and concurrency scaling, in the cluster's WLM
parameter group.

## Data Loading

### COPY from S3 (Recommended)

```bash
benchbox run --platform redshift --benchmark tpch --scale 10.0 \
  --output s3://bucket/benchbox/
```

### IAM Role Setup

These commands create an IAM role for COPY, attach an S3 read policy, and associate the role with the cluster:

```bash
aws iam create-role --role-name RedshiftS3Access \
  --assume-role-policy-document file://trust-policy.json

aws iam attach-role-policy --role-name RedshiftS3Access \
  --policy-arn arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess

aws redshift modify-cluster-iam-roles \
  --cluster-identifier my-cluster \
  --add-iam-roles arn:aws:iam::123456789:role/RedshiftS3Access
```

### Direct Insert (Small Datasets)

For datasets under 1GB, direct INSERT is used automatically:

```bash
benchbox run --platform redshift --benchmark tpch --scale 0.01
```

## Cost Optimization

### Concurrency Scaling

Concurrency scaling adds burst capacity. Turn it on per WLM queue in the
cluster's parameter group; BenchBox does not change it.

### Pause/Resume

Pause the cluster when not in use, and resume it before a benchmark:

```bash
aws redshift pause-cluster --cluster-identifier my-cluster

aws redshift resume-cluster --cluster-identifier my-cluster
```

### Serverless RPU

Set the workgroup's base and maximum RPU capacity in the Redshift console or
with `aws redshift-serverless update-workgroup`; BenchBox does not change it.
Point BenchBox at the workgroup with `--platform-option compute_resource=benchbox`.

## Troubleshooting

### Connection Refused

Check the cluster status, verify that the security group allows your IP, and add your IP to the security group if it does not:

```bash
aws redshift describe-clusters --cluster-identifier my-cluster

aws ec2 describe-security-groups --group-ids sg-xxx

aws ec2 authorize-security-group-ingress \
  --group-id sg-xxx \
  --protocol tcp \
  --port 5439 \
  --cidr YOUR_IP/32
```

### COPY Failed

Check the COPY errors, verify that the IAM role is attached, and test S3 access:

```bash
SELECT * FROM stl_load_errors ORDER BY starttime DESC LIMIT 10;

aws redshift describe-clusters --cluster-identifier my-cluster \
  --query 'Clusters[0].IamRoles'

SELECT * FROM svl_s3list
WHERE bucket = 'your-bucket';
```

### Permission Denied

Grant the required permissions:

```sql
GRANT CREATE ON DATABASE dev TO benchbox_user;
GRANT CREATE ON SCHEMA public TO benchbox_user;
GRANT ALL ON ALL TABLES IN SCHEMA public TO benchbox_user;
```

### Query Timeout

Increase the statement timeout. The value is in milliseconds, so `3600000` is
1 hour. `statement_timeout` is not a `--platform-option` key; set it in the
`redshift:` section of `~/.benchbox/credentials.yaml`:

```yaml
redshift:
  statement_timeout: 3600000
```

### Disk Space Exceeded

Check disk usage, then vacuum to reclaim space:

```bash
SELECT owner, host, diskno, used, capacity
FROM stv_partitions
ORDER BY used DESC;

VACUUM FULL lineitem;
```

## Related Documentation

- [BigQuery](bigquery.md) - Google Cloud alternative
- [Snowflake](snowflake.md) - Multi-cloud warehouse
- [Athena](athena.md) - Serverless S3 queries
- [Platform Comparison](comparison-matrix.md)
