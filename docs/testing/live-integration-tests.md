# Live Integration Tests

```{tags} contributor, guide, testing
```

This document describes BenchBox's live integration test suite that executes real benchmarks against cloud database platforms using actual credentials.

```{tip}
For platforms that run locally in Docker (ClickHouse, Trino, Presto, PostgreSQL, StarRocks, Doris, Databend, InfluxDB), see [Docker Integration Tests](docker-integration-tests.md) instead (no cloud credentials needed).
```

## Overview

Live integration tests verify that BenchBox works correctly with real cloud databases by:
- Connecting to actual Databricks, Snowflake, BigQuery, Redshift, and Athena instances
- Creating test schemas and loading data
- Executing queries and verifying results
- Cleaning up test resources

**Important**: These tests are **skipped by default** and only run when credentials are available.

## Prerequisites

### General Requirements

- BenchBox installed with cloud platform extras: `pip install "benchbox[cloud]"`
- Active cloud platform accounts with appropriate permissions
- Environment variables configured with credentials

### Cost Considerations

All tests use `scale_factor=0.01` (~10MB of data) to minimize costs:
- **Databricks**: <$0.05 per test run (SQL Warehouse usage)
- **Snowflake**: <$0.05 per test run (Warehouse credit usage)
- **BigQuery**: <$0.10 per test run (Storage + query processing)

**Total estimated cost**: <$0.20 per full test run across all platforms

## Setup Instructions

### 1. Create Environment File

Copy the template and fill in your credentials:

```bash
cp .env.example .env
```

### 2. Configure Credentials

Edit `.env` and add credentials for the platforms you want to test:

#### Databricks
```bash
DATABRICKS_HOST=your-workspace.cloud.databricks.com
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/your-warehouse-id
DATABRICKS_TOKEN=your-access-token
```

**How to get Databricks credentials**:
1. Log into your Databricks workspace
2. Go to SQL Warehouses and select a warehouse
3. Click "Connection Details"
4. Copy the Server hostname and HTTP path
5. Generate a Personal Access Token from User Settings → Access Tokens

#### Snowflake
```bash
SNOWFLAKE_ACCOUNT=your-account.snowflakecomputing.com
SNOWFLAKE_USERNAME=your-username
SNOWFLAKE_PASSWORD=your-password
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_DATABASE=BENCHBOX
SNOWFLAKE_SCHEMA=PUBLIC
```

**How to get Snowflake credentials**:
1. Your account identifier (e.g., `xy12345.us-east-1`)
2. Username and password for authentication
3. Warehouse name (create one if needed: `CREATE WAREHOUSE COMPUTE_WH`)
4. Database and schema for testing (will be created if missing)

#### BigQuery
```bash
BIGQUERY_PROJECT=your-project-id
BIGQUERY_DATASET=benchbox_test
BIGQUERY_LOCATION=US
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

**How to get BigQuery credentials**:
1. Create a Google Cloud project
2. Enable the BigQuery API
3. Create a service account with BigQuery Admin role
4. Download the service account JSON key file
5. Set `GOOGLE_APPLICATION_CREDENTIALS` to the key file path

### 3. Verify Setup

Test that credentials are configured correctly. The first command checks what would run (dry run). The second runs a single platform test:

```bash
make test-live-databricks --dry-run

make test-live-databricks
```

## Running Tests

### Run All Live Tests
This runs all platforms and requires all credentials:
```bash
make test-live-all
```

### Run Individual Platforms
The commands below run Databricks only, Snowflake only, and BigQuery only:
```bash
make test-live-databricks

make test-live-snowflake

make test-live-bigquery
```

### Run with pytest Directly
The commands below run all live tests, one platform's tests, and one test file:
```bash
uv run -- python -m pytest -m live_integration -v

uv run -- python -m pytest -m live_databricks -v

uv run -- python -m pytest tests/integration/platforms/test_databricks_live.py -v
```

## Test Coverage

### Databricks Tests (9 tests)

1. **Connection** - Verify SQL Warehouse connectivity
2. **Version Info** - Get platform metadata
3. **Catalog Access** - Verify Unity Catalog permissions
4. **Schema Creation** - Create isolated test schemas
5. **Data Loading** - Load TPC-H SF=0.01 data
6. **Simple Query** - Execute basic SELECT queries
7. **TPC-H Query** - Run TPC-H Query 1 on loaded data
8. **COPY INTO** - Test COPY INTO functionality
9. **Cleanup** - Verify schema deletion works

### Snowflake Tests (9 tests)

1. **Connection** - Verify Snowflake connectivity
2. **Version Info** - Get platform metadata
3. **Warehouse Access** - Verify warehouse and database access
4. **Schema Creation** - Create isolated test schemas
5. **Data Loading** - Load TPC-H SF=0.01 data
6. **Simple Query** - Execute basic SELECT queries
7. **TPC-H Query** - Run TPC-H Query 1 on loaded data
8. **PUT + COPY** - Test PUT + COPY INTO workflow
9. **Cleanup** - Verify schema deletion works

### BigQuery Tests (10 tests)

1. **Connection** - Verify BigQuery connectivity
2. **Version Info** - Get platform metadata
3. **Project Access** - Verify project and dataset access
4. **Dataset Creation** - Create isolated test datasets
5. **Data Loading** - Load TPC-H SF=0.01 data
6. **Simple Query** - Execute basic SELECT queries
7. **TPC-H Query** - Run TPC-H Query 1 on loaded data
8. **GCS Load** - Test GCS staging workflow
9. **Query Cost** - Verify query cost estimation
10. **Cleanup** - Verify dataset deletion works

**Total**: 28 live integration tests across 3 cloud platforms

## Security Best Practices

### Never Commit Credentials

- `.env` files are in `.gitignore` - never commit them
- Use `.env.example` as a template with placeholder values
- Rotate tokens/passwords regularly

### Use Least Privilege

Create dedicated service accounts with minimal permissions:

**Databricks**:
- SQL Warehouse usage permission
- Schema create/drop permission
- No admin/cluster create permissions needed

**Snowflake**:
- `CREATE SCHEMA` privilege
- `USAGE` on warehouse and database
- No `ACCOUNTADMIN` role needed

**BigQuery**:
- BigQuery Data Editor role
- BigQuery Job User role
- Storage Object Admin (for GCS staging)

### Isolate Test Resources

- Tests use unique schema names with timestamps
- Cleanup fixtures automatically remove test data
- No production data is touched

## Troubleshooting

### Tests are Skipped

**Problem**: All tests show `SKIPPED`

**Solution**: Check that required environment variables are set. The commands below check Databricks, Snowflake, and BigQuery in that order:
```bash
echo $DATABRICKS_TOKEN

echo $SNOWFLAKE_PASSWORD

echo $GOOGLE_APPLICATION_CREDENTIALS
```

### Authentication Errors

**Databricks**: "Invalid access token"
- Verify token hasn't expired
- Check workspace URL is correct
- Ensure SQL Warehouse is running

**Snowflake**: "Incorrect username or password"
- Verify credentials in Snowflake UI
- Check account identifier format
- Try connecting with `snowsql` CLI first

**BigQuery**: "Could not load credentials"
- Verify JSON key file path exists
- Check service account has required roles
- Try `gcloud auth application-default login`

### Data Loading Failures

**Problem**: Tests fail during data loading

**Solutions**:
- Verify sufficient warehouse/compute resources
- Check network connectivity
- Ensure sufficient storage quota
- Review platform-specific logs

### Schema Cleanup Failures

**Problem**: Warning messages about failed schema deletion

**Impact**: Usually benign - schemas with timestamps avoid conflicts

**Solution**: Manually drop test schemas if needed. The statements below apply to Databricks, Snowflake, and BigQuery in that order, and are identical across the three:
```sql
DROP SCHEMA IF EXISTS benchbox_test_1234567890 CASCADE;

DROP SCHEMA IF EXISTS benchbox_test_1234567890 CASCADE;

DROP SCHEMA IF EXISTS benchbox_test_1234567890 CASCADE;
```

## CI/CD Integration (Optional)

### GitHub Actions

Tests can run in CI with secrets. In this workflow, `workflow_dispatch` is a manual trigger and the cron schedule runs weekly on Sunday:

```yaml
name: Live Integration Tests

on:
  workflow_dispatch:
  schedule:
    - cron: '0 0 * * 0'

jobs:
  test-databricks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install ".[cloud]"
      - name: Run Databricks live tests
        env:
          DATABRICKS_HOST: ${{ secrets.DATABRICKS_HOST }}
          DATABRICKS_HTTP_PATH: ${{ secrets.DATABRICKS_HTTP_PATH }}
          DATABRICKS_TOKEN: ${{ secrets.DATABRICKS_TOKEN }}
        run: make test-live-databricks
```

**Note**: Store credentials as GitHub Secrets, never in workflow files.

### Cost Control in CI

- Run on manual trigger or schedule (not on every commit)
- Use smallest possible scale factors
- Set test timeouts to prevent runaway costs
- Monitor cloud spend regularly

## Development Workflow

### Adding New Tests

1. Follow the existing test pattern in `test_databricks_live.py`
2. Use fixtures from `conftest.py`
3. Always include cleanup logic
4. Record expected cost in the Cost Considerations section above

### Testing Without Credentials

Regular smoke tests don't require credentials and use stubs. Either command runs them:
```bash
make test-smoke
uv run -- python -m pytest -m platform_smoke -v
```

### Debugging Live Tests

Enable verbose pytest output, and capture logs with the second command:
```bash
uv run -- python -m pytest -m live_databricks -vv -s

uv run -- python -m pytest -m live_databricks -v --log-cli-level=DEBUG
```

## Additional Resources

- [BenchBox Documentation](../index.md)
- [Platform Setup Guide](../platforms/index.md)
- [Databricks SQL Warehouse Docs](https://docs.databricks.com/aws/en/compute/sql-warehouse/create)
- [Snowflake Connection Docs](https://docs.snowflake.com/en/user-guide/admin-account-identifier)
- [BigQuery Authentication Docs](https://docs.cloud.google.com/bigquery/docs/authentication)

## Support

For issues or questions:
- [GitHub Issues](https://github.com/BenchBox-dev/BenchBox/issues)
- Review test output for specific error messages
- Check platform-specific documentation

---

**Remember**: Always protect your credentials and be mindful of cloud costs when running live integration tests.
