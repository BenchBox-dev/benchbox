<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Google Cloud Dataproc Platform

```{tags} intermediate, guide, dataproc, cloud-platform
```

Google Cloud Dataproc is Google Cloud's fully managed Apache Spark and Hadoop service. BenchBox integrates with Dataproc to execute benchmarks on managed clusters, supporting both persistent and ephemeral cluster modes.

## Features

- **Managed clusters** - Automatic provisioning and monitoring
- **Flexible pricing** - Per-second billing with preemptible VM support
- **GCS integration** - Native Google Cloud Storage support
- **Auto-scaling** - Dynamic cluster sizing based on workload
- **Hive Metastore** - Managed metadata service support

## Installation

```bash
uv add benchbox --extra cloud-spark-gcp
```

This installs the Dataproc dependencies, including `google-cloud-dataproc` and `google-cloud-storage`.

## Prerequisites

1. **GCP Project** with Dataproc API enabled
2. **GCS Bucket** for data staging
3. **Service Account** with the following roles:
   - `roles/dataproc.editor` - Create/manage clusters and jobs
   - `roles/storage.objectAdmin` - Read/write GCS objects
4. **Application Default Credentials** configured

## Configuration

### Environment Variables

```bash
export DATAPROC_PROJECT_ID=my-project-id
export DATAPROC_GCS_STAGING_DIR=gs://your-bucket/benchbox/

export DATAPROC_REGION=us-central1
export DATAPROC_CLUSTER_NAME=my-cluster
export DATAPROC_DATABASE=benchbox
export DATAPROC_MASTER_TYPE=n2-standard-4
export DATAPROC_WORKER_TYPE=n2-standard-4
export DATAPROC_NUM_WORKERS=2
export DATAPROC_USE_PREEMPTIBLE=false
export DATAPROC_EPHEMERAL=false
```

`DATAPROC_PROJECT_ID` and `DATAPROC_GCS_STAGING_DIR` are required. The other variables are optional.

### CLI Usage

```bash
benchbox run --platform dataproc --benchmark tpch --scale 1.0 \
  --platform-option project_id=my-project \
  --platform-option cluster_name=my-cluster \
  --platform-option gcs_staging_dir=gs://bucket/benchbox/

benchbox run --platform dataproc --benchmark tpch --scale 10.0 \
  --platform-option project_id=my-project \
  --platform-option cluster_name=my-cluster \
  --platform-option gcs_staging_dir=gs://bucket/benchbox/ \
  --platform-option worker_machine_type=n2-highmem-8 \
  --platform-option num_workers=4

benchbox run --platform dataproc --benchmark tpch --scale 10.0 \
  --platform-option project_id=my-project \
  --platform-option cluster_name=my-cluster \
  --platform-option gcs_staging_dir=gs://bucket/benchbox/ \
  --platform-option use_preemptible=true

benchbox run --platform dataproc --benchmark tpch --dry-run ./preview \
  --platform-option project_id=my-project \
  --platform-option gcs_staging_dir=gs://bucket/benchbox/
```

The four commands are, in order: basic usage with an existing cluster, custom machine types, preemptible workers, and a dry run to preview queries.

### Platform Options

| Option | Default | Description |
|--------|---------|-------------|
| `project_id` | *required* | GCP project ID |
| `gcs_staging_dir` | *required* | GCS path for data staging |
| `region` | us-central1 | GCP region |
| `cluster_name` | auto-generated | Dataproc cluster name |
| `database` | benchbox | Hive database name |
| `master_machine_type` | n2-standard-4 | Master VM type |
| `worker_machine_type` | n2-standard-4 | Worker VM type |
| `num_workers` | 2 | Number of worker nodes |
| `use_preemptible` | false | Use preemptible workers |
| `ephemeral_cluster` | false | Create/delete cluster per job |
| `timeout_minutes` | 60 | Job timeout in minutes |

### Machine Types

| Type | vCPU | Memory | Cost/Hour | Best For |
|------|------|--------|-----------|----------|
| n2-standard-4 | 4 | 16GB | ~$0.20 | General workloads |
| n2-standard-8 | 8 | 32GB | ~$0.39 | Larger datasets |
| n2-highmem-4 | 4 | 32GB | ~$0.26 | Memory-intensive |
| n2-highmem-8 | 8 | 64GB | ~$0.52 | Large joins/aggregations |
| c2-standard-4 | 4 | 16GB | ~$0.21 | CPU-intensive |

*Preemptible VMs: ~80% cheaper but can be interrupted*

## Python API

```python
from benchbox.platforms.gcp import DataprocAdapter

adapter = DataprocAdapter(
    project_id="my-project",
    region="us-central1",
    cluster_name="my-cluster",
    gcs_staging_dir="gs://my-bucket/benchbox/",
    database="tpch_benchmark",
    num_workers=4,
)

adapter = DataprocAdapter(
    project_id="my-project",
    gcs_staging_dir="gs://my-bucket/benchbox/",
    create_ephemeral_cluster=True,
    use_preemptible_workers=True,
)

adapter.create_schema("tpch_sf1")

adapter.load_data(
    tables=["lineitem", "orders", "customer"],
    source_dir="/path/to/tpch/data",
)

result = adapter.execute_query("SELECT COUNT(*) FROM lineitem")

adapter.close()
```

The first adapter connects to an existing cluster. The second creates an ephemeral cluster. The calls then create the schema in Hive, load data to GCS and create the Hive tables, submit the query as a Dataproc job, and clean up on `close()` (which deletes the ephemeral cluster if one is configured).

## Cluster Modes

### Persistent Cluster

Best for ongoing development and multiple benchmark runs:

```python
adapter = DataprocAdapter(
    project_id="my-project",
    cluster_name="benchmark-cluster",
    gcs_staging_dir="gs://bucket/benchbox/",
)
```

- Reuse cluster across jobs
- Faster job submission (no cluster startup)
- Pay for cluster uptime

### Ephemeral Cluster

Best for one-off benchmarks and CI/CD:

```python
adapter = DataprocAdapter(
    project_id="my-project",
    gcs_staging_dir="gs://bucket/benchbox/",
    create_ephemeral_cluster=True,
)
```

- Cluster created when first job is submitted
- Cluster deleted on adapter.close()
- Pay only for actual usage

## Spark Configuration

BenchBox automatically optimizes Spark configuration based on benchmark type and scale factor. The automatic configuration covers:

- Adaptive Query Execution (AQE) settings
- Shuffle partition tuning
- Memory allocation
- Join optimization

For TPC-H at SF=10 with 4 workers, the settings include `spark.sql.shuffle.partitions = 200`, `spark.sql.adaptive.enabled = true` and `spark.sql.adaptive.skewJoin.enabled = true`.

## Cost Estimation

| Scale Factor | Data Size | Workers | Est. Runtime | Est. Cost |
|--------------|-----------|---------|--------------|-----------|
| 0.01 | ~10 MB | 2x n2-std-4 | ~30 min | ~$0.30 |
| 1.0 | ~1 GB | 2x n2-std-4 | ~2 hours | ~$1.20 |
| 10.0 | ~10 GB | 4x n2-std-8 | ~4 hours | ~$6.24 |
| 100.0 | ~100 GB | 8x n2-std-8 | ~8 hours | ~$24.96 |

*With preemptible workers: reduce worker costs by ~80%*

## IAM Configuration

Minimum required IAM roles:

```bash
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:SA_EMAIL" \
  --role="roles/dataproc.editor"

gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:SA_EMAIL" \
  --role="roles/storage.objectAdmin"

gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/dataproc.worker"
```

The first two bindings are for the service account that runs BenchBox. The third is for the Dataproc worker service account.

## Troubleshooting

### Common Issues

**Cluster creation fails:**
- Verify Dataproc API is enabled
- Check service account permissions
- Ensure region has available quota

**Job fails to start:**
- Check cluster is in RUNNING state
- Verify GCS bucket is accessible
- Review job logs in Dataproc console

**Slow performance:**
- Increase number of workers
- Use larger machine types
- Enable preemptible secondary workers
- Check for data skew in queries

**GCS access denied:**
- Verify service account has Storage Object Admin role
- Check bucket and object permissions
- Ensure bucket is in same project or cross-project access is configured

## Comparison with Other Platforms

| Aspect | Dataproc | AWS EMR | Azure HDInsight |
|--------|----------|---------|-----------------|
| Pricing | Per-second | Per-second | Per-minute |
| Preemptible | Preemptible VMs | Spot Instances | Low-priority VMs |
| Storage | GCS | S3/HDFS | ADLS/Blob |
| Metastore | Dataproc Metastore | AWS Glue Catalog | Hive Metastore |
| Scaling | Manual/Auto | Auto | Manual |

## Related

- [Apache Spark Platform](spark.md) - Local/cluster Spark usage
- [AWS Glue Platform](aws-glue.md) - Serverless Spark on AWS
- [Cloud Platforms Overview](cloud-platforms.md) - All cloud platforms
