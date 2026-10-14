<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Troubleshooting Guide

```{tags} intermediate, guide
```

This guide helps diagnose and resolve common issues when running BenchBox benchmarks.

## Quick Diagnosis

### Error Type Matrix

| Error Message Contains | Likely Cause | Jump To |
|----------------------|--------------|---------|
| "connection refused" | Platform not running | [Connection Issues](#connection-issues) |
| "authentication failed" | Invalid credentials | [Authentication](#authentication-failures) |
| "catalog not found" | Presto/Trino config | [Catalog Errors](#catalog-not-found-prestotrino) |
| "permission denied" | Access rights | [Permission Errors](#permission-errors) |
| "timeout" | Slow query/network | [Timeouts](#query-timeouts) |
| "out of memory" | Scale too large | [Memory Issues](#memory-issues) |
| "ImportError" | Missing package | [Dependencies](#missing-dependencies) |
| "file not found" | Data loading | [Data Loading](#data-loading-issues) |
| "command not found: benchbox" | PATH issue | [Installation Issues](#installation-issues) |

(installation-issues)=
## Installation Issues

### `command not found: benchbox`

**Problem**: The `benchbox` command is not available in your shell after installation.

**Solution**:

1.  **Check your PATH**: Ensure that the Python scripts directory is in your system's `PATH`. You can find the directory by running:

    ```bash
    python -m site --user-base
    ```

    Add the `bin` subdirectory of that path to your `PATH`.

2.  **Reactivate your virtual environment**: If you installed BenchBox in a virtual environment, make sure it's activated:

    ```bash
    source .venv/bin/activate
    ```

### Missing Platform Dependencies

**Problem**: You get an `ImportError` when trying to use a specific database platform.

**Solution**:

Install the required dependencies for that platform:

```bash
pip install "benchbox[snowflake]"
pip install "benchbox[databricks]"
pip install "benchbox[bigquery]"

pip install "benchbox[cloud]"

pip install "benchbox[dataframe-all]"
```

Check installation status:

```bash
benchbox platforms list
```

### Shell Reports `no matches found`

**Problem**: Shells such as zsh treat square brackets as glob patterns, producing errors like `zsh: no matches found: benchbox[cloud]`.

**Solution**: Use modern uv syntax (no quotes needed):

```bash
uv add benchbox --extra cloud
uv add benchbox --extra cloud --extra clickhouse
```

Or quote the pip-compatible syntax:

```bash
uv pip install "benchbox[cloud]"
python -m pip install "benchbox[cloud,clickhouse]"
```

## Connection Issues

### Connection Refused

**Symptoms:**
```
ConnectionRefusedError: [Errno 111] Connection refused
OperationalError: could not connect to server
```

**Diagnosis:**

```bash
curl -s http://localhost:3473/health
curl -s http://localhost:8080

docker ps | grep -E 'trino|presto|clickhouse|firebolt'
```

The first `curl` checks Firebolt Core on port 3473. The second checks Trino or Presto on port 8080. The `docker ps` command lists the Docker-based platforms that are running.

**Solutions:**

1. **Start the platform:**
   ```bash
   docker run -d -p 8080:8080 trinodb/trino

   docker run -d -p 3473:3473 ghcr.io/firebolt-db/firebolt-core:preview-rc

   docker run -d -p 9000:9000 clickhouse/clickhouse-server
   ```

2. **Check port availability:**
   ```bash
   lsof -i :8080
   netstat -an | grep 8080
   ```
   The `lsof` command shows whether the port is already in use.

3. **Verify host/port in config:**
   Trino and Presto do not accept `host` or `port` as `--platform-option`
   keys. BenchBox reads them from the platform's section of
   `~/.benchbox/credentials.yaml`, and defaults to `localhost:8080`:
   ```yaml
   trino:
     host: localhost
     port: 8080
   ```

### Network Timeout

**Symptoms:**
```
TimeoutError: Connection timed out
socket.timeout: timed out
```

**Solutions:**

1. **Cloud platforms - check firewall:**
   ```bash
   aws ec2 describe-security-groups --group-ids sg-xxx

   nc -zv your-cluster.redshift.amazonaws.com 5439
   ```

## Authentication Failures

### Invalid Credentials

**Symptoms:**
```
AuthenticationError: Invalid credentials
401 Unauthorized
Access Denied
```

**Platform-Specific Solutions:**

#### Snowflake
```bash
snowsql -a $SNOWFLAKE_ACCOUNT -u $SNOWFLAKE_USER

echo $SNOWFLAKE_ACCOUNT
```

The account should use the `account_locator.region` format. Correct values look like `xy12345.us-east-1` or `xy12345.us-east-1.aws`.

#### Databricks
```bash
curl -H "Authorization: Bearer $DATABRICKS_TOKEN" \
  https://your-workspace.cloud.databricks.com/api/2.0/clusters/list
```

This call tests that the token is valid. If the token has expired (the default lifetime is 90 days), regenerate it under User Settings > Developer > Access Tokens.

#### BigQuery
```bash
gcloud auth activate-service-account --key-file=$GOOGLE_APPLICATION_CREDENTIALS
gcloud auth application-default print-access-token

gcloud projects describe $BIGQUERY_PROJECT
```

#### Redshift
```bash
psql -h $REDSHIFT_HOST -p 5439 -U $REDSHIFT_USER -d dev

aws sts get-caller-identity
```

The `aws sts get-caller-identity` command verifies the role when you use IAM authentication.

### Token Expired

**Symptoms:**
```
Token has expired
Session expired
```

**Solutions:**

1. **Regenerate tokens:**
   - Databricks: User Settings > Access Tokens > Generate New
   - Snowflake: Tokens don't expire, check password
   - BigQuery: `gcloud auth application-default login`

2. **Use refresh tokens where supported:**
   ```bash
   gcloud auth application-default login
   ```

## Catalog Not Found (Presto/Trino)

**Symptoms:**
```
ConfigurationError: Catalog 'memory' not found
CatalogNotFoundError: Catalog does not exist
```

**Cause:** The `memory` catalog default rarely exists on production servers.

**Solutions:**

1. **List available catalogs:**
   ```bash
   benchbox platforms check trino
   ```

2. **Specify the correct catalog:**
   ```bash
   benchbox run --platform trino --benchmark tpch \
     --platform-option catalog=hive
   ```
   Replace `hive` with `iceberg`, `delta`, or another catalog name as needed.

3. **Common catalog names:**
   - `hive` - Hive Metastore
   - `iceberg` - Apache Iceberg
   - `delta` - Delta Lake
   - `tpch` - TPC-H connector (built-in)
   - `mysql`, `postgresql` - Database connectors

## Permission Errors

### Insufficient Privileges

**Symptoms:**
```
PermissionDenied: User does not have permission
AccessDenied: Access Denied
```

**Solutions by Platform:**

#### Snowflake
```sql
GRANT USAGE ON WAREHOUSE compute_wh TO ROLE benchbox_role;
GRANT CREATE DATABASE ON ACCOUNT TO ROLE benchbox_role;
GRANT USAGE ON DATABASE benchbox TO ROLE benchbox_role;
GRANT CREATE TABLE ON SCHEMA benchbox.public TO ROLE benchbox_role;
```

#### Databricks
```sql
GRANT USE CATALOG ON CATALOG benchmarks TO `user@company.com`;
GRANT CREATE SCHEMA ON CATALOG benchmarks TO `user@company.com`;
GRANT USE SCHEMA ON SCHEMA benchmarks.default TO `user@company.com`;
```

#### BigQuery
```bash
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="user:user@company.com" \
  --role="roles/bigquery.dataEditor"
```

#### Redshift
```sql
GRANT CREATE ON DATABASE dev TO benchbox_user;
GRANT ALL ON SCHEMA public TO benchbox_user;
```

## Query Timeouts

**Symptoms:**
```
QueryTimeoutError: Query exceeded time limit
Statement timeout
```

**Solutions:**

1. **Increase query timeout:**
   - Snowflake: BenchBox sets `STATEMENT_TIMEOUT_IN_SECONDS = 1800` for
     OLAP benchmarks such as TPC-H and TPC-DS, and has no setting to change
     it.
   - Redshift: `statement_timeout` (milliseconds) is not a `--platform-option`
     key. Set it in the `redshift` section of `~/.benchbox/credentials.yaml`,
     or pass it to `RedshiftAdapter` in the Python API:
     ```yaml
     redshift:
       statement_timeout: 3600000
     ```

2. **Use larger compute resources:**
   ```bash
   benchbox run --platform snowflake --benchmark tpch --scale 10 \
     --platform-option compute_resource=LARGE_WH
   ```
   A `compute_resource` on the command line overrides the warehouse saved by
   `benchbox setup`.

   On Databricks, `compute_resource` names the SQL warehouse and replaces the
   saved `http_path` with `/sql/1.0/warehouses/<warehouse_id>`:
   ```bash
   benchbox run --platform databricks --benchmark tpch --scale 10 \
     --platform-option compute_resource=large_wh_id
   ```

3. **Reduce scale factor for testing:**
   ```bash
   benchbox run --platform snowflake --benchmark tpch --scale 0.1
   ```

## Memory Issues

### Out of Memory

**Symptoms:**
```
MemoryError: Unable to allocate
OutOfMemoryError
java.lang.OutOfMemoryError: Java heap space
```

**Solutions by Platform:**

#### DuckDB
```bash
benchbox run --platform duckdb --benchmark tpch --scale 10 \
  --platform-option memory_limit=8GB \
  --platform-option temp_directory=/fast/ssd/tmp
```

The memory limit caps usage, and the temp directory is where DuckDB spills to disk once it reaches that limit.

#### Polars
```bash
benchbox run --platform polars-df --benchmark tpch --scale 10 \
  --execution-engine streaming
```

The streaming engine lets Polars process large datasets without holding them all in memory.

#### Spark
```bash
benchbox run --platform spark --benchmark tpch --scale 10 \
  --platform-option driver_memory=8g
```

`driver_memory` is the only Spark memory setting `benchbox run` accepts.
`executor_memory` is a `SparkAdapter` argument in the Python API.

#### Cloud Platforms
```bash
benchbox run --platform snowflake --benchmark tpch --scale 100 \
  --platform-option compute_resource=X_LARGE_WH
```

### Scale Factor Recommendations

| Platform | Max Recommended SF | Notes |
|----------|-------------------|-------|
| DuckDB | 10-100 | Depends on RAM |
| SQLite | 0.1-1.0 | Not for OLAP |
| Polars | 10-100 | Enable streaming |
| Snowflake | 1000+ | Scale warehouse |
| Databricks | 1000+ | Scale cluster |
| BigQuery | 1000+ | Serverless |

## Data Generation Issues

### `dbgen` or `dsdgen` not found

**Problem**: TPC-H or TPC-DS data generation fails with an error indicating that `dbgen` or `dsdgen` is not found.

**Solution**:

BenchBox attempts to compile these tools automatically, but if that fails, you may need to compile them manually.

1.  **Navigate to the tools directory**:

    ```bash
    cd _sources/tpc-h/dbgen

    cd _sources/tpc-ds/tools
    ```

    Use the first directory for TPC-H and the second for TPC-DS.

2.  **Compile the tools**:

    ```bash
    make
    ```

    If you encounter compilation errors, you may need to install a C compiler and other build tools (`build-essential` on Debian/Ubuntu, Xcode Command Line Tools on macOS).

### Slow Data Generation

**Problem**: Data generation is taking a very long time.

**Solution**:

- **Use a smaller scale factor**: For testing and development, use a small scale factor like `0.01`.
- **Run power-only cycles first**: `benchbox run --phases generate,load,power` lets you warm caches before expanding to throughput tests.
- **Check disk throughput**: Write data to a fast local volume before copying it to network storage. Use the `--output` flag to point at SSD-backed paths.
- **Persist generated data**: Reuse existing datasets with `--force` turned off (default) so future runs skip regeneration.

## Data Loading Issues

### File Not Found

**Symptoms:**
```
FileNotFoundError: Data file not found
No such file or directory
```

**Solutions:**

1. **Generate data first:**
   ```bash
   benchbox run --platform duckdb --benchmark tpch --scale 0.1 \
     --phases generate
   ```

2. **Check data directory:**
   ```bash
   ls -la ~/.cache/benchbox/tpch/sf0.1/
   ```

3. **Force regeneration:**
   ```bash
   benchbox run --platform duckdb --benchmark tpch --scale 0.1 \
     --force datagen
   ```

### Upload Failures

**Symptoms:**
```
UploadError: Failed to upload file
S3 upload failed
Storage access denied
```

**Solutions:**

#### Cloud Storage Staging
```bash
aws s3 ls s3://your-bucket/benchbox/

aws s3 cp test.txt s3://your-bucket/benchbox/

benchbox run --platform redshift --benchmark tpch --scale 10 \
  --output s3://your-bucket/benchbox/
```

#### Snowflake Stages
```bash
snowsql -q "SHOW STAGES;"

snowsql -q "CREATE STAGE IF NOT EXISTS @~/benchbox_stage;"
```

#### Databricks Volumes
```bash
databricks volumes list /Volumes/catalog/schema/

databricks volumes create catalog.schema.benchbox_data
```

## Platform-Specific Issues

### Snowflake

#### Warehouse Suspended
```bash
snowsql -q "ALTER WAREHOUSE BENCHMARK_WH RESUME;"

snowsql -q "ALTER WAREHOUSE BENCHMARK_WH SET AUTO_RESUME = TRUE;"
```

The first command resumes the suspended warehouse, and the second sets auto-resume so it restarts on demand.

### Databricks

#### Cluster Not Running
```bash
curl -X POST "https://workspace.cloud.databricks.com/api/2.0/clusters/start" \
  -H "Authorization: Bearer $DATABRICKS_TOKEN" \
  -d '{"cluster_id": "your-cluster-id"}'
```

Alternatively, use a SQL Warehouse, which has an always-on option.

### BigQuery

#### Quota Exceeded
```bash
gcloud compute project-info describe --project $PROJECT_ID
```

To request an increase, use the Console: BigQuery > Quotas > Request Increase.

## General Tips

-   **Use the `--verbose` flag**: The `-v` or `-vv` flag can provide more detailed output to help you diagnose issues.
-   **Check the logs**: BenchBox creates log files in the output directory. These can contain valuable information for troubleshooting.
-   **Start small**: When testing a new setup, start with a small scale factor (e.g., `0.01`) to quickly verify that everything is working correctly.

## Getting Help

### Diagnostic Information

When reporting issues, include:

```bash
benchbox --version
python --version
uname -a

benchbox platforms list

benchbox run --platform <platform> --benchmark tpch --scale 0.01 \
  --verbose 2>&1 | tee benchmark_error.log
```

### Resources

- [GitHub Issues](https://github.com/BenchBox-dev/benchbox/issues) - Report bugs
- [Platform Docs](../platforms/index.md) - Platform-specific guides
- [Configuration Guide](configuration.md) - Detailed options

## Related Documentation

- [Platform Selection Guide](../platforms/platform-selection-guide.md)
- [Cloud Storage Guide](../guides/cloud-storage.md)
- [Getting Started](getting-started.md)
