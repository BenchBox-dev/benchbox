<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# BigQuery Platform

```{tags} intermediate, guide, bigquery, cloud-platform
```

Google BigQuery is a serverless, highly scalable data warehouse with built-in machine learning and real-time analytics. BenchBox provides full support for BigQuery benchmarking with optimized data loading via Cloud Storage.

## Features

- **Serverless** - No infrastructure to manage
- **Separation of storage and compute** - Pay for what you use
- **Standard SQL** - ANSI SQL compliant
- **Columnar storage** - Capacitor format with automatic compression
- **Slot-based pricing** - On-demand or reserved capacity

## Prerequisites

- Google Cloud project with BigQuery API enabled
- Service account or user credentials with BigQuery permissions
- Cloud Storage bucket for data staging (recommended for large datasets)
- Billing enabled on the project

## Installation

```bash
pip install google-cloud-bigquery google-cloud-storage

pip install "benchbox[bigquery]"
```

## Configuration

### Interactive Setup (Recommended)

```bash
benchbox setup --platform bigquery
```

This saves the project ID, service account key path, location, and optional
Cloud Storage bucket. `benchbox setup` can fill these in from
`GOOGLE_APPLICATION_CREDENTIALS`, `BIGQUERY_PROJECT`, `BIGQUERY_LOCATION`, and
`BIGQUERY_STORAGE_BUCKET`; `benchbox run` does not read the `BIGQUERY_*`
variables.

### Service Account

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
export BIGQUERY_PROJECT=your-project-id

benchbox setup --platform bigquery
```

### Application Default Credentials

Without saved credentials, BenchBox uses Application Default Credentials and
their project:

```bash
gcloud auth application-default login
gcloud config set project your-project-id
```

### CLI Options

The project and location are not `--platform-option` keys. BenchBox names the
dataset after the benchmark and scale factor.

```bash
benchbox run --platform bigquery --benchmark tpch --scale 1.0 \
  --platform-option staging_root=gs://your-bucket/benchbox
```

## Platform Options

The **Set with** column shows where each setting comes from: the saved
credentials (`benchbox setup`), a `--platform-option` key on `benchbox run`,
or a `BigQueryAdapter` argument in the Python API.

| Option | Set with | Default | Description |
|--------|----------|---------|-------------|
| `project_id` | setup | (ADC project) | GCP project ID |
| `location` | setup | US | Data location (US, EU, etc.) |
| `credentials_path` | setup | (ADC) | Service account JSON path |
| `staging_root` | `--platform-option` | (none) | GCS staging path (`gs://bucket/path`) |
| `storage_bucket` | `--platform-option` | (none) | GCS bucket for staging (alternative to `staging_root`) |
| `storage_prefix` | `--platform-option` | `benchbox-data` | Path prefix within `storage_bucket` |
| `biglake_connection` | `--platform-option` | (none) | BigLake connection for external tables, as `project.region.name` |
| `maximum_bytes_billed` | Python API | (none) | Per-query bytes-billed limit |
| `driver_version` | `--platform-option` | (latest) | Pin the google-cloud-bigquery package version (e.g. `3.0.0`) |
| `driver_auto_install` | `--platform-option` | false | Auto-install the requested driver version via uv if missing |

### Testing a Specific BigQuery Connector Version

```bash
benchbox run --platform bigquery --benchmark tpch \
  --platform-option driver_version=3.0.0 \
  --platform-option driver_auto_install=true
```

The driver package for BigQuery is `google-cloud-bigquery`.

See {ref}`driver-version-management` for the full guide, including why `uv run` may
revert a manually-installed version and how to work around it.

## Authentication Methods

### Service Account

```bash
gcloud iam service-accounts create benchbox-sa \
  --display-name="BenchBox Service Account"

gcloud projects add-iam-policy-binding your-project \
  --member="serviceAccount:benchbox-sa@your-project.iam.gserviceaccount.com" \
  --role="roles/bigquery.admin"

gcloud iam service-accounts keys create benchbox-key.json \
  --iam-account=benchbox-sa@your-project.iam.gserviceaccount.com

export GOOGLE_APPLICATION_CREDENTIALS=benchbox-key.json
```

### User Credentials

```bash
gcloud auth application-default login

gcloud config set project your-project-id
```

### Workload Identity (GKE)

Use Workload Identity Federation. When no service account key path is saved,
BenchBox uses Application Default Credentials, which pick up the workload
identity. No BenchBox option is needed:

```bash
benchbox run --platform bigquery --benchmark tpch
```

## Usage Examples

### Basic Benchmark

```bash
benchbox run --platform bigquery --benchmark tpch --scale 1.0
```

### With Cloud Storage Staging

```bash
benchbox run --platform bigquery --benchmark tpch --scale 100.0 \
  --output gs://your-bucket/benchbox/
```

### With Cost Controls

Limit query costs with the `maximum_bytes_billed` argument of
`BigQueryAdapter`. It is not a `--platform-option` key. `10000000000` sets a
10 GB limit:

```python
from benchbox.platforms.bigquery import BigQueryAdapter

adapter = BigQueryAdapter(
    project_id="your-project-id",
    maximum_bytes_billed=10000000000,
)
```

### Python API

```python
from benchbox import TPCH
from benchbox.platforms.bigquery import BigQueryAdapter

adapter = BigQueryAdapter(
    project_id="your-project-id",
    dataset_id="benchbox",
    location="US",
)

benchmark = TPCH(scale_factor=1.0)
benchmark.generate_data()
adapter.load_benchmark(benchmark)
results = adapter.run_benchmark(benchmark)
```

## Pricing

### On-Demand Pricing

| Operation | Cost |
|-----------|------|
| Queries | $6.25 per TiB scanned (US/EU/Asia multi-region; per-location rates in `benchbox/core/cost/pricing_data.yaml`) |
| Storage | $0.02 per GB/month |
| Streaming inserts | $0.01 per 200MB |

### Slot Reservations

For predictable costs, assign the project to a slot reservation in BigQuery.
BenchBox has no reservation option; it reads the project's reservation
assignment and records it in the result metadata when it has permission.

## Performance Features

### Partitioning

BenchBox applies partitioning with `--tuning tuned`:

```sql
CREATE TABLE lineitem
PARTITION BY DATE(l_shipdate)
CLUSTER BY l_orderkey
AS SELECT * FROM staging.lineitem;
```

### Clustering

Clustering improves query performance:

```sql
ALTER TABLE lineitem
CLUSTER BY l_shipdate, l_orderkey;
```

### Query Caching

BenchBox disables caching for accurate benchmarks:

```python
job_config = bigquery.QueryJobConfig(
    use_query_cache=False,
    use_legacy_sql=False,
)
```

## Data Loading

### Direct Load (Small Datasets)

For datasets under 10GB, direct loading via API:

```bash
benchbox run --platform bigquery --benchmark tpch --scale 0.1
```

### Cloud Storage (Large Datasets)

For large datasets, stage in GCS first:

```bash
benchbox run --platform bigquery --benchmark tpch --scale 100.0 \
  --output gs://your-bucket/benchbox/
```

## Cost Optimization

### Query Cost Limits

Prevent runaway queries with a per-query limit. Pass
`maximum_bytes_billed=1000000000` to `BigQueryAdapter` for a 1 GB limit, as
shown in With Cost Controls above.

### Dry Run Estimates

Preview query costs:

```bash
benchbox run --platform bigquery --benchmark tpch --dry-run ./estimate
```

## Troubleshooting

### Authentication Failed

```bash
gcloud auth application-default print-access-token

gcloud projects get-iam-policy your-project \
  --filter="bindings.members:benchbox-sa"
```

### Project Not Found

```bash
gcloud projects list

gcloud config set project your-project-id
```

### Quota Exceeded

Check quotas, then request an increase in the Cloud Console under BigQuery > Quotas > Request Increase:

```bash
gcloud compute project-info describe --project your-project-id
```

### Dataset Location Mismatch

Datasets must be in the same location as the jobs that query them. The
location is not a `--platform-option` key; save it with `benchbox setup`
(the "Data location" prompt), or pass `location=` to `BigQueryAdapter`:

```bash
benchbox setup --platform bigquery
```

### Permission Denied

The account needs one of the following:

- `roles/bigquery.admin` (full access)
- Or the specific roles:
  - `roles/bigquery.dataEditor`
  - `roles/bigquery.jobUser`
  - `roles/storage.objectAdmin` (for GCS staging)

## Related Documentation

- [Snowflake](snowflake.md) - Multi-cloud warehouse
- [Redshift](redshift.md) - AWS native warehouse
- [Databricks](databricks.md) - Lakehouse alternative
- [Platform Comparison](comparison-matrix.md)
