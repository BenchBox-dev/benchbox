<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Snowflake Platform

```{tags} intermediate, guide, snowflake, cloud-platform
```

Snowflake is a cloud-native data warehouse with automatic scaling, multi-cluster compute, and separation of storage and compute. BenchBox provides comprehensive support for Snowflake benchmarking across all cloud providers (AWS, Azure, GCP).

## Features

- **Auto-scaling compute** - Virtual warehouses scale independently
- **Multi-cluster warehouses** - Concurrent query capacity
- **Zero-copy cloning** - Instant database clones for testing
- **Time travel** - Historical data access
- **Tri-cloud support** - AWS, Azure, and GCP

## Prerequisites

- Snowflake account (trial or paid)
- Virtual warehouse with appropriate size
- Database with CREATE TABLE permissions
- Internal or external stage for data loading

## Installation

```bash
pip install snowflake-connector-python

pip install "benchbox[snowflake]"
```

## Configuration

### Interactive Setup (Recommended)

```bash
benchbox setup --platform snowflake
```

This saves the account, username, password, warehouse, database, schema, and
role. `benchbox run` reads them from the saved credentials; account, user, and
password are not `--platform-option` keys.

### Environment Variables

`benchbox setup --platform snowflake` can fill in the values from these
variables. `benchbox run` does not read them.

```bash
export SNOWFLAKE_ACCOUNT=your_account_identifier
export SNOWFLAKE_USERNAME=your_username
export SNOWFLAKE_PASSWORD=your_password
export SNOWFLAKE_WAREHOUSE=your_warehouse
export SNOWFLAKE_DATABASE=BENCHBOX
export SNOWFLAKE_SCHEMA=PUBLIC
```

### CLI Options

```bash
benchbox run --platform snowflake --benchmark tpch --scale 1.0 \
  --platform-option compute_resource=COMPUTE_WH
```

`compute_resource` names the warehouse. A value on the command line overrides
the warehouse saved by `benchbox setup`.

## Platform Options

The **Set with** column shows where each setting comes from: the saved
credentials (`benchbox setup`), a `--platform-option` key on `benchbox run`,
or a `SnowflakeAdapter` argument in the Python API.

| Option | Set with | Default | Description |
|--------|----------|---------|-------------|
| `account` | setup | (none) | Snowflake account identifier |
| `username` | setup | (none) | Username for authentication |
| `password` | setup | (none) | Password for authentication |
| `compute_resource` | `--platform-option` | `COMPUTE_WH` | Named compute resource (Snowflake warehouse). Setup saves it as `warehouse`, a deprecated alias removed in 0.6.0 |
| `compute_size` | `--platform-option` | `MEDIUM` | Warehouse size. Deprecated alias: `warehouse_size` |
| `database` | setup | `BENCHBOX` | Database name |
| `schema` | setup | `PUBLIC` | Schema name |
| `role` | setup | (default) | Snowflake role |
| `staging_root` | `--platform-option` | (none) | Cloud storage path for external staging |
| `iceberg_external_volume` | `--platform-option` | (none) | EXTERNAL VOLUME name for Iceberg tables |
| `edition` | Python API | (unset) | Account edition for cost modeling: `standard`, `enterprise`, `business_critical`, or `vps`. Not queryable from the service, so normalized cost stays `unavailable` until the operator sets it. |
| `private_key_path` | Python API | (none) | Private key file for key pair authentication |
| `authenticator` | Python API | `snowflake` | Connector authenticator, such as `externalbrowser` |
| `driver_version` | `--platform-option` | (latest) | Pin the Snowflake Python connector version (e.g. `3.12.0`) |
| `driver_auto_install` | `--platform-option` | false | Auto-install the requested driver version via uv if missing |

### Testing a Specific Snowflake Connector Version

```bash
benchbox run --platform snowflake --benchmark tpch \
  --platform-option driver_version=3.12.0 \
  --platform-option driver_auto_install=true \
  --platform-option compute_resource=COMPUTE_WH
```

The driver package for Snowflake is `snowflake-connector-python`.

See {ref}`driver-version-management` for the full guide, including why `uv run` may
revert a manually-installed version and how to work around it.

### Gateway Endpoint

`--gateway espresso|greybeam|custom` routes the Snowflake connection through a gateway endpoint. Every gateway
needs `gateway_host`; the Snowflake `account` is still sent so login succeeds. Gateway options are accepted only
with a non-native gateway.

```bash
benchbox run --platform snowflake --benchmark tpch --gateway custom \
  --platform-option gateway_host=gateway.example.com \
  --platform-option gateway_port=8443
```

| Option | Default | Description |
|--------|---------|-------------|
| `gateway_host` | (required) | Bare gateway host name or address; a value with credentials (`user:password@`), a scheme or a path is rejected |
| `gateway_port` | connector default | Gateway port (1-65535) |
| `gateway_protocol` | `https` | `https` or `http` |
| `gateway_ca_bundle` | (none) | CA bundle file for a private-CA gateway. The connector reads it from the process environment, so `REQUESTS_CA_BUNDLE` must name the same file before BenchBox starts; otherwise construction fails. BenchBox never changes the environment |
| `gateway_ocsp_fail_open` | (connector default) | Passed to the connector only when set |
| `allow_insecure_gateway` | false | Disables certificate verification (`insecure_mode`) |

`allow_insecure_gateway` and `http` to a non-loopback host both require `BENCHBOX_ALLOW_INSECURE_GATEWAY=1` and
log a warning. Gateway options are not available through MCP. A gateway-routed run records
`platform.gateway`, is marked `tpc_compliant: false`, and never compares with native runs. Its
`benchmark.compliance_class` is always `unofficial_nonstandard`, whatever the benchmark reports, so TPC metrics are
suppressed and publish, submit, bundle validation, the results database and metrics readiness all refuse it as
non-official. A stored result that claims `official` while `platform.gateway.routed` is true is treated the same way.

## Authentication Methods

### Password Authentication

`benchbox setup --platform snowflake` saves the account, username, and
password. `benchbox run` then uses them:

```bash
benchbox setup --platform snowflake

benchbox run --platform snowflake --benchmark tpch
```

### Key Pair Authentication

Generate a key pair, upload the public key to Snowflake with `ALTER USER benchbox_user SET RSA_PUBLIC_KEY='MII...';`, then pass the private key to `SnowflakeAdapter`.

```bash
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out rsa_key.p8 -nocrypt
openssl rsa -in rsa_key.p8 -pubout -out rsa_key.pub
```

```python
from benchbox.platforms.snowflake import SnowflakeAdapter

adapter = SnowflakeAdapter(
    account="xy12345.us-east-1",
    username="benchbox_user",
    password="unused",
    private_key_path="/path/to/rsa_key.p8",
)
```

`private_key_path` and `authenticator` are Python API arguments, not
`--platform-option` keys, and `benchbox setup` saves password credentials
only. The adapter currently requires a `password` value even with a private
key; it does not send the password when `private_key_path` is set.

### SSO/Browser Authentication

```python
from benchbox.platforms.snowflake import SnowflakeAdapter

adapter = SnowflakeAdapter(
    account="xy12345.us-east-1",
    username="user@company.com",
    password="unused",
    authenticator="externalbrowser",
)
```

## Usage Examples

### Basic Benchmark

This runs TPC-H at scale factor 1. The warehouse comes from the saved
credentials.

```bash
benchbox run --platform snowflake --benchmark tpch --scale 1.0
```

To change the warehouse, run `benchbox setup --platform snowflake` again, or
pass `--platform-option compute_resource=<warehouse>` when no warehouse is
saved.

### With Tuning

This applies clustering and optimizations on a larger warehouse:

```bash
benchbox run --platform snowflake --benchmark tpch --scale 10.0 \
  --tuning tuned \
  --platform-option compute_resource=LARGE_WH
```

### Python API

```python
from benchbox import TPCH
from benchbox.platforms.snowflake import SnowflakeAdapter

adapter = SnowflakeAdapter(
    account="xy12345.us-east-1",
    username="benchbox_user",
    password="secure_password",
    compute_resource="COMPUTE_WH",
    database="BENCHBOX",
)

benchmark = TPCH(scale_factor=1.0)
benchmark.generate_data()
adapter.load_benchmark(benchmark)
results = adapter.run_benchmark(benchmark)
```

## Warehouse Sizing

| Warehouse Size | Credits/Hour | Recommended Scale |
|----------------|--------------|-------------------|
| X-Small | 1 | SF 0.01-0.1 |
| Small | 2 | SF 0.1-1.0 |
| Medium | 4 | SF 1.0-10.0 |
| Large | 8 | SF 10.0-100.0 |
| X-Large | 16 | SF 100.0+ |

## Performance Features

### Clustering Keys

BenchBox applies clustering keys with `--tuning tuned`:

```sql
ALTER TABLE lineitem CLUSTER BY (l_shipdate);
ALTER TABLE orders CLUSTER BY (o_orderdate);
```

For up to four clustering columns, BenchBox then runs
`ALTER TABLE <table> RESUME RECLUSTER` after a table with a clustering key
loads, so automatic
clustering has data to work on. The time is reported as
`phases.post_load_maintenance` and is not counted in data-loading time.

### Result Caching

BenchBox disables result caching for accurate benchmarks:

```sql
ALTER SESSION SET USE_CACHED_RESULT = FALSE;
```

### Query Tagging

Queries are tagged for tracking:

```sql
ALTER SESSION SET QUERY_TAG = 'benchbox:tpch:power:Q1';
```

A gateway-routed run appends a run-level suffix, `_gw-<8 hex characters>`, to the per-query tag
(for example `BenchBox_Q1_gw-3fa9c2d1`). The suffix is generated once per adapter instance, so every query in one run
shares it and a gateway vendor can join its logs to the run. The statistics lookup against `QUERY_HISTORY` uses the
same tag, so execution time, compilation time, bytes scanned and credits are still recorded. Native runs keep the
unsuffixed tag.

## Data Loading

### User Stage (Default)

Files uploaded to user stage, then loaded via COPY INTO:

```bash
benchbox run --platform snowflake --benchmark tpch --scale 1.0
```

### External Stage (S3/Azure/GCS)

For large scale factors, use external staging. The cloud `--output` path becomes the external staging root:

```bash
benchbox run --platform snowflake --benchmark tpch --scale 100.0 \
  --output s3://bucket/benchbox/ \
  --table-mode external
```

## Cost Optimization

### Auto-Suspend

Configure warehouses to auto-suspend:

```sql
ALTER WAREHOUSE BENCHMARK_WH SET AUTO_SUSPEND = 60;
```

### Multi-Cluster Control

For throughput tests, set the cluster count and scaling policy on the
warehouse itself. BenchBox has no option for them; it records the warehouse's
`max_cluster_count` and `scaling_policy` in the result metadata.

```sql
ALTER WAREHOUSE BENCHMARK_WH SET MAX_CLUSTER_COUNT = 4 SCALING_POLICY = 'STANDARD';
```

```bash
benchbox run --platform snowflake --benchmark tpch --scale 1.0 \
  --phases throughput \
  --platform-option compute_resource=BENCHMARK_WH
```

## Troubleshooting

### Authentication Failed

Verify the account identifier format. It should be `<account_locator>.<region>.<cloud>` or `<orgname>-<account_name>`. Examples: `xy12345.us-east-1`, `xy12345.us-east-1.aws`, `myorg-myaccount`. Then test the connection:

```bash
snowsql -a xy12345.us-east-1 -u benchbox_user
```

### Warehouse Not Found

List the available warehouses, and create one if needed:

```bash
snowsql -q "SHOW WAREHOUSES;"

snowsql -q "CREATE WAREHOUSE BENCHMARK_WH WITH WAREHOUSE_SIZE = 'SMALL';"
```

### Insufficient Permissions

```sql
GRANT USAGE ON WAREHOUSE COMPUTE_WH TO ROLE benchbox_role;
GRANT CREATE DATABASE ON ACCOUNT TO ROLE benchbox_role;
GRANT CREATE SCHEMA ON DATABASE benchbox TO ROLE benchbox_role;
```

### Data Loading Timeout

Use a larger warehouse (`compute_resource`) or external staging for big datasets:

```bash
benchbox run --platform snowflake --benchmark tpch --scale 100.0 \
  --output s3://bucket/benchbox/
```

## Related Documentation

- [BigQuery](bigquery.md) - Google Cloud alternative
- [Redshift](redshift.md) - AWS native warehouse
- [Databricks](databricks.md) - Lakehouse alternative
- [Platform Comparison](comparison-matrix.md)
