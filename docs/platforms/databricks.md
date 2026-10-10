<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Databricks Platform

```{tags} intermediate, guide, databricks, cloud-platform
```

Databricks provides a unified lakehouse platform combining data lakes with warehouse capabilities. BenchBox supports Databricks SQL Warehouses and classic clusters with Delta Lake optimizations.

## Features

- **Multi-format support** - Delta Lake (native), Apache Iceberg, Apache Hudi
- **Unity Catalog** - Unified governance and security
- **Photon engine** - Vectorized query execution
- **Auto-scaling** - Dynamic cluster management
- **Multi-cloud** - AWS, Azure, and GCP support

## Execution Engine

Databricks SQL declares `photon` and `standard` as observed engines, not
selectable settings. The default lets the warehouse decide. When the SDK
captures `warehouse.enable_photon`, result bundles record `photon` or
`standard` in `platform.execution_engine.observed` with
`observed_source="vendor_log"`; unavailable metadata is recorded as
`not_captured`.

## Prerequisites

- Databricks workspace (AWS, Azure, or GCP)
- SQL Warehouse or All-Purpose Cluster
- Personal Access Token or OAuth credentials
- Unity Catalog (recommended) or Hive Metastore

## Installation

Install the Databricks SQL connector, or install it via the BenchBox extras (the second command):

```bash
pip install databricks-sql-connector databricks-sdk

pip install "benchbox[databricks]"
```

## Configuration

### Environment Variables (Recommended)

```bash
export DATABRICKS_HOST=https://your-workspace.cloud.databricks.com
export DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/abc123def456
export DATABRICKS_TOKEN=dapi1234567890abcdef
```

### Interactive Setup

```bash
benchbox setup --platform databricks
```

### CLI Options

```bash
benchbox run --platform databricks --benchmark tpch --scale 1.0 \
  --platform-option server_hostname=your-workspace.cloud.databricks.com \
  --platform-option http_path=/sql/1.0/warehouses/abc123def456 \
  --platform-option access_token=dapi1234...
```

## Platform Options

| Option | Default | Description |
|--------|---------|-------------|
| `server_hostname` | (env) | Workspace URL |
| `http_path` | (env) | SQL Warehouse or cluster path |
| `access_token` | (env) | Personal Access Token |
| `catalog` | (default) | Unity Catalog name |
| `schema` | (auto) | Schema name |
| `use_volumes` | true | Use UC Volumes for staging |
| `volume_path` | (auto) | Path within volume |
| `driver_version` | (latest) | Pin the Databricks SQL connector version (e.g. `3.3.0`) |
| `driver_auto_install` | false | Auto-install the requested driver version via uv if missing |
| `table_format` | `delta` | Table format: `delta` or `hudi` (Hudi emits `USING HUDI` DDL) |
| `hudi_primary_key` | (none) | Hudi record key column (recommended; required at write time) |
| `hudi_precombine_field` | (none) | Hudi precombine (ordering) field |
| `hudi_table_type` | `cow` | Hudi table type: `cow` (copy-on-write) or `mor` (merge-on-read) |

### Apache Hudi Tables

```bash
benchbox run --platform databricks --benchmark tpch \
  --platform-option table_format=hudi \
  --platform-option hudi_primary_key=l_orderkey \
  --platform-option hudi_precombine_field=l_commitdate
```

Hudi support is DDL-level and validated by unit tests only (no live-warehouse
validation yet). Requirements and limits:

- The target runtime must support Hudi (Databricks Runtime with Hudi support
  and the Hudi Spark bundle / `HoodieSparkSessionExtension` where needed);
  BenchBox only emits the `USING HUDI` DDL and does not install libraries.
- `hudi_primary_key` should be set: Hudi needs a record key at write time,
  and BenchBox passes it through as the `primaryKey` table property (only
  for tables whose column list defines it). Keyless tables are created but
  cannot be written.
- Tuned dry-run previews (`DeltaDDLGenerator`) render Delta DDL and ZORDER
  post-load statements regardless of `table_format`; only executed DDL goes
  through the Hudi conversion described here. Each key is emitted
  only for tables whose DDL defines that column, so one global key never
  leaks into the other tables of a multi-table benchmark; tables without
  the column still get `USING HUDI` plus the table type.
- Managed data loads (`COPY INTO`) are Delta-only, so `load_data` raises for
  Hudi tables: load Hudi tables through a Hudi-aware Spark job. Delta-only
  maintenance (`OPTIMIZE`, `VACUUM`, `ZORDER`, Liquid Clustering) is recorded
  as skipped for Hudi tables instead of emitting invalid SQL.

### Testing a Specific Databricks Connector Version

```bash
benchbox run --platform databricks --benchmark tpch \
  --platform-option driver_version=3.3.0 \
  --platform-option driver_auto_install=true \
  --platform-option compute_resource=abc123xyz
```

The driver package for Databricks is `databricks-sql-connector`.

See {ref}`driver-version-management` for the full guide, including why `uv run` may
revert a manually-installed version and how to work around it.

## Authentication Methods

### Personal Access Token

Generate the token under User Settings > Developer > Access Tokens.

```bash
export DATABRICKS_TOKEN=dapi1234567890abcdef

benchbox run --platform databricks --benchmark tpch --scale 1.0
```

### OAuth (M2M)

This uses service principal authentication.

```bash
export DATABRICKS_CLIENT_ID=your_client_id
export DATABRICKS_CLIENT_SECRET=your_client_secret

benchbox run --platform databricks --benchmark tpch \
  --platform-option auth_type=oauth-m2m
```

### Azure AD (Azure Databricks)

This uses an Azure Active Directory token.

```bash
export ARM_CLIENT_ID=your_client_id
export ARM_CLIENT_SECRET=your_client_secret
export ARM_TENANT_ID=your_tenant_id

benchbox run --platform databricks --benchmark tpch \
  --platform-option auth_type=azure-ad
```

## Usage Examples

### Basic Benchmark

This runs TPC-H on a SQL Warehouse.

```bash
benchbox run --platform databricks --benchmark tpch --scale 1.0
```

### With Unity Catalog

Specify the catalog and schema:

```bash
benchbox run --platform databricks --benchmark tpch --scale 10.0 \
  --platform-option catalog=benchmarks \
  --platform-option schema=tpch_sf10
```

### With Tuning

This applies Delta Lake optimizations:

```bash
benchbox run --platform databricks --benchmark tpch --scale 10.0 \
  --tuning tuned
```

### Python API

```python
from benchbox import TPCH
from benchbox.platforms.databricks import DatabricksAdapter

adapter = DatabricksAdapter(
    server_hostname="your-workspace.cloud.databricks.com",
    http_path="/sql/1.0/warehouses/abc123def456",
    access_token="dapi1234567890abcdef",
    catalog="benchmarks",
)

benchmark = TPCH(scale_factor=1.0)
benchmark.generate_data()
adapter.load_benchmark(benchmark)
results = adapter.run_benchmark(benchmark)
```

## SQL Warehouse Sizing

| Size | DBU/Hour | Recommended Scale |
|------|----------|-------------------|
| 2X-Small | 2 | SF 0.01-0.1 |
| X-Small | 4 | SF 0.1-1.0 |
| Small | 8 | SF 1.0-10.0 |
| Medium | 16 | SF 10.0-100.0 |
| Large | 32 | SF 100.0+ |

## Performance Features

### Delta Lake Optimizations

BenchBox applies Delta optimizations with `--tuning tuned`. The statements optimize the file layout and apply liquid
clustering:

```sql
OPTIMIZE lineitem ZORDER BY (l_shipdate);

ALTER TABLE lineitem CLUSTER BY (l_shipdate, l_orderkey);
```

Clustering is set before the data loads. After a tuned table loads, BenchBox
also executes `OPTIMIZE <table>` and `ANALYZE TABLE <table> COMPUTE STATISTICS`
on Delta tables, unless `enable_delta_optimization` is `false`. The time is
reported as `phases.post_load_maintenance` and is not counted in data-loading
time. Tables the tuning does not cover, and untuned runs, still get a plain
`OPTIMIZE` after load while `enable_delta_optimization` is on, and that time
counts as load time. BenchBox does not run `VACUUM` as part of tuning.

The resolved strategy is recorded per run in the result bundle at
`platform.config.databricks_clustering_strategy` (`"z_order"`,
`"liquid_clustering"`, `"liquid_clustering_auto"`, or `"none"` for untuned
runs); see `docs/reference/result-formats.md`.

### Photon Acceleration

Photon is automatically enabled on SQL Warehouses. This command verifies that it is enabled:

```bash
benchbox run --platform databricks --benchmark tpch --scale 1.0 \
  --platform-option check_photon=true
```

### Query Caching

BenchBox disables caching for accurate benchmarks by using unique query tags.

## Data Loading

### Unity Catalog Volumes (Default)

Data uploaded to managed volumes, then loaded via COPY INTO. This is automatic when Unity Catalog is enabled:

```bash
benchbox run --platform databricks --benchmark tpch --scale 1.0
```

### External Location (S3/ADLS/GCS)

For large datasets, use external cloud storage. This configures external staging:

```bash
benchbox run --platform databricks --benchmark tpch --scale 100.0 \
  --output s3://bucket/benchbox/
```

### DBFS (Legacy)

For workspaces without Unity Catalog:

```bash
benchbox run --platform databricks --benchmark tpch --scale 1.0 \
  --platform-option use_volumes=false \
  --platform-option dbfs_path=/tmp/benchbox/
```

## Cost Optimization

### Auto-Stop

Configure warehouses to auto-stop. Set this in the UI or API, under Warehouse Settings > Auto Stop > 10 minutes.


### Serverless Warehouses

For variable workloads:

```bash
benchbox run --platform databricks --benchmark tpch \
  --platform-option http_path=/sql/1.0/warehouses/serverless_wh
```

## Troubleshooting

### Authentication Failed

Verify that the token is valid with the request below. Also check the token expiration: tokens expire after 90 days by
default.

```bash
curl -H "Authorization: Bearer $DATABRICKS_TOKEN" \
  https://your-workspace.cloud.databricks.com/api/2.0/clusters/list

```

### SQL Warehouse Not Found

List the warehouses via the API, and verify the `http_path` format. For a SQL Warehouse it is
`/sql/1.0/warehouses/<warehouse_id>`. For a cluster it is `/sql/protocolv1/o/<org_id>/<cluster_id>`.

```bash
curl -H "Authorization: Bearer $DATABRICKS_TOKEN" \
  https://your-workspace.cloud.databricks.com/api/2.0/sql/warehouses

```

### Unity Catalog Access Denied

Grant catalog access:

```sql
GRANT USE CATALOG ON CATALOG benchmarks TO `user@company.com`;
GRANT CREATE SCHEMA ON CATALOG benchmarks TO `user@company.com`;
```

### Volume Upload Failed

Verify that the volume exists and has write access. The first statement creates the volume if needed (run it on a SQL
Warehouse), and the second grants permissions.

```bash
CREATE VOLUME IF NOT EXISTS benchmarks.staging.uploads;

GRANT WRITE VOLUME ON VOLUME benchmarks.staging.uploads TO `user@company.com`;
```

## Related Documentation

- [Snowflake](snowflake.md) - Cloud warehouse alternative
- [BigQuery](bigquery.md) - Google Cloud alternative
- [Spark](spark.md) - Open-source Spark
- [Platform Comparison](comparison-matrix.md)
