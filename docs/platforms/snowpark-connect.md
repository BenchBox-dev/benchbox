<!-- Copyright 2026 Joe Harris / BenchBox Project. Licensed under the MIT License. -->

# Snowpark Connect for Spark

```{tags} intermediate, guide, snowflake, cloud-platform
```

Snowpark Connect provides a PySpark DataFrame API that executes natively on Snowflake. This is **NOT Apache Spark** - DataFrame operations are translated to Snowflake SQL, providing a familiar API without requiring a Spark cluster.

## Features

- **PySpark Compatibility** - Familiar DataFrame API for Spark developers
- **No Cluster Required** - Executes on Snowflake's native engine
- **Instant Startup** - No cluster provisioning delay
- **Snowflake Optimization** - Benefits from Snowflake's query optimizer
- **Credit-Based Billing** - Standard Snowflake warehouse credits

## Important Limitations

Unlike Apache Spark, Snowpark Connect has some limitations:

| Feature | Snowpark Connect | Apache Spark |
|---------|------------------|--------------|
| RDD APIs | Not supported | Full support |
| DataFrame.hint() | No-op | Optimizer hints |
| DataFrame.repartition() | No-op | Partition control |
| UDFs | Snowpark UDFs only | Python/Scala UDFs |
| Streaming | Not supported | Structured Streaming |

## Installation

```bash
uv add benchbox --extra cloud-spark-snowflake
```

This installs `snowflake-snowpark-python` and PySpark.

## Prerequisites

1. **Snowflake account** with active warehouse
2. **Credentials** configured:
   - Username/password authentication
   - Key-pair authentication
   - OAuth (browser-based)

## Configuration

### Credentials

Pass credentials to `SnowparkConnectAdapter` in the Python API (see below).
`benchbox run` has no way to supply them for this platform: account, user,
and password are not `--platform-option` keys, and the adapter does not read
`SNOWFLAKE_*` environment variables or the credentials saved by
`benchbox setup --platform snowflake`.

### CLI Usage

Without credentials, the CLI is limited to dry runs that preview the queries:

```bash
benchbox run --platform snowpark-connect --benchmark tpch --dry-run ./preview \
  --platform-option compute_resource=BENCHMARK_WH
```

### Platform Options

The **Set with** column shows how to set each option: a `--platform-option`
key on `benchbox run`, or a `SnowparkConnectAdapter` argument in the Python
API. In the Python API, the warehouse arguments are named `warehouse` and
`warehouse_size`.

| Option | Set with | Default | Description |
|--------|----------|---------|-------------|
| `account` | Python API | *required* | Snowflake account identifier |
| `user` | Python API | *required* | Snowflake username |
| `password` | Python API | - | Password (required unless using key auth) |
| `compute_resource` | `--platform-option` | COMPUTE_WH | Named compute resource (Snowflake warehouse; deprecated alias: `warehouse`, removed in 0.6.0) |
| `database` | Python API | BENCHBOX | Database name |
| `schema` | Python API | PUBLIC | Schema name |
| `role` | Python API | - | Role to use for session |
| `authenticator` | Python API | - | Auth method (snowflake, externalbrowser, oauth) |
| `private_key_path` | Python API | - | Path to private key for key-pair auth |
| `compute_size` | `--platform-option` | MEDIUM | Compute size for scaling (deprecated alias: `warehouse_size`, removed in 0.6.0) |

## Python API

```python
from benchbox.platforms.snowpark_connect import SnowparkConnectAdapter

adapter = SnowparkConnectAdapter(
    account="xy12345.us-east-1",
    user="my_user",
    password="my_password",
    warehouse="COMPUTE_WH",
    database="BENCHBOX",
)

adapter.create_connection()

adapter.create_schema("tpch_benchmark")

result = adapter.execute_query("SELECT COUNT(*) FROM lineitem")

df = adapter.get_dataframe("lineitem")
result = adapter.execute_dataframe(df.filter(df["l_quantity"] > 30).limit(10))

adapter.close()
```

The example initializes the adapter with credentials, then creates the session (no Spark cluster is needed). It creates a schema and executes a SQL query. It then uses the DataFrame API and closes the session.

## Execution Model

Snowpark Connect uses a fundamentally different execution model than Spark:

1. **Session Start** - Create Snowpark session (instant, no cluster)
2. **DataFrame Operations** - Build DataFrame expression tree
3. **Translation** - Operations translated to Snowflake SQL
4. **Execution** - SQL runs on Snowflake warehouse
5. **Results** - Data returned directly

## When to Use Snowpark Connect

**Good fit:**
- Teams familiar with PySpark who use Snowflake
- Existing Snowflake warehouse investments
- DataFrame-based analytics without cluster management
- Quick ad-hoc analysis with PySpark syntax

**Consider alternatives:**
- Need RDD APIs → Apache Spark
- Need Spark Streaming → Databricks/EMR
- Need full Spark UDF support → Apache Spark
- Need partition control → Apache Spark

## Authentication

### Password Authentication

```python
adapter = SnowparkConnectAdapter(
    account="xy12345.us-east-1",
    user="my_user",
    password="my_password",
)
```

### Key-Pair Authentication

```python
adapter = SnowparkConnectAdapter(
    account="xy12345.us-east-1",
    user="my_user",
    private_key_path="/path/to/rsa_key.p8",
    private_key_passphrase="optional_passphrase",
)
```

### Browser-Based SSO

```python
adapter = SnowparkConnectAdapter(
    account="xy12345.us-east-1",
    user="my_user",
    authenticator="externalbrowser",
)
```

## Cost Estimation

Snowpark Connect uses standard Snowflake credit consumption:

| Warehouse Size | Credits/Hour | Typical $/Credit |
|----------------|--------------|------------------|
| X-Small | 1 | $2-4 |
| Small | 2 | $2-4 |
| Medium | 4 | $2-4 |
| Large | 8 | $2-4 |
| X-Large | 16 | $2-4 |

| Scale Factor | Data Size | Est. Runtime | Est. Cost* |
|--------------|-----------|--------------|------------|
| 0.01 | ~10 MB | ~5 min | ~$0.05 |
| 1.0 | ~1 GB | ~30 min | ~$0.50 |
| 10.0 | ~10 GB | ~2 hours | ~$4.00 |
| 100.0 | ~100 GB | ~6 hours | ~$20.00 |

*Estimates based on Medium warehouse. Actual costs vary by contract.

## Comparison with Other Platforms

| Aspect | Snowpark Connect | Snowflake SQL | Databricks Spark |
|--------|------------------|---------------|------------------|
| API | PySpark DataFrame | SQL | PySpark/SQL |
| Cluster | None (Snowflake) | None (Snowflake) | Spark cluster |
| RDD Support | No | N/A | Yes |
| Startup | Instant | Instant | Minutes |
| Billing | Credits | Credits | DBU + compute |
| Use Case | DataFrame + Snowflake | SQL + Snowflake | Full Spark |

## Troubleshooting

### Common Issues

**Account identifier format:**
- Format: `xy12345.region` or `xy12345.region.cloud`
- Example: `xy12345.us-east-1` or `xy12345.us-east-1.aws`
- Don't include `.snowflakecomputing.com`

**Warehouse not found:**
- Verify warehouse exists: `SHOW WAREHOUSES;`
- Check permissions on warehouse
- Warehouse may be suspended (auto-resumes on first query)

**Authentication errors:**
- Verify account identifier is correct
- Check username and password
- For key-pair: ensure private key format is correct (PKCS#8)

**Permission denied:**
- Check role has necessary privileges
- Verify database and schema access
- Check warehouse usage permission

## Related

- [Snowflake SQL](snowflake.md) - Direct SQL access to Snowflake
- [Databricks](databricks.md) - Full Spark with Delta Lake
- [PySpark](pyspark-dataframe.md) - Apache Spark DataFrame API
- [Cloud Platforms Overview](cloud-platforms.md) - All cloud platforms
